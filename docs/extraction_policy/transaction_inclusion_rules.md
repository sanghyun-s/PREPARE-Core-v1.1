# Transaction Inclusion Rules

**PREPARE Core — Bookkeeping reconciliation workspace with 1099 pre-review**
**Status:** Diagnostic policy specification (v1.2 development)
**Audience:** PREPARE engineering, mentor review, accountant collaborators

---

## Why this document exists

PREPARE Core processes bank and credit card statement PDFs and aggregates per-vendor totals. Aggregation only makes sense if every row in the source PDF has been classified as either *part of vendor-payment totals* or *not part of vendor-payment totals* before the aggregation step runs.

Until v1.2 this classification has been implicit. The pipeline extracts transactions, normalizes vendor names, sums amounts, and reports. It does not explicitly ask "is this row a vendor payment?" The result is that non-vendor-payment rows (deposits, balances, transfers, payroll) can leak into vendor totals if the extractor mis-identifies them.

Recent ground-truth comparison on `sample_bank_3col_clean.pdf` and `sample_bank_multicolumn.pdf` showed all three model engines (Haiku, Sonnet, Opus) overcounting transactions and over-flagging $600-threshold candidates. The most likely root cause is implicit classification, not model selection.

This policy defines explicit inclusion rules so:

- The pipeline can apply them deterministically
- Test samples can be classified row-by-row to produce ground truth
- Discrepancies between expected and actual extraction become diagnosable
- Accountants reviewing PREPARE output understand what's in scope and what isn't

---

## Scope of "vendor-payment transaction"

PREPARE's primary aggregation target is the **vendor-payment transaction**: a line item on a bank or credit card statement where the account holder paid an external party for goods or services rendered to the business.

The aggregation surface is designed for **1099 pre-review**, which means the question being answered is: *"How much was paid to each external vendor across all statements in this period?"*

Therefore, only rows that represent **outgoing payments to external vendors** should contribute to per-vendor totals.

---

## Inclusion rules

### Include

These row types **MUST be included** in vendor-payment aggregation:

| Type | Examples | Rationale |
|---|---|---|
| **Vendor payment** | "Adobe Systems Inc 599.88" | Payment to an external service provider for goods/services. Core 1099 candidate. |
| **Contractor payment** | "John Smith Consulting 800.00", "Mary Johnson Consulting 850.00" | Payment to an external contractor or freelancer. Core 1099 candidate. |
| **Card charge for expense** | "ADOBE SYSTEMS INC $599.88" (credit card) | Card-purchased goods/services paid to external vendor. Equivalent to vendor payment. |
| **Recurring subscription/utility** | "VERIZON WIRELESS AUTOPAY", "PG&E ELECTRIC AUTOPAY", "COMCAST BUSINESS" | Payment to utility or service vendor. Most are EXEMPT from 1099 (corporations) but still belong in bookkeeping totals. |
| **Travel/lodging** | "DELTA AIR LINES", "MARRIOTT HOTELS" | Vendor payment for travel-related services. Belongs in aggregation. |
| **Office supplies** | "STAPLES INC #0421", "OFFICE DEPOT CORP" | Vendor payment for supplies. Belongs in aggregation. |

### Exclude

These row types **MUST NOT contribute** to vendor-payment aggregation:

| Type | Examples | Rationale |
|---|---|---|
| **Opening / Ending balance** | "OPENING BALANCE 12,450.00", "ENDING BALANCE 33,811.67" | These are statement metadata, not transactions. They have no date-of-payment, no vendor, no amount-paid. |
| **Payroll direct deposit** | "PAYROLL DIRECT DEPOSIT 6,500.00", "PAYROLL DIRECT DEP 8,200.00" | Income deposit (money flowing IN), not an outgoing vendor payment. Does not create a 1099 obligation; the employer-employee relationship is on a W-2. |
| **Bank deposit (other)** | "DEPOSIT 1,500.00" (no vendor descriptor) | Generic incoming funds. Not a vendor payment. |
| **Transfer between own accounts** | "TRANSFER FROM SAVINGS 2,000.00" | Movement of funds between the account holder's own accounts. No external vendor. |
| **Bank fees** | "MONTHLY MAINTENANCE FEE 12.00", "OVERDRAFT FEE" | Paid to the bank, not an external vendor in the 1099 sense. *Could* be included in a broader bookkeeping view but should be **excluded from 1099 vendor aggregation** by default. |
| **Interest earned** | "INTEREST 0.42" | Income deposit, not vendor payment. |
| **Reversed/voided transactions** | "REVERSAL", paired matching debit and credit lines | Net-zero rows that should not double-count. Pipeline should detect and net out. |
| **Internal owner draws** | "OWNER DRAW", "DISTRIBUTION TO MEMBER" | Equity transactions, not vendor payments. Surfaces in bookkeeping but not in 1099 review. |
| **Statement metadata** | "Beginning Balance: $8,500.00", "Total New Charges $12,617.15" | Statement summary fields, not transaction rows. |

### Conditional / requires policy decision

These rows may or may not be included depending on stated policy. v1.2 default is conservative (exclude unless explicitly classified):

| Type | Examples | Default Treatment | Rationale |
|---|---|---|---|
| **Check payments** | "CHECK #1847 250.00" | **Include with note** | Payee unknown from statement description. Counts toward bookkeeping totals but flagged for human review on payee identity (cannot 1099-classify a numbered check without payee context). |
| **Generic Amazon marketplace** | "AMZN MKTP US*4H2LK9 87.43" | **Include with note** | Could be office supplies (deductible), personal (non-deductible), or mixed. Aggregate it as a vendor payment but flag for accountant judgment on category — exactly the kind of mixed-merchant signal PREPARE is designed to surface. |
| **Reimbursement to employee** | "REIMB JANE DOE $145.00" | **Exclude from 1099, include in bookkeeping** | Employee reimbursement is not vendor payment for 1099 purposes (employee is on W-2). Counts toward overall expense totals. |
| **Refund credit** | "REFUND ADOBE -50.00" | **Net against vendor's total** | Should reduce that vendor's total, not be aggregated separately. |

---

## Decision tree — how the pipeline should classify each row

```
┌─────────────────────────────────────────────────────────────────────┐
│  Row from PDF                                                       │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
    Has parseable date?                        No date / metadata
              │                                       │
              ▼                                       ▼
              │                              EXCLUDE — statement metadata
              │                              (balance lines, summaries)
              ▼
    Description contains balance keyword?
    ("BALANCE", "BEGINNING", "ENDING", "OPENING")
              │
   YES ───────┴─────── NO
    │                   │
    ▼                   ▼
EXCLUDE          Has amount in WITHDRAWAL/DEBIT column,
(balance row)    or single-amount column?
                       │
              YES ─────┴──────── NO
                │                 │
                ▼                 ▼
        Description contains      Amount only in DEPOSIT/CREDIT?
        deposit keyword?                  │
        ("DEPOSIT", "PAYROLL",   YES ─────┴──────── NO
        "TRANSFER FROM",          │                  │
        "INTEREST")               ▼                  ▼
                │                EXCLUDE     EXCLUDE — no parseable amount
        YES ────┴──── NO         (income/transfer)
        │             │
        ▼             ▼
    EXCLUDE      Description matches CHECK pattern
    (deposit/    ("CHECK #1234")?
    transfer)    │
                 ▼
            YES ─┴── NO
            │       │
            ▼       ▼
    INCLUDE     Description contains internal-account keyword?
    flag for    ("OWNER DRAW", "DISTRIBUTION", "REIMB")
    payee
    review      │
            YES ─┴── NO
            │       │
            ▼       ▼
        EXCLUDE   INCLUDE as vendor-payment transaction
        from 1099,
        flag for
        bookkeeping
```

This decision tree should run **after raw row extraction** and **before vendor normalization**. Each row gets tagged with:

- `transaction_type`: one of `vendor_payment | check | balance | deposit | payroll | transfer | fee | interest | metadata | reimbursement | owner_draw | unknown`
- `include_for_1099`: boolean
- `exclusion_reason`: string (empty if included)

Aggregation then sums only rows where `include_for_1099 == True`.

---

## Implementation note (deferred to v1.2.x or v1.3)

This document defines the **policy**, not the implementation. The actual changes to encode this policy in code would touch:

- `backend/pdf_extractor.py` — row-level classification logic (or pass row context forward to a classifier stage)
- `backend/transaction_classifier.py` — **new module** implementing the decision tree above
- `schemas.py` — extend `Transaction` to include `transaction_type` and `include_for_1099` fields
- `backend/transaction_aggregator.py` — filter on `include_for_1099` before summing
- `backend/master_excel_generator.py` — optional new sheet "Excluded Transactions" showing what was filtered out and why

That work is not part of this diagnostic pass. The current pass produces the policy and the ground-truth classification of the two debug samples, so we can compare against the current extractor and identify the smallest possible fix.

---

## Open questions worth surfacing now

These are decisions the policy doesn't yet make. They should be answered before implementation begins:

1. **Bank fees: bookkeeping-only or full exclude?** Current default: exclude from 1099 aggregation. But should they appear in a broader bookkeeping summary sheet?

2. **Reimbursements: how does the pipeline detect them?** Current default: exclude. But "REIMB JANE DOE" is a textual signal that requires recognizing employee names. May require a known-employee CSV in the same way vendor matching uses a known-vendor CSV.

3. **Cross-account transfers between businesses with the same owner.** A transfer to a related entity is technically not a vendor payment but is genuinely a payment that may have 1099 implications (related-party scrutiny). Defer to v1.3 roadmap.

4. **Statements that mix checking and savings on one PDF.** Out of scope for current samples but worth noting.

5. **Foreign currency transactions.** All current samples are USD. Multi-currency support is out of scope for v1.2.

---

## Summary

The current pipeline aggregates whatever the extractor returns, which means classification accuracy is implicit. The fix is to make classification explicit:

1. Every row gets a `transaction_type` label
2. Every row gets an `include_for_1099` flag
3. Aggregation sums only the included rows
4. The workbook can optionally surface excluded rows for transparency

This document is the policy. Next: classify the two debug samples row-by-row to produce ground truth, compare against actual extractor output, and recommend the smallest-possible fix.
