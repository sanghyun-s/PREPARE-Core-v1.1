"""
Main Pipeline: PDF Bank Statement -> Accountant-Grade Excel Workbook
---------------------------------------------------------------------
This is the end-to-end orchestration script. It takes:
    - A bank/credit card statement PDF
    - An optional vendor list (CSV with known canonical names)

And produces:
    - A three-sheet Excel workbook (Vendor Summary / Transactions / Stats)

In TAU integration, this logic wraps behind a FastAPI endpoint:
    POST /api/tax/pdf-to-excel
         body: multipart form with pdf_file (required) and vendor_list (optional)
         response: generated Excel file (or path to it)

v1.2 — Transaction Classifier integration
-----------------------------------------
After raw extraction (Step 1), every transaction passes through the
classifier (backend/transaction_classifier.py), which tags each row with
transaction_type, include_for_1099, review_required, and exclusion_reason.

Only rows where include_for_1099 == True flow into normalization and
aggregation. The full classified list (including excluded rows) is
preserved in the response payload for transparency. This eliminates the
multi-column extraction artifact where payroll deposits and balance
lines were being summed into vendor totals.

v1.3 — PDF Skill ingestion path
--------------------------------
New function `run_pipeline_pdf_skill()` is the alternative ingestion path
using Anthropic's pre-built `pdf` Agent Skill via the Claude Agent SDK.
The existing `run_pipeline()` (rule-based, pdfplumber + regex) is
unchanged and kept as a fallback engine.

Both functions return the same shape so server.py can route between
engines uniformly.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from pdf_extractor import extract_transactions
from pdf_skill_adapter import (
    extract_from_pdf as extract_pdf_skill,
    to_pipeline_transactions,
    PDFSkillExtractionResult,
)
from vendor_normalizer import normalize_vendor
from transaction_aggregator import aggregate_by_vendor
from transaction_classifier import classify_transactions, filter_for_aggregation
from excel_generator import generate_excel_report
from vendor_classifier_1099 import classify_all_vendors


def load_vendor_list(csv_path: str) -> list[str]:
    """Load known vendor names from a CSV (first column, header optional)."""
    if not csv_path:
        return []
    vendors = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        if first_row:
            # If first cell looks like a header, skip it
            if first_row[0].lower() in ("vendor", "vendor name", "name", "canonical name"):
                pass
            else:
                vendors.append(first_row[0].strip())
        for row in reader:
            if row and row[0].strip():
                vendors.append(row[0].strip())
    return vendors


# ---------------------------------------------------------------------------
# v1.3 — PDF Skill ingestion path
# ---------------------------------------------------------------------------

def _avg_confidence(txns: list[dict]) -> float:
    """Helper for run_pipeline_pdf_skill — average row-level confidence."""
    if not txns:
        return 0.0
    confs = [float(t.get("confidence", 0) or 0) for t in txns]
    return round(sum(confs) / len(confs), 3) if confs else 0.0


# v1.4 (Phase 4): reconciliation balance equation + status.
RECONCILIATION_TOLERANCE = 0.01   # dollars; one cent (matches Phase 4 spec)

_RECON_INPUT_FIELDS = (
    "beginning_balance", "total_deposits", "total_withdrawals",
    "checks", "transfers", "fees", "reported_ending_balance",
)


def _compute_reconciliation(snapshot: dict) -> dict:
    """
    Take the raw reconciliation_snapshot transcribed by the PDF Skill (the
    seven AS-STATED figures) and compute calculated_ending_balance, difference,
    and status. Returns a complete snapshot dict ready for the schema.

    The model NEVER computes the balance — only transcribes. This is the single
    place the arithmetic happens, so the check is independent of the statement's
    own stated ending.

        calculated_ending = beginning + deposits - withdrawals - checks
                            - transfers - fees
        difference        = calculated_ending - reported_ending
        status:
          "balanced"     if all seven figures present and |difference| <= 0.01
          "needs_review" if all seven present and |difference|  > 0.01
          "unavailable"  if any required figure is missing (None)

    `checks` and `transfers` are treated as 0.0 when the statement legitimately
    has no such section (the prompt returns 0.0 for genuinely-absent sections),
    so a missing checks/transfers line does NOT make the snapshot unavailable.
    The figures that gate availability are the five always-present ones plus
    the reported ending.
    """
    if not snapshot or not isinstance(snapshot, dict):
        return {
            "beginning_balance": None, "total_deposits": None,
            "total_withdrawals": None, "checks": None, "transfers": None,
            "fees": None, "reported_ending_balance": None,
            "calculated_ending_balance": None, "difference": None,
            "status": "unavailable", "extraction_complete": False,
            "fields_found": [], "notes": None,
        }

    def _num(key):
        v = snapshot.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    beginning = _num("beginning_balance")
    deposits = _num("total_deposits")
    withdrawals = _num("total_withdrawals")
    checks = _num("checks")
    transfers = _num("transfers")
    fees = _num("fees")
    reported = _num("reported_ending_balance")

    # checks/transfers default to 0.0 (a genuinely-absent section is zero).
    checks_eff = 0.0 if checks is None else checks
    transfers_eff = 0.0 if transfers is None else transfers

    # Required for a balance computation: the five flow figures + reported end.
    required = [beginning, deposits, withdrawals, fees, reported]
    extraction_complete = all(v is not None for v in required)

    if not extraction_complete:
        calc = diff = None
        status = "unavailable"
    else:
        calc = round(beginning + deposits - withdrawals - checks_eff
                     - transfers_eff - fees, 2)
        diff = round(calc - reported, 2)
        status = "balanced" if abs(diff) <= RECONCILIATION_TOLERANCE else "needs_review"

    ff = snapshot.get("fields_found")
    notes = snapshot.get("notes")
    return {
        "beginning_balance": beginning,
        "total_deposits": deposits,
        "total_withdrawals": withdrawals,
        "checks": checks_eff if extraction_complete or checks is not None else checks,
        "transfers": transfers_eff if extraction_complete or transfers is not None else transfers,
        "fees": fees,
        "reported_ending_balance": reported,
        "calculated_ending_balance": calc,
        "difference": diff,
        "status": status,
        "extraction_complete": extraction_complete,
        "fields_found": list(ff) if isinstance(ff, list) else [],
        "notes": notes if isinstance(notes, str) and notes else None,
    }


def _compute_source_b(all_transactions: list, recon_snapshot: dict) -> dict:
    """
    v1.4 (Phase 4 — Source B) — extraction-completeness cross-check.

    Companion check to _compute_reconciliation (Source A). Where Source A
    asks "does the statement's stated math balance?", Source B asks
    "do the extracted rows sum to the statement's stated activity totals?"

    Source B is the row-sum side: it buckets every extracted Transaction by
    `transaction_type`, sums the amounts per bucket, and compares against the
    stated activity figures already in recon_snapshot. Same arithmetic-in-one-
    place discipline as Source A; same tolerance (0.01).

    Bucketing (locked per the spike — see source_b_spike.py):
      deposits     ← deposit + interest + reimbursement
      withdrawals  ← vendor_payment  (NOT checks, NOT fees — those have their
                                       own stated lines)
      checks       ← check_payment
      transfers    ← transfer + owner_draw
      fees         ← bank_fee
    Skipped (do not contribute to activity): balance_line, payroll_deposit,
      metadata, unknown.

    Status rules:
      complete    — recon_snapshot is available (extraction_complete=True),
                    AND every bucket's delta is within RECONCILIATION_TOLERANCE.
                    A bucket where stated is None but row_sum is 0 also passes
                    (genuinely-absent section).
      incomplete  — recon_snapshot is available but at least one bucket's
                    |delta| exceeds tolerance (likely missed/miscounted rows).
      unavailable — recon_snapshot is not available (no stated totals to
                    compare against; could be rule_based engine, or PDF Skill
                    that found no account summary).

    Returns a dict ready for the ExtractionCheck schema. None of the buckets
    are required to be present in recon_snapshot — missing stated figures
    fold into "unavailable" naturally because there's nothing to compare.
    """
    # Bucket mapping (raw transaction_type values → bucket key)
    BUCKET_MAP = {
        "deposit":         "deposits",
        "interest":        "deposits",
        "reimbursement":   "deposits",
        "vendor_payment":  "withdrawals",
        "check_payment":   "checks",
        "transfer":        "transfers",
        "owner_draw":      "transfers",
        "bank_fee":        "fees",
        # Skipped (not in BUCKET_MAP): balance_line, payroll_deposit,
        # metadata, unknown. These don't represent activity flows in the
        # statement's account-summary section.
    }

    # If no snapshot or snapshot incomplete, Source B is unavailable —
    # nothing to compare row sums against.
    snapshot_available = bool(
        recon_snapshot
        and isinstance(recon_snapshot, dict)
        and recon_snapshot.get("extraction_complete")
    )

    # Sum each bucket from the extracted rows.
    row_sums = {"deposits": 0.0, "withdrawals": 0.0, "checks": 0.0,
                "transfers": 0.0, "fees": 0.0}
    for t in all_transactions or []:
        ttype = getattr(t, "transaction_type", None)
        bucket = BUCKET_MAP.get(ttype)
        if bucket is None:
            continue
        try:
            row_sums[bucket] += float(getattr(t, "amount", 0) or 0)
        except (TypeError, ValueError):
            continue
    # Round to two decimals to avoid float drift before comparison.
    row_sums = {k: round(v, 2) for k, v in row_sums.items()}

    if not snapshot_available:
        return {
            "status": "unavailable",
            "deposits_stated":     None, "deposits_row_sum":     row_sums["deposits"],     "deposits_delta":     None,
            "withdrawals_stated":  None, "withdrawals_row_sum":  row_sums["withdrawals"],  "withdrawals_delta":  None,
            "checks_stated":       None, "checks_row_sum":       row_sums["checks"],       "checks_delta":       None,
            "transfers_stated":    None, "transfers_row_sum":    row_sums["transfers"],    "transfers_delta":    None,
            "fees_stated":         None, "fees_row_sum":         row_sums["fees"],         "fees_delta":         None,
            "notes": None,
        }

    # Build the comparison for each bucket. Stated values come from the
    # already-computed recon_snapshot (where None means the statement didn't
    # have that line — checks/transfers commonly absent).
    def _stated(key):
        v = recon_snapshot.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    stated = {
        "deposits":    _stated("total_deposits"),
        "withdrawals": _stated("total_withdrawals"),
        "checks":      _stated("checks"),
        "transfers":   _stated("transfers"),
        "fees":        _stated("fees"),
    }

    # Delta per bucket: row_sum - stated. None when stated absent AND row_sum
    # is also zero (a genuinely-absent section is fine; we don't ding it).
    deltas = {}
    any_mismatch = False
    for k in ("deposits", "withdrawals", "checks", "transfers", "fees"):
        rs = row_sums[k]
        st = stated[k]
        if st is None:
            # No stated figure. If the row sum is also zero, that's fine —
            # genuinely-absent section. Otherwise treat as a mismatch (row
            # data exists but statement didn't summarize it).
            if abs(rs) <= RECONCILIATION_TOLERANCE:
                deltas[k] = None
            else:
                deltas[k] = round(rs, 2)   # row sum is the "drift"
                any_mismatch = True
        else:
            d = round(rs - st, 2)
            deltas[k] = d
            if abs(d) > RECONCILIATION_TOLERANCE:
                any_mismatch = True

    status = "incomplete" if any_mismatch else "complete"

    return {
        "status": status,
        "deposits_stated":     stated["deposits"],
        "deposits_row_sum":    row_sums["deposits"],
        "deposits_delta":      deltas["deposits"],
        "withdrawals_stated":  stated["withdrawals"],
        "withdrawals_row_sum": row_sums["withdrawals"],
        "withdrawals_delta":   deltas["withdrawals"],
        "checks_stated":       stated["checks"],
        "checks_row_sum":      row_sums["checks"],
        "checks_delta":        deltas["checks"],
        "transfers_stated":    stated["transfers"],
        "transfers_row_sum":   row_sums["transfers"],
        "transfers_delta":     deltas["transfers"],
        "fees_stated":         stated["fees"],
        "fees_row_sum":        row_sums["fees"],
        "fees_delta":          deltas["fees"],
        "notes": None,
    }


def run_pipeline_pdf_skill(
    pdf_path: str,
    output_path: str,
    vendor_list_path: str = None,
    source: str = "bank",
    model: str = "claude-sonnet-4-6",
    verbose: bool = True,
) -> dict:
    """
    v1.3: PDF Skill ingestion path.

    Same return-dict shape as run_pipeline (rule-based path) so the FastAPI
    layer can treat both engines uniformly. On PDF Skill failure, returns
    a structured `success=False` dict — does NOT raise.

    Stability: PDF Skill failure does not crash the app. Caller (server.py)
    surfaces the failure as a per-statement card showing the failure_reason.
    """
    if verbose:
        print(f"[1/5] Calling PDF Skill on {Path(pdf_path).name}...")

    skill_result = extract_pdf_skill(pdf_path, model=model)

    # ── Translate PDF Skill failure into pipeline response shape ──
    if not skill_result.success:
        if verbose:
            print(f"      PDF Skill FAILED: {skill_result.failure_reason}")
            print(f"      Details: {skill_result.failure_details}")
        return {
            "success": False,
            "error": f"PDF Skill extraction failed: {skill_result.failure_reason}",
            "error_details": skill_result.failure_details,
            "engine_used": "pdf_skill",
            "model": skill_result.model,
            "failure_reason": skill_result.failure_reason,
            "cost_usd": skill_result.cost_usd,
            "agent_seconds": skill_result.agent_seconds,
            "skill_was_used": skill_result.skill_was_used,
            "tool_calls": skill_result.tool_calls,
        }

    # ── Translate PDF Skill success into pipeline shape ──
    included_txns, all_txns = to_pipeline_transactions(skill_result)
    excluded_txns = [t for t in all_txns if not getattr(t, "include_for_1099", True)]

    if verbose:
        print(f"      PDF Skill OK · {skill_result.agent_seconds:.1f}s · "
              f"${skill_result.cost_usd:.4f}")
        print(f"      {len(all_txns)} total / {len(included_txns)} included / "
              f"{len(excluded_txns)} excluded")
        if skill_result.breakdown:
            breakdown_str = ", ".join(
                f"{n} {t}" for t, n in sorted(
                    skill_result.breakdown.items(), key=lambda x: -x[1]
                )
            )
            print(f"      Type breakdown: {breakdown_str}")

    if not included_txns:
        return {
            "success": False,
            "error": "PDF Skill extracted no included transactions",
            "engine_used": "pdf_skill",
            "model": skill_result.model,
            "all_transactions_count": len(all_txns),
            "excluded_transactions_count": len(excluded_txns),
            "cost_usd": skill_result.cost_usd,
            "agent_seconds": skill_result.agent_seconds,
        }

    # ── Now feed into existing normalization → aggregation → Excel flow ──
    known_vendors = load_vendor_list(vendor_list_path) if vendor_list_path else []
    if verbose:
        if known_vendors:
            print(f"[2/5] Loaded {len(known_vendors)} known vendors from vendor list")
        else:
            print(f"[2/5] No vendor list provided — using extracted names as canonical")

    if verbose:
        print(f"[3/5] Normalizing vendor names...")
    normalized = [
        normalize_vendor(t.description, known_vendors)
        for t in included_txns
    ]
    review_count = sum(1 for n in normalized if n.needs_review)
    if verbose:
        print(f"      {review_count} transactions flagged by normalizer for review")

    if verbose:
        print(f"[4/5] Aggregating by vendor...")
    summaries = aggregate_by_vendor(included_txns, normalized)

    if verbose:
        print(f"[5/5] Running 1099 eligibility classifier and generating Excel report...")
    eligibility = classify_all_vendors(summaries)

    # v1.4 Phase 4D: compute the reconciliation snapshot ONCE here so both the
    # Excel generator (below) and the response dict (further down) reference the
    # exact same computed result — guaranteeing the Excel waterfall and the web
    # card waterfall are identical, and keeping arithmetic in one place.
    
    recon_snapshot = _compute_reconciliation(skill_result.reconciliation_snapshot)

    # v1.4 Phase 4 — Source B: extraction-completeness check. Buckets the
    # extracted transaction rows by transaction_type and compares the
    # row-sums against recon_snapshot's stated activity totals. Independent
    # of Source A: Source A asks "does the statement's stated math balance?",
    # Source B asks "did we extract every row the statement reported?".
    # Falls back to status="unavailable" when no usable snapshot exists.
    extraction_check = _compute_source_b(all_txns, recon_snapshot)

    # Generate per-statement Excel — pass ALL transactions (including excluded)
    # so the workbook can show the full picture, even though only included rows
    # contributed to vendor totals.
    generate_excel_report(
        output_path=output_path,
        transactions=included_txns,
        normalized=normalized,
        summaries=summaries,
        eligibility=eligibility,
        # v1.3: optional kwargs the updated generator can use; existing
        # generator ignores them gracefully via **kwargs.
        all_transactions=all_txns,
        pdf_skill_metadata=skill_result.metadata,
        # v1.4 Phase 4D: drive the Summary Stats reconciliation waterfall +
        # activity classification. Generator omits them gracefully if absent.
        reconciliation_snapshot=recon_snapshot,
        # v1.4 Phase 4 — Source B: drive the Source B sub-block on Summary
        # Stats. Generator omits it gracefully when status == "unavailable".
        extraction_check=extraction_check,
        breakdown=skill_result.breakdown,
        # v1.4 Phase 4D: same statement-level confidence the response dict and
        # web card use (avg row-level confidence), so the Excel reads identically.
        confidence=_avg_confidence(skill_result.all_transactions),
    )

    # ── Build response (same shape as rule-based run_pipeline) ──
    total_amount = sum(s.total_amount for s in summaries)
    vendors_over_600 = sum(1 for s in summaries if s.total_amount >= 600)
    vendors_needing_review = sum(1 for s in summaries if s.needs_review)
    vendors_1099_nec = sum(1 for r in eligibility.values() if r.form_type == "1099-NEC")
    vendors_1099_misc = sum(1 for r in eligibility.values() if r.form_type == "1099-MISC")
    vendors_eligible_review = sum(1 for r in eligibility.values() if r.form_type == "REVIEW")

    if verbose:
        print(f"\n✓ PDF Skill report generated: {output_path}")
        print(f"  - {len(included_txns)} transactions (included for 1099 aggregation)")
        print(f"  - {len(excluded_txns)} excluded (preserved for UI/Excel)")
        print(f"  - {len(summaries)} unique vendors")
        print(f"  - ${total_amount:,.2f} total reconciled")
        print(f"  - Cost: ${skill_result.cost_usd:.4f}")

    # v1.3 master-fix: serialize transactions to dicts so the master workbook
    # generator can iterate them. `included_txns` and `all_txns` are local
    # Python Transaction objects; convert them here to the same dict shape
    # the rule-based path produces.
    def _txn_to_dict(t, is_excluded: bool):
        """Serialize a Transaction dataclass to the dict shape master expects."""
        canonical = ""
        if not is_excluded:
            try:
                idx = included_txns.index(t)
                canonical = normalized[idx].canonical_name
            except (ValueError, IndexError):
                canonical = ""
        return {
            "date": getattr(t, "date", "") or "",
            "raw_description": getattr(t, "description", "") or "",
            "canonical_name": canonical,
            "amount": getattr(t, "amount", 0.0),
            "excluded": is_excluded,
            "exclusion_reason": getattr(t, "exclusion_reason", "") or "",
            "transaction_type": getattr(t, "transaction_type", "vendor_payment"),
            "include_for_1099": getattr(t, "include_for_1099", not is_excluded),
            "review_required": getattr(t, "review_required", False),
        }

    all_txn_dicts = (
        [_txn_to_dict(t, is_excluded=False) for t in included_txns]
        + [_txn_to_dict(t, is_excluded=True)  for t in excluded_txns]
    )
    included_txn_dicts = [d for d in all_txn_dicts if not d["excluded"]]

    return {
        "success": True,
        "output_path": output_path,
        "engine_used": "pdf_skill",
        "model": skill_result.model,
        # Standard fields (compatible with rule-based response)
        "transaction_count": len(included_txns),
        "transactions_extracted_raw": len(all_txns),
        "transactions_excluded": len(excluded_txns),
        "excluded_breakdown": skill_result.breakdown,
        "vendor_count": len(summaries),
        "total_amount": total_amount,
        "vendors_over_600": vendors_over_600,
        "vendors_needing_review": vendors_needing_review,
        "vendors_1099_nec": vendors_1099_nec,
        "vendors_1099_misc": vendors_1099_misc,
        "vendors_eligible_review": vendors_eligible_review,
        "extraction_method": "pdf_skill_agent_sdk",
        "confidence": _avg_confidence(skill_result.all_transactions),
        "document_type": skill_result.metadata.get("detected_type", "unknown"),
        "warnings": [],
        # v1.3-specific fields
        "pdf_skill_metadata": skill_result.metadata,
        "pdf_skill_breakdown": skill_result.breakdown,
        "cost_usd": skill_result.cost_usd,
        "agent_seconds": skill_result.agent_seconds,
        "skill_was_used": skill_result.skill_was_used,
        # v1.3: surface vendor summaries so server.py can build per-statement response
        "vendor_summaries": summaries,
        # v1.4 (Phase 4): reconciliation snapshot — raw figures transcribed by
        # the PDF Skill, with calculated_ending/difference/status computed here.
        # Empty/unavailable when the prompt produced no account-summary figures.
        "reconciliation_snapshot": recon_snapshot,
        # v1.4 (Phase 4 — Source B): extraction-completeness cross-check. Buckets
        # extracted rows by transaction_type and compares row sums to
        # recon_snapshot's stated activity totals. status: complete | incomplete |
        # unavailable. Surfaced in per-statement Excel + master 4E roll-up; no
        # frontend surface (decision locked in May 26 spec).
        "extraction_check": extraction_check,
        # v1.3 master-fix: transactions serialized as list-of-dicts so the
        # master workbook generator's `for t in out.get("transactions", [])`
        # loop can read .date, .raw_description, .canonical_name, .amount,
        # .excluded, .exclusion_reason. all_transactions includes excluded
        # rows; included_transactions has only the 1099-aggregation rows.
        "transactions": all_txn_dicts,
        "all_transactions": all_txn_dicts,
        "included_transactions": included_txn_dicts,
    }


# ---------------------------------------------------------------------------
# Rule-based pipeline (existing v1.2 path, unchanged)
# ---------------------------------------------------------------------------

def run_pipeline(
    pdf_path: str,
    output_path: str,
    vendor_list_path: str = None,
    source: str = "bank",
    verbose: bool = True,
) -> dict:
    """
    Execute the full pipeline. Returns a summary dict for API responses.

    v1.2: classifier runs between extraction and normalization to filter out
    payroll deposits, balance lines, transfers, fees, and unidentified checks
    before they reach vendor aggregation.
    """
    # Step 1: Extract transactions from PDF
    if verbose:
        print(f"[1/5] Extracting transactions from {Path(pdf_path).name}...")
    extraction = extract_transactions(pdf_path, source=source)
    if verbose:
        print(f"      {len(extraction.transactions)} transactions found "
              f"(method: {extraction.extraction_method}, pages: {extraction.pages_processed})")
        for warning in extraction.warnings:
            print(f"      ⚠ {warning}")

    if not extraction.transactions:
        return {
            "success": False,
            "error": "No transactions could be extracted from the PDF",
            "warnings": extraction.warnings,
            "raw_text_preview": extraction.raw_text[:500],
        }

    # ── v1.2: classify each row before normalization/aggregation ──
    # Tags every Transaction with transaction_type, include_for_1099,
    # review_required, and (where applicable) exclusion_reason. The full
    # classified list is preserved on extraction.transactions; downstream
    # steps only operate on the included subset.
    classify_transactions(extraction.transactions)
    included_txns = filter_for_aggregation(extraction.transactions)
    excluded_txns = [
        t for t in extraction.transactions
        if not getattr(t, "include_for_1099", True)
    ]
    if verbose:
        print(f"      Classifier: {len(included_txns)} included for 1099 / "
              f"{len(excluded_txns)} excluded")
        if excluded_txns:
            type_counts = Counter(
                getattr(t, "transaction_type", "unknown") for t in excluded_txns
            )
            breakdown = ", ".join(f"{n} {t}" for t, n in type_counts.most_common())
            print(f"      Excluded breakdown: {breakdown}")

    # Step 2: Load known vendor list (if provided)
    known_vendors = load_vendor_list(vendor_list_path) if vendor_list_path else []
    if verbose and known_vendors:
        print(f"[2/5] Loaded {len(known_vendors)} known vendors from vendor list")
    elif verbose:
        print(f"[2/5] No vendor list provided — using extracted names as canonical")

    # Step 3: Normalize vendor names (only included rows)
    if verbose:
        print(f"[3/5] Normalizing vendor names...")
    normalized = [
        normalize_vendor(t.description, known_vendors)
        for t in included_txns
    ]
    review_count = sum(1 for n in normalized if n.needs_review)
    if verbose:
        print(f"      {review_count} transactions flagged by normalizer for review")

    # Step 4: Aggregate by vendor (operates on included rows only)
    if verbose:
        print(f"[4/5] Aggregating by vendor...")
    summaries = aggregate_by_vendor(included_txns, normalized)

    # Step 5: Classify 1099 eligibility + generate Excel report
    if verbose:
        print(f"[5/5] Running 1099 eligibility classifier and generating Excel report...")
    eligibility = classify_all_vendors(summaries)

    # Generate Excel — pass the FULL extraction.transactions so the workbook
    # has access to all rows (including excluded ones) if it wants to show
    # them. Aggregation-derived sheets (Vendor Summary, etc.) already reflect
    # the filtered totals via `summaries`.
    #
    # NOTE: excel_generator's `transactions` and `normalized` parameters are
    # still expected to be aligned (same length, same order). Pass the
    # included subset so the All Transactions sheet shows only those rows
    # that contributed to vendor totals. Excluded rows can be added in a
    # separate sheet in a future v1.2.x polish round.
    generate_excel_report(
        output_path=output_path,
        transactions=included_txns,
        normalized=normalized,
        summaries=summaries,
        eligibility=eligibility,
    )

    # Build response summary
    total_amount = sum(s.total_amount for s in summaries)
    vendors_over_600 = sum(1 for s in summaries if s.total_amount >= 600)
    vendors_needing_review = sum(1 for s in summaries if s.needs_review)
    vendors_1099_nec = sum(1 for r in eligibility.values() if r.form_type == "1099-NEC")
    vendors_1099_misc = sum(1 for r in eligibility.values() if r.form_type == "1099-MISC")
    vendors_eligible_review = sum(1 for r in eligibility.values() if r.form_type == "REVIEW")

    if verbose:
        print(f"\n✓ Report generated: {output_path}")
        print(f"  - {len(included_txns)} transactions (after classifier)")
        print(f"  - {len(excluded_txns)} excluded by classifier")
        print(f"  - {len(summaries)} unique vendors")
        print(f"  - ${total_amount:,.2f} total reconciled")
        print(f"  - {vendors_over_600} vendors crossed $600 threshold")
        print(f"  - {vendors_needing_review} vendors need human review")

    return {
        "success": True,
        "output_path": output_path,
        # v1.2: post-classifier counts. transaction_count keeps the historical
        # meaning (rows contributing to vendor totals = included rows). New
        # fields surface the classifier's work for transparency.
        "transaction_count": len(included_txns),
        "transactions_extracted_raw": len(extraction.transactions),
        "transactions_excluded": len(excluded_txns),
        "excluded_breakdown": dict(Counter(
            getattr(t, "transaction_type", "unknown") for t in excluded_txns
        )),
        "vendor_count": len(summaries),
        "total_amount": total_amount,
        "vendors_over_600": vendors_over_600,
        "vendors_needing_review": vendors_needing_review,
        "vendors_1099_nec": vendors_1099_nec,
        "vendors_1099_misc": vendors_1099_misc,
        "vendors_eligible_review": vendors_eligible_review,
        "extraction_method": extraction.extraction_method,
        "confidence": extraction.confidence,
        "document_type": extraction.document_type,
        "warnings": extraction.warnings,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convert bank statement PDF to accountant-grade Excel workbook"
    )
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("-o", "--output", required=True, help="Path to output .xlsx")
    parser.add_argument("-v", "--vendor-list", help="Path to CSV of known vendor names (optional)")
    parser.add_argument("-s", "--source", default="bank", choices=["bank", "credit_card"],
                        help="Statement source type")
    parser.add_argument("--engine", default="rule_based",
                        choices=["rule_based", "pdf_skill"],
                        help="Extraction engine: rule_based (default) or pdf_skill")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Model for pdf_skill engine (default: claude-sonnet-4-6)")
    args = parser.parse_args()

    if args.engine == "pdf_skill":
        result = run_pipeline_pdf_skill(
            pdf_path=args.pdf,
            output_path=args.output,
            vendor_list_path=args.vendor_list,
            source=args.source,
            model=args.model,
            verbose=True,
        )
    else:
        result = run_pipeline(
            pdf_path=args.pdf,
            output_path=args.output,
            vendor_list_path=args.vendor_list,
            source=args.source,
            verbose=True,
        )
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()