# V1_2_STATUS.md — PREPARE Status Log

This file tracks completed phases of PREPARE v1.2 work and forward-looking notes for v1.3 / v1.4. It is the running technical journal that complements `V1_3_PLAN.md` (the integration plan) and `V1_3_PHASE3_PLAN.md` (the polish + redesign roadmap).

---

## Phase 2B — Future bookkeeping KPI enhancement

*(Documented for direction-setting; not implemented in v1.2.)*

Phase 2A (shipped in v1.2) reorganizes the Per-Statement Review section presentation-only. Helper text, three-group expanded card layout, and per-statement Excel wording all clarify that this layer is statement-level bookkeeping visibility, distinct from Consolidated Validation's cross-file reconciliation.

Phase 2B is a planned enhancement contingent on a more reliable extraction layer (specifically, the v1.3 Claude PDF Skill prototype producing trustworthy `transaction_type` and `include_for_1099` fields per row). Once that extraction layer is validated, the Per-Statement Bookkeeping Summary group can show real cash-flow breakdowns:

- Outgoing Payments (withdrawals, ACH, wire, card charges representing expenses)
- Deposits / Credits (incoming non-payroll deposits)
- Checks (with payee verification status)
- Bank Fees
- Transfers (internal account-to-account)
- Excluded / Non-1099 Rows (count + reason)
- Review-Required Rows (rows the extractor flagged for human verification)

These KPIs are the path toward Per-Statement Review functioning as a true bookkeeping aid rather than a presentation reskin. They are explicitly **not implemented in v1.2** because:

1. The current pdfplumber + regex extractor does not reliably produce `transaction_type` for every row across diverse PDF layouts. Synthesizing these counts from current data would require backend changes that compete with the v1.3 PDF Skill direction.
2. Fabricating these metrics from incomplete data would mislead users about what the app actually knows.

The honest path is: ship Phase 2A as a presentation update now, validate the PDF Skill prototype, then build Phase 2B on top of a trustworthy extraction layer. Until then, the Per-Statement expanded card carries a small italic note signaling this direction without overpromising.

---

## Phase 1 — Master Workbook contract fix (v1.3) — COMPLETED

Phase 1 fixed three pre-existing bugs in the Master Workbook generation that surfaced once PDF Skill engine produced richer per-row data. None of these required touching `master_excel_generator.py` itself — all fixes landed in `pipeline.py` and `server.py` (the standing v1.3 constraint on master_excel_generator.py was respected).

The three bugs killed:

1. **Total Transactions count showed 0** — `pipeline.run_pipeline_pdf_skill()` was returning `transaction_count` as a scalar but not surfacing the underlying transactions list to the master. Fixed by serializing transactions as list-of-dicts so the master workbook generator's `for t in out.get("transactions", [])` loop could read row-level data including `excluded` flags.

2. **Per-Agent Summary Cost ($) always showed $0.00** — the cost field wasn't being passed through from the agent result to the agent_output dict. Fixed by adding the `cost_usd` field to the per-statement output dict.

3. **All Transactions sheet showed only included rows** — the master generator received only the post-filter `included_txn_dicts`. Fixed by passing the full `all_txn_dicts` list (which contains both included and excluded rows with their `excluded` flags) through to the master.

Verification: single-PDF and 2-PDF test runs on both PDF Skill and rule-based engines confirmed all three sheets now show correct data.

---

## Phase 2 — Frontend label rework (v1.3) — COMPLETED

Phase 2 reframed the Per-Statement Review UI from "transaction extraction" terminology to "bookkeeping review" terminology. Seven changes in `frontend/index.html` (2918 → 2919 lines after Phase 2):

1. **Workspace KPI #1** label: "Total Transactions" → "Included Payments". Sublabel: "Across all statements" → "Vendor payments in 1099 aggregation".
2. **Per-Statement view subtitle** rewritten with bookkeeping framing: "One card per statement. Reviews how each PDF was parsed — included vendor payments, excluded rows (payroll, balance, etc.), and statement-level signals — before cross-statement reconciliation."
3. **Per-Statement card metric #1** label: "Transactions" → "Included Payments".
4. **New activity line** below the 5 metrics (PDF Skill engine only): `39 parsed · 31 included · 8 excluded`. Hidden for rule-based since rule-based doesn't produce row-level classification.
5. **Group A status detail** (PDF Skill only): real per-row counts and excluded-types breakdown, e.g., "39 rows identified. 31 included as vendor payments for 1099 aggregation. 8 excluded (6 payroll deposits, 2 balance lines)."
6. **Group A breakdown table title**: "Excluded Transaction Breakdown" → "Statement Activity Breakdown" (more accurate — the table includes vendor_payment rows too, not just excluded types).
7. **Group A helper note** rewritten to describe row-level classification approach when PDF Skill data is present, with separate fallback text when absent.

Verification:
- Rule-based engine: 1 PDF + 2 PDF tests, all 7 changes verified, fallback paths intact (no activity line, generic status messages)
- PDF Skill engine: 1 PDF + 2 PDF tests, all 7 changes verified, real numbers in activity line and status detail
- Cross-statement validation findings populate correctly when 2+ statements are present
- Master Workbook downloads still work; structure unchanged

Total Phase 2 API cost: ~$1.50 across test runs.

---

## Phase 3a — Master Workbook + Validation engine polish (v1.3) — COMPLETED

Phase 3a lifted the standing constraints on `master_excel_generator.py` and `validation_engine.py` to finish accountant-readability polish that was deferred from Phase 1. Five code changes plus this status update.

### Changes shipped

**A1 — Master Excel KPI label rename** (`master_excel_generator.py`)
Executive Summary KPI #1 renamed "Total Transactions" → "Included Payments" with sublabel "Vendor payments in 1099 aggregation". Matches the WebUI Workspace KPI label set in Phase 2.

**A2 — Validation Report column widths** (`master_excel_generator.py`)
Column E widened 20 → 60 to fit the "Per-Statement Breakdown" cell content (filename + currency pairs, ~75-90 chars per cross-match row). Adaptive row heights added for breakdown cells over 55 chars (30pt) and over 110 chars (45pt).

**A3 — UUID filenames → original filenames in Review Reasons** (`master_excel_generator.py`)
Master Vendor Summary "Review Reasons" column now substitutes internal UUID-based filenames (e.g., `29f0b14961914821ae5624686f0321a0.pdf`) with original filenames (e.g., `sample_bank_multicolumn.pdf`) via regex post-processing. Added `_make_text_resolver()` helper that wraps the existing `filename_map` lookup with a UUID-pattern regex substitution.

**A5 — Included Payments count filter** (`master_excel_generator.py`)
Executive Summary "Included Payments" KPI value now filters out rows tagged `excluded=True` so the value matches the WebUI Workspace KPI of the same name. Filter operates on per-transaction `excluded` flags set by the PDF Skill engine path.

Asymmetry note: rule-based engine path (via `agent_app.py.run_engine()`) does not set the `excluded` flag on its transaction dicts. Rule-based runs continue to show the unfiltered count in the master Executive Summary (76 in the 2-PDF test instead of 68). The PDF Skill engine — the v1.3 production default — works correctly. Extending the fix to rule-based requires touching `agent_app.py` and is queued as a v1.4 backlog item (see below).

**B1 — Symmetric name variant dedup** (`validation_engine.py`)
Added `_dedup_name_variants()` helper that collapses A↔B and B↔A entries of the same name pair into one canonical entry per unique pair (alphabetically-ordered by name_a, case-insensitive). On the 2-PDF test corpus, this reduces 5 entries to 3 conceptually-distinct findings. ReviewFlags propagation is unaffected — dedup operates at the consumer-facing list only.

### Verification approach

- All five code changes verified with `py_compile` and static structure checks
- Functional regression on the 2-PDF rule-based test (free, ~10 sec) confirmed A1, A2, A3, B1 all visible end-to-end
- One PDF Skill test ($0.60) confirmed A5 works on the production engine
- Per-Statement card expanded view still renders correctly after B1 dedup (frontend `isA` logic handles the per-statement perspective)
- All other Master Workbook sheets unchanged in structure

### Total Phase 3a API cost

~$0.60 across one PDF Skill regression run + multiple free rule-based runs. Cumulative v1.3 development (all phases): ~$3.75.

### What's NOT in Phase 3a (deferred to v1.4)

| Item | Why deferred |
|---|---|
| Extend A5 `excluded` flag tagging to rule-based engine via `agent_app.py` | Requires investigation of legacy engine plumbing shared with multi_agent — outside Phase 3a polish scope |
| B2 (review-needed counting basis: per-instance vs deduped) | Philosophical, needs operator decision possibly informed by user testing |
| D1 (per-PDF try/except in server.py) | Not demo-critical; revisit when real-world batches show fragility |
| D3 (automatic engine fallback) | Operator decision + cost-control complexity |
| E1 (test corpus expansion 2 → ~10 PDFs) | v1.4 scope, out of Phase 3 character |

---

## Phase 3b — Bookkeeping-first card redesign (v1.3) — COMPLETED

**Status**: Closed May 20, 2026
**Sessions**: 3 (C1 → C1b → C2)
**Files changed**: `frontend/index.html` only
**Cost**: ~$1.20 in PDF Skill verification runs across the three sessions

## What shipped

The Per-Statement card was restructured from a vendor-review layout to a
bookkeeping-first layout. The headline now answers the bookkeeping question
("how did this PDF parse — what was included, what was excluded, why")
before the 1099 question ("how many vendors crossed the threshold, how
much in total, how confident").

Card body, top to bottom:

1. **3-tile headline**: Parsed / Included / Excluded — the central
   reconciliation triad. Renders Excluded as an em-dash with tooltip when
   the engine is rule-based (no row-level classification available).
2. **Activity Classification line**: type breakdown from
   `bookkeeping_breakdown` (Vendor payments N · Payroll deposits N · …)
   in canonical order, present types only. Italic fallback for rule-based.
3. **Vendor / 1099 Review row**: single dot-separated compact line carrying
   Included Total · Vendors · Review Needed · Over $600 · Confidence.
   Review Needed value styled red when > 0, neutral when 0 (preserves
   accountant muscle memory from the pre-3b cards).
4. **Yellow / green review summary strip**: unchanged.

The expansion's Group A was renamed from "Statement-Level Bookkeeping
Summary" to "Statement Processing Details" and simplified — the breakdown
table and helper note were removed because the always-visible Activity
Classification section now carries that information. The natural-language
status detail line was preserved for audit value.

## Session-by-session

**C1** (Session 1) — Defensive dual-layout introduction. Headline tiles
and activity classification rendered above the existing 5-metric row for
side-by-side comparison. CSS for `.ps-vendor-review` added but the helper
not yet called. Color treatment for the 3 tiles deferred for live decision
with eyes on rendered output. All six in-scope verification cases passed.

**C1b** (Session 2) — Demotion. Removed legacy `.ps-metrics` block and
`.ps-activity-line` injection. Wired in `renderVendorReview()`. Renamed
and simplified Group A in the expansion. Responsive verified at 1440 /
1024 / 412 px.

**C2** (Session 3) — Cleanup. Removed orphan CSS (`.ps-metrics`,
`.ps-metric*`, `.ps-activity-line*` rules plus the two responsive
overrides) and the orphan `renderMetric()` JS function. Consolidated
changelog comments from "C1/C1b" to just "Phase 3b". No visual changes —
the C1b state shipped as the final Phase 3b state.

## Verification matrix outcome

| Test | Result |
|---|---|
| 1-PDF clean (PDF Skill) | ✓ Parsed=Included, Excluded=0, single-type activity classification |
| 1-PDF mixed (PDF Skill) | ✓ All 3 tiles distinct, multi-type activity classification |
| 2-PDF mixed (PDF Skill) | ✓ Both cards render correctly with different breakdowns |
| 1-PDF rule-based | ✓ Excluded shows em-dash + tooltip, italic activity fallback |
| Failed card | Path untouched, no regression possible |
| Responsive 1440 / 1024 / 640 | ✓ Tested down to 412px (Samsung Galaxy A51 emulation); headline tiles stack to 1-column at <640 |
| Master Workbook deliverable | ✓ Unchanged across all three sessions — 5 sheets, all data flows intact |
| Workspace KPIs | ✓ Math reconciles end-to-end (e.g. Included 68 = 37+31, Total $28,270.94 stable) |

## Color treatment decision

Three candidate options (X = left border, Y = value color, Z = bottom
accent bar) were specified in the design spec and shipped as commented-out
CSS in C1. After review of the rendered output in all four card states
(PDF Skill clean, PDF Skill mixed, rule-based × 2), neutral was chosen.

Rationale: the Excluded value reads as informational, not as a warning
("rows correctly filtered by the parser is good news, not a problem to
fix"). Coloring it amber would imply a problem state where none exists.
The labels + tabular numbers already establish hierarchy without color.
The commented-out option blocks remain in the CSS for future revisit.

## Decisions and open questions captured

**Deferred to Phase 3d** — A separate recalibration document (drafted
mid-Phase-3b but explicitly held out of scope) proposes a 1099 Review
Priority Summary (High / Medium / Low) in the Master Workbook and a
sharpening of the Per-Statement vs Consolidated lens distinction. Saved
for a future phase. The priority classification rule table needs to be
written down before any Master Workbook code is touched. W-9 status as a
priority driver is blocked on having a data source (currently always
"Unknown").

**Deferred to Phase 4** — The Statement Reconciliation Snapshot (balance
reconciliation). Phase 3b reserved layout space conceptually but rendered
nothing. Phase 4 will require backend schema changes and is the right
home for it.

**Deferred to a dedicated rebrand mini-phase** — Product rename from
PREPARE Core v1.3 to a shorter "PREP" brand. Discussed during Phase 3b
but kept out of scope to preserve release-notes coherence. The rebrand
will get its own session, possibly bundled with the first slice of Phase
3d to earn an honest minor-version bump.

## Lessons

1. **The 3-session split (C1 dual-layout → C1b demotion → C2 cleanup) was
   the right level of caution.** Each session ended in a verifiable state.
   The C1 dual-layout in particular bought visibility into the new
   structure before committing to removing the old one.

2. **Color decisions cannot be made in the abstract.** Spec'ing three
   candidate options as commented-out CSS blocks made the live decision
   point trivial. Worth doing again for future UI work.

3. **Orphan-cleanup-as-its-own-session (C2) kept the destructive C1b
   session focused on the structural change.** C1b made one decision
   ("remove the legacy block"); C2 made one decision ("clean up what's
   now unreferenced"). Mixing them would have made C1b's diff harder to
   audit.

4. **Verification matrix discipline matters.** Running the free rule-based
   regression before the paid PDF Skill regression in each session caught
   any structural issues without burning API budget. The 412px Samsung
   emulation test exceeded spec — useful precedent for future responsive
   work.

5. **The recalibration document (Phase 3d direction) emerged mid-phase
   and was correctly held out of scope.** That instinct paid off; folding
   the Master Workbook restructure into Phase 3b would have blown up the
   scope. The document is now queued as a real design spec for the next
   discrete chunk of work.
   
---

## v1.4 backlog (open items)

Captured for future planning. Not committed; each item needs scope refinement before becoming work.

1. **Rule-based engine `excluded` flag tagging** (from A5 closure). Bring rule-based and multi_agent engine outputs to the same transaction-serialization contract as PDF Skill so "Included Payments" KPI matches across engines. Investigate `agent_app.py.run_engine()` output structure; add `excluded` tagging at the serialization point. Risk: medium (shared plumbing with legacy multi_agent engine).
2. **B2 — review-needed counting basis**. Currently the count is per-statement-instance (Adobe in 2 statements = 2 review flags). Could be argued to dedup by vendor. Defer until user testing surfaces a preference.
3. **D1 — per-PDF try/except in server.py**. Prevent one PDF crash from killing a batch run.
4. **D3 — automatic engine fallback**. Optional auto-retry with rule-based when PDF Skill fails. Operator decision.
5. **E1 — ground-truth corpus expansion (2 → ~10 PDFs)**. Builds confidence in production behavior across diverse layouts.

---

## Cumulative v1.2 → v1.3 spend summary

| Phase | Description | API cost |
|---|---|---|
| v1.2 work | Per-Statement layout, validation engine, master workbook | (pre-v1.3) |
| Track 2 prototype | PDF Skill prototype investigation (4 tests) | ~$4.45 |
| Phase 1 | Master Workbook contract fix | ~$1.30 (combined PDF Skill tests) |
| Phase 2 | Frontend label rework + regression tests | ~$1.50 |
| Phase 3a | Master + validation polish + regression | ~$0.60 |
| **Total v1.3** | **All threads through Phase 3a** | **~$3.40** (development), plus $4.45 prototype |

Well within the $5-7 budget estimate from the V1_3_PLAN.

Phase 3b verification will add ~$0.65-1.30. Phase 4 / v1.4 work is not estimated.
