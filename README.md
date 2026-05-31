# PREPARE — Bookkeeping Reconciliation Aid for 1099 Pre-Review

PREPARE turns messy bank and credit card statement PDFs into classified, reconciled, review-ready accounting evidence. It is built for the work that happens *before* a 1099 form gets filed: extracting every transaction, classifying which rows are 1099-relevant, normalizing vendor names that vary across statements, verifying that each statement's stated math balances, confirming that extraction captured every row the statement reported, and producing an Excel workbook a CPA can review without going back to the source PDFs.

PREPARE is the first shipped app in the **Accounting Meets AI** portfolio — a five-app suite of connected accounting workflow tools, each tackling a specific stage of real accounting work: reconciliation (PREPARE), Q&A and analytics (CoReckoner), audit risk detection (AI Audit Risk Analyzer), and two upcoming tax-form-focused tools (IRS Form Processor, Vendor-to-Schedule Classifier). All five share a common foundation (TAU), and each is designed to stand alone while interoperating with the others.

It is deliberately not a 1099 filer. The deliverable is a workbook, not a tax return.

The codebase is a FastAPI backend with a single-page vanilla-JavaScript frontend. The production extraction engine uses Anthropic's PDF Skill (Claude Agent SDK with Sonnet by default; Opus available as an advanced option). Two earlier engines — rule-based and multi-agent — remain in the codebase as fallbacks and as comparison baselines.

---

## What PREPARE actually solves

Most bookkeeping software assumes structured data: bank feeds, CSV exports, ledger entries that already know what they are. The 1099 problem starts further upstream and is harder for three concrete reasons.

The first reason is that source data arrives as PDFs. Bank and credit card statements are monthly documents with layouts that vary by institution. Vendor names are inconsistent across statements (Verizon Wireless — Business Mobile Plan in one PDF, Verizon Wireless — Mobile Fleet in another), and amounts can be split across categories that different statements summarize differently. A naive parser can pull text reliably; an *accounting-aware* parser has to decide what each row means.

The second reason is that 1099 logic is per-payee, not per-transaction. A vendor paid $500 in March and $250 in October crosses the $600 1099 threshold for the year — but no single statement shows that. The work is fundamentally cross-statement.

The third reason is that mistakes have IRS consequences. Filing 1099s for payees who shouldn't receive one, or missing payees who should, creates correspondence, penalties, and rework. The pre-filing deliverable has to be defensible row by row.

PREPARE attacks all three: PDF extraction with row-level classification, vendor normalization across statements, and explicit cross-statement validation surfaces (matches, name variants, near-threshold review, discrepancy alerts).

---

## The development arc — and why it matters for what PREPARE became

PREPARE did not start with the architecture it has now. The shape of the app emerged from a sequence of design decisions, several of which involved removing functionality rather than adding it. The arc is worth understanding because it explains why the final structure looks the way it does.

**Started as a feature inside a larger AI hub.** The earliest version was one tab inside a broader experimental accounting tool (TAU — Transaction Agent Ultimate) alongside journal entry generators, term explainers, and session history. After mentor feedback it became clear the 1099 pre-review work was too independent — and too domain-specific — to live as a subfeature. It got pulled out into a standalone app. This was the first scope decision: not what to build, but where the boundary should be.

**Discovered the real problem wasn't PDF parsing.** The original extraction backbone was pdfplumber plus hand-tuned regex. It worked on well-formatted statements but missed the harder question: *what does each row mean?* A row marked $1,500 might be a vendor payment (1099-relevant), a payroll deposit (excluded), a balance line (not a transaction), a transfer (excluded), or a bank fee (excluded). A naive parser pulls all of them as "transactions" and the resulting 1099 candidate total is wrong. In one early benchmark, the rule-based engine reported 76 transactions across two statements; the correct count of included vendor-payment rows was 68. The 8-row gap was 6 payroll deposits and 2 balance lines.

The lesson was that this isn't a parsing problem. It's an accounting classification problem. The fix wasn't a better regex; it was an extraction layer that could tell *what kind of row* each row is.

**Switched to PDF Skill for row-level classification.** Claude PDF Skill (via Claude Agent SDK) returns more than just text: per row, it provides `transaction_type`, `include_for_1099`, `exclusion_reason`, `review_required`, `extraction_confidence`, and `source_text`. After this change, PREPARE could say not "39 transactions found" but "39 rows parsed, 31 included as vendor payments for 1099 aggregation, 8 excluded — 6 payroll deposits and 2 balance lines." That's not a small improvement; it's a different product. The app changed from a 1099 extractor into a statement activity classifier.

**Tested the model before adopting it.** PDF Skill didn't go straight into the production path. Prototype testing covered repeatability across identical inputs, accuracy comparison between Sonnet and Opus, behavior on bad and corrupted inputs, batch behavior with multiple PDFs, and structured failure handling. Haiku was dropped after batch instability. Sonnet became the default. This wasn't a feature-add — it was a deliberate replacement of the ingestion path with verified production characteristics.

**Stopped before becoming a 1099 filer.** At one point, the master workbook was sprouting features that drifted toward filing decisions: High/Medium/Low review priority, review tag taxonomies, W-9 status, entity-type risk. Each was plausible in isolation. Together they were a different product. PREPARE looks at bank and card statements; it doesn't have TIN information, W-9 forms, legal entity verification, or IRS form-field data. Adding filing-priority logic with that information missing meant offering plausible-but-untrustworthy judgments. The features were cut. Filing-priority work moved into a planned separate app (the IRS Form Processor). This was a scope cut, not a scope failure: drawing the boundary made PREPARE more trustworthy by making it answer only the questions it actually has evidence for.

**Added two orthogonal integrity checks.** Once row-level classification was working, the next question wasn't "what's in the statement?" but "is the statement reliable as a unit of bookkeeping evidence?" Two checks answer this independently — Source A and Source B, described in detail below. Together they let a reviewer distinguish *bank arithmetic errors* from *extraction errors* from *clean statements*. Most reconciliation tools collapse both failure modes into a single warning. PREPARE deliberately doesn't.

**Caught an accounting bug that wasn't a software bug.** During Phase 4, the excluded-row breakdown started double-counting check payments as excluded transactions. Software-wise, the count looked off. Accounting-wise, the problem was deeper: a check is a payment *method*, not an exclusion *category*. A check written to a contractor is an included vendor payment that may need a 1099. The fix wasn't to the counter; it was to the classification logic that decided what "excluded" means. This kind of bug — where the engineering problem is also an accounting problem — is the case for why projects like this need both backgrounds.

**Information architecture cleanup at the end.** After Source A and Source B both shipped functionally, the per-statement card on the web UI had become too dense. Parsed/included/excluded counts, activity classification, vendor metrics, reconciliation waterfall, extraction completeness, and cross-statement signals all competed for the same visual level. The fix was not to add more or to remove features, but to *put each piece of information on the surface where it belongs*. Per-Statement became the statement-integrity lens. Consolidated Validation became the cross-statement lens. The Excel workbooks stayed as the audit-ready evidence package. Same data, three surfaces, each answering a different question.

The arc, in one sentence: PREPARE evolved from a 1099 pre-review extractor into a bookkeeping reconciliation aid by repeatedly deciding what to remove or relocate, not just what to add.

---

## How the three surfaces differ — and why

The clearest way to understand PREPARE is to look at what each surface answers.

### Per-Statement view — the statement-integrity lens

The per-statement card answers exactly one question: *is this individual statement parsed, reconciled, and extraction-complete?*

What lives here:

- **Headline tiles**: how many rows the PDF Skill agent parsed, how many were included as vendor payments for 1099 aggregation, how many were excluded.
- **Activity classification**: the per-type breakdown (Vendor payments / Deposits / Bank fees / Checks / Transfers / and others).
- **Vendor and 1099 metrics**: included total, vendor count, review-needed count, over-$600 count, extraction confidence.
- **Statement Reconciliation (Source A)**: full waterfall from beginning balance through deposits, withdrawals, checks, transfers, and fees to a calculated ending balance, compared against the reported ending balance. Status is `balanced`, `needs_review`, or `unavailable`. When the statement's math doesn't balance, the model's verbatim observation about why is shown.
- **Extraction Cross-Check (Source B)**: a one-line status indicator — green ✓ "Extraction complete," amber ⚠ "Extraction incomplete — see workbook," or muted "Extraction check unavailable."
- **Review signals (Group B)**: the per-statement counts of review-needed and over-$600 vendors.
- **Cross-statement pointer**: a single line indicating how many cross-statement signals involve this statement, with a direction to Consolidated Validation for the detail.

What deliberately does *not* live here: the full cross-statement tables (name variants, near-threshold vendors, discrepancy alerts). At three statements, putting these inside each card produces redundancy. At ten statements it would produce eleven copies of the same data. The pointer line acknowledges the existence of the findings without duplicating them.

### Consolidated Validation — the cross-statement command center

This view answers a different question: *across all uploaded statements, what vendor, name, and 1099 issues need review?*

What lives here:

- **Statements analyzed** scope line at the top showing which files this view is computed over.
- **Cross-Statement Matches**: vendors appearing in two or more statements with a combined total that may cross the $600 threshold even when no single statement does.
- **Name Variant Flags**: pairs of vendor names that may be the same payee (Levenshtein-based similarity scoring).
- **Discrepancy Alerts**: vendors appearing in multiple statements with amounts that diverge in suspicious ways (extraction-quality signal).
- **Near-Threshold Vendors**: vendors close to (within $100 of) the $600 1099 threshold, useful for end-of-year review.
- **Master Workbook section** at the bottom: the consolidated deliverable, downloadable as Excel.

This is the *single home* for cross-statement findings. Per-Statement cards point here; the data lives only here.

### Excel workbooks — the audit-ready evidence

Two Excel deliverables are generated per run.

**Per-statement workbook** (one per PDF). Contains, on a Summary Stats sheet: statement processing details (prose summary), activity classification, vendor and 1099 review metrics, the Statement Reconciliation waterfall (Source A), the Extraction Cross-Check (Source B) with full bucket-level detail (stated value, row sum, delta), bookkeeping review signals, and scope notes pointing the user to the master workbook for cross-statement work. Also includes an All Transactions sheet with row-level audit trail including the `transaction_type` classification and the source text from the PDF.

**Master workbook** (one per run, five sheets). Executive Summary sheet contains key metrics, validation overview, the statement reconciliation roll-up ("X of N statements reconcile" plus Source B's "X of N show complete extraction"), and top vendors by payment amount. The remaining sheets — Master Vendor Summary, Validation Report, All Transactions, Per-Agent Summary — provide the cross-statement evidence base. This is what a CPA would open in Excel for review without ever needing to revisit the original PDFs.

The split between the three surfaces is not just a UI choice. It reflects what each surface is *for*: Per-Statement is "this statement is trustworthy as a unit," Consolidated is "across statements, here's what needs review," and Excel is "here's the full evidence behind both."

---

## The two integrity checks — Source A and Source B

This is the engineering decision that distinguishes PREPARE from a generic AI PDF extractor. Two questions get asked about every PDF Skill statement, independently.

### Source A — Reconciliation snapshot

The question: *does the statement's stated math balance?*

PDF Skill transcribes the seven balance-summary fields directly from the statement (beginning balance, total deposits, total withdrawals, checks, transfers, fees, reported ending balance). The pipeline then computes the implied ending balance and compares it to the reported one. Status: `balanced`, `needs_review`, or `unavailable`.

The design principle behind this is critical and worth naming: **Transcribe, Don't Compute.** PDF Skill reads numbers and labels them; the server does the arithmetic. This protects against the failure mode where an AI silently "fixes" a discrepancy by adjusting numbers until they balance. In live testing, one statement reported an ending balance of $4,000 but the arithmetic ($3,000 + $6,000 − $4,820 − $30) implied $4,150. PREPARE surfaces the $150 discrepancy and flags the statement for review rather than papering over it. Most reconciliation tools fail in the opposite direction; PREPARE is built to fail loudly and visibly.

### Source B — Extraction cross-check

The question: *did we extract every row the statement reported?*

The pipeline buckets every extracted transaction by `transaction_type` and sums the buckets. The sums are compared against Source A's stated activity totals. Status: `complete`, `incomplete`, or `unavailable`.

The bucket mapping (verified against three real test PDFs in a pre-production spike):

```
deposits     ← deposit + interest + reimbursement
withdrawals  ← vendor_payment              (NOT checks, NOT fees)
checks       ← check_payment
transfers    ← transfer + owner_draw
fees         ← bank_fee
```

Withdrawals contains `vendor_payment` only because bank statements summarize checks and fees on separate lines. Folding checks into withdrawals would have flagged every check-itemizing statement as incomplete with a non-zero checks delta. The spike confirmed every real test PDF behaved correctly with this mapping.

### Why orthogonal checks matter

Source A and Source B can fail independently. The combinations are diagnostically useful:

- **Source A balanced, Source B complete** → clean statement. Trust it.
- **Source A needs review, Source B complete** → the bank's stated math doesn't balance. The statement itself has an arithmetic issue. Verify against the source PDF.
- **Source A balanced, Source B incomplete** → the statement balances, but extraction missed at least one row. Re-extract or verify manually.
- **Both flag** → the statement and the extraction both need a closer look.

Most reconciliation tools collapse all of these into a single ambiguous warning. PREPARE tells the reviewer which of the four states each statement is in. This is more useful than higher accuracy on any single metric, because it tells the human *what to do next*.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Web UI (vanilla JS, single-page)              │
│      Workspace · Per-Statement · Consolidated · Technical        │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ JSON (ProcessResponse contract)
┌─────────────────────────────────▼────────────────────────────────┐
│                     FastAPI server (server.py)                   │
│      /api/process — orchestrates extraction, validation, output  │
│      /api/download/{file_id} — serves generated workbooks        │
└──┬──────────────────────────────────────────────────────────┬────┘
   │                                                          │
   │ run_pipeline_pdf_skill                                   │ run_engine
   │ (per statement)                                          │ (legacy paths)
┌──▼─────────────────────────┐    ┌─────────────────────────▼────┐
│ pipeline.py                │    │ agent_app.py                 │
│  • PDF Skill extraction    │    │  • rule_based engine         │
│  • vendor normalization    │    │  • multi_agent engine        │
│  • _compute_reconciliation │    │                              │
│    (Source A)              │    │                              │
│  • _compute_source_b       │    │                              │
│    (Source B)              │    │                              │
└──┬─────────────────────────┘    └─────────────────────────┬────┘
   │                                                        │
┌──▼────────────────────────────────────────────────────────▼────┐
│ validation_engine.py — deterministic cross-statement validation │
│ vendor_classifier_1099.py — 1099 eligibility rules              │
│ review_flag_engine.py — per-statement review signals            │
└──┬──────────────────────────────────────────────────────────────┘
   │
┌──▼──────────────────────────────────────────────────────────────┐
│ excel_generator.py              master_excel_generator.py        │
│ Per-statement workbooks         Five-sheet master workbook       │
└──────────────────────────────────────────────────────────────────┘
```

Two architectural notes worth surfacing:

**Arithmetic-in-one-place.** Both Source A and Source B do their arithmetic in `pipeline.py`. The Excel renderers and the frontend display whatever the pipeline computed — they don't recompute. This avoids the bug class where a web UI's "calculated balance" disagrees with the workbook's because the same math was duplicated in two places.

**Single response contract.** Every run returns a `ProcessResponse` (defined in `schemas.py`) that covers summary KPIs, per-statement results, validation findings, the workbook reference, and technical metadata. The frontend renders strictly against this contract. New fields are added as `Optional` so older response payloads from legacy engines don't crash the UI.

---

## Performance characteristics

These numbers reflect the production PDF Skill engine. They are real measurements from the three-PDF reference test set (Harbor National, Northgate Bank, Summit Credit Union).

| Metric | Value |
|---|---|
| PDF Skill accuracy on row classification | 100% on test set (vendor_payment / payroll_deposit / balance_line / bank_fee / transfer / check_payment / deposit) |
| Source A reconciliation detection | Correctly flagged the one $150 arithmetic discrepancy across the test set |
| Source B extraction completeness | 100% — 3 of 3 statements showed complete extraction with $0.00 deltas across all 15 (5 buckets × 3 statements) bucket comparisons |
| Processing time per PDF | 1–4 minutes (depends on PDF length and complexity) |
| Cost per PDF | $0.12–$0.60 (Sonnet; Opus 2–3× higher) |
| Maximum PDFs per run | 10 |
| Master workbook generation time | <2 seconds for 3-PDF runs |

For a small accounting practice processing 50 statements per quarter, total API cost runs $30–60 per quarter. The rule-based engine is available as a free fallback for cost-sensitive use cases, with the trade-off of weaker row classification.

---

## Running PREPARE

### Prerequisites

- Python 3.10 or later
- An Anthropic API key (for the PDF Skill engine)
- The Claude Agent SDK

### Installation

```bash
git clone <repo-url>
cd PREPARE-Core-v1.1

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### Running locally

```bash
uvicorn server:app --port 8000
```

Open `http://localhost:8000`. The upload screen accepts up to 10 PDFs per run plus an optional known-vendor CSV (improves name matching against an authoritative vendor list).

### Engine selection

- **PDF Skill** (default, recommended): Claude Agent SDK with Sonnet. Highest accuracy, full row-level classification, Source A and Source B both available. $0.12–0.60 per PDF.
- **Rule-based**: Free fallback. Lower accuracy on non-uniform layouts; no row classification; Source A and Source B unavailable. Useful when accuracy is less critical than cost.
- **Multi-agent**: Legacy comparison baseline. Three Claude calls per statement (extraction, normalization, classification) instead of one consolidated PDF Skill call. Kept for evaluation purposes.

### Output

Master workbook downloadable from the Workspace view. Per-statement workbooks downloadable from each per-statement card.

---

## Project layout

```
PREPARE-Core-v1.1/
├── server.py                       # FastAPI app, /api/process, /api/download
├── schemas.py                      # Pydantic models — the response contract
├── pipeline.py                     # PDF Skill orchestration + Sources A & B
├── agent_app.py                    # Legacy engines (rule_based, multi_agent)
├── pdf_skill_adapter.py            # Claude Agent SDK wrapper
├── backend/prototypes/
│   ├── README.md                   # What's in this directory
│   ├── pdf_skill_prompt.md         # The extraction prompt (production)
│   └── sample_pdf_skill_test.py    # Model-selection test harness
├── validation_engine.py            # Deterministic cross-statement validation
├── vendor_classifier_1099.py       # 1099 eligibility rules
├── review_flag_engine.py           # Per-statement review signals
├── backend/
│   ├── excel_generator.py          # Per-statement workbook generator
│   └── master_excel_generator.py   # Master workbook generator
├── frontend/
│   └── index.html                  # Single-page UI (vanilla JS)
├── V1_4_STATUS_*.md                # Per-day development log
└── README.md                       # This file
```

---

## What PREPARE is not

A few things to set expectations honestly.

**Not a 1099 filer.** PREPARE produces the workbook a CPA reviews *before* filing. The filing itself — IRS form generation, e-file integration, payee TIN collection — is a separate problem and a planned separate app (the IRS Form Processor).

**Not a general-purpose bank statement parser.** Extraction is tuned for 1099-relevant categories. Other categories (balance lines, payroll, metadata) are recognized but excluded from the deliverable. The audit trail in the per-statement workbook preserves them for reference.

**Not an autonomous agent.** Every output is meant to be reviewed. The deliverable surfaces review flags aggressively. The human decides what to file.

**Not free to run at scale.** PDF Skill costs add up. The rule-based engine exists for cost-sensitive runs, but it doesn't produce Source A or Source B signals.

---

## Engineering principles worth surfacing

A handful of decisions shaped how the codebase is organized. Naming them here for anyone reading the source.

**Transcribe, Don't Compute.** AI is allowed to read and label; deterministic logic does the arithmetic. This is what protects Source A from the failure mode where an AI silently smooths discrepancies into appearing correct.

**Arithmetic-in-one-place.** Source A and Source B both compute in `pipeline.py`. The Excel renderers and frontend display the pipeline's outputs. Duplicate computation paths invite drift between surfaces.

**Carry-or-drop pattern.** Computed fields (`reconciliation_snapshot`, `extraction_check`) must be explicitly carried through every layer: `pipeline` return dict → `server.agent_outputs.append` → `Statement` model construction. Forgetting to carry a field at any layer drops it silently — exactly the bug that delayed Source A landing by half a day during development.

**Diagnostic-before-edit.** The codebase has multiple workspace backup files alongside live code. After a stale-file slip during Phase 4C work, the protocol became: grep for the exact symbol being changed in the actual current file, confirm uniqueness and context, then apply patches with grep-anchored str_replace. Patch-by-patch with verification between each. Slower than batched edits; robust against ambiguity.

**Backward compatibility on optional fields.** Newer fields are `Optional` on the Pydantic model; frontend render functions return empty strings when fields are missing. Older response payloads and the rule-based engine coexist with the newer fields without crashes.

**Schema versioning.** `schemas.py:SCHEMA_VERSION` is read by the frontend on every response and logged as a console warning on mismatch. Currently `"1.0"`. A future breaking change to the response contract bumps this.

---

## Where the human–AI division of labor sits

This project was built by one developer in close collaboration with Claude. The division of labor is worth naming honestly because it explains what the codebase looks like and what it represents:

- **Architecture, scope decisions, and domain judgment**: the human. Which features to add, which to remove, where boundaries should fall between PREPARE and the future IRS Form Processor, what counts as a real bug versus a misclassification, when to stop adding to Per-Statement and start relocating to Consolidated. The accounting domain knowledge — knowing that a check isn't an exclusion category, that "Transcribe, Don't Compute" matters, that statement reconciliation and extraction completeness are independently failable — came from professional accounting practice.

- **Patch-level code, schema definitions, and routine implementation**: AI-assisted. Function implementations, grep-anchored edits, Pydantic model boilerplate, Excel formatting code, regex patterns, documentation. The bug-catching protocol (diagnostic-before-edit, patch-by-patch verification) developed in response to a real failure mode and applies to both human and AI work.

This isn't unique to PREPARE — it reflects how careful AI-assisted development actually works in 2026 — but it's worth being explicit about. A reader who builds apps with AI assistance will recognize the workflow signatures (the response contract, the schema-first approach, the discipline around live-file editing) and trust the project more for the honesty than for the alternative.

---

## License

[To be added before public release.]

---

## Acknowledgments

PDF Skill — Anthropic's Claude Agent SDK and Claude Sonnet model. The reconciliation methodology and information architecture iterated through real test PDFs and several rounds of "this works at three statements but would break at ten."
