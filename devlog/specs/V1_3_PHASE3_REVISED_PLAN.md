# PREPARE v1.3 — Phase 3b/3c/3d + Phase 4 Plan (Revised)

**Status**: Drafted May 18, 2026 after Phase 3a completion.
**Supersedes**: The Phase 3b section in `V1_3_PHASE3_PLAN.md`.
**Standing constraints**: None — `master_excel_generator.py`, `validation_engine.py`, `frontend/index.html`, and `excel_generator.py` are all fair game for editing.

---

## Strategic reframing

PREPARE has two product identities sharing infrastructure:

1. **1099 pre-review / vendor aggregation tool** — fully served by v1.3 Phases 1, 2, 3a
2. **Statement-level bookkeeping reconciliation aid** — foundation laid (PDF Skill row classification) but UI not yet committed to it

Phase 3b through Phase 4 extend the product to serve the second identity without removing the first. Vendor / 1099 review metrics remain visible; they demote from headline to supporting role within a per-statement view. The bookkeeping question — "did the parser understand this statement, what did it include for review, what did it exclude and why" — becomes the headline.

This phasing is intentional. Each sub-phase is independently verifiable. None overpromises features the data contract can't support.

---

## Phase 3b — Per-Statement card bookkeeping-first redesign

**Goal**: Restructure the Per-Statement card so the bookkeeping classification (parsed / included / excluded with type breakdown) is the headline view. Preserve 1099/vendor metrics as a secondary "Accounting Review Summary" section or collapsible "Statement Details."

**File touched**: `frontend/index.html` only.
**Estimated effort**: 2-3 sessions.
**API cost**: ~$0.65-1.30 for 2-PDF PDF Skill verification.
**Risk**: Medium-high (visual hierarchy change to the largest card component).

### Target card structure

```
┌────────────────────────────────────────────────────────────────────┐
│ 📄 sample_bank_multicolumn.pdf      SUCCESS · PDF Skill            │
│                                                                    │
│ STATEMENT RECONCILIATION SNAPSHOT     [Phase 4 deferred]           │
│   (Beginning Balance / Deposits / Payments / Ending Balance)       │
│   When the data contract supports it, this section anchors the     │
│   card. For Phase 3b, this section is OMITTED.                     │
│                                                                    │
│ ACTIVITY CLASSIFICATION                                            │
│   Parsed Rows  39   Included Payments  31   Excluded Rows  8       │
│   Vendor payments 31 · Payroll deposits 6 · Balance lines 2        │
│                                                                    │
│ VENDOR / 1099 REVIEW                                               │
│   Included Total $13,688.33  Vendors 14  Review 12  Over $600  8   │
│   Confidence 97%                                                   │
│                                                                    │
│ ⚠ Review: 12 flagged · 8 over $600 · 5 name variants               │
│                                                                    │
│ ▼ Details: row-level transactions, review flags, cross-signals     │
└────────────────────────────────────────────────────────────────────┘
```

### What changes

| Section | Before (Phase 2) | After (Phase 3b) |
|---|---|---|
| **Headline KPIs** | 5 metrics: Included Payments, Vendors, Total Amount, Review Needed, Confidence | 3 metrics: Parsed Rows, Included Payments, Excluded Rows (with type subline) |
| **Activity line** | Small grey text below 5 metrics | Promoted to "ACTIVITY CLASSIFICATION" section with bookkeeping breakdown always visible |
| **Vendor / 1099 metrics** | Headline | Demoted to "VENDOR / 1099 REVIEW" — visible but secondary |
| **Yellow Review summary strip** | Below metrics | Unchanged — still surfaces aggregate review signal |
| **Card expansion** | Three groups (A: Bookkeeping Summary, B: Review Signals, C: Cross-Statement Signals) | Consolidated under "Details" expansion |
| **Reconciliation snapshot** | Not present | Reserved space but not rendered (Phase 4) |

### What does NOT change in Phase 3b

- The data shape (no schema changes)
- The backend pipeline (no Python touched)
- The Master Workbook output
- The Validation Report view
- Cross-statement signal detection
- The engine dropdown / processing flow

### Rule-based engine fallback

Rule-based engine doesn't produce `excluded` flags. The new card layout must degrade gracefully:

| Field | PDF Skill | Rule-based |
|---|---|---|
| Parsed Rows | Real count | Real count (same as included for rule-based) |
| Included Payments | Real count | Real count |
| Excluded Rows | Real count + types | `—` with helper tooltip: "Row classification available with PDF Skill engine" |
| Activity classification | Type breakdown | Hidden or single-row "Vendor payments: N" |
| Vendor / 1099 Review | Real values | Real values |

Same graceful-fallback pattern Phase 2 used. Users understand engine choice has consequences without the UI breaking.

### Verification approach

1. Static check: `frontend/index.html` line count, all required selectors present
2. Free rule-based 2-PDF test: confirm graceful degradation, all sections render
3. One PDF Skill 2-PDF test (~$0.60): confirm full data renders, type breakdown correct, demoted metrics still accurate
4. Responsive test at 3 breakpoints: 1440px, 1024px, 640px
5. Compare side-by-side with Phase 2 baseline screenshots/text dumps

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| Card feels visually cluttered with 3 KPI sections | Use clear typography hierarchy and section dividers; the bookkeeping section is primary, vendor section visibly secondary |
| "Statement details" expansion gets bloated | Move detail tables into the expansion as today; only the metric summary lives in the demoted section |
| Card height grows too much, hurting list scannability | Keep section headers compact; consider collapsing the Vendor/1099 section by default if it gets long |
| Rule-based engine looks broken without excluded data | Test fallback paths early in Session 1; degrade to single-line "Parsed: N · Included: N" if needed |

---

## Phase 3c — Per-Statement Excel output alignment

**Goal**: Apply the bookkeeping-first framing to the per-statement Excel output so the deliverable matches the card. An accountant who opens the per-statement Excel sees the same parsed/included/excluded headline they saw in the web UI.

**File touched**: `excel_generator.py` (per-statement Excel generator).
**Estimated effort**: 1 session.
**API cost**: $0 (use existing PDF Skill outputs for verification).
**Risk**: Low-medium.

### What changes

The per-statement Excel currently has these sheets:

1. STATEMENT-LEVEL BOOKKEEPING REVIEW (vendor summary)
2. TRANSACTIONS — THIS STATEMENT
3. STATEMENT BOOKKEEPING SUMMARY (statistics)

Phase 3c modifies:

#### Sheet 1 — STATEMENT-LEVEL BOOKKEEPING REVIEW
- Add a header block before the vendor table:
  ```
  STATEMENT ACTIVITY CLASSIFICATION
  Parsed Rows:        39
  Included Payments:  31
  Excluded Rows:      8 (6 payroll deposits, 2 balance lines)
  ```
- Keep the vendor table as-is

#### Sheet 2 — TRANSACTIONS
- Already has 12 columns with PDF Skill classification (`Transaction Type`, `Include for 1099`, `Exclusion Reason`, `Review Required`)
- Add visual emphasis to excluded rows: subtle grey background fill (already partially implemented per Phase 1 code comment) — confirm it's actively rendering
- Add a note at the top: "Rows shown in grey were excluded from 1099 aggregation. See `Exclusion Reason` column for details."

#### Sheet 3 — STATEMENT BOOKKEEPING SUMMARY
- Restructure as parallel sections:
  ```
  STATEMENT-LEVEL ACTIVITY CLASSIFICATION
    Total rows parsed:        39
    Included as vendor pmt:   31  (for 1099 review)
    Excluded from 1099:        8
      Payroll deposits:         6
      Balance lines:            2
  
  1099 PRE-REVIEW SIGNALS
    Vendors crossing $600:     8
    Vendors with entity suffix: 5
    
  BOOKKEEPING REVIEW SIGNALS
    Vendors needing review:   10
    Review rate:           64.3%
  ```

### Rule-based engine fallback

For rule-based engine output:
- Activity Classification section shows "Parsed: N / Included: N / Excluded: N/A — Row classification available with PDF Skill engine"
- Transactions sheet doesn't have the 4 classification columns (they're empty); existing 8-column layout still works

### Verification approach

1. `py_compile` check
2. Run existing PDF Skill 2-PDF test output through the new generator (reuse the saved transaction dicts; no new API call needed)
3. Open generated per-statement Excel files; verify all three sheets render correctly
4. Open with rule-based engine output to confirm fallback

---

## Phase 3d — Master Workbook Review Tag Taxonomy (formerly A4)

**Goal**: Convert long verbose Review Reasons into canonical short tags + optional detail. Same accountant-readability principle as A3 (UUIDs → filenames).

**Files touched**: `master_excel_generator.py`, possibly `validation_engine.py` and `review_flag_engine.py`.
**Estimated effort**: 1-2 sessions.
**API cost**: $0 (use existing outputs for verification).
**Risk**: Low-medium.

### The 7-tag taxonomy (approved last session)

| Tag | Replaces (current prose) |
|---|---|
| `LOW_NAME_MATCH` | "Low vendor name match confidence" |
| `ENTITY_UNKNOWN` | "Entity type unknown — verify LLC/Corp/Individual via W-9 before filing decision" |
| `CLASSIFIER_FLAG` | "Contains transactions flagged for review by classifier" |
| `MULTI_VARIANT` | "Multiple raw name variants grouped together (N)" |
| `NAME_VARIANT` | "Name variant detected — 'X' in stmt_a ~ 'Y' in stmt_b (N% similar)" |
| `NEAR_THRESHOLD` | "Near-threshold ($X) — verify all payments captured before filing" |
| `AMOUNT_DISCREPANCY` | "Combined total across statements ($X) crosses $600 threshold" / amount mismatch reasons |

### Master Vendor Summary column layout change

Before (Phase 3a output):

| Vendor | ... | Review Reasons (one long column) |
|---|---|---|
| Mary Johnson Consulting | ... | "Flagged during normalization — Contains transactions flagged for review by classifier; Name variant detected — 'Mary Johnson Consulting' in sample_bank_multicolumn.pdf ~ 'John Smith Consulting' in sample_bank_3col_clean.pdf (73% similar)" |

After (Phase 3d):

| Vendor | ... | Review Tags | Tag Details |
|---|---|---|---|
| Mary Johnson Consulting | ... | `CLASSIFIER_FLAG`, `NAME_VARIANT` | Classifier flagged transactions for verification; Name variant: "Mary Johnson Consulting" ~ "John Smith Consulting" in sample_bank_3col_clean.pdf (73%) |

The Review Tags column makes the workbook sortable and filterable: "show me all rows where Tags contains NAME_VARIANT." The Tag Details column preserves human-readable context. Accountant scans Tags for triage; reads Details only for rows worth investigating.

### Implementation approach

Two options for where the tag mapping lives:

**Option A — Map at write-time in `master_excel_generator.py`**
- Maintain a small mapping table that converts prose → tag
- Pros: minimal touching of validation_engine; one place to change
- Cons: brittle if prose changes

**Option B — Tag at source in `review_flag_engine.py` and `validation_engine.py`**
- Add a `tags` field to `ReviewFlags` alongside `reasons`
- Each reason-adding call also adds a canonical tag
- Pros: source of truth is correct; tags don't depend on prose matching
- Cons: more files touched

**My recommendation: Option B**. The data structure should carry the canonical tag; the prose is for human display. Phase 3d's "real" form follows the same architectural principle that drove Phase 1's contract fixes — explicit data over inferred-from-string parsing.

### Verification approach

1. `py_compile` check on all touched files
2. Run 2-PDF rule-based test (free) — confirm tags appear in Master Vendor Summary
3. Confirm Tag Details column still readable (no information lost)
4. Filter test: in Excel, filter Review Tags column by "NAME_VARIANT" — confirm only expected rows show

---

## Phase 4 — Statement Reconciliation Snapshot (FUTURE — separate planning required)

**Goal**: Add a Statement Reconciliation Snapshot section showing Beginning Balance + Deposits/Credits - Payments/Debits = Ending Balance with a balanced/needs-review status.

**Why this is NOT Phase 3**: This requires:
1. **PDF Skill schema extension** — adding `beginning_balance`, `ending_balance`, `total_deposits`, `total_withdrawals` as structured fields (currently we have row-level types but not summary fields)
2. **PDF Skill prompt revision** — instructing the agent to extract these specific fields, not just classify each row
3. **Per-statement Excel schema update** — three more rows in the Bookkeeping Summary sheet
4. **WebUI card** — render the snapshot at the top of the per-statement card
5. **Validation logic** — does the reconciliation balance? If not, by how much? Compare implied balance from rows vs reported balance.

These are real schema-and-extraction changes, not UI changes. Phase 4 is its own planning effort, deserving its own plan document and prototype testing.

**Estimated effort for Phase 4** (rough, pending detailed planning): 4-6 sessions including prompt revision, schema extension, prototype testing on the 2-PDF corpus, UI rendering, and reconciliation validation.

**Estimated Phase 4 API cost**: ~$2-4 for new prompt testing on Sonnet across the test corpus.

### Phase 4 should NOT be started until

1. Phase 3b, 3c, 3d are complete and stable
2. v1.3 final is shipped and documented
3. A dedicated planning document is written (`V1_4_RECONCILIATION_PLAN.md` or similar)
4. The PDF Skill prompt revision is prototyped on test PDFs

---

## Summary: phasing structure

```
v1.3 (in progress)
├── Phase 1: Master Workbook contract fix          ✅ DONE
├── Phase 2: Frontend label rework                 ✅ DONE
├── Phase 3a: Master + validation polish           ✅ DONE
├── Phase 3b: Per-Statement card redesign          ⏭ NEXT (2-3 sessions)
├── Phase 3c: Per-Statement Excel alignment        ⏸ after 3b (1 session)
└── Phase 3d: Review Tag taxonomy                  ⏸ after 3c (1-2 sessions)

v1.4 (future)
└── Phase 4: Statement Reconciliation Snapshot     ⏸ separate planning needed
```

After Phase 3b+3c+3d complete, v1.3 final ships with both product identities served:
- 1099 pre-review workflow: complete (was already complete after Phase 3a)
- Bookkeeping reconciliation aid: foundation complete, balance reconciliation pending Phase 4

v1.4 work focuses on the balance reconciliation layer and other v1.4 backlog items (rule-based engine convergence, corpus expansion, long-extraction UX).

---

## Open questions to resolve before Phase 3b starts

These can wait until the start of the Phase 3b session:

1. **Color treatment for Parsed / Included / Excluded tiles**. Phase 2 used the existing color tokens. Phase 3b could keep them or introduce specific role colors (e.g., Parsed = neutral, Included = green, Excluded = amber).

2. **Vendor / 1099 Review section: always visible or collapsible?** Always visible matches the "preserve don't replace" intent. Collapsible saves vertical space. Recommendation: always visible but visually subordinate (smaller text, no border emphasis).

3. **What happens to the yellow review summary strip?** Phase 2 added it. Phase 3b — does it still appear in the same place? Recommendation: keep it where it is, between Activity Classification and Vendor / 1099 Review.

4. **Activity classification — show all types or only non-zero?** PDF Skill produces a count per type. Showing "0" rows for absent types adds clutter; showing only present types makes the list shorter. Recommendation: only present types (matches current Phase 2 behavior).

---

## Communication & ground rules for Phase 3b+

Same as Phase 3a:

- File-by-file delivery, complete drop-in replacements
- Compile / build checks before any test run
- Backup before any session touching multi-section files
- Free rule-based regression test before any PDF Skill test
- No GitHub push without explicit review

---

*Drafted to supersede the Phase 3b section in V1_3_PHASE3_PLAN.md. Save this file alongside the other V1_* status documents.*
