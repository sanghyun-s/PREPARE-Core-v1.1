## Phase 4 — Statement Reconciliation Snapshot (v1.4) — IN PROGRESS

Phase 4 completes the bookkeeping-first thesis at the statement level: the
Per-Statement card (and per-statement Excel) gain a reconciliation snapshot
answering "does this statement balance?" It is a continuation of the Phase 3b
card remake, not a new section — Phase 3b answered "what was in this statement?",
Phase 4 answers "does it reconcile?"

Phase 4 is divided into five sub-phases (4A spike → 4B schema → 4C UI →
4D per-statement Excel → 4E validation display). 4A is a go/no-go gate;
4B–4E do not start until 4A returns GO. Spec: `devlog/specs/V1_4_PHASE4_DESIGN_SPEC.md`.

**Scope boundary (locked PREPARE directive):** PREPARE is a bookkeeping and
statement reconciliation aid — it ingests statements, classifies activity,
separates included vendor payments from excluded rows, and helps review
statement-level reconciliation. It may surface basic 1099 threshold signals
but is NOT a full 1099 filing processor. Balance reconciliation (Phase 4) is
in scope; 1099 filing-priority (the shelved Phase 3d work) moved to App 2A.

---

### Phase 4A — Extraction spike (GO/NO-GO gate) — PASSED

**Status**: Closed May 24, 2026 — VERDICT: GO
**Question**: Can the PDF Skill reliably and stably extract the seven
account-summary balance figures (beginning, deposits, withdrawals, checks,
transfers, fees, reported ending) so a reconciliation check can be built on them?

**Method.** Three synthetic test statements were generated with known balance
ground truth (`phase4_test/`, with `GROUND_TRUTH_KEY.md`):
- `northgate_bank_clean.pdf` — balanced, no transfer (baseline)
- `summit_cu_transfer.pdf` — balanced, includes a $3,000 transfer (tests the transfers field)
- `harbor_national_unbalanced.pdf` — intentionally off by $150 (tests needs_review detection)

The v0.3 PDF Skill prompt was extended with a `reconciliation_snapshot` object
and a transcribe-don't-compute policy (the model reports figures AS STATED; the
app does the arithmetic). A scoring harness (`phase4_test/score_4a.py`) ran the
adapter on all three PDFs and scored against ground truth across four criteria:
field accuracy (±$0.01), discrepancy detection, absent-section handling, and
honesty (no false claims).

**Result.** Clean pass on the full stability gate (3 PDFs × 3 runs):
- Field accuracy: all 7 figures matched ground truth within $0.01, all PDFs, all runs
- Stability: each PDF returned byte-identical balance figures across all 3 runs
  (STABLE — 1 distinct signature over 3 runs). Balance-summary figures are
  stable in a way edge-row classification is not, because they are large,
  clearly-labeled, single values in a structured summary box.
- Discrepancy detection: Harbor correctly computed difference $150.00 →
  `needs_review`, all three runs. The model transcribed the unbalanced figure
  faithfully rather than "correcting" it — the trust-critical behavior held.
- Absent-section handling: Northgate transfers and Harbor checks correctly
  returned 0.0.

**Cost**: ~$2.50 total across the full 4A spike (dry-run + sanity + stability pass).

**Note on the scorer.** An initial run flagged a false FAIL on `fields_found`
metadata (a correct-but-unlisted-field check that was too strict). The honesty
criterion was relaxed to catch only genuine false claims (claiming a field that
is actually absent), not missing provenance metadata — since field-accuracy
already verifies every value. `fields_found` is treated as optional metadata.

---

### Phase 4B — Schema + plumbing — COMPLETE (verified)

**Status**: Closed May 24, 2026
**Files changed**: `schemas.py`, `pdf_skill_adapter.py`, `pipeline.py`, `server.py`
**Cost**: ~$0.12 (one PDF Skill verification run)

**What landed.** The `reconciliation_snapshot` now flows through the full
production path to the API response. No UI yet (that is 4C) — 4B is pure
data-plumbing plus the server-side computation.

1. **`schemas.py`** — new `ReconciliationSnapshot` Pydantic model (7 extracted
   figures + 3 computed: calculated_ending_balance, difference, status +
   provenance: extraction_complete, fields_found, notes). Attached to the
   `Statement` model as an optional field. Backward-compatible: engines that
   don't extract balances (rule_based, multi_agent) leave it None.

2. **`pdf_skill_adapter.py`** — `PDFSkillExtractionResult` gains a
   `reconciliation_snapshot` field; the success path pulls it from the parsed
   agent JSON (the v0.3 prompt produces it). Absent → empty dict.

3. **`pipeline.py`** — new `_compute_reconciliation()` helper is the single
   place the arithmetic happens. Takes the transcribed figures, computes
   calculated_ending_balance, difference, and status at a locked
   `RECONCILIATION_TOLERANCE = 0.01`. Status is "balanced" / "needs_review" /
   "unavailable". `checks` and `transfers` default to 0.0 when a section is
   genuinely absent, so a missing checks/transfers line does not make the
   snapshot unavailable. `run_pipeline_pdf_skill` returns the computed snapshot.

4. **`server.py`** — carries the snapshot through both hops (pipeline dict →
   agent_outputs → Statement), with a dict→model conversion. Failure branch
   sets it None.

**Verification.**
- All four files py_compile clean.
- Unit test of `_compute_reconciliation` against the 4A ground truth: Northgate
  / Summit → balanced $0.00; Harbor → needs_review $150.00; missing fields →
  unavailable; absent-checks(None) → defaults to 0.0. All pass.
- Schema round-trip: full `ProcessResponse` serializes with the snapshot null
  on rule-based statements and populated on PDF Skill — contract intact,
  backward-compatible.
- End-to-end live run (Path A): `harbor_national_unbalanced.pdf` through
  `run_pipeline_pdf_skill` returned `status=needs_review`, `difference=150.0`,
  `extraction_complete=true`, matching the 4A spike exactly — now through the
  real production pipeline rather than the test harness. The model's `notes`
  field independently explained the discrepancy and confirmed it reported the
  figure verbatim rather than correcting it. Incidental cross-check: the
  snapshot's total_withdrawals ($4,820.00) equals the sum of the 7 extracted
  vendor rows — a preview of the planned Source B row-sum cross-check.

---

### Phase 4 remaining sub-phases (not yet started)

| Sub-phase | Job | Files | Status |
|---|---|---|---|
| 4C | Per-Statement card reconciliation block (continuation of Phase 3b remake) | `frontend/index.html` | Not started |
| 4D | Per-statement Excel reconciliation section | `excel_generator.py` (per-statement; NOT master_excel_generator.py) | Not started |
| 4E | Validation display — surface difference + balanced/needs_review prominently | folds into 4C/4D | Not started |

### Source A vs Source B (recorded direction)

- **Source A** (this phase): transcribe the statement's stated account-summary
  totals → check the statement balances internally. Implemented in 4A/4B.
- **Source B** (planned follow-on): sum the extracted transaction rows and
  cross-check against the stated summary → confirms extraction completeness.
  Stronger bookkeeping-trust signal, but depends on extraction completeness
  (a known soft spot), so sequenced after Source A proves out. The Harbor
  run already shows the two numbers agree, an encouraging early sign.

### Prompt versioning

The PDF Skill prompt is now at v0.3 (`backend/prototypes/pdf_skill_prompt.md`),
adding the `reconciliation_snapshot` object and the transcribe-don't-compute /
honesty rules. Transaction extraction and classification rules unchanged from
v0.2. The production extraction path now emits reconciliation_snapshot on every
PDF Skill run; 4B is what makes the app consume it.
