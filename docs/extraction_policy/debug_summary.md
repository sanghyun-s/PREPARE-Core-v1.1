# Debug Summary

**PREPARE Core — Pipeline diagnosis based on row-level ground truth**
**Status:** v1.2 diagnostic finding (no code changes yet)
**Date:** v1.2 development cycle

---

## TL;DR

The overcounting bug is most likely **NOT a model selection issue and NOT a model variance issue**. It is a deterministic extraction artifact:

> The extractor reads every dated line in a PDF as a transaction, regardless of which amount column contains the actual transaction amount. For multi-column PDFs (Withdrawals / Deposits / Balance), this means payroll deposit rows and opening/ending balance rows are extracted as transactions with $0 amount (because the extractor only reads from the Withdrawals column). The amount totals stay correct, but the **row count and over-$600 candidate count are inflated** by the number of non-vendor-payment rows in the PDF.

This explanation is consistent with all observed evidence and is testable with a small comparison script (provided below).

---

## Evidence trail

### Row-level ground truth

From manual analysis of the raw PDF text:

| Sample | Total rows | Vendor-payment rows | Excluded rows | Excluded $$ |
|---|---|---|---|---|
| `sample_bank_3col_clean.pdf` | 37 | 37 | 0 | $0 |
| `sample_bank_multicolumn.pdf` | 39 | 31 | 8 (6 payroll + 2 balance) | $39,000 (payroll) |
| **Combined** | **76** | **68** | **8** | **$39,000** |

(The $39,000 is total payroll-deposit dollar amount across the 6 payroll rows in PDF2. Balance rows have no transactional dollar amount — the number shown is end-of-day balance, not a payment.)

### Observed extractor output

From the v1.1 same-input test runs and the v1.2 Pass A test runs on the same two PDFs:

| Engine | Reported transactions | Reported total | Δ count vs GT | Δ total vs GT |
|---|---|---|---|---|
| Haiku | 76 | $28,270.94 | **+8 (76 − 68)** | **$0 (exact match)** |
| Sonnet | 78 | $27,376.66 | +10 | −$894.28 |
| Opus | 74 | $29,165.22 | +6 | +$894.28 |

### The Haiku case is the diagnostic cracker

Haiku's transaction count (76) **exactly equals the total raw row count** (37 + 39 = 76). And Haiku's total amount **exactly matches ground truth** ($28,270.94).

The only way both can be true simultaneously is if:

1. The extractor extracted all 76 raw rows from both PDFs (including 6 payroll + 2 balance rows from PDF2)
2. The amount field was populated from the Withdrawals column for vendor-payment rows
3. The amount field was zero or blank for payroll-deposit rows (because the extractor ignored the Deposits column)
4. The amount field was zero or blank for balance rows (because there's no withdrawal column entry for those)
5. Aggregation summed the amounts correctly ($13,688.33 + $14,582.61 = $28,270.94) since zero-amount rows don't affect the sum

So **rows are leaking through extraction, but their amounts aren't**. Count is wrong, total is right.

This explains the "Vendors over $600" inflation too: the extra phantom rows likely have descriptions like "PAYROLL DIRECT DEPOSIT" or "OPENING BALANCE." Vendor normalization probably treats these as new vendors, and the cross-statement aggregator sees them with $0 amounts. They wouldn't independently cross $600, but their presence may distort the vendor-count and review-flag logic in ways that compound. Need extractor output to confirm.

### Sonnet and Opus deviate slightly

Sonnet (78, −$894) and Opus (74, +$894) don't have the clean Haiku pattern. Two possibilities:

- **Sonnet:** also extracts all rows but assigns small phantom amounts to some payroll/balance rows (e.g., reads $894 of "deposit" amount as a withdrawal somewhere)
- **Opus:** correctly excludes some rows (74 < 76) but its total is high by $894 — maybe it's including one payroll row at face value somewhere

Without the per-statement raw extractor output, this is speculation. The comparison script below will tell us definitively.

---

## Recommended smallest-possible fix

**No code changes proposed in this diagnostic pass.** This is the policy specification only. Once we confirm the hypothesis with the comparison script, the fix is one of three options ranked by scope:

### Fix Option A — Row filter at extraction time (smallest)

Inside `pdf_extractor.py`, after extracting raw rows but before returning them, filter out rows where:

- Description matches a balance pattern (`"OPENING BALANCE"`, `"ENDING BALANCE"`, `"BEGINNING BALANCE"`)
- Description matches a deposit pattern (`"PAYROLL"`, `"DIRECT DEPOSIT"`)
- Description matches a transfer pattern (`"TRANSFER FROM"`, `"TRANSFER TO"`)
- Amount field is zero or blank

This is the smallest change. Estimated effort: 2-3 hours. Risk: low. Downside: hard-coded keyword list is fragile and doesn't extend gracefully to new bank formats.

### Fix Option B — Add explicit transaction_type field (medium)

Implement what `transaction_inclusion_rules.md` describes: classify each row into a transaction_type during extraction, and mark `include_for_1099` accordingly. Aggregation then filters on the flag.

This is the right long-term fix. It makes the policy explicit, testable, and observable in the workbook (could surface excluded rows as a sheet for transparency). Estimated effort: 1–2 days. Risk: medium (touches schema + extraction + aggregation + workbook).

### Fix Option C — PDF Skill refactor (largest, deferred)

The mentor's recommended direction. Replace the pdfplumber + regex stage with Claude's PDF Skill, which can be prompted to return only vendor-payment rows by asking it to classify. Removes the layout-fragility entirely.

Defer to v1.3. The current fix should validate that the policy works at the lower architectural level before betting on a bigger refactor.

### Recommendation

**Implement Option B in v1.2.x.** Reasoning:

- It validates the policy this document defines
- It produces an artifact (the `transaction_type` field) that the v1.3 PDF Skill refactor can also produce, so the schema doesn't need to change again later
- It's testable: the comparison script below can be re-run after Option B lands and compared to ground truth directly
- It fixes the observed bug without committing to the larger PDF Skill direction

Option A would also fix the immediate bug but doesn't generalize. Option C is the right long-term answer but should wait until we know the policy is correct.

---

## How to confirm the hypothesis

Run the comparison script `compare_extractor_to_ground_truth.py` (provided in this folder). Steps:

1. The script ingests the actual `pdf_extractor.extract_transactions(pdf_path)` output for both debug PDFs
2. Compares against the ground truth in `debug_3col_clean.csv` and `debug_multicolumn.csv`
3. Prints:
   - **Per-row matching** (which extracted rows have ground-truth equivalents)
   - **Spurious rows** (extracted rows with no ground-truth equivalent — likely the bug source)
   - **Missed rows** (ground-truth rows that didn't get extracted — would indicate a different bug)
   - **Amount mismatches** (rows that match descriptions but have different amounts)

Expected output if hypothesis is correct:

- For `sample_bank_3col_clean.pdf`: 37 matches, 0 spurious, 0 missed
- For `sample_bank_multicolumn.pdf`: 31 matches against vendor-payment rows + **8 spurious** rows corresponding to the 6 payroll deposits and 2 balance lines (likely with $0 amount)

If the actual output differs from this, the hypothesis needs revision. The script's output will tell us *exactly* which rows are leaking through, which is the data we need to write the smallest-fix code.

---

## What this diagnostic pass does NOT do

To be explicit about scope:

- **No code is changed.** Every existing module (`pdf_extractor.py`, `vendor_normalizer.py`, `transaction_aggregator.py`, etc.) is untouched.
- **No schema is modified.** `Transaction` does not yet have `transaction_type` or `include_for_1099` fields.
- **No UI is updated.** Pass B remains paused.
- **No PDF Skill prototype is built.** That stays in v1.3.
- **No production fix is applied.** The smallest-fix recommendation is a *recommendation*, not an implementation.

The diagnostic pass produces five artifacts:

1. `transaction_inclusion_rules.md` — policy specification
2. `sample_taxonomy.md` — what each test sample exercises
3. `debug_3col_clean.csv` — row-level ground truth for PDF1
4. `debug_multicolumn.csv` — row-level ground truth for PDF2
5. `compare_extractor_to_ground_truth.py` — script to confirm hypothesis against actual extractor output

After running the comparison script and reviewing its output, the next decision is: which fix option to pursue, and in what release. That decision is yours to make based on the evidence the script produces.

---

## Appendix — implications for v1.2 narrative

If the hypothesis is confirmed, the v1.2 README narrative becomes much sharper:

> **What we found:** Original extraction logic counts every dated row in a multi-column bank statement as a transaction, even when the row is a payroll deposit, opening/ending balance, or transfer. Amounts stay correct (because only the Withdrawals column is read), but row counts and threshold-flag counts are inflated. This is independent of which AI model is used for downstream analysis.
>
> **What we fixed:** Made transaction classification explicit. Each extracted row is now tagged with a `transaction_type` (vendor_payment, payroll, balance, deposit, transfer, etc.) and an `include_for_1099` flag. Aggregation only sums rows where the flag is set. The workbook surfaces excluded rows for transparency.
>
> **What we deferred:** The PDF Skill refactor (mentor's longer-term recommendation) remains a v1.3 architectural improvement. We chose to validate the inclusion policy at the current architectural layer first, since that's where the active bug lives.

This is a stronger, more defensible engineering narrative than "we switched models." It demonstrates a diagnostic process: hypothesis → evidence → smallest fix.

It's also honest about the model question. Sonnet may still be a better default than Haiku on harder real-world PDFs (the mentor's reasoning), but on this corpus we don't have evidence either way. The README should say so.
