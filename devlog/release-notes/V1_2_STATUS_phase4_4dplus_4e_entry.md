## Phase 4 — devlog continuation (4D-plus + excluded-count fix + 4E)

> **Merge note.** This file continues `V1_2_STATUS_phase4_4c_4dcore_entry.md`.
> The sections below replace that file's "Phase 4 remaining (updated)" table's
> 4D-plus and 4E "Not started" rows. Paste these `###` sections in after the
> 4D-core section, then update the remaining table so 4D-plus = COMPLETE,
> 4E = COMPLETE, and Source B remains Planned (now with a concrete plan — see
> the final section here).

---

### Phase 4D-plus — Transactions sheet type-cell coloring + AutoFilter — COMPLETE (verified live)

**Status**: Closed May 25, 2026
**Files changed**: `backend/excel_generator.py` (per-statement generator —
Transactions sheet only; NO pipeline change)
**Cost**: ~$0.24 (≈2 PDF Skill verification runs)
**Spec**: `devlog/specs/V1_4_PHASE4D_DESIGN_SPEC.md` §5 (locked Option A + Option X)

**What landed.** Two isolated enhancements on the now-known-good Transactions
sheet. Generator-only — `all_transactions` already carries `transaction_type`,
so no pipeline plumbing was needed.

1. **Per-type cell coloring (Option A + Option 1).** A new, SEPARATE set of
   `PatternFill` constants (`TYPE_FILL_*`) and a `TYPE_CELL_FILLS` map color
   ONLY the "Transaction Type" cell (column = `len(TXN_BASE_HEADERS) + 1`),
   applied AFTER the row-fill block so it overrides only that one cell. The
   row's existing review (yellow) / exclusion (grey) fills are untouched on
   every other column — the type cell becomes a color key, the row fills stay
   the review/exclusion layer. No collision. Palette (user-locked from their
   bookkeeping instinct):
   - deposit → light blue `E3F2FD`
   - interest / reimbursement → light yellow `FEF9E7`
   - check_payment → soft red/rose `FCE4E4`
   - bank_fee → light amber `FFE0B2`
   - balance_line → orange-yellow `FCE0A8`
   - transfer / owner_draw → light purple `EDE9FE`
   - payroll_deposit / metadata / unknown → light grey `F0F4F8`
   - **vendor_payment → intentionally OMITTED from the map → no fill (white).**
     The common row stays quiet (Option 1, user-chosen), so the eye goes to the
     exception rows. For a flagged vendor_payment row (needs_review / excluded),
     the Type cell INHERITS the row's yellow/grey rather than punching a white
     hole (user-chosen "inherit").

2. **Interactive AutoFilter (Option X).** `ws.auto_filter.ref =
   f"A3:{last_col_letter}{last_data_row}"` adds native filter dropdowns to every
   Transactions column (header row 3 through last data row), with a guard for
   zero data rows. The user sorts/filters in Excel by date / vendor / type /
   amount / any column — live and reversible. `freeze_panes = "A4"` coexists
   cleanly.

**Isolation guarantee.** New constants, applied to one cell, in one file. ZERO
impact on the web UI palette (that lives in `frontend/index.html`, a different
file) or on any other sheet or the existing row-fill system. This was the whole
point of choosing the cell-only Option A.

**Verification.**
- py_compile clean. Generator round-trip in isolation: every type color lands on
  the correct Type cell at the correct hex, the row-fill priority stays intact
  (excluded rows grey on all non-Type columns, review rows yellow), AutoFilter
  range correct (`A3:L11`), freeze_panes preserved.
- PDF Skill Northgate run (live, screenshots): deposit Type cell blue, bank_fee
  Type cell amber, vendor payments white, check rose, filter arrows on all 12
  columns, excluded rows grey overall with only the Type cell carrying the
  category color. Confirmed working.

---

### Excluded-count fix — Processing Details parenthetical correctness — COMPLETE (verified on 3 real workbooks)

**Status**: Closed May 25, 2026
**Files changed**: `backend/excel_generator.py` (per-statement generator,
`write_summary_stats_sheet` + two new helpers)
**Cost**: $0 (caught and fixed against the user's three real PDF Skill test
workbooks; no new paid runs needed)

**What it caught.** The user's 3-file test set (harbor / northgate / summit)
exposed a real defect in the 4D-core Processing Details line that the
single-Harbor verification had hidden. The "N excluded (…)" parenthetical was
built from the full type `breakdown` dict, while the count `N` came from the
actual excluded rows — TWO different sources that did not agree:
- **Northgate** showed "2 excluded (1 check, 1 deposit, 1 bank fee)" — three
  items listed, count says 2, and the check is WRONGLY in the excluded list.
- **Summit** showed "5 excluded (2 checks, 2 deposits, 1 transfer, 2 bank
  fees)" — items sum to 7, count says 5.

The root issue is conceptual: a **check written to a payee is an INCLUDED,
potentially-1099-reportable payment** (`include_for_1099 = YES` on the
Transactions sheet), not an excluded row. The breakdown-dict source pulled it
into the excluded list anyway.

**The fix (user-confirmed direction).** Source BOTH the count and the
parenthetical from the SAME `include_for_1099 == False` rows:
- New `_excluded_phrase_from_counts(excluded_counts)` builds the parenthetical
  from a `{transaction_type: count}` dict of genuinely-excluded rows.
- New `_singularize_type_label` shared by the phrase builders.
- `write_summary_stats_sheet` now tallies `excluded_by_type` from the
  `include_for_1099 == False` rows as it counts them, and feeds that to the new
  phrase helper. Count and list are now consistent by construction, and INCLUDED
  types (checks) can never appear in the excluded list.
- The old `_excluded_breakdown_phrase(breakdown)` is left in place (now unused,
  harmless) rather than deleted.
- The "included as vendor payments for 1099 aggregation" wording was LEFT
  UNCHANGED so the Excel stays consistent with the web card (the card uses the
  same phrasing). Tightening that phrasing — now that the included set includes
  checks — is a separate, both-surfaces wording task, not done here.

**Verification (all three real workbooks).**
- Harbor: "2 excluded (1 deposit, 1 bank fee)" — unchanged (it had no checks, so
  was never affected). Confirms no regression on the already-correct case.
- Northgate: now "2 excluded (1 deposit, 1 bank fee)" — check gone, count matches
  the list.
- Summit: now "5 excluded (2 deposits, 1 transfer, 2 bank fees)" — checks gone,
  count matches.
- 4D-plus regression re-passed in the same build.

**Known follow-up (deferred, user-scheduled after 4E).** The WEB CARD has the
SAME bug independently — `frontend/index.html` `formatExcludedBreakdown` builds
its parenthetical from the breakdown dict too, so Northgate's card still shows
"2 excluded (1 checks, 1 deposits, 1 bank fees)" (checks wrongly listed, count
mismatch, "1 checks" bad plural). Same fix logic applies: source from
`include_for_1099 == NO` rows, exclude checks, singular/plural aware. Tracked as
a one-function frontend follow-up.

---

### Phase 4E — Cross-statement reconciliation roll-up (master workbook) — COMPLETE (verified live)

**Status**: Closed May 25, 2026
**Files changed**: `backend/master_excel_generator.py` ONLY (no server, schema,
or pipeline change)
**Cost**: ~$0.36 (one live 3-PDF PDF Skill run for verification)

**Reframing.** 4E was originally scoped as vague "validation display polish."
On inspection, 4C/4D already surface the per-statement verdict adequately, so
polishing there would have been gilding. Instead 4E became a genuinely-new,
higher-value feature the user selected (Option 3): a **cross-statement
reconciliation roll-up** — a multi-statement run previously had no single place
answering "did they all reconcile?"

**Data availability (the key finding).** `server.py` (~line 583) reads
`recon_dict = out.get("reconciliation_snapshot")` — so each `agent_output` dict
already carries the computed snapshot (with `status`). The master generator
receives that SAME `agent_outputs` list, so it computes the roll-up by iterating
and reading `o["reconciliation_snapshot"]["status"]`. **No server / schema /
pipeline change was needed — 4E is a single-file change to
`master_excel_generator.py`.**

**What landed.** A new **STATEMENT RECONCILIATION** block in the Executive
Summary sheet (`write_executive_summary`), placed AFTER Validation Overview and
BEFORE Top Vendors (user-chosen — groups the two cross-statement summaries above
the per-vendor detail):
- A roll-up tally is computed alongside the other KPIs: iterate SUCCESSFUL
  statements (the denominator), tally `status`: `balanced` → count,
  `needs_review` → count, (`unavailable` OR a `None` snapshot) → "unavailable"
  count. Both flavors of "couldn't reconcile" (explicit unavailable status, and
  the missing snapshot from rule_based / multi_agent / failed) fold together,
  since from the accountant's view both mean "no reconciliation result."
- Renders a glass-half-full header line "X of N statements reconcile"
  (user-chosen phrasing), then three rows (Balanced / Needs Review /
  Unavailable) with descriptions, mirroring the Validation Overview row style.
  The **Needs Review row is amber (`REVIEW_FILL`) when its count > 0**, matching
  how Validation Overview flags non-zero findings.

**Layout safety.** `write_executive_summary` uses hardcoded absolute rows for the
KPIs (4–7) and Validation Overview (9–13), but everything from Top Vendors down
flows from a running `row` cursor. The new block continues that cursor, so Top
Vendors and Run Metadata shift down automatically — no absolute-row renumbering
of the lower sections. Confirmed there are zero hardcoded rows below the insert
that could collide.

**Graceful cases.** All-rule_based run → "0 of N statements reconcile · 0
balanced · 0 needs review · N unavailable" (honest, no amber, not broken).
Single statement → "1 of 1 statements reconcile."

**Verification.**
- py_compile clean; `scripts/recalc.py` reports 0 errors / file integrity OK.
- Generator round-trip on a reconstructed 3-statement `agent_outputs` mirroring
  the real test files (Harbor needs_review + Northgate balanced + Summit
  balanced): block reads "2 of 3 statements reconcile", Balanced 2 / Needs Review
  1 [amber] / Unavailable 0, with Top Vendors + Run Metadata confirmed present
  and non-overlapping below.
- Edge cases: all-rule_based → "0 of 2 · Unavailable 2"; single → "1 of 1".
- **Live 3-PDF PDF Skill run** (`PREP-master_threePDF-post4E_5_25.xlsx`,
  inspected at the cell level): STATEMENT RECONCILIATION at rows 15–19, header
  "2 of 3 statements reconcile", Needs Review row amber, Top Vendors table intact
  at rows 21–32. Matches the spec exactly.

---

### Phase 4 remaining (updated)

| Sub-phase | Job | Files | Status |
|---|---|---|---|
| 4C | Per-Statement card reconciliation block | `frontend/index.html` | **COMPLETE** |
| 4D-core | Per-statement Excel restructure + reconciliation | `excel_generator.py`, `pipeline.py` | **COMPLETE** |
| 4D-plus | Transactions sheet: type-cell coloring + AutoFilter | `excel_generator.py` (Transactions only) | **COMPLETE** |
| Excluded-count fix | Processing Details parenthetical correctness | `excel_generator.py` (Summary Stats) | **COMPLETE** |
| 4E | Cross-statement reconciliation roll-up | `master_excel_generator.py` | **COMPLETE** |
| Card excluded-fix | Same fix on the web card | `frontend/index.html` (`formatExcludedBreakdown`) | Deferred (one-function follow-up) |
| 4E web tile (optional) | Roll-up on the web Workspace too | `server.py` + `schemas.py` + `frontend/index.html` | Optional / deferred |
| Source B | Row-sum cross-check vs stated summary | see plan below | **Planned — final dev step** |

---

## Source B — row-sum cross-check (the planned final development step)

**Recorded direction (from `V1_2_STATUS_phase4_entry.md`).**
- **Source A** (shipped in 4A–4E): transcribe the statement's stated
  account-summary totals → check the statement balances *internally*
  (beginning + deposits − withdrawals − checks − transfers − fees = ending).
- **Source B** (this plan): **sum the extracted transaction rows** and
  cross-check that sum against the stated summary → confirms *extraction
  completeness*. This is the stronger bookkeeping-trust signal: it answers "did
  we capture every transaction?", not just "does the bank's own math add up?"

**Why it's sequenced last.** Source B depends on extraction completeness, which
is a known soft spot (PDF Skill edge-row classification variation). It only earns
trust once Source A is proven — which it now is. The Harbor run already showed
the two numbers agree, an encouraging early sign, but that needs to hold across
the corpus before Source B's verdict can be trusted.

**What Source B actually checks (the arithmetic).** For each statement, two
independent sums should agree:
1. **Stated activity** (from the account-summary, already transcribed in the
   `reconciliation_snapshot`): `total_deposits`, `total_withdrawals`, `checks`,
   `transfers`, `fees`.
2. **Row-derived activity** (summed from the extracted transaction rows, grouped
   by direction/type): deposits summed from `deposit`/`interest`/`reimbursement`
   rows; withdrawals from `vendor_payment` rows; checks from `check_payment`;
   transfers from `transfer`/`owner_draw`; fees from `bank_fee`.

If the row-derived sums match the stated sums (within the existing
`RECONCILIATION_TOLERANCE = 0.01`), extraction is complete. If they diverge, some
rows were missed or misclassified — a concrete, actionable "extraction
incomplete" flag.

**Proposed implementation (spec-before-code; this is a sketch, not yet locked).**
1. **Compute location — one place, like Source A.** Add a `_compute_source_b()`
   helper alongside `_compute_reconciliation` in `pipeline.py` (NOT in the
   generators — same single-source discipline that kept the Source A waterfall
   identical across card and Excel). It takes the extracted rows + the stated
   snapshot, returns a small dict: per-bucket `stated` vs `row_sum` vs `delta`,
   plus an overall `extraction_complete: bool` and a status
   (`complete` / `incomplete` / `unavailable`).
2. **Schema.** Extend `ReconciliationSnapshot` (or add a sibling
   `ExtractionCheck` model) in `schemas.py` with the Source B fields, optional /
   None for engines that can't produce it (rule_based has no row classification,
   so Source B is `unavailable` there — same graceful pattern as Source A).
3. **Surfaces (mirror Source A's rollout order).**
   - Per-statement card (`frontend/index.html`): a compact line under the
     existing reconciliation waterfall, e.g. "Extraction check: rows sum to
     stated activity ✓" or "⚠ rows under stated deposits by $X — possible missed
     transaction."
   - Per-statement Excel (`excel_generator.py`): a short Source B sub-block under
     the Statement Reconciliation waterfall on Summary Stats.
   - Master workbook (`master_excel_generator.py`): optionally fold an
     "extraction-complete" count into the 4E STATEMENT RECONCILIATION roll-up, or
     add a sibling roll-up line.
4. **Spike first (recommended, like 4A).** Before building, run the existing
   three test PDFs through a throwaway script that computes both sums and prints
   the deltas. If they agree across all three (Harbor already does), Source B is
   GO. If they diverge, that divergence IS the signal Source B exists to catch —
   and the spike tells us whether the divergence is real missed rows vs. a
   summary-line definitional mismatch (e.g. the Northgate/Summit note that
   `total_withdrawals` excludes checks/fees — Source B's bucketing must respect
   that same definition, or it will false-positive).

**The one real risk to watch.** The Northgate and Summit reconciliation notes
already flag that statements define `total_withdrawals` to EXCLUDE checks/fees
(those are separate summary lines). Source B's row-bucketing must use the SAME
definitions, or it will report spurious "incomplete" deltas. The spike in step 4
is specifically there to catch this before any production code is written.

**Scope guard.** Source B stays within the locked PREPARE directive — it is a
statement-integrity / extraction-completeness check (bookkeeping), not 1099
filing logic. It is the natural close of Phase 4 and, per the user, the final
planned development step for this app before any rebrand / reengineering pass.
