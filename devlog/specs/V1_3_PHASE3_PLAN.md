# PREPARE v1.3 — Phase 3 Plan

**Status**: Drafted 2026-05-15 after Phase 2 completion.
**Approach**: Two sequenced sub-phases. Phase 3a first (polish foundation),
then Phase 3b (strategic UI redesign on the clean base).
**Standing constraints lifted in Phase 3**: `master_excel_generator.py` and
`validation_engine.py` are both fair game (they were frozen in v1.3).

---

## Context — where v1.3 stands as of Phase 2 completion

### What's complete and verified end-to-end

| Component | Status | Verification |
|---|---|---|
| PDF Skill engine integration | ✅ Production-ready | 5 single-PDF runs, 1 two-PDF run, cost ~$0.65 |
| Pipeline / server / schema integration | ✅ Stable | Both engines tested |
| Master Workbook contract fix (3 bugs killed) | ✅ Verified | Single + multi-PDF |
| Per-Statement Excel 12-column classifier output | ✅ Verified | Multicolumn PDF with 8 excluded rows |
| Phase 2 frontend label rework (7 changes) | ✅ Verified | Both engines, both clean and multicolumn PDFs |
| Cross-statement validation findings (10/5/1/2) | ✅ Verified | Real findings on real data |
| Per-statement card "Cross-Statement Signals" drill-down | ✅ Verified | Activity line + status detail + signals all render |

### Total spend through Phase 2

- v1.3 development tests: ~$3.15 across 8+ runs
- Single biggest demo (2 PDFs + cross-validation): $0.5997, 4m 26s

### What surfaced during testing but wasn't fixed in v1.3 / Phase 2

Sorted by visibility and priority. These are all Phase 3 candidates:

| # | Issue | Visibility | Severity |
|---|---|---|---|
| A1 | "Total Transactions" label in master Executive Summary doesn't match WebUI "Included Payments" | High (Excel-visible) | Cosmetic |
| A2 | Validation Report column widths cramped, text wraps awkwardly | High (Excel-visible) | Cosmetic |
| A3 | Master Vendor Summary Review Reasons column shows UUID filenames instead of `original_filename` | High (Excel-visible) | Quality |
| A4 | Master Vendor Summary Review Reasons verbose prose hard to scan | Medium (Excel-visible) | UX |
| B1 | Symmetric name variant reports (3 pairs → 5 flags due to A↔B mirroring) | Medium (WebUI + Excel) | Pre-existing bug |
| B2 | Review Needed counted per-statement-instance (Adobe in 2 statements = 2 review flags) | Low (WebUI subtle) | Philosophical |
| C1 | Per-statement card uses old "transactions / vendors / amount / review / confidence" layout — should pivot to bookkeeping-first KPIs | High (WebUI-visible) | Strategic |
| D1 | server.py lacks per-PDF try/except — one PDF crash kills batch | Low (only on failure) | Robustness |
| D2 | PDF Skill non-determinism on edge rows (header/metadata classification) | Low (Excel-visible) | Documentation |
| D3 | No automatic engine fallback when PDF Skill fails | Low | Operator decision |

---

## Phase 3a — Master Workbook completion + validation cleanup

**Goal**: Lift the master_excel_generator.py + validation_engine.py constraints and finish the polish work that was blocked in v1.3.

**Time estimate**: 1-2 working sessions
**API cost**: $0 (no new test runs needed — verification uses existing PDFs)
**Risk**: Low-medium (touching previously-frozen files)

### In scope

| ID | Item | File touched | Effort |
|---|---|---|---|
| A1 | Rename "Total Transactions" → "Included Payments" in Executive Summary KPIs | master_excel_generator.py | XS |
| A2 | Validation Report column width + wrapping fix | master_excel_generator.py | S |
| A3 | Replace UUID filenames with `original_filename` in Review Reasons | master_excel_generator.py | M |
| B1 | Symmetric name variant deduplication (3 pairs → 3 flags, not 5) | validation_engine.py | M |
| F1 | Append Phase 2 + Phase 3a completion to V1_2_STATUS.md | V1_2_STATUS.md | XS |
| F2 | Draft V1_3_RELEASE_NOTES.md for portfolio/mentor review | (new file) | S |

### Out of scope (deferred to Phase 3b or later)

- A4 (Review Reasons tag reformatting) — depends on Phase 3b decisions
- B2 (review-needed counting basis) — philosophical, deserves explicit operator decision
- C1 (card redesign) — that's Phase 3b
- D1-D3 (robustness/automation) — Phase 4 candidates

### Verification approach (free)

1. Apply Phase 3a edits
2. **Re-process the existing successful 2-PDF master workbook** (no API call needed — use the existing per-statement Excel files as inputs to a fresh master_excel_generator run if possible, or do one fresh rule-based test for $0)
3. Verify:
   - Executive Summary "Total Transactions" → "Included Payments"
   - Validation Report columns readable without manual width adjustment
   - Review Reasons show `sample_bank_3col_clean.pdf` not `29f0b14961914821ae5624686f0321a0.pdf`
   - Name variant flags: 3 entries instead of 5

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| master_excel_generator.py changes break existing workbook | Keep all sheet names, column orders identical; only rename labels and dedupe values |
| Validation engine dedup changes meaning of cross-statement findings | Verify the 3 remaining flags are the canonical ones (alphabetically first name in the pair) |
| Backup of current state needs to be intact | Already captured in `PREPARE_track2_backup_20260515_*.tar.gz` |

---

## Phase 3b — Bookkeeping-first card redesign

**Goal**: Implement ChatGPT's strategic recommendation — pivot the Per-Statement card from "transaction extraction tool" framing to "bookkeeping review aid" framing. Parsed/included/excluded becomes primary KPI row, current 5 metrics demoted to secondary.

**Time estimate**: 2-3 working sessions
**API cost**: ~$0.65-1.30 (re-run 2-PDF test 1-2 times for verification)
**Risk**: Medium-high (significant UX change)

### In scope

| ID | Item | File touched | Effort |
|---|---|---|---|
| C1 | Per-statement card primary KPIs: parsed / included / excluded / cost | frontend/index.html | L |
| C1b | Per-statement card secondary metrics: collapsible "Statement-level details" expansion | frontend/index.html | M |
| A4 | Master Vendor Summary Review Reasons: short canonical tags ("LOW_NAME_MATCH", "ENTITY_UNKNOWN", "NAME_VARIANT", etc.) | master_excel_generator.py + maybe validation_engine.py | M |
| D2 | Add brief note to V1_3_RELEASE_NOTES about PDF Skill non-determinism | docs only | XS |

### Out of scope (deferred to v1.4 or beyond)

- B2 (review-needed counting basis) — defer until field demos surface user preference
- D1 (per-PDF try/except) — fine as-is for demo; revisit if real-world batches fail
- D3 (automatic engine fallback) — explicit operator decision needed first
- E1 (test corpus expansion to 10 PDFs) — v1.4 scope

### The redesign sketch (from ChatGPT's analysis)

**Current per-statement card (v1.3 Phase 2)**:
```
[Card head: filename, agent, engine pill, status, download, chevron]
[5 metrics: Included Payments | Vendors | Total Amount | Review Needed | Confidence]
[Activity line (small): 39 parsed · 31 included · 8 excluded]    ← already added in Phase 2
[Yellow Review summary strip]
```

**Phase 3b card (bookkeeping-first)**:
```
[Card head: same]
[3 primary KPIs: Parsed | Included | Excluded — large bookkeeping framing]
[Statement Activity Breakdown — always visible, not expandable]
[Cost & confidence — small, near footer]
[Vendor count / total amount / review needed → DEMOTED to "Statement details" expansion]
[Yellow Review summary strip stays for cross-statement context]
```

The shift: a bookkeeper opens the card and **immediately sees** what was parsed, what was kept, what was rejected and why — not transaction counts and dollar totals first.

### Verification approach

1. Apply Phase 3b edits
2. Run 2-PDF test on PDF Skill — same PDFs as the Phase 2 verification
3. Compare card layout side-by-side with Phase 2 baseline screenshot
4. Optionally: A/B with a bookkeeper or accountant in the network if possible

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| Demoting "Total Amount" makes the card feel less informative | Keep it visible in Statement Details expansion; large dollar totals still appear in Workspace KPIs |
| Bookkeeping-first framing may not resonate with non-accountant viewers | Keep technical/extraction framing accessible via Statement Details |
| Card redesign breaks responsive layouts | Test mobile/tablet/desktop at minimum |

---

## What's beyond Phase 3 (Phase 4 / v1.4 candidates)

These are real items but not in this phase. Documented here so they're not forgotten:

| ID | Item | Why deferred |
|---|---|---|
| B2 | Review-needed counting basis | Needs explicit operator decision, possibly user research |
| D1 | Per-PDF try/except in server.py | Not needed for demo scope; revisit when real batches show fragility |
| D3 | Automatic engine fallback | Operator decision (UX); also adds cost-control complexity |
| E1 | Ground-truth corpus expansion (2 → ~10 PDFs) | v1.4 scope; out of phase-3 character |

---

## Session strategy for Phase 3

### Phase 3a session structure (1-2 sessions)

1. **Open with backup verification** — confirm `PREPARE_track2_backup_20260515_*.tar.gz` exists and is readable
2. **A1 first** — single label change, lowest risk, fast confidence-builder
3. **A3 next** — UUID→filename, gets the worst quality issue out of the way
4. **A2 then** — column widths, cosmetic but visible
5. **B1 last** — touches a different file (validation_engine.py), good to do after master_excel work is stable
6. **F1, F2** — docs at the very end
7. **Verification run** — free rule-based test to confirm nothing broken; one PDF Skill test optional

### Phase 3b session structure (2-3 sessions)

1. **Open with re-reading ChatGPT's UI analysis** so the design direction is fresh
2. **Sketch new card layout** in pure HTML/CSS first (no JS)
3. **Migrate metric calculations** from current 5-metric `renderMetric()` calls to new 3-KPI primary row
4. **Add Statement Details expansion** for demoted metrics
5. **A4 (master tag reformatting)** done in parallel since it's separate file
6. **Verification run** on 2-PDF PDF Skill — $0.60-ish
7. **Iterate based on visual review**

---

## Communication & ground rules for Phase 3

- Continue file-by-file delivery, complete drop-in replacements (not patches)
- Compile checks before any test run
- Backup before any session that touches master_excel_generator.py or validation_engine.py
- Free rule-based regression test FIRST before PDF Skill test (cheap insurance)
- Keep master_excel_generator.py changes focused — sheet names + column ORDER must not change
- No GitHub push without explicit review

---

## When Phase 3 is complete

After Phase 3a + Phase 3b finish, the product should be at a state where:

1. ✅ Master Workbook reads professionally (no UUIDs, clean labels, readable columns)
2. ✅ Per-Statement cards lead with bookkeeping framing
3. ✅ Validation findings are clean (no symmetric duplicates)
4. ✅ Release notes document what shipped in v1.3
5. ✅ The product is **demo-ready and portfolio-quality**
6. ✅ Remaining items (B2, D1, D3, E1) are explicitly Phase 4 / v1.4 candidates, not loose ends

After that, "v1.3 final" is a meaningful milestone. Then either:
- Pause development and focus on demo / portfolio presentation
- Move to v1.4 scope (corpus expansion, operator decisions, automation)

---

## Open questions to resolve before Phase 3a starts

1. **Backup confirmation**: Has `PREPARE_track2_backup_20260515_*.tar.gz` been successfully created?
   (If no, run `bash backup_track2_artifacts.sh` first.)

2. **B1 dedup canonical ordering**: When deduping symmetric name variants (A↔B → one entry), should the kept entry be:
   - (a) The one where Name A comes first alphabetically?
   - (b) The one where Statement A is the first-uploaded?
   - (c) Some other rule?

   Default recommendation: (a) — alphabetical by Name A. Predictable, deterministic.

3. **A4 tag taxonomy**: For Phase 3b's Review Reasons reformatting, do we want a fixed list of tags
   (e.g., `LOW_NAME_MATCH`, `ENTITY_UNKNOWN`, `NEAR_THRESHOLD`, `NAME_VARIANT`, `CLASSIFIER_FLAG`)
   or keep it free-form with shorter prose?

   Default recommendation: fixed list of 5-8 canonical tags, with one-line human-readable description shown in a separate column or tooltip.

---

## Done. Save this file alongside V1_3_PLAN.md.
