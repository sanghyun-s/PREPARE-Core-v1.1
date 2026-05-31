# PREPARE v1.4 — Session entry: May 27, 2026

**Theme:** Closing Phase 4 foundation work (card excluded-fix, Workspace 4E web tile, snapshot pipeline trace). Source B production build deferred to next session.

**Status at end of session:** Phase 4 foundation 95% complete. One open gap: per-statement card reconciliation waterfall render. All other Phase 4 surfaces (Excel per-statement, Excel master, Workspace tile) verified live on three-PDF PDF Skill test run.

---

## What was attempted

Three coordinated items planned for the session, with Source B as the stretch goal:

1. **Card excluded-breakdown fix (Path A).** The per-statement card's "excluded (…)" parenthetical was listing included types (checks, vendor payments) alongside genuinely excluded types (deposits, fees) — making the human-readable count contradict the numeric count. Fix: filter the breakdown to skip included types, and singularize labels when count == 1.

2. **Workspace 4E web tile.** Mirror the master Excel STATEMENT RECONCILIATION block into the Workspace view, between the four validation tiles and the Workbook CTA. "X of N statements reconcile" headline with three pills (Balanced / Needs Review / Unavailable). Required schema extension (three new fields on `Summary`), server-side tally in the existing per-statement loop, and frontend render function.

3. **Server-side snapshot carry verification.** Trace whether the per-statement `reconciliation_snapshot` (computed in `pipeline._compute_reconciliation`) was reaching `server.py`'s `agent_outputs.append({...})` dict and from there to the `Statement` model and `Summary` roll-up.

4. **(Stretch) Source B production build.** Extraction-completeness cross-check — row-derived bucket sums vs. statement-stated activity totals. Spike from prior session verdict: GO.

---

## What landed cleanly

### Card excluded-fix (Path A) — verified

Edit to `formatExcludedBreakdown` in `frontend/index.html`:
- Added `INCLUDED_TYPES = new Set(['vendor_payment', 'check_payment'])` filter.
- Added `SINGULAR` lookup table for count==1 labels.
- Verified node-test against three real test cases:
  - Harbor: "2 excluded (1 deposit, 1 bank fee)" — unchanged ✓
  - Northgate: "2 excluded (1 deposit, 1 bank fee)" — checks removed, singular ✓
  - Summit: "5 excluded (2 deposits, 1 transfer, 2 bank fees)" — checks removed, count matches ✓

Verified live on the three-PDF test run. Card display matches the per-statement Excel byte-for-byte (which was the goal — Path B's full schema refactor was deferred as overkill for demo equivalence).

### Workspace 4E web tile — verified

Three coordinated edits:
- `schemas.py`: extended `Summary` with `reconciliation_balanced`, `reconciliation_needs_review`, `reconciliation_unavailable` (all `int = 0` defaults for backward compat).
- `server.py`: tallied recon status in the existing per-statement loop, passed to `Summary(...)` constructor.
- `frontend/index.html`: added CSS section (`.ws-recon*`), container div between `vs-grid-4` and workbook CTA, and `renderReconciliationRollup(data)` function with dispatch call in `showResults`.

Verified live: Workspace tile renders "2 of 3 statements reconcile" with Balanced 2 / Needs Review 1 (amber) / Unavailable 0 — exactly mirroring the master Excel 4E block.

### Server-side snapshot carry fix — verified

This was the deepest diagnostic of the session. The chain:
```
pipeline._compute_reconciliation()         ✓ computes snapshot
pipeline.run_pipeline_pdf_skill return     ✓ "reconciliation_snapshot": recon_snapshot
server.py PDF Skill success branch         ✗ DROPPED — agent_outputs.append({...}) didn't carry it
server.py Statement constructor            (would have worked if data reached it)
Summary roll-up tally                      (would have worked if data reached it)
```

Root cause: `server.py` line ~370's `agent_outputs.append({...})` block copied 11 fields from `stmt_result` but never copied `reconciliation_snapshot`. The pipeline computed it; the server immediately threw it away.

Fix: one-line addition.
```python
"reconciliation_snapshot": stmt_result.get("reconciliation_snapshot"),
```

Verified end-to-end via Python trace (`pipeline → agent_output dict → ReconciliationSnapshot model → Statement → Summary`) before shipping. Verified live: Workspace 4E tile populated correctly after the fix.

---

## What didn't land

### Per-statement card reconciliation waterfall

The reconciliation waterfall block (Beginning balance → Activity → Calculated ending vs Reported ending → Difference, with Balanced/Needs Review status) should render inside each per-statement card's expansion, between the Statement-Level Bookkeeping Summary and Bookkeeping Review Signals groups.

After the server-side fix, the data is reaching the frontend (proved by the Workspace tile working — that reads the same snapshot data from the same response payload). But the waterfall doesn't render on the cards.

Attempted fix mid-session: a `sed` patch inserting `parts.push(renderReconciliation(s));` in `buildExpansionContent`. The grep-confirmed-existing CSS class names and heading text led to the assumption that the function definition was also present. It wasn't (or had a different name) — the patch inserted a call to an undefined function, which broke the entire frontend with `Error: renderReconciliation is not defined`.

Recovered by restoring the pre-sed backup (`frontend/backup/index.html.bak_recon_call_20260527_174944`). Frontend back to working state, minus the waterfall — same state as start of session.

**Next-session diagnostic:**
```bash
grep -n "function renderReconciliation\|ps-recon\|Statement Reconciliation" frontend/index.html
```
That output determines whether the fix is:
- One-line call-site insertion (function exists)
- Function + call-site restoration (function lost, CSS intact)
- Full CSS + function + call-site (4C frontend work never merged)

### Source B production build

Not started. Spike verdict from prior session (GO, all 15 bucket comparisons clean across three test files) remains locked in writing. Design spec is concrete enough to skip a separate 4F spec doc. Build order documented:

1. `pipeline._compute_source_b()` helper alongside `_compute_reconciliation`.
2. `schemas.ExtractionCheck` model + `Statement.extraction_check` field.
3. `pipeline.py` wire into PDF Skill call sites.
4. `excel_generator.py` Source B sub-block on Summary Stats sheet.
5. `master_excel_generator.py` extend 4E block with "X of N show complete extraction" line.
6. Verification by re-running spike logic against productionized helper.
7. (Optional, decision pending) Per-statement card render.

Estimated 60–90 min focused work in next session, after the waterfall is restored.

---

## Discipline lessons (own them honestly)

This session's diagnostic struggles trace to two specific protocol violations, both mine:

**1. Workspace-state ≠ live-state assumption.** Early in the session, I edited files in my workspace that I assumed matched the user's live files. They didn't (workspace `pipeline.py` was a pre-Phase-4 stub; live `backend/pipeline.py` had the full 9-marker Source A logic). User correctly called this out: *"Please stop assuming that having the py file means no need for upload."* Cost: roughly an hour of misdiagnosis before the user uploaded the real live files and the actual server.py snapshot-drop was located cleanly.

**2. Grep-by-topic ≠ grep-by-symbol.** Mid-session, when patching the waterfall call site, I grepped for `renderReconciliation|ps-recon-table|Statement Reconciliation` and got 7 matches. I treated that as evidence the function existed. It wasn't — those 7 matches could all have been (and were) CSS class names, comment text, and string literals. Should have grepped specifically `grep -n "function renderReconciliation" frontend/index.html` *before* inserting a call to it. Sed patch went out anyway. Result: broke the entire frontend, recovered by backup restore.

**Protocol going forward:**
- For every change touching live code: ask for fresh uploads first. No workspace assumptions.
- Grep for the exact symbol being called/edited, not topic keywords.
- Show the diagnostic result before proposing the patch.
- Verify end-to-end trace before declaring a fix verified.

---

## State of the project at session end

**Verified working** (foundation for any future build):
- Phase 1 (schema contract v1.0)
- Phase 2 (per-statement card framing rewrite)
- Phase 3a (PDF Skill engine integration, with cost/agent_seconds tracking)
- Phase 3b (bookkeeping-first card redesign, headline tiles + activity classification)
- Phase 4A (PDF Skill prompt v0.3, reconciliation_snapshot extraction)
- Phase 4B (Source A compute + server plumbing)
- Phase 4C (per-statement Excel reconciliation block)
- Phase 4D-core (Excel restructure, confidence fix, recon section placement)
- Phase 4D-plus (Transactions sheet type-coloring + AutoFilter)
- Phase 4E (master Excel STATEMENT RECONCILIATION block + Workspace web tile)
- Card excluded-breakdown fix (Path A)
- Server snapshot carry-through (fixed this session)

**Open gap:**
- Per-statement card reconciliation waterfall — diagnostic ready for next session, fix scope TBD pending one grep.

**Not started:**
- Source B (extraction-completeness check) — spike-verified GO, design locked, build deferred.

**Sunday-target follow-ups** (independent of remaining engineering work):
- README rewrite (this entry feeds it directly)
- GitHub push
- Demo pitch / video draft
- LinkedIn narrative

---

## Cost telemetry (PDF Skill test runs)

Three test runs across the session:
- Run 1 (post-server-fix verification): 3 PDFs, ~$0.49 total, ~4 min
- Run 2 (post-frontend-attempt): 3 PDFs, ~$0.48 total, ~3 min
- Run 3 (final restore verification): 3 PDFs, ~$0.43 total, ~3 min

Total session API spend: ~$1.40 across nine PDF Skill invocations. Within budget.

---

## Closing note

Tonight cost more cycles than the work it produced should have required. Three real wins landed (card fix, 4E web tile, server snapshot carry), and the foundation is structurally complete except for one frontend render path. The waterfall gap is small, isolated, and diagnostically traceable in one grep next session.

The project's "rational til the end" discipline held — even at the worst point of the session (broken frontend, no clear cause), the recovery was one backup restore command because the user had insisted on backup-before-edit throughout. That discipline saved the project tonight. It's worth remembering when the README narrative gets written: the engineering wins matter, but so does the operational discipline that made tonight recoverable instead of catastrophic.

---

## Post-session diagnostic addendum

Final grep on the restored `frontend/index.html` (after the backup recovery):

```
grep -n "function renderReconciliation\|ps-recon\|Statement Reconciliation" frontend/index.html

   6: <title>PREPARE Core v1.3 · Statement Reconciliation Workspace</title>
1515:   <div class="subtitle">Statement Reconciliation Workspace</div>
1599:   <h1 class="upload-page-title">Statement Reconciliation Workspace</h1>
2566: function renderReconciliationRollup(data) {
2587:   <div class="ws-recon-title">Statement Reconciliation</div>
```

**Finding:** Phase 4C per-statement card waterfall frontend code (`function renderReconciliation`, `.ps-recon-*` CSS, `<div class="ps-recon-table">` markup) is **NOT present** in the live file. Only the Phase 4E Workspace tile work (built today) is present.

The earlier May 25 screenshots showing the working waterfall must have come from a different `index.html` version that had 4C merged, but a pre-4C backup got restored at some point (the file explorer view earlier today shows multiple `index.html.bak*` and `index.html.phase2_pre_3bC1_*` — that's where the regression occurred).

**Next-session scope** is therefore larger than the original "one-line call-site" assumption, but still well-bounded:
- ~40 lines of CSS (`.ps-recon-*` styling block)
- ~30-line function (`renderReconciliation(s)` operating on the existing `s.reconciliation_snapshot` data)
- 1-line call-site insertion in `buildExpansionContent`

Three independent anchored edits. Apply in CSS → function → call-site order (safest activation sequence: each step is visually neutral until the call-site lands). Estimated 30–45 min focused work including verification on the three test cases.

The data plumbing is already complete (proved by today's Workspace tile rendering correctly), so this is purely additive frontend work with no backend changes.
