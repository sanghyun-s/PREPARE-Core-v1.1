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
"""

import argparse
import csv
import sys
from pathlib import Path

from pdf_extractor import extract_transactions
from vendor_normalizer import normalize_vendor
from transaction_aggregator import aggregate_by_vendor
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


def run_pipeline(
    pdf_path: str,
    output_path: str,
    vendor_list_path: str = None,
    source: str = "bank",
    verbose: bool = True,
) -> dict:
    """
    Execute the full pipeline. Returns a summary dict for API responses.
    """
    # Step 1: Extract transactions from PDF
    if verbose:
        print(f"[1/4] Extracting transactions from {Path(pdf_path).name}...")
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

    # Step 2: Load known vendor list (if provided)
    known_vendors = load_vendor_list(vendor_list_path) if vendor_list_path else []
    if verbose and known_vendors:
        print(f"[2/4] Loaded {len(known_vendors)} known vendors from vendor list")
    elif verbose:
        print(f"[2/4] No vendor list provided — using extracted names as canonical")

    # Step 3: Normalize vendor names
    if verbose:
        print(f"[3/4] Normalizing vendor names...")
    normalized = [
        normalize_vendor(t.description, known_vendors)
        for t in extraction.transactions
    ]
    review_count = sum(1 for n in normalized if n.needs_review)
    if verbose:
        print(f"      {review_count} transactions flagged for human review")

    # Step 4: Aggregate by vendor
    if verbose:
        print(f"[4/5] Aggregating by vendor...")
    summaries = aggregate_by_vendor(extraction.transactions, normalized)

    # Step 5: Classify 1099 eligibility
    if verbose:
        print(f"[5/5] Running 1099 eligibility classifier and generating Excel report...")
    eligibility = classify_all_vendors(summaries)

    # Generate Excel
    generate_excel_report(
        output_path=output_path,
        transactions=extraction.transactions,
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
        print(f"  - {len(extraction.transactions)} transactions")
        print(f"  - {len(summaries)} unique vendors")
        print(f"  - ${total_amount:,.2f} total reconciled")
        print(f"  - {vendors_over_600} vendors crossed $600 threshold")
        print(f"  - {vendors_needing_review} vendors need human review")

    return {
        "success": True,
        "output_path": output_path,
        "transaction_count": len(extraction.transactions),
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
    args = parser.parse_args()

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
