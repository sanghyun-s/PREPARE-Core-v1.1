"""
Transaction Aggregation Module
------------------------------
Takes a list of individual transactions and groups them by canonical vendor.

Why this matters for 1099 prep:
    The IRS $600 threshold is PER VENDOR PER YEAR, not per transaction.
    A bank statement might show 12 separate $75 payments to "John Smith Consulting"
    across the year — each individually under $600, but totaling $900.
    That vendor needs a 1099-NEC.

    Without aggregation, you miss 1099 obligations.
    That's the real pain point this solves.

v1.2 — Transaction Classifier integration
-----------------------------------------
The Transaction dataclass now carries three classifier-set fields:
    transaction_type   — vendor_payment / payroll / balance / etc.
    include_for_1099   — bool; aggregator filters on this
    review_required    — bool; surfaced for human-review flagging

Aggregation now skips rows where include_for_1099 is False, in addition
to the pre-existing norm.excluded check. This catches payroll deposits,
opening/ending balance lines, transfers, fees, and unidentified checks
that pdfplumber/regex extracts but should not contribute to vendor totals.

The classifier itself lives in backend/transaction_classifier.py and is
invoked by pipeline.py (rule-based engine) and agent_tools.py (AI engines)
between extraction and normalization.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from collections import defaultdict

from vendor_normalizer import NormalizedVendor


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    """A single line item from a bank or credit card statement."""
    date: Optional[str]         # "01/15/2024" or similar
    description: str            # Raw vendor string from statement
    amount: float               # Positive for payments out
    source: str = "bank"        # "bank" or "credit_card"

    # ── v1.2: classifier-populated fields ──
    # Set by backend/transaction_classifier.py.classify_transactions().
    # Defaults preserve backward compatibility: any code path that builds
    # Transactions without invoking the classifier still aggregates correctly.
    transaction_type: str = "vendor_payment"
    include_for_1099: bool = True
    review_required: bool = False
    exclusion_reason: str = ""


@dataclass
class VendorSummary:
    """Aggregated data for one canonical vendor."""
    canonical_name: str
    entity_type: Optional[str]
    total_amount: float
    transaction_count: int
    first_payment_date: Optional[str]
    last_payment_date: Optional[str]
    raw_name_variants: list[str] = field(default_factory=list)
    match_confidence: float = 1.0
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_by_vendor(
    transactions: list[Transaction],
    normalized_vendors: list[NormalizedVendor],
) -> list[VendorSummary]:
    """
    Group transactions by canonical vendor name and produce a summary row per vendor.

    Args:
        transactions: List of Transaction objects from PDF extraction
        normalized_vendors: Parallel list of NormalizedVendor results from normalizer
                            (must be same length as transactions, same order)

    Returns:
        List of VendorSummary objects, one per unique canonical vendor,
        sorted by total_amount descending.

    v1.2: A row is skipped from aggregation if EITHER:
        - norm.excluded is True (vendor normalizer rejected the row), OR
        - txn.include_for_1099 is False (classifier excluded the row, e.g.
          payroll deposit, balance line, transfer, unidentified check)

    Both gates are applied to preserve existing normalizer behavior while
    adding classifier-level exclusions on top. Defense in depth.
    """
    if len(transactions) != len(normalized_vendors):
        raise ValueError(
            f"transactions ({len(transactions)}) and normalized_vendors "
            f"({len(normalized_vendors)}) must have the same length"
        )

    # Group by canonical name — skip excluded entries (deposits, payroll,
    # transfers, balance lines, unidentified checks, normalizer-rejected rows)
    groups: dict[str, list[tuple[Transaction, NormalizedVendor]]] = defaultdict(list)
    excluded_count = 0
    for txn, norm in zip(transactions, normalized_vendors):
        # Pre-existing normalizer-level exclusion
        if norm.excluded:
            excluded_count += 1
            continue
        # v1.2: classifier-level exclusion. getattr() preserves backward
        # compat for any old Transaction objects predating the field.
        if not getattr(txn, "include_for_1099", True):
            excluded_count += 1
            continue
        groups[norm.canonical_name].append((txn, norm))

    summaries: list[VendorSummary] = []
    for canonical, items in groups.items():
        txns = [t for t, _ in items]
        norms = [n for _, n in items]

        amounts = [t.amount for t in txns]
        dates = sorted([t.date for t in txns if t.date])

        # Collect raw name variants (unique)
        raw_variants = list({n.raw_name for n in norms})

        # Confidence: take the minimum across all transactions for this vendor
        # (if any one match was low-confidence, flag the whole vendor)
        min_confidence = min(n.match_confidence for n in norms)
        any_needs_review = any(n.needs_review for n in norms)

        # v1.2: also flag the vendor if any constituent transaction was
        # marked review_required by the classifier (e.g. a "check" with
        # visible payee that still needs payee verification).
        any_classifier_review = any(
            getattr(t, "review_required", False) for t in txns
        )

        # Entity type: use the first non-None entity type found
        entity_type = next((n.entity_type for n in norms if n.entity_type), None)

        review_reasons = []
        if any_needs_review:
            review_reasons.append("Low vendor name match confidence")
        if len(raw_variants) > 1 and min_confidence < 0.9:
            review_reasons.append(
                f"Multiple raw name variants grouped together ({len(raw_variants)})"
            )
        if any_classifier_review:
            review_reasons.append(
                "Contains transactions flagged for review by classifier "
                "(e.g. check payment with payee that should be verified)"
            )

        summaries.append(VendorSummary(
            canonical_name=canonical,
            entity_type=entity_type,
            total_amount=round(sum(amounts), 2),
            transaction_count=len(txns),
            first_payment_date=dates[0] if dates else None,
            last_payment_date=dates[-1] if dates else None,
            raw_name_variants=raw_variants,
            match_confidence=round(min_confidence, 2),
            needs_review=any_needs_review or any_classifier_review,
            review_reasons=review_reasons,
        ))

    # Sort by total amount descending (biggest vendors first — usually most important)
    summaries.sort(key=lambda v: v.total_amount, reverse=True)
    return summaries


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from vendor_normalizer import normalize_vendor

    # Simulate extracted transactions
    test_txns = [
        Transaction("01/15/2024", "JOHN SMITH LLC", 1200.00),
        Transaction("03/22/2024", "John Smith Consulting", 800.00),
        Transaction("06/10/2024", "J Smith LLC", 500.00),
        Transaction("02/03/2024", "AMZN Mktp US*2X4YT", 45.99),
        Transaction("05/17/2024", "Amazon.com", 127.50),
        Transaction("08/29/2024", "HOMEDEPOT.COM 6547", 234.88),
        Transaction("09/04/2024", "Home Depot #4411", 189.22),
    ]
    known_vendors = ["John Smith", "Amazon", "Home Depot"]

    # Normalize each
    norms = [normalize_vendor(t.description, known_vendors) for t in test_txns]

    # Aggregate
    summaries = aggregate_by_vendor(test_txns, norms)

    print(f"{'Vendor':<25} {'Entity':<6} {'Total':>10} {'Count':>6} {'Review'}")
    print("-" * 70)
    for s in summaries:
        entity = s.entity_type or "-"
        review = "⚠ " + "; ".join(s.review_reasons) if s.needs_review else "✓"
        print(f"{s.canonical_name:<25} {entity:<6} ${s.total_amount:>9,.2f} {s.transaction_count:>6} {review}")