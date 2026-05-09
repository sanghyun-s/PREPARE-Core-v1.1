"""
Excel Output Generator
----------------------
Produces the accountant-grade Excel workbook with three sheets:

    1. "Vendor Summary"  — aggregated view, one row per canonical vendor
    2. "Transactions"    — full transaction detail with normalization metadata
    3. "Summary Stats"   — totals, counts, and review-needed flags

Design principles (this is the portfolio differentiator):
    - Column headers encode ACCOUNTING logic, not just data fields
    - Review-needed rows are visually flagged (yellow highlight)
    - Output is IMPORTABLE into QuickBooks / 1099-ETC, not just readable
    - Every number is either a formula or clearly sourced

NOTE on Session 3 vs Session 4 scope:
    The "1099 Eligible" and "Needs W-9" columns are STUBBED in Session 3
    (marked "TBD" or left blank). Session 4 will populate them with real logic.
    The column structure is in place now so the Session 4 upgrade is additive,
    not a schema change.
"""

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime

from transaction_aggregator import VendorSummary, Transaction
from vendor_normalizer import NormalizedVendor
from vendor_classifier_1099 import EligibilityResult


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

HEADER_FILL  = PatternFill("solid", start_color="1F3A5F")   # Navy
HEADER_FONT  = Font(name="Arial", bold=True, color="FFFFFF", size=11)
REVIEW_FILL  = PatternFill("solid", start_color="FFF4CC")   # Yellow — needs review
ELIGIBLE_FILL = PatternFill("solid", start_color="E8F5E9")  # Light green — 1099 required
MISC_FILL    = PatternFill("solid", start_color="E3F2FD")   # Light blue — 1099-MISC
EXEMPT_FILL  = PatternFill("solid", start_color="F5F5F5")   # Light grey — exempt
BODY_FONT = Font(name="Arial", size=10)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")

THIN_BORDER = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def style_header_row(ws, row_num: int, num_cols: int):
    for col in range(1, num_cols + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = THIN_BORDER
    ws.row_dimensions[row_num].height = 22


def set_column_widths(ws, widths: dict[str, int]):
    for col_letter, width in widths.items():
        ws.column_dimensions[col_letter].width = width


# ---------------------------------------------------------------------------
# Sheet 1: Vendor Summary (THE HERO SHEET)
# ---------------------------------------------------------------------------

VENDOR_SUMMARY_COLUMNS = [
    ("Vendor Name",         22, "left"),
    ("Entity Type",         12, "center"),
    ("Total Paid ($)",      15, "right"),
    ("# Payments",          11, "center"),
    ("First Payment",       13, "center"),
    ("Last Payment",        13, "center"),
    ("1099 Eligible",       14, "center"),    # TBD in Session 3, filled in Session 4
    ("W-9 on File",         12, "center"),    # TBD in Session 3, filled in Session 4
    ("Category",            14, "center"),
    ("Confidence",          11, "center"),
    ("Review Needed",       14, "center"),
    ("Review Reason",       40, "left"),
    ("Raw Name Variants",   40, "left"),
]


def write_vendor_summary_sheet(
    wb: Workbook,
    summaries: list[VendorSummary],
    eligibility: dict[str, EligibilityResult] = None,
):
    ws = wb.create_sheet("Vendor Summary", 0)

    # Title row
    ws["A1"] = "1099 PRE-RECONCILIATION WORKSHEET"
    ws["A1"].font = Font(name="Arial", bold=True, size=14, color="1F3A5F")
    ws.merge_cells("A1:M1")

    ws["A2"] = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws["A2"].font = Font(name="Arial", italic=True, size=9, color="666666")
    ws.merge_cells("A2:M2")

    # Header row (row 4)
    for col_idx, (label, width, _) in enumerate(VENDOR_SUMMARY_COLUMNS, start=1):
        ws.cell(row=4, column=col_idx, value=label)
    style_header_row(ws, 4, len(VENDOR_SUMMARY_COLUMNS))

    # Column widths
    for col_idx, (_, width, _) in enumerate(VENDOR_SUMMARY_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Data rows
    row = 5
    for s in summaries:
        elig = (eligibility or {}).get(s.canonical_name)
        eligible_val = elig.form_type if elig else "TBD"
        w9_val = elig.w9_on_file if elig else "TBD"

        row_data = [
            s.canonical_name,
            s.entity_type or "Individual?",
            s.total_amount,
            s.transaction_count,
            s.first_payment_date or "",
            s.last_payment_date or "",
            eligible_val,
            w9_val,
            classify_category(s),
            s.match_confidence,
            "YES" if s.needs_review else "NO",
            "; ".join(s.review_reasons),
            "; ".join(s.raw_name_variants),
        ]
        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row, column=col_idx, value=value)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            _, _, align = VENDOR_SUMMARY_COLUMNS[col_idx - 1]
            cell.alignment = {"left": LEFT, "right": RIGHT, "center": CENTER}[align]

        # Format amount as currency
        ws.cell(row=row, column=3).number_format = '$#,##0.00;($#,##0.00);-'
        # Format confidence as percentage
        ws.cell(row=row, column=10).number_format = '0%'

        # Row color: eligibility takes priority over review flag
        if elig:
            if elig.form_type in ("1099-NEC",):
                row_fill = ELIGIBLE_FILL
            elif elig.form_type == "1099-MISC":
                row_fill = MISC_FILL
            elif elig.form_type == "REVIEW":
                row_fill = REVIEW_FILL
            else:
                row_fill = None  # EXEMPT — no fill
        elif s.needs_review:
            row_fill = REVIEW_FILL
        else:
            row_fill = None

        if row_fill:
            for col_idx in range(1, len(VENDOR_SUMMARY_COLUMNS) + 1):
                ws.cell(row=row, column=col_idx).fill = row_fill

        row += 1

    # Totals row at the bottom
    total_row = row
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(name="Arial", bold=True, size=11)
    ws.cell(row=total_row, column=3, value=f"=SUM(C5:C{row - 1})")
    ws.cell(row=total_row, column=3).number_format = '$#,##0.00'
    ws.cell(row=total_row, column=3).font = Font(name="Arial", bold=True, size=11)
    ws.cell(row=total_row, column=4, value=f"=SUM(D5:D{row - 1})")
    ws.cell(row=total_row, column=4).font = Font(name="Arial", bold=True, size=11)
    for col_idx in range(1, len(VENDOR_SUMMARY_COLUMNS) + 1):
        ws.cell(row=total_row, column=col_idx).border = Border(
            top=Side(style="medium", color="1F3A5F")
        )

    # Freeze header row
    ws.freeze_panes = "A5"


def classify_category(summary: VendorSummary) -> str:
    """
    Simple category classification.
    Session 4 will replace this with a GPT-based classifier that reads memos.
    For now, use a heuristic based on entity type + common vendor patterns.
    """
    name_upper = summary.canonical_name.upper()

    # Known utility/corporate patterns
    utility_keywords = ["VERIZON", "COMCAST", "PG&E", "AT&T", "ELECTRIC", "GAS", "WATER"]
    retail_keywords = ["AMAZON", "HOME DEPOT", "STAPLES", "OFFICE DEPOT", "WALMART", "TARGET", "COSTCO"]

    if any(k in name_upper for k in utility_keywords):
        return "Utility"
    if any(k in name_upper for k in retail_keywords):
        return "Supplies/Retail"
    if summary.entity_type in ("LLC", "INC", "CORP"):
        return "Contractor"
    if "UNRESOLVED" in name_upper:
        return "Unclear"
    return "Unclear"


# ---------------------------------------------------------------------------
# Sheet 2: Transactions (detail view)
# ---------------------------------------------------------------------------

def write_transactions_sheet(
    wb: Workbook,
    transactions: list[Transaction],
    normalized: list[NormalizedVendor],
):
    ws = wb.create_sheet("Transactions")

    ws["A1"] = "TRANSACTION DETAIL"
    ws["A1"].font = Font(name="Arial", bold=True, size=14, color="1F3A5F")
    ws.merge_cells("A1:H1")

    headers = [
        ("Date", 12),
        ("Raw Description", 35),
        ("Canonical Vendor", 25),
        ("Entity Type", 12),
        ("Amount ($)", 14),
        ("Source", 12),
        ("Confidence", 12),
        ("Review?", 10),
    ]
    for col_idx, (label, width) in enumerate(headers, start=1):
        ws.cell(row=3, column=col_idx, value=label)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    style_header_row(ws, 3, len(headers))

    for i, (txn, norm) in enumerate(zip(transactions, normalized), start=4):
        values = [
            txn.date or "",
            txn.description,
            norm.canonical_name,
            norm.entity_type or "",
            txn.amount,
            txn.source,
            norm.match_confidence,
            "YES" if norm.needs_review else "",
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=i, column=col_idx, value=value)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER

        ws.cell(row=i, column=5).number_format = '$#,##0.00'
        ws.cell(row=i, column=7).number_format = '0%'

        if norm.needs_review:
            for col_idx in range(1, len(headers) + 1):
                ws.cell(row=i, column=col_idx).fill = REVIEW_FILL

    ws.freeze_panes = "A4"


# ---------------------------------------------------------------------------
# Sheet 3: Summary Statistics
# ---------------------------------------------------------------------------

def write_summary_stats_sheet(
    wb: Workbook,
    summaries: list[VendorSummary],
    transactions: list[Transaction],
):
    ws = wb.create_sheet("Summary Stats")

    ws["A1"] = "RECONCILIATION SUMMARY"
    ws["A1"].font = Font(name="Arial", bold=True, size=14, color="1F3A5F")
    ws.merge_cells("A1:B1")

    ws["A2"] = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws["A2"].font = Font(name="Arial", italic=True, size=9, color="666666")
    ws.merge_cells("A2:B2")

    # Compute stats (these are hardcoded because they're metadata, not model values)
    total_txns = len(transactions)
    total_vendors = len(summaries)
    total_amount = sum(s.total_amount for s in summaries)
    vendors_over_600 = sum(1 for s in summaries if s.total_amount >= 600)
    review_needed = sum(1 for s in summaries if s.needs_review)
    llc_vendors = sum(1 for s in summaries if s.entity_type in ("LLC", "INC", "CORP"))

    stats = [
        ("", ""),
        ("PROCESSING METRICS", ""),
        ("Total transactions processed", total_txns),
        ("Unique vendors identified", total_vendors),
        ("Total $ reconciled", total_amount),
        ("", ""),
        ("1099 PRE-SCREEN (Session 4 will refine)", ""),
        ("Vendors crossing $600 threshold", vendors_over_600),
        ("Vendors with entity suffix (LLC/Inc/Corp)", llc_vendors),
        ("", ""),
        ("REVIEW FLAGS", ""),
        ("Vendors needing human review", review_needed),
        ("Review rate", f"{review_needed / max(total_vendors, 1):.1%}"),
    ]

    row = 4
    for label, value in stats:
        cell_a = ws.cell(row=row, column=1, value=label)
        cell_b = ws.cell(row=row, column=2, value=value)
        cell_a.font = BODY_FONT
        cell_b.font = BODY_FONT
        # Section headers
        if label and not value and label != "":
            cell_a.font = Font(name="Arial", bold=True, size=11, color="1F3A5F")
        # Currency format for total amount
        if label == "Total $ reconciled":
            cell_b.number_format = '$#,##0.00'
        row += 1

    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 18

    # Session 3/4 scope note at the bottom
    row += 2
    ws.cell(row=row, column=1, value="SCOPE NOTES").font = Font(name="Arial", bold=True, size=11, color="1F3A5F")
    row += 1
    notes = [
        "Session 3 (current): PDF extraction, vendor normalization, aggregation, review flagging",
        "Session 4 (next): 1099 eligibility logic (entity type + attorney/medical/merchandise rules),",
        "                  W-9 status tracking, cross-validation vs client-reported amounts,",
        "                  GPT-based anomaly explanations in plain English",
    ]
    for note in notes:
        ws.cell(row=row, column=1, value=note).font = Font(name="Arial", italic=True, size=9, color="666666")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        row += 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_excel_report(
    output_path: str,
    transactions: list[Transaction],
    normalized: list[NormalizedVendor],
    summaries: list[VendorSummary],
    eligibility: dict[str, EligibilityResult] = None,
):
    """Generate the full three-sheet Excel report."""
    wb = Workbook()
    wb.remove(wb.active)

    write_vendor_summary_sheet(wb, summaries, eligibility)
    write_transactions_sheet(wb, transactions, normalized)
    write_summary_stats_sheet(wb, summaries, transactions)

    wb.save(output_path)
    return output_path
