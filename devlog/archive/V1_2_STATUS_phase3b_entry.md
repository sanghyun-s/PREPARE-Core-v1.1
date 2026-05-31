# Phase 3b — Per-Statement Card Bookkeeping-First Redesign (COMPLETE)

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
