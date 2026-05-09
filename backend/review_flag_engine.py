"""
Review Flag Engine
------------------
Consolidates multiple signals into a single unified `review_needed` flag
with a list of human-readable reasons explaining WHY each vendor needs review.

The five signals tracked:

    1. LOW_MATCH_CONFIDENCE
       Vendor name normalization match confidence < 80%
       (existing logic from vendor_normalizer.py)

    2. NAME_VARIANT_DETECTED
       Same canonical entity appears under different raw names across
       multiple statements (set by validation_engine after cross-checking)

    3. NEAR_THRESHOLD
       Vendor's combined total is within $500-$700 — close enough to
       the $600 1099 threshold that one more payment could cross it

    4. CROSS_STATEMENT_MISMATCH
       Same canonical vendor in multiple statements with > 5x amount
       ratio AND > $500 absolute difference — possible extraction issue

    5. UNKNOWN_ENTITY_OVER_THRESHOLD
       Total >= $600 but entity type not detected (LLC/Inc/Corp absent)
       — accountant must verify W-9 status before filing decision

This module does NOT mutate the original VendorSummary objects. Instead it
produces a parallel ReviewFlags dict keyed by canonical_name that the
master Excel generator reads alongside the summaries.

Important: signals are flags for accountant attention, not "errors."
Labels deliberately use "Review Needed" / "Possible Issue" not "Error".
"""

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_THRESHOLD = 0.80      # Below this, name match is suspect
NEAR_THRESHOLD_LOW       = 500.00    # Lower bound of "review zone"
NEAR_THRESHOLD_HIGH      = 700.00    # Upper bound of "review zone"
THRESHOLD_AMOUNT         = 600.00    # The 1099 filing threshold itself

# Cross-statement mismatch detection
MISMATCH_RATIO_MIN       = 5.0       # Amounts must differ by > 5x
MISMATCH_ABSOLUTE_MIN    = 500.00    # AND absolute diff must be > $500


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ReviewFlags:
    """
    Per-vendor review flags. The aggregate `needs_review` is True if ANY
    individual signal is True. The `reasons` list contains human-readable
    explanations the accountant can read in the Excel output.
    """
    canonical_name: str
    needs_review: bool = False

    # Individual signals
    low_match_confidence: bool = False
    name_variant_detected: bool = False
    near_threshold: bool = False
    cross_statement_mismatch: bool = False
    unknown_entity_over_threshold: bool = False

    # Confidence scores carried through for display
    extraction_confidence: float = 1.0
    match_confidence: float = 1.0

    # Human-readable reasons (joined with "; " in Excel)
    reasons: list[str] = field(default_factory=list)

    def add_reason(self, reason: str):
        """Append a unique reason string."""
        if reason not in self.reasons:
            self.reasons.append(reason)
        self.needs_review = True


# ---------------------------------------------------------------------------
# Signal detectors
# ---------------------------------------------------------------------------

def check_low_match_confidence(flags: ReviewFlags, match_confidence: float):
    """Signal 1: vendor name match confidence below threshold."""
    flags.match_confidence = match_confidence
    if match_confidence < LOW_CONFIDENCE_THRESHOLD:
        flags.low_match_confidence = True
        flags.add_reason(
            f"Low name-match confidence ({match_confidence:.0%}) — "
            "verify this is the correct payee"
        )


def check_near_threshold(flags: ReviewFlags, total_amount: float):
    """Signal 3: combined total in $500-$700 review zone."""
    if NEAR_THRESHOLD_LOW <= total_amount <= NEAR_THRESHOLD_HIGH:
        flags.near_threshold = True
        if total_amount < THRESHOLD_AMOUNT:
            flags.add_reason(
                f"Near-threshold (${total_amount:,.2f}) — "
                f"may cross $600 with one more payment"
            )
        else:
            flags.add_reason(
                f"Near-threshold (${total_amount:,.2f}) — "
                f"verify all payments captured before filing"
            )


def check_unknown_entity_over_threshold(
    flags: ReviewFlags,
    total_amount: float,
    entity_type: Optional[str],
):
    """Signal 5: over $600 but entity type unknown."""
    if total_amount >= THRESHOLD_AMOUNT and not entity_type:
        flags.unknown_entity_over_threshold = True
        flags.add_reason(
            "Entity type unknown — verify LLC/Corp/Individual via W-9 "
            "before filing decision"
        )


def check_cross_statement_mismatch(
    flags_a: ReviewFlags,
    amount_a: float,
    statement_a: str,
    flags_b: ReviewFlags,
    amount_b: float,
    statement_b: str,
):
    """
    Signal 4: same vendor in two statements with suspicious amount variance.
    Both ReviewFlags get the flag set, since either side might be the wrong one.
    """
    if amount_a == 0 or amount_b == 0:
        return

    larger  = max(amount_a, amount_b)
    smaller = min(amount_a, amount_b)
    ratio = larger / smaller if smaller > 0 else float("inf")
    abs_diff = abs(amount_a - amount_b)

    if ratio > MISMATCH_RATIO_MIN and abs_diff > MISMATCH_ABSOLUTE_MIN:
        msg = (
            f"Possible extraction issue — "
            f"${amount_a:,.2f} in {statement_a} vs ${amount_b:,.2f} in {statement_b} "
            f"({ratio:.1f}x variance)"
        )
        flags_a.cross_statement_mismatch = True
        flags_b.cross_statement_mismatch = True
        flags_a.add_reason(msg)
        flags_b.add_reason(msg)


def mark_name_variant(flags: ReviewFlags, variant_info: str):
    """Signal 2: same payee under different names across statements."""
    flags.name_variant_detected = True
    flags.add_reason(f"Name variant detected — {variant_info}")


# ---------------------------------------------------------------------------
# Per-statement build helper
# ---------------------------------------------------------------------------

def build_flags_for_statement(
    summaries: list,
    extraction_confidence: float = 1.0,
) -> dict[str, ReviewFlags]:
    """
    Build ReviewFlags for every vendor in one statement.
    Runs the per-vendor signals (1, 3, 5) but NOT the cross-statement ones (2, 4).
    Cross-statement signals are added later by validation_engine.
    """
    flags_by_name: dict[str, ReviewFlags] = {}

    for s in summaries:
        f = ReviewFlags(
            canonical_name=s.canonical_name,
            extraction_confidence=extraction_confidence,
        )

        # Signal 1: low match confidence
        check_low_match_confidence(f, s.match_confidence)

        # Signal 3: near threshold
        check_near_threshold(f, s.total_amount)

        # Signal 5: unknown entity over threshold
        check_unknown_entity_over_threshold(f, s.total_amount, s.entity_type)

        # Carry through any pre-existing review flag from normalizer
        if s.needs_review and not f.needs_review:
            f.add_reason(
                "Flagged during normalization — "
                + "; ".join(s.review_reasons) if s.review_reasons
                else "Flagged during normalization"
            )

        flags_by_name[s.canonical_name] = f

    return flags_by_name


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Mock VendorSummary
    @dataclass
    class MockSummary:
        canonical_name: str
        entity_type: Optional[str]
        total_amount: float
        match_confidence: float
        needs_review: bool = False
        review_reasons: list = field(default_factory=list)

    test_vendors = [
        # name, entity, total, conf, expected_review_reason_count
        ("John Smith",            None,   3950.00, 0.82, 1),  # entity unknown over threshold
        ("Mary Johnson Consulting","LLC", 3950.00, 1.00, 0),  # clean
        ("Robert Kim",             "LLC", 1850.00, 0.95, 0),  # clean
        ("PG&E",                   None,   650.00, 1.00, 1),  # near threshold
        ("Adobe Systems",          "INC",  599.88, 1.00, 1),  # near threshold under $600
        ("Stripe",                 None,   720.00, 0.65, 2),  # low confidence + entity unknown
        ("Comcast Business",       None,   499.98, 1.00, 0),  # under review zone
        ("Verizon",                None,   810.00, 1.00, 1),  # entity unknown over threshold
    ]

    mocks = [
        MockSummary(n, e, t, c, False, [])
        for n, e, t, c, _ in test_vendors
    ]

    flags = build_flags_for_statement(mocks)

    print(f"\n{'Vendor':<28} {'Total':>10} {'Review?':<10} {'Reasons'}")
    print("-" * 110)
    passed = 0
    for s, (name, _, _, _, expected_count) in zip(mocks, test_vendors):
        f = flags[s.canonical_name]
        actual = len(f.reasons)
        status = "PASS" if actual == expected_count else f"FAIL (got {actual}, expected {expected_count})"
        if actual == expected_count:
            passed += 1
        review = "YES" if f.needs_review else "no"
        first_reason = f.reasons[0] if f.reasons else ""
        print(f"{name:<28} ${s.total_amount:>9,.2f} {review:<10} {first_reason[:65]}  {status}")
    print(f"\n{passed}/{len(test_vendors)} cases passed")

    # Test cross-statement mismatch
    print("\n--- Cross-statement mismatch test ---")
    fa = ReviewFlags(canonical_name="Comcast")
    fb = ReviewFlags(canonical_name="Comcast")
    check_cross_statement_mismatch(
        fa, 104108.94, "boa_2024.pdf",
        fb, 499.98,    "sample_2024.pdf",
    )
    print(f"Flag A reasons: {fa.reasons}")
    print(f"Flag B reasons: {fb.reasons}")
    print(f"Both flagged: {fa.cross_statement_mismatch and fb.cross_statement_mismatch}")
