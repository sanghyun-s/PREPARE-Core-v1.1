# PREPARE — 1099 Pre-Reconciliation Workspace

PREPARE is a bookkeeping tool that turns bank and credit card statement PDFs into accountant-ready 1099 pre-reconciliation deliverables. It extracts every transaction, classifies which rows are 1099-relevant, normalizes vendor names across statements, and produces an Excel workbook that a CPA can open and review without having to touch the original PDFs again.

It is not a 1099 filer. It is the step that comes before filing — the messy reconciliation work that has to happen before any 1099 form can be confidently produced. The deliverable is a workbook, not a tax return.

The app is a FastAPI backend with a single-page vanilla-JS frontend. The current production extraction engine uses Anthropic's PDF Skill (Claude Agent SDK calling Sonnet or Opus). Two earlier extraction engines (rule-based and multi-agent) remain in the codebase as fallbacks and as comparison baselines.

---

## What problem is this solving

Most bookkeeping software handles transactions that arrive through bank feeds or are already in a structured format. The 1099 problem is harder because:

- **The source data is PDFs.** Bank statements arrive as monthly PDFs, often laid out in non-uniform ways across institutions. Vendor names are inconsistent across statements ("Verizon Wireless — Business Mobile Plan" vs "Verizon Wireless — Mobile Fleet"), and amounts can be split across categories the statement summarizes differently.
- **The threshold logic is per-payee, not per-transaction.** A vendor paid $500 in March and $250 in October crosses the $600 1099 threshold for the year, but no single statement shows that. The work is fundamentally cross-statement.
- **Mistakes have real consequences.** Filing 1099s for payees who shouldn't get one (or missing payees who should) creates IRS correspondence, penalties, and rework. The deliverable has to be defensible.

PREPARE attacks all three problems: PDF extraction with row-level classification, vendor normalization across statements, and explicit cross-statement validation surfaces (matches, name variants, near-threshold review, discrepancy alerts).

---

## What you get when you run it

Upload one to ten PDFs through the web UI. The app processes them sequentially (one statement per "Agent"), then presents four views:

**Workspace** — run-level KPIs, processing summary, cross-statement reconciliation roll-up, master workbook download.

**Per-Statement Review** — one card per statement. Each card answers a single question: *is this individual statement parsed, reconciled, and extraction-complete?* The expanded view shows the bookkeeping summary, a full Source A reconciliation waterfall (beginning balance → activity → calculated ending vs reported ending), and a one-line Source B status (extraction completeness).

**Consolidated Validation** — the cross-statement command center. Cross-statement vendor matches, name variant flags (Levenshtein-based), discrepancy alerts, near-threshold vendors. This is where review work happens at the batch level.

**Technical Details** — engine, model, processing time, total cost in USD.

Plus the deliverable: a five-sheet Excel workbook (Executive Summary, Master Vendor Summary, Validation Report, All Transactions, Per-Agent Summary) and per-statement Excel files with full audit trail including row-level transaction classification.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Web UI (vanilla JS)                     │
│       Workspace · Per-Statement · Consolidated · Technical       │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────┐
│                   FastAPI server (server.py)                     │
│     /api/process — orchestrates extraction, validation, output   │
│     /api/download/{file_id} — serves generated workbooks         │
└──┬──────────────────────────────────────────────────────────┬───┘
   │                                                          │
   │ run_pipeline_pdf_skill (per statement)                   │ run_engine
   │                                                          │ (legacy)
┌──▼─────────────────────────┐    ┌─────────────────────────▼───┐
│ pipeline.py                │    │ agent_app.py                │
│  - PDF Skill extraction    │    │  - rule_based engine        │
│  - vendor normalization    │    │  - multi_agent engine       │
│  - _compute_reconciliation │    │                             │
│  - _compute_source_b       │    │                             │
└──┬─────────────────────────┘    └─────────────────────────┬───┘
   │                                                        │
┌──▼────────────────────────────────────────────────────────▼───┐
│ validation_engine.py — deterministic cross-statement validation │
│ vendor_classifier_1099.py — 1099 eligibility rules              │
│ review_flag_engine.py — per-statement review signals            │
└──┬─────────────────────────────────────────────────────────────┘
   │
┌──▼─────────────────────────────────────────────────────────────┐
│ excel_generator.py            master_excel_generator.py         │
│ Per-statement workbooks       Five-sheet master workbook        │
└─────────────────────────────────────────────────────────────────┘
```

The response is a single typed payload (`schemas.py:ProcessResponse`) covering summary KPIs, per-statement results, validation findings, the workbook reference, and technical metadata. The frontend renders strictly against this contract.

---

## Two integrity checks per statement

The v1.4 release added two orthogonal statement-integrity signals that run on every PDF Skill statement:

**Source A — Reconciliation snapshot.** *"Does the statement's stated math balance?"* PDF Skill transcribes the seven balance-summary fields (beginning balance, total deposits, total withdrawals, checks, transfers, fees, reported ending balance). The pipeline computes the implied ending balance and compares. Status: `balanced` / `needs_review` / `unavailable`.

**Source B — Extraction cross-check.** *"Did we extract every row the statement reported?"* The pipeline buckets every extracted transaction by `transaction_type`, sums the buckets, and compares against the snapshot's stated activity totals. Status: `complete` / `incomplete` / `unavailable`.

These are genuinely orthogonal. Source A fails when the statement itself has a math error (bank's mistake). Source B fails when extraction lost a row (our mistake). A CPA reviewing the deliverable can distinguish *"the bank made an error"* from *"the extraction missed something — verify the source"* from *"this statement is clean."*

The bucket mapping (locked after a real-statement spike, verified against three test PDFs):

```
deposits     ← deposit + interest + reimbursement
withdrawals  ← vendor_payment              (NOT checks, NOT fees)
checks       ← check_payment
transfers    ← transfer + owner_draw
fees         ← bank_fee
```

Withdrawals contains `vendor_payment` only because bank statements summarize checks and fees on separate lines. Folding checks into withdrawals would have flagged every check-itemizing statement as incomplete with a non-zero checks delta. The spike confirmed every real test PDF behaved correctly with this mapping.

---

## Information architecture

This took several iterations to get right and is worth describing because it's where the design judgment lives:

**Per-Statement view** answers: *is this individual statement parsed, reconciled, and extraction-complete?* Only statement-local data. Source A waterfall + Source B status as paired integrity signals. The view does NOT carry full cross-statement tables — instead, a compact pointer line: *"Cross-statement signals involving this statement: X findings · View in Consolidated Validation."*

**Consolidated Validation** answers: *across all statements, what vendor/name/1099 issues need review?* Single source of truth for cross-statement findings: vendor matches, name variants, near-threshold vendors, discrepancy alerts. The full tables live only here.

**Excel workbooks** are the audit-ready evidence package. Per-statement workbooks contain full row-level classification, the Source A waterfall, the Source B bucket table (stated / row_sum / delta per bucket), and review signals. The master workbook aggregates across statements with the same integrity signals plus the top-vendors and validation roll-ups.

The split matters because the alternative — putting everything everywhere — produced cards that worked at three statements but would have been unreadable at ten. The same name-variant data used to render four times across the UI (once inside each per-statement card + once in Consolidated). After Phase 5A, it renders once.

---

## Running it

### Prerequisites

- Python 3.10+
- An Anthropic API key (for the PDF Skill engine)
- The Claude Agent SDK (used by PDF Skill)

### Setup

```bash
git clone <repo-url>
cd PREPARE_app_v.1.0

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

# Add your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### Run

```bash
uvicorn server:app --port 8000
```

Open `http://localhost:8000`. The web UI is the upload screen. Add up to 10 PDFs, optionally upload a known-vendor CSV for better name matching, choose an engine (PDF Skill recommended), and click Process.

### Engine selection

- **PDF Skill** (recommended for production): Claude Agent SDK with Sonnet or Opus. ~$0.12–0.60 per PDF depending on length. Highest accuracy. 1–4 minutes per statement.
- **Rule-based**: Free, fast, zero AI dependency. Lower accuracy on non-uniform layouts. Useful as a fallback when API budget matters more than accuracy.
- **Multi-agent**: Legacy comparison baseline. Three Claude calls per statement (extraction + normalization + classification) instead of one consolidated PDF Skill call. Kept for evaluation but superseded by PDF Skill in v1.3.

### Output

After processing, the workspace shows the four views. Excel workbooks are downloadable from the Workspace page (master workbook) or each per-statement card (per-statement workbooks).

---

## What's in v1.4

This release added the two integrity checks (Source A and Source B), restructured the information architecture into the Per-Statement / Consolidated / Excel split described above, and shipped Phase 5 polish (per-statement card slimming, Consolidated framing, Excel alignment verification).

**Five-day shipping log** (May 27 – May 29, 2026):

| Date | Work |
|---|---|
| May 27 | Source A reconciliation snapshot landed across all four surfaces. Per-statement waterfall restored after a server snapshot-carry bug was caught and fixed. Frontend Phase 4C waterfall was missing from live and rebuilt. |
| May 28 | Source B extraction-completeness check built across five files (pipeline, schemas, server, per-statement Excel, master Excel). Bucket mapping locked after spike. Real-test verification on three PDFs plus synthetic incomplete edge case. |
| May 29 (5A) | Per-statement card information-architecture cleanup. Removed redundant breakdowns and Group C tables, added Source B web surface, collapsed cross-statement tables to a pointer line. |
| May 29 (5B) | Consolidated Validation framing strengthened. Subtitle rewrite, "Statements analyzed" scope line. |
| May 29 (5C) | Excel alignment review pass. No code changes needed — workbooks were already aligned with the new architecture. |

The full per-day devlogs are in `V1_4_STATUS_*.md`.

---

## Project layout

```
PREPARE_app_v.1.0/
├── server.py                    # FastAPI app, /api/process, /api/download
├── schemas.py                   # Pydantic models — the response contract
├── pipeline.py                  # PDF Skill orchestration + Sources A & B
├── agent_app.py                 # Legacy engines (rule_based, multi_agent)
├── pdf_skill_adapter.py         # Claude Agent SDK wrapper
├── pdf_skill_prompt.md          # The extraction prompt
├── validation_engine.py         # Deterministic cross-statement validation
├── vendor_classifier_1099.py    # 1099 eligibility rules
├── review_flag_engine.py        # Per-statement review signals
├── backend/
│   ├── excel_generator.py       # Per-statement workbook generator
│   └── master_excel_generator.py # Master workbook generator
├── frontend/
│   └── index.html               # Single-page UI (vanilla JS)
├── V1_4_STATUS_*.md             # Per-day devlogs
└── README.md                    # This file
```

---

## What this isn't

A few things to set expectations honestly:

- **Not a 1099 filer.** PREPARE produces the workbook a CPA reviews before filing. The filing itself is a separate problem (IRS form generation, e-file integration, payee TIN collection). That's a planned separate app, not part of this one.
- **Not a general-purpose bank statement parser.** The extraction is tuned for 1099-relevant transaction types. Other categories (balance lines, payroll, metadata) are recognized but excluded from the deliverable. The audit trail preserves them in the per-statement workbook.
- **Not a fully autonomous agent.** Every output is meant to be reviewed. The deliverable surfaces review flags aggressively (over-$600 vendors, near-threshold vendors, name variants, extraction discrepancies, balance mismatches). The human decides what to file.
- **Not cheap to run at scale.** PDF Skill costs add up. A small accounting practice with 50 client statements per quarter will spend $30–60 in API costs per quarter. Larger volumes need the rule-based engine for non-critical statements, or a cost-optimization pass.

---

## Design notes worth surfacing

A few decisions that shaped how the code is organized:

**Arithmetic-in-one-place.** Both Source A and Source B do their arithmetic in `pipeline.py` (`_compute_reconciliation`, `_compute_source_b`). The Excel renderers and the frontend display whatever the pipeline computed. This avoids the bug class where the web UI's "calculated balance" disagrees with the workbook's because the same math was duplicated in two places.

**Carry-or-drop pattern.** Computed fields like `reconciliation_snapshot` and `extraction_check` have to be explicitly carried through every layer (`pipeline` return dict → `server.agent_outputs.append` → `Statement` model construction). Forgetting to carry a field at any layer drops it silently. This bit us once for `reconciliation_snapshot` (one missing line caused every statement to show "Unavailable") and was the first thing checked when shipping Source B.

**Diagnostic-before-edit.** The codebase has multiple workspace backup files alongside live code. After a near-miss on May 27 where a stale backup got edited instead of the live file, the working pattern became: grep for the exact symbol you're about to change in the actual current file, confirm uniqueness and context, then apply patches with grep-anchored str_replace. Patch-by-patch with verification between each. Slower than batched edits, robust against the stale-file slip.

**Backward compatibility on optional fields.** Newer fields (`reconciliation_snapshot`, `extraction_check`) are `Optional` on the Pydantic model and the frontend's render functions return empty strings when the field is missing. Older response payloads, the rule-based engine, and the multi-agent engine all coexist with the new fields without crashes or empty UI elements.

**Schema versioning.** `schemas.py:SCHEMA_VERSION` is read by the frontend on every response and logged as a console warning on mismatch. Currently `"1.0"`. A future breaking change to the response contract bumps this; clients can branch on it without guessing.

---

## License

[To be added.]

## Acknowledgments

Built in collaboration with Claude (Anthropic) using Claude Code and the Claude Agent SDK. The reconciliation methodology and information architecture iterated through real test PDFs and several rounds of "this works at 3 statements but would break at 10."
