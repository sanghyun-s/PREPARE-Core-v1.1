# Phase 4 / v1.4 — Statement Reconciliation Snapshot (Design Spec)

**Status**: Drafted for review. Thin lock document — Phase 4's risk is empirical
(can the fields be extracted stably?), not design ambiguity, so this spec locks
just enough to run the 4A spike against clear criteria.
**Target version**: v1.4
**Scope frame**: This is a CONTINUATION of the Phase 3b Per-Statement card remake,
not a new section. Phase 3b answered "what was in this statement?"; Phase 4
answers "does this statement reconcile?" Together they complete the Per-Statement
card as a bookkeeping aid.

---

## 0. PREPARE directive (locked — the scope boundary)

> PREPARE is a bookkeeping and statement reconciliation aid. It ingests bank/card
> statements, classifies statement activity, separates included vendor payments
> from excluded non-vendor rows, and helps users review statement-level
> reconciliation. It may surface basic 1099 threshold signals, but it is NOT a
> full 1099 filing processor.

Phase 4 (balance reconciliation) is squarely IN scope: it is about statement
integrity — "does what the statement says balance?" — which is the heart of
bookkeeping. 1099 *filing priority* (the shelved Phase 3d work) is OUT of scope
and moves to App 2A (IRS Form Processor), where TIN/legal-name/form-type data
actually exists.

---

## 1. Goal

Add a Statement Reconciliation Snapshot to each Per-Statement card (and the
per-statement Excel summary) that shows whether the statement's own arithmetic
balances:

```
beginning_balance
  + total_deposits
  − total_withdrawals
  − checks
  − transfers
  − fees
  = calculated_ending_balance
  vs. reported ending_balance
  → difference, and a balanced / needs_review status
```

The value to a bookkeeper: at a glance, "did this statement parse completely and
consistently?" A statement that doesn't balance signals either an extraction
problem or a real discrepancy — both worth a human look BEFORE trusting any
downstream vendor totals.

---

## 2. The reconciliation_snapshot structure (locked)

Added to the per-statement response. Six EXTRACTED input fields + three COMPUTED
fields + status. Critical design point: the app extracts the inputs and the
reported ending, then COMPUTES calculated_ending / difference / status
server-side. The calculation is never extracted — only the raw figures are.

```
reconciliation_snapshot = {
    # ── Extracted from the statement (PDF Skill) ──
    "beginning_balance":   float | None,
    "total_deposits":      float | None,
    "total_withdrawals":   float | None,
    "checks":              float | None,   # 0.0 if section absent
    "transfers":           float | None,   # 0.0 if section absent
    "fees":                float | None,
    "reported_ending_balance": float | None,

    # ── Computed server-side (NOT extracted) ──
    "calculated_ending_balance": float | None,   # beginning + deposits − withdrawals − checks − transfers − fees
    "difference":          float | None,         # calculated − reported
    "status":              str,                  # "balanced" | "needs_review" | "unavailable"

    # ── Provenance ──
    "extraction_complete": bool,   # True only if all required inputs present
}
```

Field semantics:
- All extracted fields are `None` when the statement/engine didn't provide them.
- `checks` and `transfers` default to `0.0` when those sections are simply absent
  (a statement with no checks legitimately has $0 checks, not missing data) — but
  this requires the extractor to affirmatively report "no checks section" vs
  "couldn't read." 4A must clarify which the PDF Skill does (see §4).
- `status`:
  - `"balanced"` — extraction_complete AND |difference| ≤ threshold
  - `"needs_review"` — extraction_complete AND |difference| > threshold
  - `"unavailable"` — extraction_complete is False (missing required inputs; e.g.
    rule-based engine, or PDF Skill couldn't find the balance lines). The UI shows
    a graceful "reconciliation not available for this statement" state — NEVER a
    fabricated balance.

---

## 3. Balanced / needs_review threshold (locked)

**Threshold = $0.01** (one cent).

- |difference| ≤ $0.01 → `balanced`
- |difference| > $0.01 → `needs_review`

Rationale: a real bank statement balances to the penny. One cent of slack absorbs
floating-point/rounding artifacts (the same class of issue `_round_currency`
already guards in the Excel layer) without masking a genuine discrepancy. The
$150 Harbor National test trips any threshold; the slack only matters for the
balanced statements, where we want zero tolerance for real gaps but immunity to
rounding noise.

Defined as a single module constant `RECONCILIATION_TOLERANCE = 0.01` so it lives
in one place and is trivially tunable if real statements prove noisier.

---

## 4. Phase 4A — the spike, and its MEASURABLE success criteria

4A is a GO/NO-GO GATE, not phase-1-of-5. Its only job: prove the PDF Skill can
extract the six input fields + reported ending stably, BEFORE we build 4B–4E on
top. A plausible-but-wrong reconciliation destroys trust faster than none.

**Test corpus**: the three synthetic statements in `phase4_test/`
(northgate_bank_clean, summit_cu_transfer, harbor_national_unbalanced) + their
GROUND_TRUTH_KEY.md answer key. Optionally also the existing real samples.

**What 4A must prove — measurable pass criteria:**

| # | Criterion | Pass bar |
|---|---|---|
| 1 | **Field accuracy** | All 7 extracted fields (beginning, deposits, withdrawals, checks, transfers, fees, reported ending) match GROUND_TRUTH_KEY within $0.01, on all 3 statements |
| 2 | **Stability** | Same statement, 3 repeated runs → same extracted values (no run-to-run drift on the balance fields). Edge-row classification drift (already documented in v1.3) is tolerated; BALANCE-FIELD drift is not |
| 3 | **Discrepancy detection** | harbor_national_unbalanced → difference computes to $150.00, status `needs_review`. The balanced two → difference $0.00, status `balanced` |
| 4 | **Absent-section handling** | Statements with no checks/transfers section → those fields come back as a clear "absent" signal (0.0 or explicit None), NOT a hallucinated number or a crash |
| 5 | **Failure honesty** | If a field genuinely can't be found, the extractor says so (None) rather than guessing. Tested by inspecting whether any field is fabricated |

**GO** = criteria 1–4 pass on all three statements across the stability runs.
**NO-GO** = field accuracy or stability fails → STOP. Document why. The snapshot
either stays out of PREPARE, or ships gated as "experimental, hidden by default"
until extraction improves. This decision is the whole point of the spike.

**4A deliverable**: a short findings note (pass/fail per criterion, the actual
extracted-vs-truth numbers, cost, and the GO/NO-GO call). NOT code integration —
4A is a throwaway prompt experiment.

**4A cost/time**: ~$1–2 (several PDF Skill runs while iterating the prompt),
1 session. Run locally (Path A — needs API key + pdf_skill_adapter.py).

---

## 5. Phases 4B–4E (scoped, contingent on 4A = GO)

| Phase | Job | Files | Cost | Time | Risk |
|---|---|---|---|---|---|
| 4B | Add `reconciliation_snapshot` to schema + populate it; compute calculated/difference/status server-side | `schemas.py`, `pipeline.py`, `pdf_skill_adapter.py`, `server.py` | $0 rule-based + ~$0.60 PDF Skill | 1 session | Low (additive schema, like Phase 1 `bookkeeping_breakdown`) |
| 4C | Per-Statement card reconciliation block (continuation of Phase 3b remake), with graceful "unavailable" fallback | `frontend/index.html` | $0 + ~$0.60 | 1–2 sessions | Medium (real card-layout change) |
| 4D | Per-statement Excel reconciliation section | `excel_generator.py` (per-statement; NOT master_excel_generator.py) | $0 + optional confirm | 1 session | Low |
| 4E | Validation display — surface difference + balanced/needs_review prominently; fold the threshold logic in | folds into 4C/4D (status computed in 4B, displayed here) | $0 | ~0.5 session | Low |

**Total (4A–4E)**: 4–6 sessions, ~$3–5 API. 4A is a genuine off-ramp.

Sequencing rule: **4B–4E do not start until 4A returns GO.** Same spec-or-spike-
before-code discipline that made Phases 3a/3b/3d ship cleanly.

---

## 6. Card layout direction (4C — sketch only, finalized after 4A)

The reconciliation snapshot sits in the Per-Statement card as a continuation of
the Phase 3b bookkeeping-first body. Rough placement (to be refined in 4C):

```
[Card head: filename, agent, engine pill, status, download, chevron]
[3-tile headline: Parsed · Included · Excluded]          ← Phase 3b
[Activity Classification line]                            ← Phase 3b
[Vendor / 1099 Review compact row]                        ← Phase 3b
[Review summary strip]                                    ← Phase 3b
[NEW: Statement Reconciliation Snapshot]                  ← Phase 4
   beginning → +deposits → −withdrawals → −checks
   → −transfers → −fees → calculated ending
   vs reported ending  →  [Balanced ✓]  or  [Needs Review ⚠ — diff $150.00]
```

Visual states:
- **Balanced** → green check, calm styling
- **Needs review** → amber/red, difference shown prominently
- **Unavailable** → muted "Reconciliation not available for this statement"
  (rule-based engine, or fields not extracted) — never a fabricated balance

This is layout DIRECTION, not final markup — 4C designs the real block once 4A
confirms the data is trustworthy.

---

## 7. What Phase 4 explicitly does NOT do

- No 1099 filing priority (shelved → App 2A).
- No NEW Per-Statement section — this CONTINUES the Phase 3b card remake.
- No master_excel_generator.py changes (4D touches the per-statement Excel only).
- No multi-currency, no statement-type auto-detection beyond what extraction provides.
- No automatic correction of unbalanced statements — PREPARE flags, the human decides.

---

## 8. Session plan

1. ✅ Test statements + ground truth key (`phase4_test/`).
2. **This spec → your review.** Lock the snapshot structure (§2), threshold (§3),
   and 4A criteria (§4).
3. **Execute 4A** (you, locally): extend the PDF Skill prompt, run on the 3
   statements ×3 for stability, score against the key, return GO/NO-GO findings.
4. **If GO**: proceed 4B → 4C → 4D → 4E, one at a time, complete drop-ins + Path A
   verification each step.
5. **If NO-GO**: document, and the snapshot is shelved/gated. No 4B–4E.
6. Append Phase 4 entry to `devlog/status/V1_2_STATUS.md` as it progresses.
