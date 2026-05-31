"""
Transaction Classifier — v1.2
==============================

Classifies extracted transactions into types (vendor_payment, payroll,
balance, etc.) and decides which rows are eligible for 1099 aggregation.

Why this module exists
----------------------
The extractor (`pdf_extractor.py`) reads every dated row from a PDF
regardless of which column contains the transaction amount. On
multi-column bank statements (Withdrawals / Deposits / Balance), this
means payroll deposits, opening/ending balances, and transfers leak
into the transaction list. Without filtering, they distort vendor
totals, transaction counts, and $600-threshold candidate counts.

This module runs *after* extraction and *before* vendor normalization /
aggregation. Each transaction gets three new fields:

  - transaction_type:  one of 11 categories (vendor_payment, check,
                       check_unverified, balance, deposit, payroll,
                       transfer, fee, interest, metadata, unknown)
  - include_for_1099:  bool — whether to include in vendor aggregation
  - review_required:   bool — whether human review is needed

Aggregation downstream filters on `include_for_1099 == True`. Excluded
rows are preserved in the response payload for transparency.

Policy reference: docs/extraction_policy/transaction_inclusion_rules.md

Scope boundaries
----------------
This module:
  - Reads transaction descriptions (string)
  - Reads transaction amounts (float)
  - Returns classification decisions
  - Does NOT modify the input transaction objects in place

It does not:
  - Make accounting classification decisions (COGS vs office expense, etc.)
  - Determine 1099 form type (NEC vs MISC) — that's vendor_classifier_1099.py
  - Decide whether a vendor is a corporation (entity_type lookup) — that's
    vendor_normalizer.py
  - Touch the extractor or the workbook generator

The classifier is intentionally conservative: ambiguous rows default to
review_required=True rather than silently aggregating.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable
import re


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    """The output of classifying one transaction."""
    transaction_type: str = "vendor_payment"
    include_for_1099: bool = True
    review_required: bool = False
    exclusion_reason: str = ""


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------
# All matching is case-insensitive (descriptions are upper-cased before check).
# Keywords are matched as whole-word patterns where possible to avoid false
# positives (e.g., "DEPOSIT" should not match "DEPOSIT FROM ADOBE" if we
# determine the latter is actually a vendor refund).

# Statement-metadata patterns (no transactional value)
_BALANCE_PATTERNS = [
    "OPENING BALANCE",
    "ENDING BALANCE",
    "BEGINNING BALANCE",
    "BALANCE FORWARD",
    "PRIOR BALANCE",
    "STARTING BALANCE",
    "CLOSING BALANCE",
]

# Statement summary metadata
_METADATA_PATTERNS = [
    "TOTAL NEW CHARGES",
    "PREVIOUS BALANCE",
    "NEW BALANCE",
    "MINIMUM PAYMENT",
    "PAYMENT DUE",
    "STATEMENT TOTAL",
    "PAGE TOTAL",
    "SUBTOTAL",
]

# Income / non-vendor-payment outflows
_PAYROLL_PATTERNS = [
    "PAYROLL DIRECT DEPOSIT",
    "PAYROLL DIRECT DEP",
    "PAYROLL DEPOSIT",
    "PAYROLL DEP",
    "DIRECT DEPOSIT PAYROLL",
    "PAYROLL",  # broad fallback, last in list
]

_TRANSFER_PATTERNS = [
    "TRANSFER FROM",
    "TRANSFER TO",
    "XFER FROM",
    "XFER TO",
    "INTERNAL TRANSFER",
    "BOOK TRANSFER",
]

_INTEREST_PATTERNS = [
    "INTEREST EARNED",
    "INTEREST PAID",
    "INTEREST CREDIT",
]

_DEPOSIT_PATTERNS = [
    # Generic deposits — only matched when no clearer signal applies
    "ATM DEPOSIT",
    "MOBILE DEPOSIT",
    "REMOTE DEPOSIT",
    "WIRE TRANSFER IN",
    "WIRE IN",
    "ACH CREDIT",
    "DEPOSIT REFUND",
]

_FEE_PATTERNS = [
    "MAINTENANCE FEE",
    "MONTHLY FEE",
    "SERVICE CHARGE",
    "OVERDRAFT FEE",
    "OVERDRAFT CHARGE",
    "NSF FEE",
    "NSF CHARGE",
    "WIRE FEE",
    "ATM FEE",
    "STOP PAYMENT FEE",
    "RETURNED ITEM FEE",
    "ANNUAL FEE",
]

# Check patterns — match if description starts with check indicator
_CHECK_PATTERNS = [
    "CHECK #",
    "CHECK NO",
    "CHECK NUMBER",
    "CHK #",
    "CHK NO",
    "CHECK PAID",
    "CHECK WRITTEN",
    "CHECK CLEAR",
]

# A check description has a "visible payee" if it contains alphabetic
# content beyond the check keyword and number. Compile as regex:
#   "CHECK #1234" → no alpha after the number → no payee
#   "CHECK #1234 ACME LLC" → alpha after number → has payee
_CHECK_NUMBER_RE = re.compile(
    r"^\s*(CHECK\s*(?:#|NO\.?|NUMBER)?\s*\d+|CHK\s*(?:#|NO\.?)?\s*\d+|CHECK\s+PAID|CHECK\s+WRITTEN|CHECK\s+CLEAR)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------

def _starts_with_any(desc: str, patterns: list[str]) -> str | None:
    """Return the matching pattern if desc starts with any of them."""
    for p in patterns:
        if desc.startswith(p):
            return p
    return None


def _contains_any(desc: str, patterns: list[str]) -> str | None:
    """Return the matching pattern if desc contains any of them."""
    for p in patterns:
        if p in desc:
            return p
    return None


def _is_check(desc: str) -> bool:
    """True if the description appears to be a check withdrawal."""
    if _starts_with_any(desc, _CHECK_PATTERNS):
        return True
    # Also catch "CHECK 1234" without # — common pattern
    if re.match(r"^\s*CHECK\s+\d+\b", desc, re.IGNORECASE):
        return True
    return False


def _check_has_visible_payee(desc: str) -> bool:
    """
    True if a check description has a payee name beyond just the check number.

    Examples:
      "CHECK #1234"               → False (just the number)
      "CHECK #1234 250.00"        → False (number + amount, no payee)
      "CHECK #1234 ACME LLC"      → True  (alpha content after number)
      "CHECK PAID TO JOHN SMITH"  → True
    """
    # Strip the leading check pattern and number. What remains should contain
    # alphabetic content (a payee name) for there to be a visible payee.
    remainder = _CHECK_NUMBER_RE.sub("", desc, count=1).strip()
    # Remove any trailing dollar amounts / numbers
    remainder = re.sub(r"[\d,$.]+\s*$", "", remainder).strip()
    # Look for alphabetic content of meaningful length (≥3 chars to avoid
    # noise like "TO" or "OF")
    alpha = re.findall(r"[A-Za-z]{3,}", remainder)
    return len(alpha) >= 1


# ---------------------------------------------------------------------------
# Public API — single-row classifier
# ---------------------------------------------------------------------------

def classify_one(description: str, amount: float | None = None) -> ClassificationResult:
    """
    Classify a single transaction.

    Args:
        description: Raw transaction description string. Will be upper-cased
                     internally for keyword matching.
        amount:      Transaction amount. May be None for balance/metadata
                     rows. Currently informational only — classification is
                     primarily description-driven.

    Returns:
        ClassificationResult with type, inclusion flag, review flag, and
        exclusion reason populated.
    """
    if not description:
        return ClassificationResult(
            transaction_type="unknown",
            include_for_1099=False,
            review_required=True,
            exclusion_reason="No transaction description",
        )

    desc = description.upper().strip()

    # 1. Statement metadata — balance lines, summaries
    matched = _contains_any(desc, _BALANCE_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="balance",
            include_for_1099=False,
            review_required=False,
            exclusion_reason=f"Statement balance line ({matched.title()})",
        )

    matched = _contains_any(desc, _METADATA_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="metadata",
            include_for_1099=False,
            review_required=False,
            exclusion_reason=f"Statement summary metadata ({matched.title()})",
        )

    # 2. Payroll — must be checked before generic deposit/transfer
    matched = _contains_any(desc, _PAYROLL_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="payroll",
            include_for_1099=False,
            review_required=False,
            exclusion_reason="Payroll direct deposit (income, not a vendor payment)",
        )

    # 3. Transfers between own accounts
    matched = _contains_any(desc, _TRANSFER_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="transfer",
            include_for_1099=False,
            review_required=False,
            exclusion_reason=f"Internal account transfer ({matched.title()})",
        )

    # 4. Interest earned
    matched = _contains_any(desc, _INTEREST_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="interest",
            include_for_1099=False,
            review_required=False,
            exclusion_reason="Interest earned (income, not a vendor payment)",
        )

    # 5. Generic deposits (only those matching specific patterns; the broad
    #    word "DEPOSIT" alone is not enough — it might be part of a vendor
    #    name like "DEPOSIT-INSURED VENDOR")
    matched = _contains_any(desc, _DEPOSIT_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="deposit",
            include_for_1099=False,
            review_required=False,
            exclusion_reason=f"Incoming deposit ({matched.title()})",
        )

    # 6. Bank fees
    matched = _contains_any(desc, _FEE_PATTERNS)
    if matched:
        return ClassificationResult(
            transaction_type="fee",
            include_for_1099=False,
            review_required=False,
            exclusion_reason=f"Bank fee ({matched.title()}; excluded from 1099 by policy)",
        )

    # 7. Check payments
    if _is_check(desc):
        if _check_has_visible_payee(desc):
            return ClassificationResult(
                transaction_type="check",
                include_for_1099=True,
                review_required=True,
                exclusion_reason="",
            )
        else:
            return ClassificationResult(
                transaction_type="check_unverified",
                include_for_1099=False,
                review_required=True,
                exclusion_reason=(
                    "Check payee not visible in statement description; "
                    "verify with check image or client records before "
                    "treating as a vendor payment"
                ),
            )

    # 8. Default — vendor payment
    if amount is not None and amount > 0:
        return ClassificationResult(
            transaction_type="vendor_payment",
            include_for_1099=True,
            review_required=False,
        )

    # 9. Fallback — couldn't classify (zero or missing amount, no keyword match)
    return ClassificationResult(
        transaction_type="unknown",
        include_for_1099=False,
        review_required=True,
        exclusion_reason="Could not determine transaction type from description and amount",
    )


# ---------------------------------------------------------------------------
# Public API — batch classifier
# ---------------------------------------------------------------------------

def classify_transactions(transactions: Iterable[Any]) -> list[Any]:
    """
    Classify a list of transactions and tag each with the new fields.

    Operates on **duck-typed** transaction objects. Works with:
      - Pydantic models from schemas.py
      - dataclasses from pdf_extractor
      - dicts from agent JSON output

    For each transaction, sets these attributes (or dict keys):
      - transaction_type     (str)
      - include_for_1099     (bool)
      - review_required      (bool)
      - exclusion_reason     (str — empty if not excluded)

    Existing fields like `excluded` and `exclusion_reason` (if present) are
    also updated for backward compatibility with any downstream code that
    reads them.

    Returns the same list, mutated. (We mutate in place rather than returning
    new objects to avoid breaking callers that hold references.)
    """
    results = []
    for txn in transactions:
        # Read description — try multiple attribute names for compatibility
        desc = (
            _get(txn, "raw_description")
            or _get(txn, "description")
            or ""
        )
        amount = _get(txn, "amount")

        result = classify_one(desc, amount)

        # Set the new fields
        _set(txn, "transaction_type", result.transaction_type)
        _set(txn, "include_for_1099", result.include_for_1099)
        _set(txn, "review_required", result.review_required)

        # Update legacy fields for backward compat (only if classifier excluded
        # the row — don't override prior exclusion reasons from elsewhere)
        if not result.include_for_1099:
            _set(txn, "excluded", True)
            if result.exclusion_reason:
                _set(txn, "exclusion_reason", result.exclusion_reason)

        results.append(txn)

    return results


# ---------------------------------------------------------------------------
# Filter helper — convenience
# ---------------------------------------------------------------------------

def filter_for_aggregation(transactions: Iterable[Any]) -> list[Any]:
    """
    Return only transactions where include_for_1099 is True.

    Use this between classification and aggregation:

        classified = classify_transactions(extracted)
        included = filter_for_aggregation(classified)
        summaries = aggregate_by_vendor(included, ...)
    """
    return [t for t in transactions if _get(t, "include_for_1099", default=True)]


# ---------------------------------------------------------------------------
# Duck-typing helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, attr: str, default: Any = None) -> Any:
    """Read attr from object or dict, returning default if missing."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _set(obj: Any, attr: str, value: Any) -> None:
    """Set attr on object or dict."""
    if isinstance(obj, dict):
        obj[attr] = value
    else:
        try:
            setattr(obj, attr, value)
        except (AttributeError, TypeError):
            # Some Pydantic models with frozen=True or __slots__ may resist;
            # in that case caller is responsible for using model.copy(update={...})
            pass
