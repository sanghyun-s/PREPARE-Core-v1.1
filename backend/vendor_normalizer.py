"""
Vendor Normalization Module
---------------------------
Handles the messy reality of vendor names on bank statements.

Real-world examples this module solves:
    "AMZN Mktp US*2X4YT"        -> "Amazon"
    "JOHN SMITH LLC"             -> "John Smith" (normalized)
    "John Smith Consulting"      -> "John Smith" (maps to same canonical vendor)
    "HOMEDEPOT.COM 6547"         -> "Home Depot"
    "CHECK #1847"                -> unresolved (flagged for review)
    "PAYROLL DIRECT DEP"         -> excluded (incoming deposit, not a vendor)
    "TRANSFER FROM SAVINGS"      -> excluded (internal transfer, not a vendor)
    "OPENING BALANCE"            -> excluded (balance row, not a transaction)

Strategy:
    0. Pre-filter: exclude deposits, transfers, payroll, and balance rows
       before normalization — these are never 1099-eligible vendors
    1. Clean raw bank-statement strings (strip transaction codes, location suffixes)
    2. Normalize entity-type suffixes (LLC / Inc / Corp / Co / Ltd)
    3. Fuzzy-match against the running vendor list using difflib
    4. Return canonical name + confidence score + review flag
"""

import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Exclusion patterns — entries that are NOT vendors
# ---------------------------------------------------------------------------
# These match descriptions that represent incoming money, internal transfers,
# balance rows, bank fees, or payroll — none of which are 1099-eligible vendors.

DEPOSIT_PATTERNS = [
    # Payroll / direct deposits
    re.compile(r"\bPAYROLL\b", re.IGNORECASE),
    re.compile(r"\bDIRECT\s+DEP(OSIT)?\b", re.IGNORECASE),
    re.compile(r"\bDIR\s+DEP\b", re.IGNORECASE),
    re.compile(r"\bDDP\b", re.IGNORECASE),

    # Transfers in
    re.compile(r"\bTRANSFER\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bXFER\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bTRF\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bMOBILE\s+DEP(OSIT)?\b", re.IGNORECASE),
    re.compile(r"\bATM\s+DEP(OSIT)?\b", re.IGNORECASE),

    # Balance / summary rows
    re.compile(r"\bOPENING\s+BALANCE\b", re.IGNORECASE),
    re.compile(r"\bCLOSING\s+BALANCE\b", re.IGNORECASE),
    re.compile(r"\bBEGINNING\s+BALANCE\b", re.IGNORECASE),
    re.compile(r"\bENDING\s+BALANCE\b", re.IGNORECASE),
    re.compile(r"\bCURRENT\s+BALANCE\b", re.IGNORECASE),
    re.compile(r"^BALANCE$", re.IGNORECASE),

    # Credit card payments received
    re.compile(r"\bPAYMENT\s*[-–]\s*THANK\s+YOU\b", re.IGNORECASE),
    re.compile(r"\bAUTO\s*PAY\s+RECEIVED\b", re.IGNORECASE),
    re.compile(r"\bONLINE\s+PAYMENT\b", re.IGNORECASE),
    re.compile(r"\bPAYMENT\s+RECEIVED\b", re.IGNORECASE),

    # Refunds / credits back
    re.compile(r"\bREFUND\b", re.IGNORECASE),
    re.compile(r"\bCASHBACK\b", re.IGNORECASE),
    re.compile(r"\bREWARDS?\s+CREDIT\b", re.IGNORECASE),

    # Interest income
    re.compile(r"\bINTEREST\s+EARNED\b", re.IGNORECASE),
    re.compile(r"\bINTEREST\s+PAID\b", re.IGNORECASE),
    re.compile(r"\bDIVIDEND\b", re.IGNORECASE),

    # Zelle / Venmo incoming
    re.compile(r"\bZELLE\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bVENMO\s+FROM\b", re.IGNORECASE),
]

# Descriptions that contain dollar amounts embedded in the name
# (a sign the raw text bleed a balance/amount column into the description)
EMBEDDED_AMOUNT_PATTERN = re.compile(
    r"^\$?[\d,]+\.\d{2}$|"      # pure number like "8,500.00"
    r".*\$[\d,]+\.\d{2}.*",     # contains embedded $ amount
)


# ---------------------------------------------------------------------------
# Noise patterns — strip from vendor names but keep the rest
# ---------------------------------------------------------------------------

NOISE_PATTERNS = [
    r"\*[A-Z0-9]{4,}",          # "*2X4YT" transaction codes
    r"#\d+",                     # "#1847" check numbers
    r"\d{4,}",                   # Trailing 4+ digit location codes
    r"\.COM\b",                  # ".COM"
    r"\bMKTP\b",                 # Amazon marketplace code
    r"\bPAYPAL\s*\*",            # "PAYPAL *"
    r"\bSQ\s*\*",                # "SQ *" (Square)
    r"\bPOS\b",                  # "POS" point-of-sale marker
    r"\bACH\b",                  # "ACH" transfer code
    r"\bDEBIT\b",                # "DEBIT" marker
    r"\bCREDIT\b",               # "CREDIT" marker
    r"\bAUTOPAY\b",              # "AUTOPAY" suffix
    r"\bONLINE\b",               # "ONLINE" suffix
    r"\bPURCHASE\b",             # "PURCHASE" suffix
    r"\bPMT\b",                  # "PMT" payment abbreviation
]

# Entity-type suffixes — strip for normalization but record them
ENTITY_SUFFIXES = [
    "LLC", "L.L.C.", "L.L.C",
    "INC", "INC.", "INCORPORATED",
    "CORP", "CORP.", "CORPORATION",
    "CO", "CO.", "COMPANY",
    "LTD", "LTD.", "LIMITED",
    "LP", "L.P.", "LLP",
    "PC", "P.C.", "PLLC",
    "NA", "N.A.",               # Banks: "Chase NA"
    "FSB",                      # Federal Savings Bank
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NormalizedVendor:
    raw_name: str               # Original string from bank statement
    cleaned_name: str           # After noise removal
    canonical_name: str         # Final matched/cleaned name
    entity_type: Optional[str]  # "LLC", "INC", etc. if detected
    match_confidence: float     # 0.0 – 1.0
    needs_review: bool          # True if confidence below threshold
    excluded: bool = False      # True if this is a deposit/transfer/balance row
    exclusion_reason: str = ""  # Why it was excluded


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def is_excluded(raw_name: str) -> tuple[bool, str]:
    """
    Check if a description should be excluded from vendor normalization.
    Returns (excluded: bool, reason: str).

    Excluded entries: deposits, payroll, transfers in, balance rows,
    refunds, and descriptions that are pure numbers (balance columns
    that leaked into the description field).
    """
    # Check for embedded amounts (pure numbers or $ amounts in description)
    if EMBEDDED_AMOUNT_PATTERN.match(raw_name.strip()):
        return True, "embedded_amount"

    # Check against deposit/non-vendor patterns
    for pattern in DEPOSIT_PATTERNS:
        if pattern.search(raw_name):
            matched = pattern.pattern.replace("\\b", "").replace("\\s+", " ").strip()
            return True, f"deposit/transfer: {matched[:40]}"

    return False, ""


def strip_noise(raw: str) -> str:
    """Remove bank-statement transaction noise from vendor string."""
    s = raw.upper().strip()
    for pattern in NOISE_PATTERNS:
        s = re.sub(pattern, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_entity_type(name: str) -> tuple[str, Optional[str]]:
    """
    Strip entity suffix and return (name_without_suffix, entity_type).
    Entity type matters for 1099 determination (corporations are generally exempt).
    """
    name_upper = name.upper().strip()
    for suffix in ENTITY_SUFFIXES:
        pattern = rf"\b{re.escape(suffix)}\.?\s*$"
        if re.search(pattern, name_upper):
            cleaned = re.sub(pattern, "", name_upper).strip().rstrip(",").strip()
            canonical_suffix = suffix.replace(".", "").upper()
            return cleaned, canonical_suffix
    return name_upper, None


def titlecase_name(name: str) -> str:
    """Convert ALLCAPS vendor name to Title Case for display."""
    keep_upper = {
        "LLC", "INC", "CORP", "CO", "LTD", "LP", "LLP",
        "PC", "PLLC", "USA", "US", "NA", "FSB", "ATM",
    }
    words = name.split()
    result = []
    for w in words:
        if w.upper() in keep_upper:
            result.append(w.upper())
        else:
            result.append(w.capitalize())
    return " ".join(result)


def similarity(a: str, b: str) -> float:
    """Return similarity ratio between two strings (0.0 – 1.0)."""
    return SequenceMatcher(None, a.upper(), b.upper()).ratio()


def find_best_match(
    cleaned_name: str,
    vendor_list: list[str],
    threshold: float = 0.75,
) -> tuple[Optional[str], float]:
    """
    Find the best-matching vendor in vendor_list for the given cleaned name.
    Returns (matched_name, confidence).
    If no match above threshold, returns (None, best_score).
    """
    if not vendor_list:
        return None, 0.0

    best_match = None
    best_score = 0.0

    for candidate in vendor_list:
        candidate_cleaned, _ = extract_entity_type(candidate)
        score = similarity(cleaned_name, candidate_cleaned)
        if score > best_score:
            best_score = score
            best_match = candidate

    if best_score >= threshold:
        return best_match, best_score
    return None, best_score


def normalize_vendor(
    raw_name: str,
    vendor_list: Optional[list[str]] = None,
    match_threshold: float = 0.75,
    review_threshold: float = 0.85,
) -> NormalizedVendor:
    """
    Main entry point. Takes a raw bank-statement vendor string and returns
    a NormalizedVendor with canonical name, entity type, and review flag.

    New in this version:
        - Pre-filters deposits, payroll, transfers, and balance rows
          before normalization. Excluded entries get excluded=True and
          are skipped by the aggregation layer.

    Args:
        raw_name:         Raw string from bank statement
        vendor_list:      Optional list of known canonical vendor names
        match_threshold:  Minimum similarity to accept a match (default 0.75)
        review_threshold: Below this confidence, flag for review (default 0.85)
    """
    vendor_list = vendor_list or []

    # ── Step 0: Pre-filter non-vendor entries ──
    excluded, reason = is_excluded(raw_name)
    if excluded:
        return NormalizedVendor(
            raw_name=raw_name,
            cleaned_name=raw_name,
            canonical_name=f"[EXCLUDED] {raw_name[:50]}",
            entity_type=None,
            match_confidence=0.0,
            needs_review=False,
            excluded=True,
            exclusion_reason=reason,
        )

    # ── Step 1: Strip noise ──
    cleaned = strip_noise(raw_name)

    # ── Step 2: Extract entity type ──
    name_without_suffix, entity_type = extract_entity_type(cleaned)

    # ── Step 3: Match against known vendor list ──
    match, confidence = find_best_match(name_without_suffix, vendor_list, match_threshold)

    if match:
        canonical = match
    else:
        canonical = titlecase_name(name_without_suffix)
        confidence = 1.0 if not vendor_list else confidence

    needs_review = confidence < review_threshold and bool(vendor_list)

    # ── Special case: very short or purely numeric ──
    if len(name_without_suffix.strip()) < 3 or name_without_suffix.strip().isdigit():
        needs_review = True
        canonical = f"UNRESOLVED: {raw_name}"

    return NormalizedVendor(
        raw_name=raw_name,
        cleaned_name=cleaned,
        canonical_name=canonical,
        entity_type=entity_type,
        match_confidence=round(confidence, 2),
        needs_review=needs_review,
        excluded=False,
        exclusion_reason="",
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_cases = [
        # Should normalize → vendors
        ("AMZN Mktp US*2X4YT",          "vendor"),
        ("JOHN SMITH LLC",               "vendor"),
        ("John Smith Consulting",        "vendor"),
        ("HOMEDEPOT.COM 6547",           "vendor"),
        ("CHECK #1847",                  "vendor"),
        ("PAYPAL *UBER EATS",            "vendor"),
        ("Staples Inc.",                 "vendor"),
        ("Office Depot Corp",            "vendor"),
        ("ATT UVERSE AUTOPAY",           "vendor"),
        ("VERIZON WIRELESS AUTOPAY",     "vendor"),
        # Should be excluded → deposits/transfers/balance
        ("PAYROLL DIRECT DEP",           "excluded"),
        ("TRANSFER FROM SAVINGS",        "excluded"),
        ("OPENING BALANCE",              "excluded"),
        ("ENDING BALANCE",               "excluded"),
        ("PAYMENT - THANK YOU",          "excluded"),
        ("MOBILE DEPOSIT",               "excluded"),
        ("INTEREST EARNED",              "excluded"),
        ("ZELLE FROM JAMES",             "excluded"),
        ("8,500.00",                     "excluded"),
        ("DIRECT DEP PAYROLL",           "excluded"),
    ]

    known_vendors = ["Amazon", "John Smith", "Home Depot", "Staples"]

    print(f"\n{'Raw':<35} {'Expected':<10} {'Result':<10} {'Canonical':<28} {'Reason'}")
    print("-" * 110)
    for raw, expected in test_cases:
        result = normalize_vendor(raw, known_vendors)
        actual = "excluded" if result.excluded else "vendor"
        status = "✓" if actual == expected else "✗ WRONG"
        reason = result.exclusion_reason if result.excluded else (
            "⚠ review" if result.needs_review else f"{result.match_confidence:.0%}"
        )
        print(
            f"{raw:<35} {expected:<10} {actual:<10} "
            f"{result.canonical_name[:26]:<28} {reason}  {status}"
        )
