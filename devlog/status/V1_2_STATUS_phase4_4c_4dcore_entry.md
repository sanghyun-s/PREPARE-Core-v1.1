## Phase 4 — devlog continuation (4C + 4D-core)

> **Merge note.** This file continues `V1_2_STATUS_phase4_entry.md`. The 4C and
> 4D-core sections below replace the "Phase 4 remaining sub-phases" table's
> 4C/4D "Not started" rows. Paste these `###` sections in after the Phase 4B
> section, then update the remaining-sub-phases table so 4C = COMPLETE,
> 4D-core = COMPLETE, 4D-plus + 4E = Not started.

---

### Phase 4C — Per-Statement card reconciliation block — COMPLETE (verified live)

**Status**: Closed May 24, 2026
**Files changed**: `frontend/index.html` (the live C2 file — 4 surgical anchored edits)
**Cost**: ~$0.12 (one PDF Skill verification run) + free rule-based regression

**What landed.** The Per-Statement card now displays the reconciliation snapshot
that 4B plumbed through. It reads `s.reconciliation_snapshot` and renders inside
the Group A expansion, below "Statement Processing Details."

Four anchored edits (delivered as `PHASE_4C_SNIPPETS.md`):
1. CSS `.ps-recon-*` block (waterfall table, verdict, notes styling).
2. `renderReconciliation(s)` + `fmtReconAmount()` JS helpers.
3. `parts.push(renderReconciliation(s))` after the Group A status block.
4. A subtle always-visible breadcrumb in the review strip — when
   `status == "needs_review"`, the strip ends with "· reconciliation needs
   review" so an unbalanced statement hints at it without expanding the card.

**Design decisions (locked, user-chosen).**
- Full waterfall (every line), inside the expandable Group A details section.
- needs_review loudness = MODERATE: amber text + ⚠ icon, NO full band.
- Three states: balanced (green check, $0.00 muted), needs_review (amber + ⚠),
  unavailable (muted note — rule-based / multi-agent, or PDF Skill that found no
  account-summary section). Gated like `renderActivityClassification`.

**Verification.**
- node syntax check + integration test across Harbor / Northgate / rule-based.
- Rule-based 3-PDF run (live, screenshots): all three cards show "Statement
  Reconciliation — not available (balance summary not extracted)" below
  Processing Details, no breadcrumb — correct graceful degradation.
- PDF Skill Harbor run (live, screenshots): full waterfall renders —
  3,000 / +6,000 − 4,820 − 0 − 0 − 30 = 4,150 calculated vs 4,000 reported,
  Difference $150.00 amber, ⚠ Needs Review, the model's verbatim note, and the
  breadcrumb present. Fully demoable.

---

### Phase 4D-core — Per-statement Excel reconciliation + restructure — COMPLETE (verified live)

**Status**: Closed May 24, 2026
**Files changed**: `backend/excel_generator.py` (per-statement generator — NOT
master_excel_generator.py), `backend/pipeline.py` (PDF Skill call site only)
**Cost**: ~$0.36 (≈3 PDF Skill verification runs incl. the confidence-fix re-run)
**Spec**: `devlog/specs/V1_4_PHASE4D_DESIGN_SPEC.md`

**What landed.** The per-statement Excel workbook was restructured so its landing
sheet mirrors the web Per-Statement card, and the reconciliation waterfall now
appears in the deliverable — fed by the SAME computed snapshot the UI uses.

1. **Sheet reorder.** Workbook now opens on **Summary Stats** (index 0), with
   Vendor Summary (1) and Transactions (2) following as detail sheets. Sheet TAB
   NAMES are unchanged — only positions changed — so the hardcoded-string lookups
   in `agent_app.py.run_single_agent` (and elsewhere) still resolve. Verified by
   round-trip: `wb.sheetnames == ['Summary Stats','Vendor Summary','Transactions']`.

2. **Summary Stats enriched** to mirror the card, top to bottom: Statement
   Processing Details (with the "(1 deposit, 1 bank fee)" excluded parenthetical),
   Activity Classification (type breakdown), Vendor / 1099 Review (totals line),
   **Statement Reconciliation** (the waterfall), Bookkeeping Review Signals, and
   the retained scope notes. The reconciliation block uses font color (amber
   `EA580C` / green `16A34A`), not heavy fills — matching the card's MODERATE
   treatment. Activity Classification and the reconciliation block AUTO-OMIT
   gracefully when their data is absent (rule-based).

3. **Pipeline plumbing.** `run_pipeline_pdf_skill`'s `generate_excel_report` call
   (PDF Skill path only) now passes `reconciliation_snapshot`, `breakdown`, and
   `confidence`. Critically, `_compute_reconciliation` was lifted into a local
   (`recon_snapshot`) used by BOTH the Excel call and the response dict, so the
   arithmetic still happens EXACTLY ONCE — the Excel waterfall and the card
   waterfall are guaranteed identical (same dict, same source). The rule-based
   call site (line ~483) was left untouched; its missing snapshot triggers the
   generator's graceful omit.

**Confidence fix (caught during verification).** The first 4D-core build derived
the Summary Stats "Confidence %" from `max(vendor match_confidence)`, which read
100% while the card showed the statement-level average. Fixed: the generator now
takes a `confidence` kwarg and renders the same value the response dict / card
use (`_avg_confidence(skill_result.all_transactions)`), with a vendor-average
fallback if a caller doesn't pass it. Re-verified: Excel now reads 97% to match
the Harbor card exactly.

**Verification.**
- Both files py_compile clean. `_compute_reconciliation` confirmed called exactly
  twice (def + the one local), so no double arithmetic.
- Generator round-trip tested in isolation across three scenarios (PDF Skill
  unbalanced, PDF Skill balanced, rule-based) before delivery — sheet order,
  block presence/absence, font colors, and currency formats all confirmed at the
  cell level.
- Rule-based 3-PDF run (live): Summary Stats leads, reconciliation + activity
  blocks correctly absent, other sheets intact.
- PDF Skill Harbor run (live): full waterfall in the Excel matching the UI —
  $150.00 amber, ⚠ Needs Review, verbatim note, Confidence 97%.

**Scope note.** 4D was split: 4D-core (this entry — restructure + reconciliation
+ plumbing) is done; **4D-plus** (Transactions-sheet type-cell coloring +
interactive AutoFilter) is the remaining, isolated second pass on the
now-known-good Transactions sheet.

---

### Phase 4 remaining (updated)

| Sub-phase | Job | Files | Status |
|---|---|---|---|
| 4C | Per-Statement card reconciliation block | `frontend/index.html` | **COMPLETE** |
| 4D-core | Per-statement Excel restructure + reconciliation | `excel_generator.py`, `pipeline.py` | **COMPLETE** |
| 4D-plus | Transactions sheet: type-cell coloring + AutoFilter | `excel_generator.py` (Transactions sheet only) | Not started |
| 4E | Validation display polish — surface difference prominently | folds into 4C/4D | Not started |
| Source B | Row-sum cross-check vs stated summary | TBD | Planned (after Source A proves out) |
