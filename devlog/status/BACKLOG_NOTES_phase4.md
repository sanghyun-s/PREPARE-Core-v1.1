# PREPARE Core — Backlog Notes (recorded during Phase 4 verification)

Two pre-existing issues surfaced while verifying Phase 4C/4D-core. **Neither is a
Phase 4 bug** — Phase 4 only made them more *visible* by putting more numbers on
screen and in the deliverable. Recording here so they aren't re-investigated from
scratch later.

---

## Backlog 1 — Rule-based engine has no classifier; its totals differ from PDF Skill / ground truth

**Severity**: Low (by design) · **Area**: rule-based engine path · **Type**: known limitation, not a defect

**Observation.** On `harbor_national_unbalanced.pdf`:

| Field | Rule-based | PDF Skill | Ground truth |
|---|---|---|---|
| Parsed rows | 8 | 9 | 9 |
| Included | 8 | 7 | 7 |
| Excluded | 0 | 2 (1 deposit, 1 fee) | 2 |
| Included Total | $10,820 | $4,820 | $4,820 |

**Cause.** The rule-based engine has no row classifier. It treats every extracted
row as an included vendor payment, so it cannot separate the $6,000 incoming
client deposit and the $30 bank fee from genuine vendor payments. It therefore
over-counts both the included row count and the included total. PDF Skill
classifies correctly (excludes the deposit + fee) and matches the ground-truth
key ($4,820, 7 vendor payments).

**Why this is by design.** Rule-based is the fast/free fallback; classification
accuracy is exactly the capability PDF Skill exists to provide, and why PDF Skill
is the recommended engine. The reconciliation waterfall reinforces this: it only
appears on PDF Skill runs, because rule-based never extracts the balance summary
("Reconciliation not available").

**Why it's more visible now.** Phase 4D-core puts Included Total / counts on the
Summary Stats landing sheet (the first thing opened), so the rule-based
over-count is now front-and-center rather than buried.

**Possible future action (if ever desired).** Either (a) add a light
classification heuristic to the rule-based path (scope creep — probably not worth
it), or (b) add a one-line caption on rule-based output noting "rule-based engine
does not classify rows; totals may include non-vendor activity — use PDF Skill
for 1099-accurate figures." Option (b) is cheap and honest if the over-count ever
confuses a user. No action needed now.

---

## Backlog 2 — KPI vs Excel: "Review Needed" / "Included Payments" counts disagree across surfaces

**Severity**: Low–Medium · **Area**: counting paths (frontend KPI vs Excel/master generators) · **Type**: consistency defect

**Observation (two instances of the same root).**
- **Review Needed (rule-based Harbor):** web card showed `Review Needed 6`; the
  per-statement Excel Summary Stats showed `Review Needed 0`. (PDF Skill run: both
  showed 5 — they agree when classification runs.)
- **Included Payments (earlier 3-PDF rule-based run):** Workspace KPI and per-card
  tiles showed 27; the master Excel Executive Summary showed 32.

**Cause (suspected).** The web/card surfaces and the Excel/master generators
compute review-needed and included counts from different sources:
- The Excel Summary Stats counts `needs_review` from `summaries[].needs_review`.
- The card's number comes from a different field / a different point in the
  pipeline (likely a pre-aggregation or review-flag-engine count).
The two diverge specifically on the **rule-based** path; PDF Skill appears to
agree across surfaces (Harbor PDF Skill: both 5).

**Why it's not a Phase 4 bug.** Both discrepancies pre-date Phase 4 (the
Included-Payments 27-vs-32 was noted back in 4C). 4D-core surfaces the
review-needed number on the landing sheet, making one instance newly visible, but
did not change any counting logic.

**Suggested investigation (later session).**
1. Pick the single rule-based run and trace `needs_review` from `review_flag_engine`
   → `summaries` → response dict (`vendors_needing_review`?) → frontend KPI vs the
   value `write_summary_stats_sheet` counts.
2. Likewise trace Included Payments: card/KPI source vs
   `master_excel_generator` Executive Summary count.
3. Decide one canonical source per metric and make all surfaces read it.

Low priority while the recommended (PDF Skill) path is consistent; worth fixing
before the rule-based path is ever positioned as more than a fallback.

---

*Recorded: May 24, 2026, end of the Phase 4C / 4D-core session.*
