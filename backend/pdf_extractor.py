"""
PDF Extraction Module
---------------------
Extracts transactions from bank/credit card statements, 1099s, W-2s,
and tax schedules using a three-tier strategy:

    Tier 1 — Table extraction (pdfplumber)
              Works on statements with clean tabular layout (Chase, BofA, Amex, etc.)

    Tier 2 — Regex line extraction
              Broader pattern set covering 15+ real bank statement formats,
              credit card statements, date variants, and debit/credit column layouts.

    Tier 3 — Claude AI fallback
              When Tiers 1+2 produce zero or low-confidence results, the raw PDF
              text is sent to Claude (claude-haiku-4-5-20251001) for structured
              extraction. This handles:
                - Non-standard layouts (credit unions, international banks)
                - 1099-NEC / 1099-MISC forms (box-based IRS layout)
                - W-2 forms
                - Schedule C / Schedule E tax schedules
                - Scanned PDFs with messy OCR text
                - Foreign-language statements

    Confidence scoring: each result is scored 0.0–1.0. If the rule-based
    tiers score below CONFIDENCE_THRESHOLD, Claude fallback is triggered
    automatically (if ANTHROPIC_API_KEY is available).
"""

import re
import os
import json
import pdfplumber
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from transaction_aggregator import Transaction


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.4   # Below this score → trigger Claude fallback
MIN_TRANSACTIONS_RULE_BASED = 3  # Fewer than this → low confidence even if some found
CLAUDE_FALLBACK_MODEL = "claude-haiku-4-5-20251001"
MAX_TEXT_CHARS_FOR_FALLBACK = 12000  # Truncate very long PDFs to fit context


# ---------------------------------------------------------------------------
# Regex patterns — Tier 2
# ---------------------------------------------------------------------------
# Covers real-world formats from: Chase, BofA, Wells Fargo, Citi, Amex,
# Discover, Capital One, credit unions, QuickBooks exports, and IRS forms.

TXN_LINE_PATTERNS = [
    # ── BALANCE-AWARE PATTERNS (must come first) ──
    # When a line has TWO trailing decimal numbers, the FIRST is the
    # transaction amount and the SECOND is the running balance.
    # This handles BoA, Wells Fargo, and other "withdrawal | balance"
    # multi-column layouts that pdfplumber renders as single text lines.
    #
    # Example: "01/05/24 JOHN SMITH LLC 1,200.00 11,250.00"
    #   Without this pattern, generic patterns capture 11,250.00 as the
    #   amount (the balance), inflating totals dramatically.

    # MM/DD/YYYY  Description  Amount  Balance
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s+"
        r"\$?-?[\d,]+\.\d{2}\s*$"        # trailing balance — discarded
    ),
    # MM/DD  Description  Amount  Balance
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s+"
        r"\$?-?[\d,]+\.\d{2}\s*$"
    ),
    # YYYY-MM-DD  Description  Amount  Balance
    re.compile(
        r"^\s*(?P<date>\d{4}-\d{1,2}-\d{1,2})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s+"
        r"\$?-?[\d,]+\.\d{2}\s*$"
    ),

    # ── ORIGINAL PATTERNS (single-space separator) ──
    # These handled the original sample PDF and must stay first.

    # MM/DD/YYYY  Description  Amount
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s*$"
    ),
    # MM/DD  Description  Amount
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s*$"
    ),
    # YYYY-MM-DD  Description  Amount
    re.compile(
        r"^\s*(?P<date>\d{4}-\d{1,2}-\d{1,2})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s*$"
    ),

    # ── EXTENDED PATTERNS (multi-space / trailing balance) ──
    # For real bank statements with debit/credit/balance columns.

    # MM/DD/YYYY  Description  Amount  Balance  (trailing balance ignored)
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
        r"(?P<description>.+?)\s{2,}"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})"
        r"(?:\s+\$?-?[\d,]+\.\d{2})?\s*$"
    ),
    # MM/DD  Description  Amount  Balance
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2})\s+"
        r"(?P<description>.+?)\s{2,}"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})"
        r"(?:\s+\$?-?[\d,]+\.\d{2})?\s*$"
    ),
    # Mon DD or Mon DD, YYYY  (e.g. "Jan 15" or "Jan 15, 2024")
    re.compile(
        r"^\s*(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+\d{1,2}(?:,\s*\d{4})?)\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s*$",
        re.IGNORECASE
    ),
    # DD Mon YYYY  (European: "15 Jan 2024")
    re.compile(
        r"^\s*(?P<date>\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+\d{2,4})\s+"
        r"(?P<description>.+?)\s+"
        r"\$?(?P<amount>-?[\d,]+\.\d{2})\s*$",
        re.IGNORECASE
    ),
    # Debit/Credit/Balance column layout
    re.compile(
        r"^\s*(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
        r"(?P<description>.+?)\s{2,}"
        r"(?P<amount>[\d,]+\.\d{2})\s+"
        r"(?:[\d,]+\.\d{2}\s+)?"
        r"[\d,]+\.\d{2}\s*$"
    ),
    # Amount-first format
    re.compile(
        r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+"
        r"\$?(?P<amount>[\d,]+\.\d{2})\s+"
        r"(?P<description>[A-Z].+?)\s*$"
    ),
]

# IRS form patterns — 1099, W-2, Schedule C
IRS_PATTERNS = {
    "1099_nec": re.compile(
        r"(?:nonemployee\s+compensation|box\s*1|nec)\s*[\$:]?\s*"
        r"(?P<amount>[\d,]+\.?\d*)", re.IGNORECASE
    ),
    "1099_misc_rents": re.compile(
        r"(?:rents|box\s*1)\s*[\$:]?\s*(?P<amount>[\d,]+\.?\d*)", re.IGNORECASE
    ),
    "1099_misc_other": re.compile(
        r"(?:other\s+income|box\s*3)\s*[\$:]?\s*(?P<amount>[\d,]+\.?\d*)", re.IGNORECASE
    ),
    "1099_int": re.compile(
        r"(?:interest\s+income|box\s*1)\s*[\$:]?\s*(?P<amount>[\d,]+\.?\d*)", re.IGNORECASE
    ),
    "w2_wages": re.compile(
        r"(?:wages,?\s+tips|box\s*1)\s*[\$:]?\s*(?P<amount>[\d,]+\.?\d*)", re.IGNORECASE
    ),
    "schedule_c": re.compile(
        r"(?:gross\s+receipts|line\s*1|part\s+i)\s*[\$:]?\s*(?P<amount>[\d,]+\.?\d*)",
        re.IGNORECASE
    ),
}

# Lines to skip — running balances, headers, footers, section titles
SKIP_PATTERNS = [
    re.compile(r"^\s*(?:beginning|ending|opening|closing)\s+balance", re.IGNORECASE),
    re.compile(r"^\s*(?:total|subtotal|grand\s+total)", re.IGNORECASE),
    re.compile(r"^\s*(?:date|description|amount|balance|debit|credit|transaction)", re.IGNORECASE),
    re.compile(r"^\s*page\s+\d+", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),  # Lone page numbers
    re.compile(r"^\s*[-=]{3,}\s*$"),  # Separator lines
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    transactions: list[Transaction]
    raw_text: str
    pages_processed: int
    extraction_method: str   # "table" | "regex" | "claude" | "irs_form" | "none"
    confidence: float        # 0.0 – 1.0
    warnings: list[str]
    document_type: str       # "bank" | "credit_card" | "1099" | "w2" | "schedule" | "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_amount(amt_str: str) -> Optional[float]:
    """Convert '1,200.00' or '(45.99)' or '-45.99' to float."""
    if not amt_str:
        return None
    try:
        cleaned = (
            amt_str
            .replace(",", "")
            .replace("$", "")
            .strip()
        )
        # Parentheses = negative (accounting notation)
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def should_skip_line(line: str) -> bool:
    """Return True if the line is a header, total, or separator."""
    return any(p.search(line) for p in SKIP_PATTERNS)


def detect_document_type(raw_text: str) -> str:
    """Heuristically identify the document type from its text content."""
    text_lower = raw_text.lower()
    if any(k in text_lower for k in ["nonemployee compensation", "1099-nec", "1099 nec"]):
        return "1099_nec"
    if any(k in text_lower for k in ["1099-misc", "1099 misc", "miscellaneous"]):
        return "1099_misc"
    if any(k in text_lower for k in ["1099-int", "interest income", "1099 int"]):
        return "1099_int"
    if any(k in text_lower for k in ["w-2", "w2", "wages, tips", "employer identification"]):
        return "w2"
    if any(k in text_lower for k in ["schedule c", "profit or loss from business"]):
        return "schedule_c"
    if any(k in text_lower for k in ["schedule e", "supplemental income"]):
        return "schedule_e"
    if any(k in text_lower for k in ["credit card", "card ending", "minimum payment due"]):
        return "credit_card"
    return "bank"


def score_extraction(transactions: list[Transaction], doc_type: str) -> float:
    """
    Score the quality of rule-based extraction (0.0–1.0).
    Low score triggers Claude fallback.
    """
    if not transactions:
        return 0.0
    count = len(transactions)
    if count >= 20:
        base = 0.9
    elif count >= 10:
        base = 0.75
    elif count >= MIN_TRANSACTIONS_RULE_BASED:
        base = 0.5
    else:
        base = 0.2

    # IRS forms legitimately have very few "transactions"
    if doc_type in ("1099_nec", "1099_misc", "1099_int", "w2", "schedule_c", "schedule_e"):
        base = min(base + 0.3, 1.0)

    return base


# ---------------------------------------------------------------------------
# Tier 1 — Table extraction
# ---------------------------------------------------------------------------

def extract_from_tables(pdf_path: str, source: str = "bank") -> list[Transaction]:
    """Extract transactions from PDF tables (pdfplumber)."""
    transactions = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables or []:
                if not table or len(table) < 2:
                    continue
                header = [str(c or "").strip().lower() for c in table[0]]

                # Find column indices by header name
                date_idx = next((i for i, h in enumerate(header) if "date" in h), None)
                desc_idx = next((i for i, h in enumerate(header) if any(
                    k in h for k in [
                        "description", "payee", "vendor", "merchant",
                        "transaction", "memo", "details", "narrative"
                    ]
                )), None)
                amt_idx = next((i for i, h in enumerate(header) if any(
                    k in h for k in [
                        "amount", "debit", "withdrawal", "payment",
                        "charge", "debit amount", "transaction amount"
                    ]
                )), None)
                # Credit column — use if debit is empty for a row
                credit_idx = next((i for i, h in enumerate(header) if any(
                    k in h for k in ["credit", "deposit", "credit amount"]
                )), None)

                if date_idx is None or desc_idx is None:
                    continue
                if amt_idx is None and credit_idx is None:
                    continue

                for row in table[1:]:
                    if not row:
                        continue
                    max_idx = max(
                        date_idx, desc_idx,
                        amt_idx if amt_idx is not None else 0,
                        credit_idx if credit_idx is not None else 0
                    )
                    if len(row) <= max_idx:
                        continue

                    date_val = str(row[date_idx] or "").strip()
                    desc_val = str(row[desc_idx] or "").strip()

                    # Try debit column first, then credit
                    amt_val = None
                    if amt_idx is not None:
                        amt_val = parse_amount(str(row[amt_idx] or ""))
                    if (amt_val is None or amt_val == 0) and credit_idx is not None:
                        amt_val = parse_amount(str(row[credit_idx] or ""))

                    if date_val and desc_val and amt_val and amt_val != 0:
                        if not should_skip_line(desc_val):
                            transactions.append(Transaction(
                                date=date_val,
                                description=desc_val,
                                amount=abs(amt_val),
                                source=source,
                            ))
    return transactions


# ---------------------------------------------------------------------------
# Tier 2 — Regex line extraction
# ---------------------------------------------------------------------------

def extract_from_text_lines(text: str, source: str = "bank") -> list[Transaction]:
    """Line-by-line regex extraction with broader pattern coverage."""
    transactions = []
    for line in text.split("\n"):
        line = line.rstrip()
        if not line.strip() or should_skip_line(line):
            continue
        for pattern in TXN_LINE_PATTERNS:
            m = pattern.match(line)
            if m:
                amt = parse_amount(m.group("amount"))
                if amt is None or amt == 0:
                    continue
                desc = m.group("description").strip()
                if len(desc) < 2:
                    continue
                transactions.append(Transaction(
                    date=m.group("date"),
                    description=desc,
                    amount=abs(amt),
                    source=source,
                ))
                break
    return transactions


# ---------------------------------------------------------------------------
# IRS form extraction — for 1099s, W-2s, schedules
# ---------------------------------------------------------------------------

def extract_from_irs_form(raw_text: str, doc_type: str) -> list[Transaction]:
    """
    Extract key amounts from IRS tax forms.
    Returns a minimal Transaction list (usually 1-5 items) representing
    the key boxes/lines on the form.
    """
    transactions = []
    patterns_to_try = {
        "1099_nec":   ["1099_nec"],
        "1099_misc":  ["1099_misc_rents", "1099_misc_other"],
        "1099_int":   ["1099_int"],
        "w2":         ["w2_wages"],
        "schedule_c": ["schedule_c"],
        "schedule_e": ["schedule_c"],  # Similar pattern
    }
    relevant = patterns_to_try.get(doc_type, [])
    for key in relevant:
        pattern = IRS_PATTERNS.get(key)
        if not pattern:
            continue
        for m in pattern.finditer(raw_text):
            amt = parse_amount(m.group("amount"))
            if amt and amt > 0:
                label = key.replace("_", " ").title()
                transactions.append(Transaction(
                    date="",
                    description=f"[{label}] {m.group(0)[:60].strip()}",
                    amount=amt,
                    source=doc_type,
                ))
    return transactions


# ---------------------------------------------------------------------------
# Tier 3 — Claude AI fallback
# ---------------------------------------------------------------------------

CLAUDE_EXTRACTION_SYSTEM = """You are a financial document parser. Extract every \
payment/expense transaction from the document text provided.

Return ONLY a JSON array. Each element must have exactly these fields:
  "date": string (any format found in the document, empty string if not found)
  "description": string (vendor/payee name or transaction description)
  "amount": number (positive float, representing dollars paid out)

Rules:
- Include only outgoing payments/charges/expenses
- Exclude: deposits, transfers in, running balances, fees charged by the bank,
  interest charges, rewards redemptions
- For IRS forms (1099, W-2, Schedule C/E): extract the key box amounts as transactions
  with description = the box label (e.g. "Box 1 - Nonemployee Compensation")
- If you cannot find any transactions, return an empty array: []
- Do NOT include any text before or after the JSON array
"""

def claude_extract_transactions(
    raw_text: str,
    source: str = "bank",
    doc_type: str = "bank",
) -> tuple[list[Transaction], str]:
    """
    Tier 3: Send raw PDF text to Claude for structured transaction extraction.
    Returns (transactions, method_label).
    Falls back gracefully if API key is unavailable or call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return [], "claude_unavailable"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Truncate text to avoid token limits
        truncated = raw_text[:MAX_TEXT_CHARS_FOR_FALLBACK]
        if len(raw_text) > MAX_TEXT_CHARS_FOR_FALLBACK:
            truncated += "\n\n[... document truncated for length ...]"

        user_message = (
            f"Document type detected: {doc_type}\n\n"
            f"Extract all payment transactions from this document:\n\n"
            f"{truncated}"
        )

        response = client.messages.create(
            model=CLAUDE_FALLBACK_MODEL,
            max_tokens=4096,
            system=CLAUDE_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_json = response.content[0].text.strip()

        # Strip markdown code fences if Claude wrapped the JSON
        if raw_json.startswith("```"):
            raw_json = re.sub(r"^```(?:json)?\n?", "", raw_json)
            raw_json = re.sub(r"\n?```$", "", raw_json)

        parsed = json.loads(raw_json)
        if not isinstance(parsed, list):
            return [], "claude_bad_response"

        transactions = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            amt = item.get("amount")
            desc = item.get("description", "").strip()
            date = item.get("date", "").strip()
            if isinstance(amt, (int, float)) and amt > 0 and desc:
                transactions.append(Transaction(
                    date=date,
                    description=desc,
                    amount=float(amt),
                    source=source,
                ))

        return transactions, "claude"

    except json.JSONDecodeError:
        return [], "claude_json_error"
    except Exception as e:
        return [], f"claude_error: {type(e).__name__}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_transactions(
    pdf_path: str,
    source: str = "bank",
    force_claude: bool = False,
) -> ExtractionResult:
    """
    Extract transactions from any financial PDF using a three-tier strategy.

    Args:
        pdf_path:     Path to the PDF file.
        source:       Hint about the document source ("bank", "credit_card", etc.)
        force_claude: Skip rule-based tiers and go straight to Claude fallback.

    Returns:
        ExtractionResult with transactions, confidence score, method label,
        document type, raw text, and any warnings.
    """
    pdf_path = str(pdf_path)
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    warnings: list[str] = []
    raw_text_parts: list[str] = []
    pages_processed = 0

    # --- Extract raw text from all pages ---
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_processed += 1
            page_text = page.extract_text() or ""
            raw_text_parts.append(page_text)

    raw_text = "\n".join(raw_text_parts)

    if not raw_text.strip():
        warnings.append(
            "No text could be extracted. PDF may be image-based (scanned). "
            "OCR support is planned for a future version."
        )
        return ExtractionResult(
            transactions=[],
            raw_text=raw_text,
            pages_processed=pages_processed,
            extraction_method="none",
            confidence=0.0,
            warnings=warnings,
            document_type="unknown",
        )

    # --- Detect document type ---
    doc_type = detect_document_type(raw_text)

    # --- Skip rule-based for forced Claude mode ---
    if force_claude:
        txns, method = claude_extract_transactions(raw_text, source, doc_type)
        confidence = 0.85 if txns else 0.0
        if not txns:
            warnings.append(f"Claude fallback produced no transactions (method: {method}).")
        return ExtractionResult(
            transactions=txns,
            raw_text=raw_text,
            pages_processed=pages_processed,
            extraction_method=method,
            confidence=confidence,
            warnings=warnings,
            document_type=doc_type,
        )

    # --- Tier 1: IRS form extraction (if applicable) ---
    if doc_type in ("1099_nec", "1099_misc", "1099_int", "w2", "schedule_c", "schedule_e"):
        irs_txns = extract_from_irs_form(raw_text, doc_type)
        if irs_txns:
            return ExtractionResult(
                transactions=irs_txns,
                raw_text=raw_text,
                pages_processed=pages_processed,
                extraction_method="irs_form",
                confidence=0.85,
                warnings=warnings,
                document_type=doc_type,
            )
        warnings.append(
            f"IRS form detected ({doc_type}) but pattern extraction yielded no amounts. "
            "Falling through to Claude fallback."
        )

    # --- Tier 1: Table extraction ---
    table_txns: list[Transaction] = []
    try:
        table_txns = extract_from_tables(pdf_path, source)
    except Exception as e:
        warnings.append(f"Table extraction failed: {e}")

    # --- Tier 2: Regex line extraction ---
    regex_txns = extract_from_text_lines(raw_text, source)

    # Choose best rule-based result
    if len(table_txns) >= len(regex_txns) and table_txns:
        rule_txns = table_txns
        rule_method = "table"
    elif regex_txns:
        rule_txns = regex_txns
        rule_method = "regex"
    else:
        rule_txns = []
        rule_method = "none"

    confidence = score_extraction(rule_txns, doc_type)

    # --- Tier 3: Claude fallback if confidence is low ---
    if confidence < CONFIDENCE_THRESHOLD:
        if rule_txns:
            warnings.append(
                f"Rule-based extraction found only {len(rule_txns)} transaction(s) "
                f"(confidence {confidence:.0%}). Trying Claude fallback for better coverage."
            )
        else:
            warnings.append(
                "Rule-based extraction found no transactions. "
                "Trying Claude fallback extraction."
            )

        claude_txns, claude_method = claude_extract_transactions(raw_text, source, doc_type)

        if claude_txns:
            # Use Claude results if they are better than rule-based
            if len(claude_txns) >= len(rule_txns):
                return ExtractionResult(
                    transactions=claude_txns,
                    raw_text=raw_text,
                    pages_processed=pages_processed,
                    extraction_method=claude_method,
                    confidence=0.80,
                    warnings=warnings,
                    document_type=doc_type,
                )
            else:
                warnings.append(
                    f"Claude found {len(claude_txns)} vs rule-based {len(rule_txns)}. "
                    "Keeping rule-based result."
                )
        else:
            if claude_method == "claude_unavailable":
                warnings.append(
                    "ANTHROPIC_API_KEY not set — Claude fallback unavailable. "
                    "Set the key in .env to enable AI-assisted extraction for "
                    "non-standard PDFs."
                )
            else:
                warnings.append(f"Claude fallback also produced no results ({claude_method}).")

    # Return best rule-based result (even if low confidence)
    if not rule_txns:
        warnings.append(
            "No transactions extracted by any method. "
            "If this is a scanned PDF, OCR support is planned for a future version."
        )

    return ExtractionResult(
        transactions=rule_txns,
        raw_text=raw_text,
        pages_processed=pages_processed,
        extraction_method=rule_method,
        confidence=confidence,
        warnings=warnings,
        document_type=doc_type,
    )
