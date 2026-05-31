"""
Validation Engine — v1.2 (Phase 3a, B1)
----------------------------------------
Deterministic, rule-based cross-statement validation. Does not rely on
the LLM for accuracy-critical checks — produces concrete data structures
that the master Excel generator and the validation report consumer can use.

v1.2 (Phase 3a) changes:
    * B1: Symmetric name variant entries are now deduplicated before being
      returned. The pair-comparison loop produces both (A, B) and (B, A)
      directions when the same two names appear in opposite statements;
      `_dedup_name_variants()` collapses these to one canonical entry per
      unique name-pair (alphabetically-ordered). ReviewFlags propagation
      is unaffected — dedup is consumer-facing only.

The engine produces a DeterministicValidation record containing:

  cross_matches:        same canonical name in 2+ statements with combined totals
  threshold_crossings:  vendors whose combined total crosses $600 even when
                        no single statement crossed alone
  near_threshold:       vendors in the $500-$700 review zone
  name_variants:        likely-same-payee under different names (fuzzy match)
  amount_mismatches:    same vendor, suspicious amount variance across statements

Each finding mutates the per-statement ReviewFlags so that signals propagate
back into the per-vendor review flag for the Excel output.

The Claude validation agent receives this structured data and writes the
narrative report on top of it — best of both worlds.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from review_flag_engine import (
    ReviewFlags,
    THRESHOLD_AMOUNT,
    NEAR_THRESHOLD_LOW,
    NEAR_THRESHOLD_HIGH,
    check_cross_statement_mismatch,
    mark_name_variant,
)


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

NAME_VARIANT_SIMILARITY_THRESHOLD = 0.65   # Fuzzy match threshold for "same payee"
                                            # Catches "J Smith" ~ "John Smith LLC" (0.67)
                                            # but rejects unrelated names


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CrossMatch:
    """Same canonical vendor in multiple statements."""
    canonical_name: str
    appearances: list[dict] = field(default_factory=list)
    # appearances: [{"statement": "boa.pdf", "amount": 1234.56, "count": 3}]
    combined_total: float = 0.0
    crosses_threshold_combined_only: bool = False


@dataclass
class NameVariant:
    """Likely-same payee appearing under different names."""
    statements_involved: list[str]    # statement labels
    name_a: str
    name_b: str
    similarity: float
    amount_a: float
    amount_b: float
    statement_a: str
    statement_b: str


@dataclass
class AmountMismatch:
    """Same vendor with suspicious amount variance across statements."""
    canonical_name: str
    statement_a: str
    amount_a: float
    statement_b: str
    amount_b: float
    ratio: float
    abs_diff: float


@dataclass
class NearThreshold:
    """Vendor in the $500-$700 review zone."""
    canonical_name: str
    total_amount: float
    statement: str   # or "Combined" if cross-statement
    distance_to_threshold: float


@dataclass
class DeterministicValidation:
    """Complete output of the validation engine — pure data."""
    cross_matches: list[CrossMatch] = field(default_factory=list)
    threshold_crossings: list[CrossMatch] = field(default_factory=list)
    name_variants: list[NameVariant] = field(default_factory=list)
    amount_mismatches: list[AmountMismatch] = field(default_factory=list)
    near_threshold: list[NearThreshold] = field(default_factory=list)
    statements_processed: list[str] = field(default_factory=list)
    statements_excluded: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    """Fuzzy similarity ratio between two vendor name strings."""
    return SequenceMatcher(None, a.upper(), b.upper()).ratio()

def _dedup_name_variants(variants: list[NameVariant]) -> list[NameVariant]:
    """
    Deduplicate symmetric name variant entries.

    The pair-comparison loop in `run_deterministic_validation` produces both
    (A, B) and (B, A) when the same two vendor names appear in opposite
    statements. This collapses them into one canonical entry per unique
    name-pair, keeping the entry where name_a sorts alphabetically before
    name_b (case-insensitive). Predictable, deterministic ordering makes
    the Master Excel Name Variant Flags section diff-able across runs.

    Note: dedup operates on the consumer-facing list only. The flag
    propagation in `mark_name_variant()` still fires on every cross-direction
    comparison so that both statements' ReviewFlags correctly record the
    cross-statement signal — only the visible findings list is collapsed.

    v1.2 (Phase 3a, B1): Introduced to fix Master Vendor Summary Name
    Variant Flags section and Consolidated Validation count showing
    inflated numbers (e.g., 3 underlying pairs reported as 5 entries on
    the 2-PDF test corpus where the same name-pair appeared in both
    cross-directions).
    """
    seen: set[tuple[str, str]] = set()
    deduped: list[NameVariant] = []
    for nv in variants:
        # Canonical key: alphabetically-ordered name pair (case-insensitive)
        pair = tuple(sorted([nv.name_a.upper(), nv.name_b.upper()]))
        if pair in seen:
            continue
        seen.add(pair)
        # Normalize entry so name_a is alphabetically first.
        # This makes Excel output stable and easier to compare across runs.
        if nv.name_a.upper() > nv.name_b.upper():
            nv = NameVariant(
                statements_involved=nv.statements_involved,
                name_a=nv.name_b,
                name_b=nv.name_a,
                similarity=nv.similarity,
                amount_a=nv.amount_b,
                amount_b=nv.amount_a,
                statement_a=nv.statement_b,
                statement_b=nv.statement_a,
            )
        deduped.append(nv)
    return deduped

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_deterministic_validation(
    agent_outputs: list[dict],
    flags_by_statement: dict[str, dict[str, ReviewFlags]],
) -> DeterministicValidation:
    """
    Cross-validate vendor data across multiple agent outputs.

    Args:
        agent_outputs: list of dicts, each with:
            - "statement_label": str (filename)
            - "status": str ("success" | "partial" | "failed_*")
            - "vendors": list of dicts {canonical_name, entity_type,
                                         total_amount, transaction_count,
                                         match_confidence, needs_review}
            - "error_message": str | None
        flags_by_statement: dict mapping statement_label -> dict[name, ReviewFlags]
            Mutated in-place to add cross-statement findings.

    Returns:
        DeterministicValidation record with all findings.
    """
    result = DeterministicValidation()

    # Track which statements actually contributed data
    successful = []
    for out in agent_outputs:
        status = out.get("status", "failed_other")
        if status in ("success", "partial") and out.get("vendors"):
            successful.append(out)
            result.statements_processed.append(out["statement_label"])
        else:
            result.statements_excluded.append({
                "statement": out["statement_label"],
                "status": status,
                "reason": out.get("error_message") or "No vendor data extracted",
            })

    if len(successful) < 2:
        # Not enough data for cross-validation — return what we have
        # (per-statement flags already built by review_flag_engine)
        return result

    # ── 1. Cross-statement vendor matches (exact canonical name) ──
    # Build a global map: canonical_name -> [(statement, amount, count), ...]
    name_map: dict[str, list[tuple[str, float, int]]] = {}
    for out in successful:
        label = out["statement_label"]
        for v in out["vendors"]:
            name_map.setdefault(v["canonical_name"], []).append(
                (label, v["total_amount"], v["transaction_count"])
            )

    for name, appearances in name_map.items():
        if len(appearances) < 2:
            continue

        combined = sum(a[1] for a in appearances)
        cm = CrossMatch(
            canonical_name=name,
            appearances=[
                {"statement": s, "amount": amt, "count": c}
                for s, amt, c in appearances
            ],
            combined_total=combined,
        )

        # Does combined total cross threshold while no single statement does?
        max_individual = max(a[1] for a in appearances)
        if combined >= THRESHOLD_AMOUNT and max_individual < THRESHOLD_AMOUNT:
            cm.crosses_threshold_combined_only = True
            result.threshold_crossings.append(cm)
            # Flag in EVERY participating statement's review flags
            for stmt, _, _ in appearances:
                if name in flags_by_statement.get(stmt, {}):
                    flags_by_statement[stmt][name].add_reason(
                        f"Combined total across statements (${combined:,.2f}) "
                        f"crosses $600 threshold"
                    )

        result.cross_matches.append(cm)

    # ── 2. Amount mismatches (same name, suspicious variance) ──
    for name, appearances in name_map.items():
        if len(appearances) < 2:
            continue
        # Pairwise compare each appearance
        for i in range(len(appearances)):
            for j in range(i + 1, len(appearances)):
                stmt_a, amt_a, _ = appearances[i]
                stmt_b, amt_b, _ = appearances[j]

                if amt_a == 0 or amt_b == 0:
                    continue
                ratio = max(amt_a, amt_b) / min(amt_a, amt_b)
                abs_diff = abs(amt_a - amt_b)
                from review_flag_engine import MISMATCH_RATIO_MIN, MISMATCH_ABSOLUTE_MIN
                if ratio > MISMATCH_RATIO_MIN and abs_diff > MISMATCH_ABSOLUTE_MIN:
                    result.amount_mismatches.append(AmountMismatch(
                        canonical_name=name,
                        statement_a=stmt_a, amount_a=amt_a,
                        statement_b=stmt_b, amount_b=amt_b,
                        ratio=ratio, abs_diff=abs_diff,
                    ))
                    # Mark in flags
                    fa = flags_by_statement.get(stmt_a, {}).get(name)
                    fb = flags_by_statement.get(stmt_b, {}).get(name)
                    if fa and fb:
                        check_cross_statement_mismatch(
                            fa, amt_a, stmt_a,
                            fb, amt_b, stmt_b,
                        )

    # ── 3. Name variant detection (fuzzy match across statements) ──
    # Compare each pair of statements, all vendor pairs
    for i, out_a in enumerate(successful):
        for out_b in successful[i + 1:]:
            for v_a in out_a["vendors"]:
                for v_b in out_b["vendors"]:
                    name_a = v_a["canonical_name"]
                    name_b = v_b["canonical_name"]

                    # Skip exact matches (already handled above)
                    if name_a == name_b:
                        continue

                    sim = _similarity(name_a, name_b)
                    if sim >= NAME_VARIANT_SIMILARITY_THRESHOLD:
                        result.name_variants.append(NameVariant(
                            statements_involved=[
                                out_a["statement_label"],
                                out_b["statement_label"],
                            ],
                            name_a=name_a, name_b=name_b,
                            similarity=sim,
                            amount_a=v_a["total_amount"],
                            amount_b=v_b["total_amount"],
                            statement_a=out_a["statement_label"],
                            statement_b=out_b["statement_label"],
                        ))
                        # Add reason to both flags
                        fa = flags_by_statement.get(out_a["statement_label"], {}).get(name_a)
                        fb = flags_by_statement.get(out_b["statement_label"], {}).get(name_b)
                        info = (
                            f"\"{name_a}\" in {out_a['statement_label']} "
                            f"~ \"{name_b}\" in {out_b['statement_label']} "
                            f"({sim:.0%} similar)"
                        )
                        if fa: mark_name_variant(fa, info)
                        if fb: mark_name_variant(fb, info)

    # ── 4. Near-threshold list (combined totals) ──
    # Track which names already appear as "Combined" so we don't double-list
    combined_names = set()
    for cm in result.cross_matches:
        if NEAR_THRESHOLD_LOW <= cm.combined_total <= NEAR_THRESHOLD_HIGH:
            result.near_threshold.append(NearThreshold(
                canonical_name=cm.canonical_name,
                total_amount=cm.combined_total,
                statement="Combined",
                distance_to_threshold=THRESHOLD_AMOUNT - cm.combined_total,
            ))
            combined_names.add(cm.canonical_name)

    # Single-statement near-threshold (skip if already counted as combined)
    for out in successful:
        for v in out["vendors"]:
            amt = v["total_amount"]
            name = v["canonical_name"]
            # Skip if already in combined list
            if name in combined_names:
                continue
            # Skip if vendor appears in multiple statements (will be aggregated)
            if len(name_map.get(name, [])) > 1:
                continue
            if NEAR_THRESHOLD_LOW <= amt <= NEAR_THRESHOLD_HIGH:
                result.near_threshold.append(NearThreshold(
                    canonical_name=name,
                    total_amount=amt,
                    statement=out["statement_label"],
                    distance_to_threshold=THRESHOLD_AMOUNT - amt,
                ))

    # B1: Dedupe symmetric name variant entries before returning.
    # When the same two vendor names appear in opposite statements
    # (e.g., "Mary Johnson Consulting" in A vs "John Smith Consulting" in B,
    # AND "John Smith Consulting" in A vs "Mary Johnson Consulting" in B),
    # the pair-comparison loop above flags both directions. Collapse to one.
    # ReviewFlags propagation already happened in-loop; this is consumer-
    # facing cleanup only.
    result.name_variants = _dedup_name_variants(result.name_variants)

    return result


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Mock two statements with realistic data
    agent_outputs = [
        {
            "statement_label": "boa_business_2024.pdf",
            "status": "success",
            "vendors": [
                {"canonical_name": "Comcast Business", "entity_type": None,
                 "total_amount": 104108.94, "transaction_count": 2,
                 "match_confidence": 1.0, "needs_review": False},
                {"canonical_name": "John Smith LLC", "entity_type": "LLC",
                 "total_amount": 400.00, "transaction_count": 1,
                 "match_confidence": 1.0, "needs_review": False},
                {"canonical_name": "Mary Johnson Consulting", "entity_type": "LLC",
                 "total_amount": 3500.00, "transaction_count": 4,
                 "match_confidence": 1.0, "needs_review": False},
                {"canonical_name": "Adobe", "entity_type": None,
                 "total_amount": 580.00, "transaction_count": 1,
                 "match_confidence": 1.0, "needs_review": False},
            ],
            "error_message": None,
        },
        {
            "statement_label": "sample_bank_2024.pdf",
            "status": "success",
            "vendors": [
                {"canonical_name": "Comcast Business", "entity_type": None,
                 "total_amount": 499.98, "transaction_count": 2,
                 "match_confidence": 1.0, "needs_review": False},
                {"canonical_name": "J Smith", "entity_type": None,
                 "total_amount": 250.00, "transaction_count": 1,
                 "match_confidence": 1.0, "needs_review": False},
                {"canonical_name": "Mary Johnson", "entity_type": None,
                 "total_amount": 1000.00, "transaction_count": 2,
                 "match_confidence": 1.0, "needs_review": False},
            ],
            "error_message": None,
        },
    ]

    # Build empty flags
    flags = {
        out["statement_label"]: {
            v["canonical_name"]: ReviewFlags(canonical_name=v["canonical_name"])
            for v in out["vendors"]
        }
        for out in agent_outputs
    }

    result = run_deterministic_validation(agent_outputs, flags)

    print(f"\n=== Deterministic Validation Results ===")
    print(f"Statements processed: {result.statements_processed}")
    print(f"Statements excluded:  {result.statements_excluded}")
    print(f"\nCross-statement matches: {len(result.cross_matches)}")
    for cm in result.cross_matches:
        crosses = " 🚨 crosses combined" if cm.crosses_threshold_combined_only else ""
        print(f"  • {cm.canonical_name}: ${cm.combined_total:,.2f} combined{crosses}")
        for a in cm.appearances:
            print(f"      - {a['statement']}: ${a['amount']:,.2f}")

    print(f"\nThreshold crossings (combined-only): {len(result.threshold_crossings)}")
    for cm in result.threshold_crossings:
        print(f"  • {cm.canonical_name}: ${cm.combined_total:,.2f}")

    print(f"\nName variants: {len(result.name_variants)}")
    for nv in result.name_variants:
        print(f"  • \"{nv.name_a}\" ~ \"{nv.name_b}\" ({nv.similarity:.0%})")

    print(f"\nAmount mismatches: {len(result.amount_mismatches)}")
    for am in result.amount_mismatches:
        print(f"  • {am.canonical_name}: ${am.amount_a:,.2f} vs ${am.amount_b:,.2f} "
              f"({am.ratio:.1f}x)")

    print(f"\nNear-threshold: {len(result.near_threshold)}")
    for nt in result.near_threshold:
        print(f"  • {nt.canonical_name}: ${nt.total_amount:,.2f} ({nt.statement})")
