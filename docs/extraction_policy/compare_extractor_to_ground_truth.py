"""
compare_extractor_to_ground_truth.py
=====================================

Diagnostic script — confirms (or falsifies) the overcounting hypothesis.

Reads ground truth CSVs (manually classified row-by-row) and compares
against actual output from the project's pdf_extractor.

Run from the project root:

    python3 docs/extraction_policy/compare_extractor_to_ground_truth.py \\
            samples/sample_bank_3col_clean.pdf \\
            samples/sample_bank_multicolumn.pdf

Output:
    - Per-PDF count and total comparison
    - Spurious rows (extracted but not in ground truth)
    - Missed rows (in ground truth but not extracted)
    - Amount mismatches
    - Final hypothesis verdict (CONFIRMED / FALSIFIED / PARTIAL)

This script does NOT modify any project code. It only reads.
"""

import csv
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

# Locate the docs/extraction_policy directory containing the ground truth CSVs.
HERE = Path(__file__).parent
GROUND_TRUTH_DIR = HERE

# Path to the user's extractor — adjust if needed.
PROJECT_ROOT = HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# Import the project's extractor.
try:
    from backend.pdf_extractor import extract_transactions
except ImportError:
    try:
        from pdf_extractor import extract_transactions
    except ImportError as e:
        print(f"ERROR: Could not import pdf_extractor: {e}")
        print(f"  Tried: {PROJECT_ROOT}/backend/pdf_extractor.py")
        print(f"  Tried: {PROJECT_ROOT}/pdf_extractor.py")
        print(f"  Adjust the imports at the top of this script if your")
        print(f"  project layout is different.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------

def load_ground_truth(csv_filename: str) -> list[dict]:
    """Load row-level ground truth from a debug CSV."""
    path = GROUND_TRUTH_DIR / csv_filename
    if not path.exists():
        raise FileNotFoundError(
            f"Ground truth CSV not found: {path}\n"
            f"Expected location: docs/extraction_policy/{csv_filename}"
        )
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize numeric fields
            try:
                row["parsed_amount"] = float(row["parsed_amount"]) if row["parsed_amount"] else None
            except ValueError:
                row["parsed_amount"] = None
            row["row_index"] = int(row["row_index"])
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def normalize_for_match(s: str) -> str:
    """Lowercase + strip + collapse whitespace, for fuzzy matching."""
    if not s:
        return ""
    return " ".join(s.lower().split())


def find_match(extracted_txn, ground_truth_rows: list[dict]) -> tuple[dict, str] | None:
    """
    Try to match an extracted transaction against a ground-truth row.

    Returns (matched_row, match_quality) or None.
    match_quality: 'exact_amount' | 'amount_only' | 'description_only' | None
    """
    extracted_desc = normalize_for_match(getattr(extracted_txn, "description", ""))
    extracted_amt = round(getattr(extracted_txn, "amount", 0.0) or 0.0, 2)

    # First pass — exact match on description AND amount
    for gt in ground_truth_rows:
        gt_desc = normalize_for_match(gt["parsed_description"])
        gt_amt = round(gt["parsed_amount"] or 0.0, 2)
        if gt_desc == extracted_desc and gt_amt == extracted_amt:
            return gt, "exact"

    # Second pass — description match (any amount)
    for gt in ground_truth_rows:
        gt_desc = normalize_for_match(gt["parsed_description"])
        if gt_desc == extracted_desc:
            return gt, "description_only"

    # Third pass — partial-substring match on description
    for gt in ground_truth_rows:
        gt_desc = normalize_for_match(gt["parsed_description"])
        if gt_desc and extracted_desc and (gt_desc in extracted_desc or extracted_desc in gt_desc):
            gt_amt = round(gt["parsed_amount"] or 0.0, 2)
            if abs(gt_amt - extracted_amt) < 0.01:
                return gt, "exact_substring"
            return gt, "substring_only"

    return None


def compare_extraction(pdf_path: str, ground_truth_csv: str) -> dict:
    """Run extractor on a PDF and compare against ground truth."""
    pdf_name = Path(pdf_path).name

    print("=" * 75)
    print(f"DEBUGGING: {pdf_name}")
    print("=" * 75)

    # Load ground truth
    gt_rows = load_ground_truth(ground_truth_csv)
    gt_included = [r for r in gt_rows if r["include_for_1099"] == "yes"]
    gt_excluded = [r for r in gt_rows if r["include_for_1099"] == "no"]

    print(f"\nGround truth:")
    print(f"  Total rows:               {len(gt_rows)}")
    print(f"  Vendor-payment rows:      {len(gt_included)}")
    print(f"  Excluded rows:            {len(gt_excluded)}")
    if gt_included:
        gt_total = sum(r["parsed_amount"] or 0 for r in gt_included)
        print(f"  Vendor-payment total $:   ${gt_total:,.2f}")

    # Run actual extractor
    print(f"\nRunning extract_transactions() on {pdf_path}...")
    try:
        result = extract_transactions(pdf_path)
        extracted = result.transactions
    except Exception as e:
        print(f"  ERROR: extractor threw exception: {e}")
        return {"pdf": pdf_name, "error": str(e)}

    extracted_total = sum(round(getattr(t, "amount", 0.0) or 0.0, 2) for t in extracted)

    print(f"\nExtractor returned:")
    print(f"  Transactions:             {len(extracted)}")
    print(f"  Total amount:             ${extracted_total:,.2f}")

    # Per-row matching
    matched_pairs = []     # (extracted_idx, gt_row, match_quality)
    spurious = []          # extracted rows that don't match anything
    matched_gt_indices = set()

    for i, txn in enumerate(extracted):
        match = find_match(txn, gt_rows)
        if match is None:
            spurious.append((i, txn))
        else:
            gt_row, quality = match
            matched_pairs.append((i, txn, gt_row, quality))
            matched_gt_indices.add(gt_row["row_index"])

    missed = [r for r in gt_rows if r["row_index"] not in matched_gt_indices]

    print(f"\nMatching summary:")
    print(f"  Matched pairs:            {len(matched_pairs)}")
    print(f"  Spurious (extra) rows:    {len(spurious)}")
    print(f"  Missed (gt-only) rows:    {len(missed)}")

    # ── Critical question: are the spurious rows the excluded categories? ──
    if spurious:
        print(f"\n--- SPURIOUS ROWS (extracted but not in ground truth) ---")
        for i, txn in spurious[:20]:  # show up to 20
            desc = getattr(txn, "description", "")[:50]
            amt = getattr(txn, "amount", 0.0)
            print(f"  [{i}] desc={desc!r}  amount=${amt:,.2f}")
        if len(spurious) > 20:
            print(f"  ... and {len(spurious) - 20} more")

    # ── Are excluded ground-truth rows being extracted? (the bug indicator) ──
    excluded_descs = [normalize_for_match(r["parsed_description"]) for r in gt_excluded]
    extracted_descs_in_excluded_zone = []
    for i, txn in enumerate(extracted):
        d = normalize_for_match(getattr(txn, "description", ""))
        for excluded_desc in excluded_descs:
            if d and excluded_desc and (d == excluded_desc or excluded_desc in d):
                extracted_descs_in_excluded_zone.append((i, txn, excluded_desc))
                break

    if extracted_descs_in_excluded_zone:
        print(f"\n--- EXTRACTED ROWS THAT MATCH EXCLUDED GROUND TRUTH ---")
        print(f"  These rows SHOULD have been filtered out per inclusion policy:")
        for i, txn, gt_desc in extracted_descs_in_excluded_zone[:20]:
            desc = getattr(txn, "description", "")[:50]
            amt = getattr(txn, "amount", 0.0)
            print(f"  [{i}] desc={desc!r}  amount=${amt:,.2f}  → matches excluded GT '{gt_desc[:40]}'")
        if len(extracted_descs_in_excluded_zone) > 20:
            print(f"  ... and {len(extracted_descs_in_excluded_zone) - 20} more")

    # ── Verdict ──
    print(f"\n--- VERDICT ---")
    if len(extracted_descs_in_excluded_zone) == len(gt_excluded) and len(extracted_descs_in_excluded_zone) > 0:
        print(f"  HYPOTHESIS CONFIRMED: extractor includes ALL {len(gt_excluded)} excluded rows.")
        print(f"  Bug source: extractor reads every dated line regardless of column semantics.")
    elif len(extracted_descs_in_excluded_zone) > 0:
        print(f"  HYPOTHESIS PARTIALLY CONFIRMED: extractor includes")
        print(f"  {len(extracted_descs_in_excluded_zone)} of {len(gt_excluded)} excluded rows.")
        print(f"  Some classification is happening, but not enough.")
    elif len(gt_excluded) == 0:
        print(f"  No excluded rows in ground truth — this PDF doesn't test the hypothesis.")
    else:
        print(f"  HYPOTHESIS FALSIFIED: extractor correctly excludes the rows that should be excluded.")
        print(f"  Bug source is elsewhere — investigate spurious-row count and amount mismatches.")

    return {
        "pdf": pdf_name,
        "ground_truth_rows": len(gt_rows),
        "ground_truth_included": len(gt_included),
        "extractor_rows": len(extracted),
        "matched": len(matched_pairs),
        "spurious": len(spurious),
        "missed": len(missed),
        "excluded_rows_leaking_through": len(extracted_descs_in_excluded_zone),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 compare_extractor_to_ground_truth.py <pdf_path> [<pdf_path>...]")
        print()
        print("Recommended invocation (from project root):")
        print("  python3 docs/extraction_policy/compare_extractor_to_ground_truth.py \\")
        print("          samples/sample_bank_3col_clean.pdf \\")
        print("          samples/sample_bank_multicolumn.pdf")
        sys.exit(1)

    pdf_paths = sys.argv[1:]

    # Map each PDF to the corresponding ground-truth CSV
    csv_map = {
        "sample_bank_3col_clean.pdf":   "debug_3col_clean.csv",
        "sample_bank_multicolumn.pdf":  "debug_multicolumn.csv",
    }

    results = []
    for path in pdf_paths:
        name = Path(path).name
        if name not in csv_map:
            print(f"\n⚠ No ground-truth CSV for {name} — skipping.")
            print(f"  This script currently has ground truth only for:")
            for k in csv_map.keys():
                print(f"    - {k}")
            continue
        result = compare_extraction(path, csv_map[name])
        results.append(result)
        print()

    # Final summary
    if len(results) > 1:
        print("=" * 75)
        print("OVERALL SUMMARY")
        print("=" * 75)
        for r in results:
            if "error" in r:
                print(f"  {r['pdf']}: extractor ERROR — {r['error']}")
                continue
            print(f"  {r['pdf']}:")
            print(f"    Extracted: {r['extractor_rows']} rows  vs  GT vendor-payment: {r['ground_truth_included']}")
            print(f"    Excluded rows leaking through: {r['excluded_rows_leaking_through']}")


if __name__ == "__main__":
    main()
