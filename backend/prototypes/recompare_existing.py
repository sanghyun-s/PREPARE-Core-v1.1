"""
recompare_existing.py — Re-compare existing PDF Skill JSON outputs against
ground-truth CSVs using a fixed loader. Does NOT call the API.

Place this file in backend/prototypes/ alongside sample_pdf_skill_test.py.

Run from project root:
    python backend/prototypes/recompare_existing.py

Reads:
    outputs/pdf_skill_tests/sample_bank_3col_clean_pdf_skill.json
    outputs/pdf_skill_tests/sample_bank_multicolumn_pdf_skill.json
    docs/extraction_policy/debug_3col_clean.csv
    docs/extraction_policy/debug_multicolumn.csv

Writes:
    outputs/pdf_skill_tests/recompare_summary.md
"""

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "pdf_skill_tests"

TIER_1_PDFS = [
    {
        "filename": "sample_bank_3col_clean.pdf",
        "json_file": "sample_bank_3col_clean_pdf_skill.json",
        "ground_truth_csv": "docs/extraction_policy/debug_3col_clean.csv",
    },
    {
        "filename": "sample_bank_multicolumn.pdf",
        "json_file": "sample_bank_multicolumn_pdf_skill.json",
        "ground_truth_csv": "docs/extraction_policy/debug_multicolumn.csv",
    },
]


@dataclass
class GroundTruthRow:
    date: str
    description: str
    amount: float
    transaction_type: str
    include_for_1099: bool
    exclusion_reason: str = ""


def load_ground_truth_fixed(csv_path: Path) -> list[GroundTruthRow]:
    """
    Fixed loader recognizing PREPARE's actual column names:
        parsed_date / raw_date / date
        parsed_description / raw_description / description
        parsed_amount / raw_amount / amount
        include_for_1099 (yes/no)
        transaction_type
        exclusion_reason
    """
    if not csv_path.exists():
        print(f"  WARNING: CSV not found: {csv_path}")
        return []

    rows: list[GroundTruthRow] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        reader.fieldnames = fieldnames

        for row in reader:
            def get(*keys, default=""):
                for k in keys:
                    if k in row and row[k] is not None and str(row[k]).strip() != "":
                        return str(row[k]).strip()
                return default

            # Try parsed_amount first (canonical), then raw_amount, then amount
            amt_str = get("parsed_amount", "raw_amount", "amount", "amt")
            amt_str = amt_str.replace("$", "").replace(",", "")
            try:
                amount = float(amt_str) if amt_str else 0.0
            except ValueError:
                amount = 0.0

            inc_str = get("include_for_1099", "include", "included").lower()
            include = inc_str in ("true", "yes", "y", "1", "include")

            date = get("parsed_date", "raw_date", "date")
            description = get(
                "parsed_description", "raw_description", "description", "desc"
            )

            rows.append(GroundTruthRow(
                date=date,
                description=description,
                amount=amount,
                transaction_type=get("transaction_type", "type"),
                include_for_1099=include,
                exclusion_reason=get("exclusion_reason", "reason"),
            ))
    return rows


def compare(extracted_txns: list, ground_truth: list[GroundTruthRow]) -> dict:
    """Row-by-row comparison."""
    gt_included = [r for r in ground_truth if r.include_for_1099]
    gt_excluded = [r for r in ground_truth if not r.include_for_1099]
    gt_inc_total = round(sum(r.amount for r in gt_included), 2)

    ex_included = [t for t in extracted_txns if t.get("include_for_1099") is True]
    ex_excluded = [t for t in extracted_txns if t.get("include_for_1099") is False]
    ex_inc_total = round(
        sum(float(t.get("amount", 0) or 0) for t in ex_included), 2
    )

    counts_match = (
        len(gt_included) == len(ex_included)
        and len(gt_excluded) == len(ex_excluded)
    )
    total_delta = round(ex_inc_total - gt_inc_total, 2)
    totals_match = abs(total_delta) < 0.05

    def row_key(amount: float, desc: str) -> tuple:
        return (round(amount, 2), desc[:20].upper().strip())

    gt_keys = {row_key(r.amount, r.description): r for r in ground_truth}
    ex_keys = {
        row_key(float(t.get("amount", 0) or 0), str(t.get("description", ""))): t
        for t in extracted_txns
    }

    missing, spurious, misclassified = [], [], []
    for k, gt_row in gt_keys.items():
        if k not in ex_keys:
            missing.append({
                "date": gt_row.date,
                "description": gt_row.description,
                "amount": gt_row.amount,
                "include": gt_row.include_for_1099,
                "type": gt_row.transaction_type,
            })
    for k, ex_row in ex_keys.items():
        if k not in gt_keys:
            spurious.append({
                "date": ex_row.get("date", ""),
                "description": ex_row.get("description", ""),
                "amount": ex_row.get("amount", 0),
                "include": ex_row.get("include_for_1099"),
                "type": ex_row.get("transaction_type", ""),
            })
    for k, gt_row in gt_keys.items():
        if k in ex_keys:
            ex_row = ex_keys[k]
            if bool(ex_row.get("include_for_1099")) != gt_row.include_for_1099:
                misclassified.append({
                    "description": gt_row.description,
                    "amount": gt_row.amount,
                    "gt_include": gt_row.include_for_1099,
                    "ex_include": bool(ex_row.get("include_for_1099")),
                    "ex_type": ex_row.get("transaction_type", ""),
                })

    return {
        "gt_included_count": len(gt_included),
        "gt_excluded_count": len(gt_excluded),
        "gt_included_total": gt_inc_total,
        "ex_included_count": len(ex_included),
        "ex_excluded_count": len(ex_excluded),
        "ex_included_total": ex_inc_total,
        "counts_match": counts_match,
        "totals_match": totals_match,
        "total_delta": total_delta,
        "missing": missing,
        "spurious": spurious,
        "misclassified": misclassified,
    }


def main():
    print("== Re-compare existing PDF Skill outputs ==")
    print(f"Project root: {PROJECT_ROOT}")
    print()

    report_lines = ["# PDF Skill Re-Compare Summary", "", ""]
    all_pass = True

    for spec in TIER_1_PDFS:
        json_path = OUTPUT_DIR / spec["json_file"]
        csv_path = PROJECT_ROOT / spec["ground_truth_csv"]

        print(f"── {spec['filename']} ──")

        if not json_path.exists():
            print(f"  JSON not found: {json_path}")
            print(f"  Run the prototype first to generate it.\n")
            continue

        with open(json_path) as f:
            json_data = json.load(f)

        if not json_data.get("success") or not json_data.get("parsed"):
            print(f"  JSON marked unsuccessful or empty.\n")
            continue

        txns = json_data["parsed"].get("transactions", [])

        # Verify loader works
        gt_rows = load_ground_truth_fixed(csv_path)
        gt_inc = sum(1 for r in gt_rows if r.include_for_1099)
        gt_exc = sum(1 for r in gt_rows if not r.include_for_1099)
        gt_tot = sum(r.amount for r in gt_rows if r.include_for_1099)
        print(f"  Ground truth loaded: {len(gt_rows)} rows "
              f"({gt_inc} included, {gt_exc} excluded, ${gt_tot:,.2f})")

        cmp = compare(txns, gt_rows)
        counts = "✓" if cmp["counts_match"] else "✗"
        totals = "✓" if cmp["totals_match"] else "✗"
        print(f"  Extracted:           {len(txns)} rows "
              f"({cmp['ex_included_count']} included, {cmp['ex_excluded_count']} excluded, "
              f"${cmp['ex_included_total']:,.2f})")
        print(f"  vs GT: {counts} counts · {totals} totals · "
              f"Δ ${cmp['total_delta']:+.2f} · "
              f"{len(cmp['missing'])} missing · "
              f"{len(cmp['spurious'])} spurious · "
              f"{len(cmp['misclassified'])} misclassified")

        if not (cmp["counts_match"] and cmp["totals_match"]):
            all_pass = False

        # Report block
        report_lines.append(f"## `{spec['filename']}`")
        report_lines.append("")
        report_lines.append(f"- **Ground truth:** {gt_inc} included, {gt_exc} excluded, ${gt_tot:,.2f}")
        report_lines.append(f"- **Extracted:** {cmp['ex_included_count']} included, "
                            f"{cmp['ex_excluded_count']} excluded, ${cmp['ex_included_total']:,.2f}")
        report_lines.append(f"- **Counts match:** {counts}")
        report_lines.append(f"- **Totals match:** {totals} (Δ ${cmp['total_delta']:+.2f})")
        report_lines.append(f"- Missing rows: {len(cmp['missing'])}")
        report_lines.append(f"- Spurious rows: {len(cmp['spurious'])}")
        report_lines.append(f"- Misclassified rows: {len(cmp['misclassified'])}")
        report_lines.append("")

        if cmp["missing"]:
            report_lines.append("**Missing** (in GT, not in extraction):")
            for r in cmp["missing"][:10]:
                report_lines.append(f"- {r['date']} · {r['description']} · "
                                    f"${r['amount']:,.2f} · {r['type']} · "
                                    f"include={r['include']}")
            if len(cmp["missing"]) > 10:
                report_lines.append(f"- ...and {len(cmp['missing']) - 10} more")
            report_lines.append("")

        if cmp["spurious"]:
            report_lines.append("**Spurious** (in extraction, not in GT):")
            for r in cmp["spurious"][:10]:
                report_lines.append(f"- {r['date']} · {r['description']} · "
                                    f"${r['amount']} · {r['type']} · "
                                    f"include={r['include']}")
            if len(cmp["spurious"]) > 10:
                report_lines.append(f"- ...and {len(cmp['spurious']) - 10} more")
            report_lines.append("")

        if cmp["misclassified"]:
            report_lines.append("**Misclassified** (matched amount+desc, include differs):")
            for r in cmp["misclassified"][:10]:
                report_lines.append(f"- {r['description']} · ${r['amount']:,.2f} · "
                                    f"GT={r['gt_include']} / Extract={r['ex_include']} "
                                    f"(type={r['ex_type']})")
            if len(cmp["misclassified"]) > 10:
                report_lines.append(f"- ...and {len(cmp['misclassified']) - 10} more")
            report_lines.append("")

        report_lines.append("")
        print()

    report_lines.append("---")
    report_lines.append("")
    if all_pass:
        report_lines.append("## Bottom line: Tier 1 PASS")
        report_lines.append("")
        report_lines.append("Both Tier 1 PDFs match ground truth on counts and totals.")
        report_lines.append("Pre-built `pdf` Agent Skill via Claude Agent SDK is **viable**")
        report_lines.append("as the v1.3 ingestion path.")
        print("Bottom line: Tier 1 PASS")
    else:
        report_lines.append("## Bottom line: Tier 1 has differences")
        report_lines.append("")
        report_lines.append("See per-PDF detail above for which rows differ.")
        print("Bottom line: Tier 1 has differences (see report)")

    out_path = OUTPUT_DIR / "recompare_summary.md"
    out_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nWritten: {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
