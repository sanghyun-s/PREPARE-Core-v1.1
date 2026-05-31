# Test Sample Taxonomy

**PREPARE Core — Diagnostic test corpus**
**Status:** v1.2 diagnostic documentation
**Purpose:** Classify each test sample by what layout risk it exercises, so future regression tests target specific extraction failure modes.

---

## Why this taxonomy exists

The diagnostic comparison on `sample_bank_3col_clean.pdf` and `sample_bank_multicolumn.pdf` showed that the current pipeline overcounts transactions across all model engines (Haiku +8, Sonnet +10, Opus +6 vs ground truth of 68). A reasonable hypothesis is that **layout complexity** drives the overcount more than model selection.

To validate this, samples should be classified by layout risk rather than treated as interchangeable. This document does that classification for all six PDFs currently in the test corpus.

The two highest-priority samples (`sample_bank_3col_clean.pdf` and `sample_bank_multicolumn.pdf`) get full row-by-row analysis in `debug_3col_clean.csv` and `debug_multicolumn.csv`. The other four get the lighter taxonomy treatment in this document; they can be promoted to full row-level analysis later if specific bugs are identified.

---

## Sample taxonomy

### 1. sample_bank_3col_clean.pdf

**Layout:** 3-column simple — Date / Description / Amount
**Period:** 6 months (Jan–Jun 2024)
**Account holder:** Acme Consulting Group LLC (First National Business Bank)
**Raw data lines:** 37
**Expected vendor-payment rows:** 37
**Expected excluded rows:** 0

**Layout risks tested:**
- ☐ Multi-column amount disambiguation
- ☐ Balance line filtering
- ☐ Deposit filtering
- ☐ Payroll filtering
- ☐ Transfer filtering
- ☐ Statement metadata filtering
- ☑ **Vendor name normalization across variants** (Adobe Systems Inc / Adobe; Mary Johnson Consulting / Mary Johnson Consulting LLC; Robert Kim LLC; John Smith Consulting / John Smith LLC)
- ☑ **Store-number stripping** (Home Depot #6547, Home Depot #8832, Staples Inc #0421, Staples.com)

**What this sample is good for:**
This is the **baseline sanity case**. Every row is a vendor payment. There are no deposits, no balances, no transfers, no payroll. If the extractor fails to produce 37 transactions on this PDF, the bug is upstream of any classification logic — it's in pure row parsing. This sample isolates extraction-mechanics correctness from policy correctness.

**What this sample does NOT test:**
- Anything involving column semantics
- Anything involving statement structure (headers, balances, summaries)
- Anything involving deposit/payment disambiguation

**Status:** Full row-level ground truth produced in `debug_3col_clean.csv`.

---

### 2. sample_bank_multicolumn.pdf

**Layout:** Multi-column bank — Date / Description / Withdrawals / Deposits / Balance
**Period:** 6 months (Jan–Jun 2024)
**Account holder:** Brightside Marketing LLC (Meridian Bank)
**Raw data lines:** 39
**Expected vendor-payment rows:** 31
**Expected excluded rows:** 8 (6 payroll deposits + 2 balance lines: opening, ending)

**Layout risks tested:**
- ☑ **3-column amount disambiguation** (which of withdrawal/deposit/balance is the transaction amount?)
- ☑ **Balance line filtering** (OPENING BALANCE, ENDING BALANCE)
- ☑ **Deposit filtering** (PAYROLL DIRECT DEPOSIT × 6)
- ☑ **Payroll filtering** (specifically — the rows have "PAYROLL DIRECT DEPOSIT" descriptor)
- ☐ Transfer filtering
- ☑ **Vendor name normalization across variants** (J SMITH LLC vs JOHN SMITH LLC; MARY JOHNSON CONSULTING vs MARY JOHNSON CONSULTING LLC; ATT UVERSE vs telephone bills)

**What this sample is good for:**
This is the **classification policy stress test**. Eight of 39 rows MUST be excluded for the workflow to be correct. A pipeline that ignores classification and treats every dated row as a transaction will overcount by ~8 (specifically: 6 payroll deposits at $6,500 each = $39,000 of false-positive vendor "payments," plus 2 balance amounts at ~$8,500 and ~$33,811 that would massively distort totals if treated as vendor payments).

The extractor's behavior on this PDF is **the single most diagnostic data point** for the overcounting hypothesis.

**Status:** Full row-level ground truth produced in `debug_multicolumn.csv`.

---

### 3. sample_credit_card_chase.pdf

**Layout:** Credit card statement — Date / Description / Category / Amount
**Period:** 5 months (Jan–May 2024)
**Card holder:** (not specified in PDF; account ending in 7741)
**Raw data lines:** 28 transaction rows + 1 metadata summary row ("Total New Charges")
**Expected vendor-payment rows:** 28
**Expected excluded rows:** 0 transaction rows; 1 summary line that should not be parsed as a transaction

**Layout risks tested:**
- ☐ Multi-column amount disambiguation (single amount column)
- ☐ Balance line filtering (no balance column on credit card statements)
- ☐ Deposit filtering (no deposits on credit card statements)
- ☐ Payroll filtering
- ☐ Transfer filtering
- ☑ **Statement metadata filtering** ("Total New Charges $12,617.15" looks like a transaction row but is a summary)
- ☑ **Category column handling** (credit card statements have an extra Category column the extractor must skip)

**What this sample is good for:**
This sample tests the **category-column** layout that's specific to credit card statements. The Chase format has Date / Description / **Category** / Amount, which is a 4-column layout where column 3 (Category) is text-only. If the extractor is column-position-naive, it could misalign and read Category text as Amount or vice versa.

It also tests a metadata footer (the "Total New Charges" line). A naive extractor could pick this up as a transaction with description "Total New Charges" and amount "$12,617.15."

**What this sample does NOT test:**
- Balance/deposit/payroll filtering (none of those exist in credit card statements)
- Multi-amount-column disambiguation
- Vendor name variation (this sample has clean, distinct vendor names)

**Status:** Lighter treatment only. Promote to full row-level analysis if extractor returns 29+ transactions or if "Total New Charges" appears in vendor list.

---

### 4. sample_wells_fargo_style.pdf

**Layout:** Multi-column bank with debit/credit — Date / Description / Debit / Credit / Balance
**Period:** 6 months (Apr–Sep 2024)
**Account holder:** Northgate Engineering LLP (Pacific Ridge Bank)
**Raw data lines:** 39 (33 debits + 6 payroll credits)
**Expected vendor-payment rows:** 33
**Expected excluded rows:** 6 (payroll deposits)

**Layout risks tested:**
- ☑ **3-column amount disambiguation** (Debit / Credit / Balance — slightly different terminology than Withdrawal/Deposit/Balance)
- ☐ Balance line filtering (this sample doesn't have explicit Opening/Ending Balance rows in its data)
- ☑ **Deposit filtering** (6 payroll direct deposits in the Credit column)
- ☑ **Payroll filtering** (specifically "PAYROLL DIRECT DEP" rows)
- ☐ Transfer filtering
- ☑ **Vendor name normalization across variants** (DAVID LEE CONTRACTOR / DAVID LEE CONSULTING — same person? Mary Johnson / MARY JOHNSON CONSULTING)

**What this sample is good for:**
This sample is essentially a **variation on `sample_bank_multicolumn.pdf`** with different column header naming (Debit/Credit instead of Withdrawals/Deposits) and a slightly different layout. If the extractor is hardcoded against "WITHDRAWALS"/"DEPOSITS" header keywords, it could fail to identify the correct column on this sample.

It's also useful for testing whether the **vendor variant detection** logic recognizes "DAVID LEE CONTRACTOR" (multiple times) as the same canonical vendor as "DAVID LEE CONSULTING" (one time, August). A naive matcher might split them; a thoughtful matcher should flag this as a high-similarity name variant.

**What this sample does NOT test:**
- Balance line filtering (this PDF doesn't include Opening/Ending Balance rows in the line items)
- Transfer filtering
- Statement metadata footers

**Status:** Lighter treatment only. Promote if extractor produces unexpected row count or fails to flag DAVID LEE name variant.

---

### 5. boa_business_checking_2024.pdf

**Layout:** Multi-column bank — Date / Description / Withdrawals / Deposits / Balance
**Period:** 9 months (Jan–Sep 2024) — longest period in corpus
**Account holder:** Rowshan & Co. LLC (Bank of America)
**Raw data lines:** 51
**Expected vendor-payment rows:** ~38–39 (depending on check policy)
**Expected excluded rows:** ~12–13 (9 payroll + 1 transfer + 2 balance + possibly 1 check)

**Layout risks tested:**
- ☑ **3-column amount disambiguation**
- ☑ **Balance line filtering** (opening + ending)
- ☑ **Deposit filtering** (9 payroll deposits)
- ☑ **Payroll filtering**
- ☑ **Transfer filtering** (1 "TRANSFER FROM SAVINGS" row)
- ☑ **Check handling policy** (1 "CHECK #1847" row — payee unknown)
- ☑ **Multi-page handling** (this is a 2-page PDF; row continuity must be preserved)
- ☑ **Long-period vendor accumulation** (9 months means more cross-month vendor occurrences, more name-variant opportunities)
- ☑ **Vendor name variant** (J SMITH LLC, JOHN SMITH LLC, JOHN SMITH CONSULTING, MARY JOHNSON, MARY JOHNSON CONSULTING, MARY JOHNSON CONSULTING LLC, DAVID LEE CONTRACTOR, DAVID LEE CONSULTING)

**What this sample is good for:**
This is the **most realistic real-world sample** in the corpus. It has every layout complication: balance lines, deposits, transfers, checks, multi-page, long period, and many vendor name variants. If `sample_bank_multicolumn.pdf` is the focused stress test, this is the integration test.

The "TRANSFER FROM SAVINGS 2,000.00 → balance 20,056.15" row is particularly important. It's a Deposit-column entry with an internal-account descriptor. The current pipeline's behavior on this row is unknown without testing.

The "CHECK #1847 250.00" row is the policy edge case. Current default per `transaction_inclusion_rules.md`: include with a "payee unknown" review flag, since the row represents a real outgoing payment but the recipient cannot be 1099-classified from the description alone.

**What this sample does NOT test:**
- Credit card statement layouts (covered by Chase samples)
- Single-amount-column layouts (covered by sample_bank_3col_clean.pdf)

**Status:** Lighter treatment. Strongly recommended for promotion to full row-level analysis as a v1.2.x or v1.3 work item — it would expose any classification policy gap.

---

### 6. chase_sapphire_business_2024.pdf

**Layout:** Credit card statement — Date / Description / Category / Amount
**Period:** 5 months (Jan–May 2024)
**Card holder:** (not specified; account ending in 7741)
**Raw data lines:** 38 transaction rows + summary metadata
**Expected vendor-payment rows:** 38
**Expected excluded rows:** 0 transaction rows; 1 metadata footer ("Total New Charges $12,617.15")

**Layout risks tested:**
- ☐ Multi-column amount disambiguation (single amount column)
- ☐ Balance line filtering
- ☐ Deposit filtering
- ☐ Payroll filtering
- ☐ Transfer filtering
- ☑ **Statement metadata filtering** (Total New Charges footer)
- ☑ **Category column handling** (4-column with Category as 3rd column)
- ☑ **Multi-page handling**

**What this sample is good for:**
This is essentially a **superset of `sample_credit_card_chase.pdf`** with more transactions and a longer period. Same risks. Useful for confirming the credit-card layout handler is consistent across volume.

The extra value over the smaller Chase sample: more vendor-category diversity (Software / Travel / Meals / Shipping all well-represented), so it tests whether the Category column is being properly skipped vs. potentially being read as part of the description.

**Status:** Lighter treatment. Useful as a regression test once credit card extraction is verified working on the smaller Chase sample.

---

## Layout-risk coverage matrix

| Risk | sample_bank_3col_clean | sample_bank_multicolumn | sample_credit_card_chase | sample_wells_fargo_style | boa_business_checking_2024 | chase_sapphire_business_2024 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Multi-column amount disambiguation | | ✓ | | ✓ | ✓ | |
| Balance line filtering | | ✓ | | | ✓ | |
| Deposit filtering | | ✓ | | ✓ | ✓ | |
| Payroll filtering | | ✓ | | ✓ | ✓ | |
| Transfer filtering | | | | | ✓ | |
| Statement metadata filtering | | | ✓ | | | ✓ |
| Category column (4-col layout) | | | ✓ | | | ✓ |
| Check payments | | | | | ✓ | |
| Multi-page | | | (1pg) | | ✓ | ✓ |
| Long period (≥9 months) | | | | | ✓ | |
| Vendor name variants | ✓ | ✓ | | ✓ | ✓ | |

---

## Recommended testing priority

If only one sample can be debugged at a time (current capacity), the priority order is:

1. **`sample_bank_multicolumn.pdf`** — focused stress test for classification policy. Highest diagnostic value per row.
2. **`sample_bank_3col_clean.pdf`** — baseline sanity check. Should produce 37 vendor-payment transactions exactly. If not, extraction mechanics are broken upstream of policy.
3. **`boa_business_checking_2024.pdf`** — integration test. Hits every risk type.
4. **`sample_wells_fargo_style.pdf`** — Debit/Credit terminology variation.
5. **`sample_credit_card_chase.pdf`** — credit card layout primary test.
6. **`chase_sapphire_business_2024.pdf`** — credit card volume regression.

Samples 1 and 2 are covered by full row-level analysis in this diagnostic pass. Samples 3–6 are awaiting promotion based on findings.
