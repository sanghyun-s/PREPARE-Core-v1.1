# PREPARE Core v1.3 — Release Notes

**Release**: v1.3
**Date**: May 2026
**Type**: Major feature release — production extraction engine + bookkeeping-first UX

---

## Summary

v1.3 turns PREPARE from a transaction-extraction tool into a bookkeeping review aid for 1099 preparation. The release lands two major changes:

1. **Anthropic PDF Skill** becomes the production extraction engine, delivering row-level transaction classification that earlier engines couldn't produce reliably.
2. **The Per-Statement Review interface** is reframed around bookkeeping concepts — included payments, excluded rows with reasons, and review signals — instead of generic transaction counts. This reframing culminates in the Phase 3b card redesign, which leads each statement card with the bookkeeping question (*how did this PDF parse*) before the 1099 question.

The result is an Excel workbook and a web interface that read like work product an accountant would actually use, not like the output of a script that happened to find some numbers.

---

## What's new

### 1. PDF Skill extraction engine (production default)

The v1.3 backbone is the Anthropic Claude Agent SDK invoking the pre-built `pdf` Skill. For each statement, the agent produces structured output including row-level fields that earlier engines lacked:

- `transaction_type`: vendor payment, payroll deposit, balance line, transfer, fee, etc.
- `include_for_1099`: whether the row should feed 1099 aggregation
- `exclusion_reason`: plain-English explanation when a row is excluded
- `review_required`: row-level flag for human verification

These per-row signals are what make the bookkeeping-first UI possible. Without them, you can show transaction counts; with them, you can show "31 included as vendor payments; 8 excluded (6 payroll deposits, 2 balance lines)" — the language an accountant uses.

Test corpus performance:
- Determinism: same input → same output across repeated runs
- Sonnet vs Opus: identical accuracy on test corpus; Sonnet ~3× cheaper, picked as default
- Cost: $0.20–0.60 per PDF depending on layout complexity
- Time: 1–4 minutes per PDF (Sonnet); 1–1.5 minutes per PDF (Opus)
- Failure handling: structured `ExtractionFailed` result with retry on subprocess errors; fallback to rule-based engine available

### 2. Bookkeeping-first Per-Statement Review

The per-statement card has been reorganized around what an accountant needs to verify before filing:

- **Headline KPI** ("Included Payments") is the count of vendor payments aggregated for 1099 review — not the raw extracted transaction count
- **Activity line** below the metrics shows "39 parsed · 31 included · 8 excluded" so the bookkeeping decision is visible at a glance
- **Status detail** uses natural language: "Statement parsed successfully with PDF Skill. 39 rows identified. 31 included as vendor payments for 1099 aggregation. 8 excluded (6 payroll deposits, 2 balance lines)."
- **Statement Activity Breakdown** showing the full type breakdown — every row type, with counts, derived from PDF Skill row classification

For rule-based engine runs, the UI gracefully falls back — no fabricated breakdown, no inflated language. Honest about what each engine can produce.

(Note: the Phase 2 layout described above was further restructured in Phase 3b — see item #5 below for the final card design.)

### 3. Master Workbook quality

Five polish items shipped in Phase 3a closed long-standing rough edges in the consolidated Excel deliverable:

| Item | Before v1.3 | After v1.3 |
|---|---|---|
| Executive Summary KPI #1 | "Total Transactions" | "Included Payments" (matches WebUI) |
| Validation Report column E | Width 20, "Per-Statement Breakdown" text clipped | Width 60 with adaptive row height |
| Master Vendor Summary Review Reasons | Internal UUIDs like `29f0b14961914821ae5624686f0321a0.pdf` | Original filenames like `sample_bank_multicolumn.pdf` |
| Included Payments KPI count | Counted all rows including excluded | Filters out excluded rows (PDF Skill engine) |
| Name Variant Flags | Symmetric duplicates (5 entries for 3 underlying pairs) | Deduplicated to canonical alphabetical-first ordering |

The workbook now reads cleanly across all 5 sheets:
- Executive Summary
- Master Vendor Summary
- Validation Report
- All Transactions (with `Excluded?` column distinguishing included vs excluded rows)
- Per-Agent Summary (with real cost per statement)

### 4. Cross-statement validation surfaces real signal

The validation engine output is now meaningful for accountants doing 1099 preparation. On a 2-PDF test (sample_bank_multicolumn.pdf + sample_bank_3col_clean.pdf), the validation surfaces:

- **10 Cross-Statement Matches** — vendors paid across multiple statements with combined totals
- **3 Name Variant Flags** — fuzzy-matched payee names that may be the same vendor (e.g., "Home Depot ~ Office Depot")
- **1 Discrepancy Alert** — same vendor with suspiciously different totals across statements (Verizon Wireless $189 vs $1,134, 6.0x ratio)
- **2 Near-Threshold Vendors** — vendors within $100 of the $600 1099 threshold

Each finding is a real accountant decision: file a 1099? Verify the payee? Investigate the discrepancy? The validation report supports the work instead of just listing data.

### 5. Bookkeeping-first card redesign (Phase 3b)

Phase 3b completed the bookkeeping-first reframing at the visual-hierarchy level. The Per-Statement Review card was restructured to lead with *how the PDF parsed* before *the 1099 metrics*, which are preserved but made visually subordinate.

The card body, top to bottom:

- **3-tile headline** — Parsed (rows seen) · Included (rows feeding 1099 aggregation) · Excluded (rows filtered out, e.g. payroll deposits, balance lines). The central reconciliation triad, at a glance.
- **Activity Classification line** — type breakdown directly below the headline (e.g. "Vendor payments 31 · Payroll deposits 6 · Balance lines 2"), present types only, in canonical order.
- **Vendor / 1099 Review row** — the previous 5-metric layout (Included Payments, Vendors, Total Amount, Review Needed, Confidence) condensed into a single compact line below the activity classification (e.g. "Included Total $14,582.61 · Vendors 15 · Review Needed 11 · Over $600 8 · Confidence 97%"). Review Needed renders red when greater than zero, neutral when zero — preserving prior muscle memory.
- **Review summary strip** — unchanged.

The expanded card's first group was renamed "Statement-Level Bookkeeping Summary" → "Statement Processing Details" and simplified; the natural-language status detail is preserved while a redundant breakdown table and helper note were removed (the always-visible activity classification now carries that information).

For the rule-based engine, the Excluded tile renders an em-dash with a tooltip and the activity classification shows an italic fallback message. No backend, API, schema, or Master Workbook changes — Phase 3b was a pure-frontend restructure verified across PDF Skill (clean + mixed) and rule-based engines, and responsive down to 412px.

---

## How to choose an engine

v1.3 ships three engines in the configuration dropdown. The choice matters; here's the guide.

### PDF Skill (Recommended — production default)

Choose this when accuracy and bookkeeping detail matter. This is the v1.3 production default.

- **Cost**: ~$0.20–0.60 per PDF
- **Time**: 1–4 minutes per PDF (Sonnet) or 1–1.5 min (Opus)
- **Strengths**: Highest accuracy on multi-column and complex layouts. Produces row-level `transaction_type` and `include_for_1099` classification — the foundation of all bookkeeping UI features.
- **Caveats**: Requires `ANTHROPIC_API_KEY` set. Cost grows with PDF count. Slight run-to-run variation on edge rows (e.g., header-like rows occasionally classified as `metadata` vs not).

### Rule-based (Fast, free fallback)

Choose this when you need a quick free run or when PDF Skill fails.

- **Cost**: $0 per PDF
- **Time**: ~5 seconds per PDF
- **Strengths**: Deterministic, fast, no API dependency. Useful for testing, demos, and as a fallback when PDF Skill encounters subprocess failures.
- **Caveats**: Limited bookkeeping detail. Does not produce row-level `excluded` classification — its "Included Payments" count includes all extracted rows (e.g., 76 instead of 68 on the 2-PDF test). This asymmetry is documented and queued for v1.4 convergence work.

### Multi-agent (Experimental — legacy comparison)

Available for A/B comparison during the v1.3 → v1.4 transition. Will be deprecated in v1.4.

- **Cost**: Variable depending on model used
- **Time**: 10–60 seconds per PDF
- **Use case**: Side-by-side comparison with PDF Skill for users still investigating the migration. Not recommended for production use.

---

## Known limitations

These are honest call-outs, not gotchas. Each has a path forward in v1.4.

### PDF Skill run-to-run variation on edge rows

On the same input PDF, PDF Skill (Sonnet) occasionally classifies "TABLE COLUMN HEADER ROW"-style rows as `metadata` in one run and skips them in another. Vendor payment classification is consistent across runs; only edge-case non-vendor rows show variation.

**Impact**: Excluded row count may differ by 1-2 across re-runs of the same PDF.
**Workaround**: For audit work where absolute determinism matters, use rule-based engine.
**v1.4 plan**: Add a prompt-tuning pass for header/metadata classification stability.

### Rule-based engine "Included Payments" count includes excluded rows

PDF Skill engine output dicts carry an `excluded` flag per transaction; rule-based engine dicts (built via the legacy `agent_app.run_engine()` path) do not. The Master Workbook KPI value matches WebUI for PDF Skill (68 on the 2-PDF test) but shows the unfiltered count for rule-based (76 on the same test).

**Impact**: Rule-based master workbook overcounts "Included Payments" by the number of payroll/balance/excluded rows.
**Workaround**: Use PDF Skill for production runs; treat rule-based as a fast-fallback for verification rather than for final deliverables.
**v1.4 plan**: Extend `excluded` flag tagging into the rule-based pipeline serialization point in `agent_app.py`. Estimated 1-2 hours of work.

### Long extractions stress the current loading UX

PDF Skill takes 1–4 minutes per PDF (a 2-PDF batch runs ~5 minutes sequentially; a 5-PDF batch can take 5–20 minutes). The current loading spinner provides general progress but not per-statement detail or estimated time remaining.

**Impact**: User uncertainty during long batches.
**Workaround**: Watch the server terminal — it logs each PDF as it completes.
**v1.4 plan**: Per-statement progress display ("Processing 2 of 5 — sample_bank_multicolumn.pdf · 45s elapsed"), estimated time remaining, optional cancel button.
**Post-v1.4 (optional)**: Latency *reduction* (distinct from the UX work above) by parallelizing per-PDF extraction — concurrent agent calls instead of sequential — which could roughly halve wall-clock time for multi-PDF batches. Deferred because it touches the server concurrency model and introduces parallel-API-call cost-spike risk.

### Agent SDK subprocess failures (rare, transient)

During Track 2 prototype testing on May 11, 2026, the Claude Agent SDK subprocess failed at startup on every PDF in a batch run. The same shell had been running successful batches earlier that day. Root cause unconfirmed; suspected interaction between Claude.app desktop client and the Agent SDK's underlying CLI binary.

**Impact**: When this occurs, all PDFs in the batch fail with `agent_subprocess_failed`.
**Workaround**: One automatic retry built into the adapter. If both attempts fail, switch to rule-based engine. Closing Claude.app may help.
**v1.4 plan**: Per-PDF try/except in `server.py` so one failure doesn't kill the batch (item D1).

---

## Architecture summary

v1.3 adds one new module (`pdf_skill_adapter.py`) and modifies several existing files (`pipeline.py`, `server.py`, `schemas.py`, `frontend/index.html`, plus Phase 3a polish in `master_excel_generator.py` and `validation_engine.py`). The adapter is the only new code; everything downstream of extraction (aggregation, normalization, validation, workbook generation) is structurally unchanged.

```
Frontend upload
      ↓
server.py /api/process
      ↓
  ┌─ engine == "pdf_skill" → pipeline.run_pipeline_pdf_skill()
  │                              ↓
  │                          pdf_skill_adapter.py (NEW)
  │                              ↓
  │                          ExtractionResult (success | failed)
  │
  └─ engine == "rule_based" → pipeline.run_pipeline() (UNCHANGED)
                                  ↓
                              pdfplumber + regex extractor
                              
            ↓ (both paths)
            
transaction_classifier → vendor_normalizer → transaction_aggregator
            ↓
validation_engine → master_excel_generator → response to frontend
```

The frontend changes span two layers: the Phase 2 label rework (presentation-only terminology) and the Phase 3b card redesign (visual-hierarchy restructure of the Per-Statement card — 3-tile bookkeeping headline, activity classification, demoted vendor/1099 row). Both are pure-frontend; no data schema changes were required beyond the Phase 1 additions (`engine_used`, `bookkeeping_breakdown`, `excluded_count` on the `Statement` model). The master workbook polish (A1, A2, A3, A5, B1) landed in `master_excel_generator.py` and `validation_engine.py`.

---

## ✅ Shipped in v1.3 — see Phase 3b section below

Items captured during v1.3 development as candidates for the next release:

1. **Master Vendor Summary tag canonicalization (A4)**. Reformat the "Review Reasons" column from verbose prose to short canonical tags (`LOW_NAME_MATCH`, `ENTITY_UNKNOWN`, `CLASSIFIER_FLAG`, `MULTI_VARIANT`, `NAME_VARIANT`, `NEAR_THRESHOLD`, `AMOUNT_DISCREPANCY`), with the detail moved to a separate column or tooltip. Originally scoped into Phase 3b but deferred — the card redesign shipped without it.

2. **Engine convergence**. Extend `excluded` flag tagging to rule-based and multi-agent engine outputs via `agent_app.py`. Bring all engines to the same transaction-serialization contract so "Included Payments" KPI matches across engines.

3. **Long-extraction UX**. Per-statement progress display, estimated time remaining, cancel button, server-side processing with check-status pattern so user doesn't need to keep the browser tab open.

4. **Cost transparency**. Estimated cost preview on upload screen ("Estimated cost: $1.50–$2.50 for 5 statements"). Per-statement cost in card footer.

5. **Test corpus expansion**. Grow from 2 ground-truth-annotated PDFs to 8–10, covering more layout types (single-column, multi-column, credit card statements, scanned PDFs).

6. **Robustness**. Per-PDF try/except in `server.py` so a single PDF crash doesn't kill the batch. Optional automatic engine fallback (PDF Skill failure → rule-based retry) gated by user preference.

7. **Deprecate multi-agent engine**. v1.3 kept it for A/B comparison during transition. Remove from dropdown in v1.4.

---

# Phase 3b — Per-Statement Card Redesign (v1.3)

The Per-Statement Review card was redesigned to lead with the bookkeeping
question — *how did this PDF parse* — before the 1099 question. The
1099 / vendor metrics remain visible but are now visually subordinate.

## What's new

**A 3-tile headline at the top of each card.** Parsed (rows seen by the
parser) · Included (rows that fed 1099 aggregation) · Excluded (rows
filtered out, e.g. payroll deposits, balance lines). At a glance, the
accountant can see whether the parser saw what they expected and how
much was set aside as non-1099 activity.

**Activity Classification line.** When the PDF Skill engine is used, the
card now shows the type breakdown directly below the headline — for
example, "Vendor payments 31 · Payroll deposits 6 · Balance lines 2".
Only present types are shown, in canonical order.

**Demoted Vendor / 1099 Review row.** The previous 5-metric layout
(Included Payments, Vendors, Total Amount, Review Needed, Confidence)
has been condensed into a single compact line below the activity
classification: "Included Total $14,582.61 · Vendors 15 · Review Needed
11 · Over $600 8 · Confidence 97%". Review Needed continues to render
in red when greater than zero, neutral when zero — matching the prior
muscle memory.

**Cleaner expansion.** The expansion area's first group was renamed from
"Statement-Level Bookkeeping Summary" to "Statement Processing Details"
and simplified. The natural-language status detail — for example, "39
rows identified. 31 included as vendor payments for 1099 aggregation. 8
excluded (6 payroll deposits, 2 balance lines)." — is preserved.
Redundant tables and helper notes that the always-visible card body now
covers have been removed.

## Engine fallback

For the rule-based engine (no row-level classification), the Excluded
tile renders an em-dash with a hover tooltip explaining that row
classification is available with the PDF Skill engine. The activity
classification section shows an italic fallback message. All other card
content is unchanged.

## Compatibility

No backend changes. No API or schema changes. No changes to the Master
Workbook deliverable. The Workspace and Consolidated Validation views
are also unchanged.

## What's not in this release

The Statement Reconciliation Snapshot (balance reconciliation) and a
1099 Review Priority Summary in the Master Workbook are planned for a
later release.


### Beyond v1.4

- **1099 Review Priority Summary** in the Master Workbook (High / Medium / Low) and a sharpened Per-Statement vs Consolidated lens distinction. Captured in a separate recalibration proposal; needs a priority-classification rule table and a W-9 data source before implementation.
- **Statement Reconciliation Snapshot** (balance reconciliation) — requires backend schema changes.
- **Latency reduction** (parallel per-PDF extraction) — see Known Limitations.
- **Product rebrand** (PREPARE → PREP) — bundled with the reengineering pass.

---

## Verification & test coverage

v1.3 was developed against a small but realistic 2-PDF test corpus:

- `sample_bank_multicolumn.pdf` — multi-column layout with 31 vendor payments, 6 payroll deposits, 2 balance lines (totaling 39 parsed rows)
- `sample_bank_3col_clean.pdf` — clean 3-column layout with 37 vendor payments (no excluded rows)

Combined, these exercise the cross-statement validation paths (10 matches, 3 name variants, 1 discrepancy, 2 near-threshold findings) and the single-engine bookkeeping classification paths.

Test runs through v1.3 development (~$3.40 in API cost on Sonnet):
- Phase 1: master workbook contract fix on PDF Skill + rule-based
- Phase 2: 7-change frontend rework, regression on both engines, single-PDF and 2-PDF
- Phase 3a: master + validation polish, regression on rule-based (free) + one PDF Skill confirmation
- Phase 3b: bookkeeping-first card redesign across three sessions (C1 → C1b → C2), regression on both engines + responsive verification (~$1.20)

Total cumulative spend including the Track 2 prototype: ~$9.05. Within the development budget projected in V1_3_PLAN (~$5–7 development + ~$4.45 prototype).

---

## Acknowledgments

This release benefited from external review and design input on the bookkeeping-first reframing approach. The Phase 3 plan that drove Phase 3a and Phase 3b is in `V1_3_PHASE3_PLAN.md`; the Phase 3b design spec is in `V1_3_PHASE3B_DESIGN_SPEC.md`. Internal status journal: `V1_2_STATUS.md`. Original integration plan: `V1_3_PLAN.md`.

---

*This document is the public-facing summary of v1.3. For implementation details, see the docstring changelogs at the top of `master_excel_generator.py` and `validation_engine.py`.*
