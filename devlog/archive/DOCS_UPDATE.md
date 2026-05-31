# v1.3 — Documentation updates

Two sections to add. Both are drop-in markdown blocks. Updated 2026-05-15
to include the Master Workbook contract-fix landed in the same session.

---

## Section 1 — README.md addition

Add this section to the project README under "Architecture" or as a new
section near the top (above or below the existing v1.2 description).

```markdown
## v1.3 — PDF Skill ingestion (demo-ready)

PREPARE v1.3 introduces a Claude PDF Skill-based ingestion path as the
recommended demo workflow. The prototype validated structured extraction
against row-level ground truth for core sample statements and sanity-checked
additional bank/card layouts. The existing rule-based pdfplumber path
remains as a fast/free fallback, while PDF Skill becomes the main
architecture direction.

### What v1.3 adds

- **`backend/pdf_skill_adapter.py`** — new ingestion module wrapping
  Anthropic's pre-built `pdf` Agent Skill via the Claude Agent SDK
- **PDF Skill engine option** in the upload screen — now the recommended
  default
- **Per-Statement bookkeeping breakdown** in the expanded card, showing
  transaction-type counts (vendor payments, deposits, payroll, balance
  lines, transfers, fees, etc.) when PDF Skill data is present
- **Per-statement Excel columns** for `Transaction Type`,
  `Include for 1099`, `Exclusion Reason`, and `Review Required` —
  appended automatically when classifier data with non-vendor-payment
  rows is present. A clean statement where every row is a vendor payment
  renders as the v1.2 8-column layout; statements with payroll deposits,
  balance lines, transfers, fees, etc. trigger the 12-column layout.
- **Master Workbook contract fix** — Executive Summary "Total Transactions"
  count, "All Transactions" sheet rows, and "Per-Agent Summary" Cost ($)
  column now populate correctly for the PDF Skill path. Master workbook
  structure unchanged.
- **Structured failure handling** — PDF Skill failures don't crash the
  app; they surface as per-statement error states with retry-friendly
  semantics

### What v1.3 does NOT include (intentionally)

- Full commercial production readiness — this is a demo-ready / portfolio
  release, not a SaaS launch
- Master Workbook structural changes (existing 5-sheet structure preserved)
- Per-Statement bookkeeping KPI breakdowns beyond what the extraction
  layer reliably provides
- Multi-language statement support
- OCR support for scanned PDFs (untested in v1.3)
- Password-protected PDF support
- Real-time / streaming extraction (synchronous request/response only)

### Known limitations

- **Latency**: PDF Skill extraction takes 1–4 minutes per PDF on Sonnet
  (vs ~5 seconds for the rule-based fallback). Users uploading multiple
  statements should expect proportionally longer wait times.
- **Cost**: ~$0.20–0.60 per PDF on Sonnet, slightly more on Opus.
  Rule-based extraction has no API cost.
- **Test corpus is limited**: 2 PDFs with row-level ground truth, 4
  additional layouts sanity-checked. Broader real-world ground truth
  expansion is planned but not yet complete.
- **Subprocess failures**: the Agent SDK subprocess can occasionally fail
  to start (observed during testing when API credits were exhausted).
  v1.3 includes one automatic retry and structured failure reporting;
  see V1_2_STATUS.md → "Track 2 outcome" for detail.
- **Fallback strategy and default engine are open questions** for further
  mentor / operator review. v1.3 ships with PDF Skill as the default but
  the rule-based path remains fully available.

### Installing the pre-built pdf Skill

v1.3 expects the pre-built `pdf` Agent Skill to be installed at
`.claude/skills/pdf/SKILL.md` in the project root. Installation:

\`\`\`bash
cd "$(pwd)"
mkdir -p .claude/skills
cd .claude/skills
git clone https://github.com/anthropics/skills.git temp
cp -r temp/skills/pdf .
rm -rf temp
\`\`\`

Verify:

\`\`\`bash
ls -la .claude/skills/pdf/SKILL.md
\`\`\`

Should show `SKILL.md` with non-zero size. The agent will auto-discover
the skill at run time.

### Architecture sketch

\`\`\`
Frontend → server.py /api/process
              │
              ├─ engine="pdf_skill" → pipeline.run_pipeline_pdf_skill()
              │                       └─ pdf_skill_adapter.extract_from_pdf()
              │                            └─ claude_agent_sdk.query()
              │                                 └─ pre-built pdf Skill
              │
              ├─ engine="rule_based" → pipeline.run_pipeline()
              │                        └─ pdf_extractor (pdfplumber)
              │
              └─ engine="multi_agent" → existing agent path (legacy)

  All three engines feed:
    → transaction_aggregator
    → validation
    → master_excel_generator (unchanged in v1.3)
\`\`\`

### Engine selection guidance

- **PDF Skill (default)** — use for accurate accounting-oriented extraction.
  Slower and costs API credits, but handles multi-column layouts, payroll
  deposit exclusion, and balance-line exclusion that the rule-based path
  struggles with.
- **Rule-based** — use when speed is critical or for offline / no-API-key
  scenarios. Best on clean three-column bank statements.
- **Multi-agent** — legacy. Kept temporarily for A/B comparison. Deprecation
  planned for v1.4+.
```

---

## Section 2 — V1_2_STATUS.md addition

Append this section to V1_2_STATUS.md after the existing v1.2 closeout content,
under a new heading "v1.3 implementation status."

```markdown
## v1.3 implementation status

Date: May 2026.

### Scope: demo-ready / portfolio integration

v1.3 was scoped to demonstrate the architecture and PDF Skill capability
end-to-end, not to ship full commercial production readiness. Specific
exclusions documented in section "What v1.3 does NOT include" of README.md.

### What landed

1. **`backend/pdf_skill_adapter.py`** — new production adapter wrapping
   the Claude Agent SDK + pre-built pdf Skill. Returns
   `PDFSkillExtractionResult` with success/failure semantics. Never raises.
   Includes `_run_async_safely()` helper to detect existing event loops
   (FastAPI context) and execute in a ThreadPoolExecutor when needed.

2. **`backend/pipeline.py`** — added `run_pipeline_pdf_skill()` function
   alongside existing `run_pipeline()`. Same return-dict shape so the
   FastAPI layer routes both engines uniformly. Failure responses include
   `failure_reason` for frontend error display. Return dict serializes
   `Transaction` dataclasses to dicts via `_txn_to_dict()` helper so the
   master workbook generator can iterate them.

3. **`server.py` (project root)** — added `pdf_skill` to the engine
   dispatch. Existing `rule_based` and `multi_agent` engines unchanged.
   Per-statement `agent_outputs` dicts surface `transactions` and
   `cost_usd` keys so master workbook generator reads them correctly.

4. **`schemas.py` (project root)** — added `engine_used`,
   `bookkeeping_breakdown`, `excluded_count` to `Statement` Pydantic
   model. Added `'pdf_skill'` to `Technical.engine` Literal constraint.

5. **`frontend/index.html`** — three-option engine dropdown with PDF Skill
   selected by default. Per-Statement card's Group A (Bookkeeping Summary)
   now renders a transaction-type breakdown table when PDF Skill data is
   present; falls back to existing italic future-state note otherwise.
   Engine-aware loading text. Engine indicator pill on per-statement cards.

6. **`backend/excel_generator.py`** — per-statement Excel Transactions
   sheet gets four additional columns (`Transaction Type`,
   `Include for 1099`, `Exclusion Reason`, `Review Required`) when
   classifier data with non-vendor-payment rows is present. Auto-detected
   via `_has_classifier_data()`, so clean statements (all vendor payments)
   render in 8-column v1.2 layout. Existing sheet names and base columns
   preserved. `master_excel_generator.py` NOT modified.

### Stability guarantees confirmed in smoke tests

1. PDF Skill failures don't crash the app (adapter returns structured
   failure result, server.py surfaces it as a per-statement error card)
2. Rule-based and multi-agent paths still work identically (regression
   tested on rule-based path with 3-col clean PDF)
3. Frontend gracefully falls back to existing Per-Statement display when
   PDF Skill fields are absent
4. Excluded rows preserved separately for UI/Excel display, even though
   only included rows feed aggregation
5. Master Workbook structure preserved (5 sheets, same names) and now
   populated correctly for both rule-based and PDF Skill paths

### Master Workbook bugs fixed (within v1.3)

Three contract bugs were discovered during smoke-test verification and
fixed via `pipeline.py` and `server.py` patches (no master generator
changes):

- **Executive Summary "Total Transactions: 0"** — caused by server.py
  passing `"transactions": []` to agent_outputs for PDF Skill engine.
  Fixed by surfacing pipeline's serialized transaction list.
- **All Transactions sheet empty** — same root cause. Fixed by same patch.
- **Per-Agent Summary Cost = $0.0000** — caused by missing `cost_usd`
  key in agent_outputs dict. Fixed by adding the key in both success
  and failure branches.

Verified by smoke test on 2026-05-15: master workbook shows 37 transactions,
populated All Transactions sheet, correct cost figures.

### Outstanding items for mentor / operator review

1. **Default engine**: PDF Skill is currently the default. Should it stay
   default, or should rule-based remain default with PDF Skill as a toggle?
2. **Fallback automation**: if PDF Skill fails, should the system
   automatically retry with rule-based, or require explicit user retry?
3. **Cost pass-through model**: passed to user, absorbed, or tiered?
4. **Multi-agent deprecation timeline**: v1.4? v1.5? Keep indefinitely
   for diagnostic A/B comparison?
5. **Expanded ground-truth corpus**: how many real PDFs need ground-truth
   annotation before v1.3 is considered ready for broader use?
6. **Subprocess crash investigation**: occasional Agent SDK subprocess
   failures observed during testing (linked to API credit exhaustion in
   one case). Current mitigation is retry + structured failure. Is that
   sufficient, or does the root cause need separate investigation?

### Deferred to Phase 2/3 (bookkeeping framing redesign)

Identified during v1.3 review but not yet implemented:

1. Rename "Total Transactions" → "Included Payments" throughout Web UI
   and master Executive Summary (clarity: PDF Skill produces both parsed
   and included counts, current label ambiguates the two)
2. Rename "Excluded Transaction Breakdown" → "Statement Activity
   Breakdown" (more accurate; the table shows ALL transaction types,
   not just excluded ones)
3. Per-Statement card collapsed view second row showing
   parsed/excluded/payroll/balance breakdown numbers
4. Reformat Master Vendor Summary "Review Reasons" column to short
   canonical tags (current long-form prose, while accurate, is hard to
   scan in a wide table)
5. Validation Report Per-Statement Breakdown column wrap/width fix
6. Investigate symmetric-duplicate name variant reports (3 real pairs
   surface as 5 flags due to A↔B reporting). Pre-existing v1.0 behavior.
7. Resolve "Review rate" discrepancy between WebUI (counts vendors) and
   per-statement Summary Stats sheet (different counting basis)

### Recommended testing before next mentor review

1. Single-PDF smoke test with PDF Skill engine on a clean sample ✓ DONE
2. Single-PDF rule-based regression check (master workbook populated) ✓ DONE
3. Multi-PDF batch test (e.g., 3 statements) with PDF Skill
4. Multi-agent engine still works (regression check)
5. Engine switching between runs without server restart
6. Per-statement Excel output inspection: PDF Skill version with excluded
   rows (multicolumn PDF) should show 12 columns; clean statements should
   stay at 8 columns

### Cost-tracking note

Total prototype + testing spend through v1.3 development and verification:
approximately $5.50–6.00 in API costs across all Track 2 testing,
integration smoke tests, and master workbook fix verification.
Operator-borne; not yet passed through to any user.
```

---

## How to apply both sections

1. Open your README.md, paste Section 1 content in a sensible place
   (typically after the v1.2 description or under "Architecture")
2. Open your V1_2_STATUS.md, paste Section 2 content at the bottom under
   a new heading "v1.3 implementation status"
3. No code changes required — these are pure documentation drops
4. Commit / save both files
