# Phase 4A — Test Statement Ground Truth (Answer Key)

Three synthetic bank statements for testing reconciliation-field extraction.
Each balances by construction (or is intentionally off, for statement 3).
Use these known values to score what the PDF Skill extracts in Phase 4A.

Balance equation: `ending = beginning + deposits − withdrawals − checks − transfers − fees`

---

## 1. northgate_bank_clean.pdf — BALANCED (baseline)
Clean operating account, no transfer. Every withdrawal is a vendor payment.

| Field | Value |
|---|---|
| beginning_balance | $12,000.00 |
| total_deposits | $8,500.00 |
| total_withdrawals | $5,747.50 |
| checks | $1,200.00 |
| transfers | $0.00 |
| fees | $35.00 |
| ending_balance (stated) | $13,517.50 |
| calculated_ending | $13,517.50 |
| difference | $0.00 |
| **status** | **balanced** |

7 vendor payments. 1 deposit. 1 check. 1 fee.

## 2. summit_cu_transfer.pdf — BALANCED (tests transfer field)
Includes a $3,000 transfer to savings and two checks. Tests whether
extraction separates transfers from vendor withdrawals.

| Field | Value |
|---|---|
| beginning_balance | $5,500.00 |
| total_deposits | $15,200.00 |
| total_withdrawals | $6,875.25 |
| checks | $2,150.00 |
| transfers | $3,000.00 |
| fees | $50.00 |
| ending_balance (stated) | $8,624.75 |
| calculated_ending | $8,624.75 |
| difference | $0.00 |
| **status** | **balanced** |

6 vendor payments. 2 deposits. 2 checks. 1 transfer. 2 fees.

## 3. harbor_national_unbalanced.pdf — NEEDS REVIEW (tests discrepancy detection)
**Intentionally unbalanced.** The stated ending balance is $150 LESS than
the calculated ending. This is the critical test: the reconciliation logic
must detect this and flag `needs_review` rather than showing a false "balanced."

| Field | Value |
|---|---|
| beginning_balance | $3,000.00 |
| total_deposits | $6,000.00 |
| total_withdrawals | $4,820.00 |
| checks | $0.00 |
| transfers | $0.00 |
| fees | $30.00 |
| calculated_ending | $4,150.00 |
| ending_balance (stated) | $4,000.00 |
| **difference** | **$150.00** |
| **status** | **needs_review** |

7 vendor payments. 1 deposit. 1 fee. No checks, no transfers.

---

## What Phase 4A should prove
For each PDF, the PDF Skill extraction should return the six input fields
(beginning, deposits, withdrawals, checks, transfers, fees) and the stated
ending. The app then COMPUTES calculated_ending + difference + status.

**Success = extracted input fields match this key within $0.01, across
repeated runs (stability), on all three statements.**

If statement 3's difference comes back as $150 and status `needs_review`,
the discrepancy-detection path works. If statements 1 & 2 come back balanced
with diff $0.00, the happy path works.
