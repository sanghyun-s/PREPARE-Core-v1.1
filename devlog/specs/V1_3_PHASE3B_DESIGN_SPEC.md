# V1_3_PHASE3B_DESIGN_SPEC.md — Per-Statement Card Redesign

**Status**: Drafted May 18, 2026 after Phase 3a completion. Ready for Session 1 implementation.
**Scope**: Frontend redesign of the Per-Statement card in `frontend/index.html`. No backend changes.
**Supersedes**: The Phase 3b implementation sketch in `V1_3_PHASE3_REVISED_PLAN.md`.
**Companion documents**: `V1_3_PHASE3_REVISED_PLAN.md` (overall Phase 3 phasing), `V1_2_STATUS.md` (project journal).

---

## Why this document exists

Phase 3b is the most visible UI change in v1.3. It pivots the Per-Statement card from a vendor-review layout to a bookkeeping-first layout. To minimize risk and accelerate Session 1, this spec captures every decision made before code was written — based on inspection of the actual production API response, the current `frontend/index.html`, and the design conversations leading up to Session 1.

When Session 1 begins, the implementer (Claude) reads this document instead of re-deriving decisions from chat history. The exact target layout, CSS class names, HTML structure, fallback behavior, and verification cases are all specified here.

---

## Strategic context

PREPARE serves two product identities sharing infrastructure:

1. **1099 pre-review / vendor aggregation tool** — fully served by v1.3 Phases 1, 2, 3a
2. **Statement-level bookkeeping reconciliation aid** — foundation laid (PDF Skill row classification), UI not yet committed to it

Phase 3b shifts the Per-Statement card's visual hierarchy to serve the second identity without removing the first. Vendor / 1099 review metrics remain visible but visibly secondary; the bookkeeping question — "did the parser understand this statement, what got included for review, what got excluded and why" — becomes the headline.

Phase 4 (Statement Reconciliation Snapshot — balance reconciliation) is deferred. Phase 3b reserves space for it in the card layout but does NOT render it.

---

## Decisions locked in (verified pre-Session 1)

| Decision | Value | Rationale |
|---|---|---|
| Files touched | `frontend/index.html` only | Verified end-to-end: all fields needed already exist in the API response. No `server.py`, no schema, no backend changes. |
| Number of headline tiles | 3 (Parsed / Included / Excluded) | Bookkeeping headline; matches `parsed = included + excluded` math from real data |
| Color treatment for the 3 tiles | DEFERRED to Session 1 (after first render visible) | Cannot decide on color in the abstract; needs eyes on rendered output |
| "0 excluded" display | Option A — show "0" plainly | Consistent with other tiles; activity classification below disambiguates |
| Rule-based engine fallback | Em-dash on Excluded tile + tooltip | Honest about engine capability without breaking layout |
| Vendor / 1099 Review demotion | Always-visible subordinate row (NOT collapsible) | "Preserve don't replace" — keeps existing 1099 metrics accessible |
| Activity classification placement | Below the 3 tiles, always visible | The "why" of the excluded number — accountant needs it adjacent |
| Phase 4 reconciliation snapshot space | Reserved structurally but NOT rendered in 3b | Discipline; balance reconciliation requires schema extension (Phase 4) |
| Session structure | 3 sessions (design + demote + polish) | Defensive incremental approach for medium-high risk visual change |

---

## Verified data foundation

Every field Phase 3b reads is present in the production API response. Verified May 18, 2026 against a 2-PDF PDF Skill run.

### Statement object schema (from `server.py` Statement model)

```typescript
{
  file_id: string,
  original_filename: string,
  status: "success" | "partial" | "failed",
  failure_reason: "rate_limit" | "extraction" | "other" | null,
  error_message: string | null,

  // Core metrics (existing — used in current card)
  transaction_count: number,        // INCLUDED count
  vendor_count: number,
  total_amount: number,
  vendors_over_threshold: number,
  review_needed: number,
  extraction_confidence: number,
  excel_file_id: string | null,

  // v1.3 fields (Phase 1 + Phase 2 additions)
  engine_used: "pdf_skill" | "rule_based" | "multi_agent" | null,
  bookkeeping_breakdown: { [type: string]: number } | null,
  excluded_count: number            // 0 for rule-based; real count for PDF Skill
}
```

### Real production values (2-PDF PDF Skill run, May 18, 2026)

**Statement A (sample_bank_multicolumn.pdf — mixed activity case)**
```json
{
  "transaction_count": 31,
  "vendor_count": 14,
  "total_amount": 13688.33,
  "vendors_over_threshold": 8,
  "review_needed": 12,
  "extraction_confidence": 0.967,
  "engine_used": "pdf_skill",
  "bookkeeping_breakdown": {
    "vendor_payment": 31,
    "payroll_deposit": 6,
    "balance_line": 2
  },
  "excluded_count": 8
}
```
- Parsed = 31 + 8 = 39
- Included = 31
- Excluded = 8 (6 payroll, 2 balance)

**Statement B (sample_bank_3col_clean.pdf — clean case)**
```json
{
  "transaction_count": 37,
  "vendor_count": 15,
  "total_amount": 14582.61,
  "vendors_over_threshold": 8,
  "review_needed": 11,
  "extraction_confidence": 0.97,
  "engine_used": "pdf_skill",
  "bookkeeping_breakdown": {
    "vendor_payment": 37
  },
  "excluded_count": 0
}
```
- Parsed = 37 + 0 = 37
- Included = 37
- Excluded = 0 (all rows were vendor payments)

---

## Target card layout (after Session 2 — final state)

### Case A: PDF Skill engine with mixed activity (Statement A)

```
┌────────────────────────────────────────────────────────────────────────┐
│  📄  sample_bank_multicolumn.pdf      Agent 1 · SUCCESS · PDF Skill   │
│      [agent badge]                                  [✓ Success pill]   │
│                                                     [Download Excel]   │
│                                                     [▼ chevron]        │
├────────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  [Phase 4 placeholder — Statement Reconciliation Snapshot — empty]    │
│                                                                       │
│  ┌──────────────┬──────────────┬──────────────┐                      │
│  │   PARSED     │   INCLUDED   │   EXCLUDED   │                      │
│  │              │              │              │                      │
│  │      39      │      31      │       8      │                      │
│  │              │              │              │                      │
│  │  rows seen   │ for 1099     │  filtered    │                      │
│  └──────────────┴──────────────┴──────────────┘                      │
│                                                                       │
│  ACTIVITY CLASSIFICATION                                              │
│  Vendor payments 31 · Payroll deposits 6 · Balance lines 2            │
│                                                                       │
│  ────────────────────────────────────────────────                     │
│  VENDOR / 1099 REVIEW                                                 │
│  Included Total $13,688.33 · Vendors 14 · Review Needed 12 ·          │
│  Over $600 8 · Confidence 97%                                         │
│                                                                       │
│  ⚠ Review: 12 flagged · 8 over $600 · 3 name variants ·               │
│            1 discrepancy · 97% confidence                             │
│                                                                       │
├──── [card expansion below, opens on chevron click] ──────────────────┤
│                                                                       │
│  STATEMENT PROCESSING DETAILS                                         │
│  ✓ Statement parsed successfully with PDF Skill                       │
│    39 rows identified. 31 included as vendor payments for 1099        │
│    aggregation. 8 excluded (6 payroll deposits, 2 balance lines).     │
│                                                                       │
│  BOOKKEEPING REVIEW SIGNALS                                           │
│  [Review needed 12]  [Over $600 8]                                    │
│                                                                       │
│  CROSS-STATEMENT SIGNALS INVOLVING THIS STATEMENT                     │
│  [Near-threshold table] [Discrepancies table] [Name variants table]   │
│                                                                       │
└────────────────────────────────────────────────────────────────────────┘
```

### Case B: PDF Skill engine with clean PDF (Statement B)

```
┌────────────────────────────────────────────────────────────────────────┐
│  📄  sample_bank_3col_clean.pdf       Agent 2 · SUCCESS · PDF Skill   │
├────────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────┬──────────────┬──────────────┐                      │
│  │   PARSED     │   INCLUDED   │   EXCLUDED   │                      │
│  │      37      │      37      │       0      │                      │
│  │  rows seen   │ for 1099     │  filtered    │                      │
│  └──────────────┴──────────────┴──────────────┘                      │
│                                                                       │
│  ACTIVITY CLASSIFICATION                                              │
│  Vendor payments 37                                                   │
│                                                                       │
│  ────────────────────────────────────────────────                     │
│  VENDOR / 1099 REVIEW                                                 │
│  Included Total $14,582.61 · Vendors 15 · Review Needed 11 ·          │
│  Over $600 8 · Confidence 97%                                         │
│                                                                       │
│  ⚠ Review: 11 flagged · 8 over $600 · 97% confidence                  │
│                                                                       │
└────────────────────────────────────────────────────────────────────────┘
```

### Case C: Rule-based engine (graceful fallback)

```
┌────────────────────────────────────────────────────────────────────────┐
│  📄  some_statement.pdf               Agent 1 · SUCCESS · Rule-based  │
├────────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────┬──────────────┬──────────────┐                      │
│  │   PARSED     │   INCLUDED   │   EXCLUDED   │                      │
│  │      37      │      37      │       —      │  ← em-dash with     │
│  │  rows seen   │ for 1099     │   ⓘ tooltip   │     tooltip          │
│  └──────────────┴──────────────┴──────────────┘                      │
│                                                                       │
│  Row classification available with PDF Skill engine.                  │
│                                                                       │
│  ────────────────────────────────────────────────                     │
│  VENDOR / 1099 REVIEW                                                 │
│  Included Total $... · Vendors ... · Review Needed ... · ...          │
│                                                                       │
│  ⚠ Review: ...                                                        │
│                                                                       │
└────────────────────────────────────────────────────────────────────────┘
```

Tooltip text on the em-dash: `"Row-level classification available when using PDF Skill engine."`

---

## CSS class structure to add

New classes scoped to `.ps-card` and its body. Existing classes preserved.

```css
/* v1.3 Phase 3b: bookkeeping-first headline tiles */
.ps-headline-tiles {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
  margin: 0 0 1rem;
}

.ps-headline-tile {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 1rem 1.15rem;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  position: relative;
}

.ps-headline-tile-label {
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.5rem;
}

.ps-headline-tile-value {
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}

.ps-headline-tile-sub {
  font-size: 0.74rem;
  color: var(--text-muted);
  margin-top: 0.35rem;
}

.ps-headline-tile-unavailable .ps-headline-tile-value {
  color: var(--text-muted);
}

/* Color treatment — DEFERRED. Default to neutral until decided in Session 1.
   Candidate color schemes (to be chosen with eyes on rendered output):
   
   Option X: Parsed neutral, Included blue, Excluded amber
     .ps-headline-tile.parsed   { border-left: 3px solid var(--neutral); }
     .ps-headline-tile.included { border-left: 3px solid var(--brand-blue); }
     .ps-headline-tile.excluded { border-left: 3px solid var(--warn); }
   
   Option Y: All tiles neutral, value color cycles
     .ps-headline-tile .ps-headline-tile-value.parsed   { color: var(--text-primary); }
     .ps-headline-tile .ps-headline-tile-value.included { color: var(--brand-blue); }
     .ps-headline-tile .ps-headline-tile-value.excluded { color: var(--warn); }
   
   Option Z: Match existing Workspace KPI style (full color bottom border)
     .ps-headline-tile.parsed::after   { content:""; ... bottom-color: var(--neutral); }
     .ps-headline-tile.included::after { content:""; ... bottom-color: var(--brand-blue); }
     .ps-headline-tile.excluded::after { content:""; ... bottom-color: var(--warn); }
*/

/* v1.3 Phase 3b: activity classification section (always visible) */
.ps-activity-classification {
  margin: 0 0 1rem;
  padding: 0 0.15rem;
}

.ps-activity-classification-label {
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.3rem;
}

.ps-activity-classification-types {
  font-size: 0.88rem;
  color: var(--text-primary);
  line-height: 1.5;
}

.ps-activity-classification-types .activity-type {
  display: inline-block;
  margin-right: 0.65rem;
}

.ps-activity-classification-types .activity-type-count {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.ps-activity-classification-types .activity-type-sep {
  color: var(--border-strong);
  margin-right: 0.65rem;
}

.ps-activity-classification-empty {
  font-size: 0.82rem;
  color: var(--text-muted);
  font-style: italic;
}

/* v1.3 Phase 3b: vendor/1099 review subordinate section */
.ps-vendor-review {
  border-top: 1px solid var(--border);
  padding-top: 0.85rem;
  margin: 0 0 0.85rem;
}

.ps-vendor-review-label {
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.3rem;
}

.ps-vendor-review-row {
  font-size: 0.85rem;
  color: var(--text-primary);
  line-height: 1.5;
}

.ps-vendor-review-row .vr-segment {
  display: inline-block;
  margin-right: 0.55rem;
}

.ps-vendor-review-row .vr-label {
  color: var(--text-secondary);
}

.ps-vendor-review-row .vr-value {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.ps-vendor-review-row .vr-sep {
  color: var(--border-strong);
  margin-right: 0.55rem;
}
```

---

## JavaScript changes (renderAgentCards modifications)

Phase 3b modifies the card-body block inside `renderAgentCards()`. Plan:

### Session 1 (C1 only) — defensive dual-layout

Insert the new 3-tile headline + activity classification ABOVE the existing 5-metric row. Do NOT remove anything. Card temporarily has both layouts. Verify, then sleep.

```javascript
// Inside renderAgentCards(), card body section becomes:
return `
  <div class="ps-card ${colorClass} ${expanded ? 'expanded' : ''}" id="ps-card-${origIdx}">
    <div class="ps-card-head">
      ${/* head structure unchanged */}
    </div>
    <div class="ps-card-body">
      
      ${/* NEW: bookkeeping-first headline tiles */}
      ${renderHeadlineTiles(s)}
      
      ${/* NEW: activity classification section */}
      ${renderActivityClassification(s)}
      
      ${/* PRESERVED in Session 1 — old 5-metric row stays for comparison */}
      <div class="ps-metrics">
        ${renderMetric('Included Payments', s.transaction_count || 0)}
        ${renderMetric('Vendors',           s.vendor_count || 0)}
        ${renderMetric('Total Amount',      '$' + Number(s.total_amount || 0).toLocaleString(...))}
        ${renderMetric('Review Needed',     review, 'review' + (review === 0 ? ' zero' : ''))}
        ${renderMetric('Confidence',        Math.round((s.extraction_confidence || 0) * 100) + '%')}
      </div>
      
      ${activityLine}                              ${/* PRESERVED in Session 1 */}
      
      <div class="ps-summary-strip ${summaryClass}">
        ${/* Yellow review summary strip — unchanged */}
      </div>
    </div>
    <div class="ps-card-expansion">${expansionContent}</div>
  </div>`;
```

### Session 2 (C1b) — demotion

Remove the old 5-metric row + activity line. Insert the new subordinate vendor/1099 review section in their place.

```javascript
// Inside renderAgentCards(), card body section becomes:
return `
  <div class="ps-card ...">
    <div class="ps-card-head">...</div>
    <div class="ps-card-body">
      ${renderHeadlineTiles(s)}
      ${renderActivityClassification(s)}
      ${renderVendorReview(s)}             ${/* NEW: demoted 1099 metrics */}
      <div class="ps-summary-strip ${summaryClass}">...</div>
    </div>
    <div class="ps-card-expansion">${expansionContent}</div>
  </div>`;
```

### Helper functions to add

```javascript
function renderHeadlineTiles(s) {
  const hasPdfSkill = hasPdfSkillData(s);
  const { parsed, included, excluded } = computeParsedCounts(s);
  
  const excludedDisplay = hasPdfSkill 
    ? excluded.toString()
    : '<span title="Row-level classification available when using PDF Skill engine.">—</span>';
  
  const excludedTileClass = hasPdfSkill 
    ? 'ps-headline-tile excluded'
    : 'ps-headline-tile excluded ps-headline-tile-unavailable';
  
  return `
    <div class="ps-headline-tiles">
      <div class="ps-headline-tile parsed">
        <span class="ps-headline-tile-label">Parsed</span>
        <span class="ps-headline-tile-value">${parsed}</span>
        <span class="ps-headline-tile-sub">rows seen</span>
      </div>
      <div class="ps-headline-tile included">
        <span class="ps-headline-tile-label">Included</span>
        <span class="ps-headline-tile-value">${included}</span>
        <span class="ps-headline-tile-sub">for 1099</span>
      </div>
      <div class="${excludedTileClass}">
        <span class="ps-headline-tile-label">Excluded</span>
        <span class="ps-headline-tile-value">${excludedDisplay}</span>
        <span class="ps-headline-tile-sub">filtered</span>
      </div>
    </div>`;
}

function renderActivityClassification(s) {
  const hasPdfSkill = hasPdfSkillData(s);
  
  if (!hasPdfSkill) {
    return `
      <div class="ps-activity-classification">
        <div class="ps-activity-classification-label">Activity Classification</div>
        <div class="ps-activity-classification-empty">
          Row classification available with PDF Skill engine.
        </div>
      </div>`;
  }
  
  const b = s.bookkeeping_breakdown;
  const segments = [];
  for (const type of TRANSACTION_TYPE_ORDER) {
    const count = b[type];
    if (count && count > 0) {
      const label = TRANSACTION_TYPE_LABELS[type] || type;
      segments.push(`
        <span class="activity-type">
          ${escapeHtml(label)} <span class="activity-type-count">${count}</span>
        </span>`);
    }
  }
  // Defensive: catch unenumerated types
  for (const type of Object.keys(b)) {
    if (TRANSACTION_TYPE_ORDER.includes(type)) continue;
    const count = b[type];
    if (count && count > 0) {
      segments.push(`
        <span class="activity-type">
          ${escapeHtml(type.replace(/_/g, ' '))} <span class="activity-type-count">${count}</span>
        </span>`);
    }
  }
  
  return `
    <div class="ps-activity-classification">
      <div class="ps-activity-classification-label">Activity Classification</div>
      <div class="ps-activity-classification-types">
        ${segments.join('<span class="activity-type-sep">·</span>')}
      </div>
    </div>`;
}

function renderVendorReview(s) {
  // Phase 3b Session 2 only — demoted 1099/vendor metrics
  const included = s.transaction_count || 0;
  const totalAmount = '$' + Number(s.total_amount || 0).toLocaleString(
    'en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }
  );
  const vendors = s.vendor_count || 0;
  const review = s.review_needed || 0;
  const overThresh = s.vendors_over_threshold || 0;
  const confidence = Math.round((s.extraction_confidence || 0) * 100);
  
  return `
    <div class="ps-vendor-review">
      <div class="ps-vendor-review-label">Vendor / 1099 Review</div>
      <div class="ps-vendor-review-row">
        <span class="vr-segment">
          <span class="vr-label">Included Total</span> 
          <span class="vr-value">${totalAmount}</span>
        </span>
        <span class="vr-sep">·</span>
        <span class="vr-segment">
          <span class="vr-label">Vendors</span> 
          <span class="vr-value">${vendors}</span>
        </span>
        <span class="vr-sep">·</span>
        <span class="vr-segment">
          <span class="vr-label">Review Needed</span> 
          <span class="vr-value">${review}</span>
        </span>
        <span class="vr-sep">·</span>
        <span class="vr-segment">
          <span class="vr-label">Over $600</span> 
          <span class="vr-value">${overThresh}</span>
        </span>
        <span class="vr-sep">·</span>
        <span class="vr-segment">
          <span class="vr-label">Confidence</span> 
          <span class="vr-value">${confidence}%</span>
        </span>
      </div>
    </div>`;
}
```

---

## Group A in expansion — adjustment for Phase 3b

Currently `buildExpansionContent()` Group A contains:
1. Status detail (with parsed/included/excluded counts and exclusion phrase)
2. Statement Activity Breakdown table
3. Helper note

After Phase 3b, the activity breakdown is now visible at the always-visible level (not in expansion). Group A inside expansion should:

- **KEEP** the status detail line ("Statement parsed successfully with PDF Skill. 39 rows identified...")
- **REMOVE** the Statement Activity Breakdown table (redundant with the always-visible activity classification section above)
- **REMOVE** the helper note (redundant)
- **RENAME** the group title from "Statement-Level Bookkeeping Summary" to "Statement Processing Details"

This keeps the natural-language status detail (which is valuable for audit purposes) but removes the duplication.

---

## Session structure

### Session 1 — C1 + dual-layout intermediate state

**Goal**: New headline tiles + activity classification render correctly. Old metrics still visible. No demotion yet.

Steps:
1. Backup `frontend/index.html`
2. Apply CSS additions (block 1 above)
3. Add JavaScript helpers (`renderHeadlineTiles`, `renderActivityClassification`)
4. Modify `renderAgentCards()` to call new helpers ABOVE existing layout
5. Free rule-based 2-PDF regression test (~5 sec) — confirm graceful fallback
6. PDF Skill 2-PDF regression test (~$0.60) — confirm both Case A and Case B render correctly
7. **Decide color treatment** with eyes on rendered output (Option X, Y, or Z, or none)
8. Stop. Sleep on intermediate state for 24 hours.

Estimated session time: 2–3 hours. Cost: ~$0.60.

### Session 2 — C1b demotion + responsive testing

**Goal**: Remove old metrics row and activity line. Add subordinate vendor/1099 review section. Card reaches final state.

Steps:
1. Backup `frontend/index.html` again (pre-C1b)
2. Remove old `.ps-metrics` block and old `.ps-activity-line`
3. Add `renderVendorReview()` helper
4. Modify `renderAgentCards()` to insert vendor review section after activity classification
5. Adjust Group A in expansion (remove breakdown table, rename title)
6. Free rule-based regression test
7. PDF Skill regression test
8. Responsive check at 3 breakpoints (1440px, 1024px, 640px)
9. Stop if visually polished. Loop back if not.

Estimated session time: 2 hours. Cost: ~$0.60 (one PDF Skill verification).

### Session 3 — Polish + verification + docs

**Goal**: Visual refinement. Update changelog comments. Mark Phase 3b complete.

Steps:
1. Adjust spacing, typography, color saturation based on session 2 findings
2. Update Phase 3b changelog comment in `frontend/index.html`
3. Final 2-PDF PDF Skill regression test
4. Append Phase 3b completion section to `V1_2_STATUS.md`
5. Append Phase 3b changes to `V1_3_RELEASE_NOTES.md` "What's New" section

Estimated session time: 1.5 hours. Cost: ~$0.60.

**Total estimated**: 3 sessions, ~5-7 hours, ~$1.80 in API verification cost.

---

## Verification cases

The verification matrix that must pass before Phase 3b is declared complete.

| Test | Engine | Expected outcome |
|---|---|---|
| 1-PDF, clean | PDF Skill | Parsed/Included tiles show same number; Excluded shows "0"; Activity classification shows "Vendor payments N" only |
| 1-PDF, mixed | PDF Skill | All 3 tiles show different numbers; Activity classification shows multiple types |
| 2-PDF, mixed | PDF Skill | Both cards render correctly with different breakdowns |
| 1-PDF | Rule-based | Parsed/Included show same real number; Excluded shows "—" with tooltip; Activity classification shows fallback message |
| 1-PDF, failed | PDF Skill or Rule-based | Failed card variant renders (no tile section at all — failed card uses simpler form) |
| Responsive | Any | Tiles stack vertically below 640px; remain horizontal at 1024px+ |

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Card height grows too tall after demotion | Medium | If demoted section is too long for one row, consider line-break behavior or smaller font. Test in Session 2 with both 2-PDF and 5-PDF scenarios. |
| 3-tile layout breaks on narrow viewports | Low | Existing `.ps-metrics` was responsive (3 cols at <1024px, 2 cols at <640px). Apply same pattern: `repeat(3, 1fr)` → stack at small viewports. |
| Color choice clashes with rest of app | Medium | Defer until Session 1 with eyes on rendered output. Default to neutral if uncertain. |
| Activity classification text overflows on long type lists | Low | Allow text to wrap naturally. Most real cases have 1-4 types. |
| Rule-based card looks broken | Low | Em-dash on Excluded tile + tooltip pattern from Phase 2 fallback. Test rule-based path FIRST in Session 1. |
| Group A in expansion becomes empty after Phase 3b changes | Medium | Status detail stays in Group A. Renaming title to "Statement Processing Details" makes the smaller content feel intentional. |

---

## What stays unchanged

To be explicit about what Phase 3b does NOT touch:

### Same file, untouched
- Card header block (filename, agent badge, status pill, download, chevron)
- Failed card variant (simpler render path)
- Card expansion logic (`buildExpansionContent`) except Group A title and content
- `buildPerStatementSignals()` cross-statement signal logic
- Per-statement sort and expand-all controls
- All other views (Workspace, Consolidated Validation, Technical Details, Upload)
- Hash-based routing
- Header bar (navy band + brand + status pill + download link)
- Reset workspace logic
- All helper functions: `escapeHtml`, `formatTimestamp`, `formatDuration`
- All existing CSS not specifically referenced above

### Other files, not touched
- `server.py` — response shape unchanged
- `backend/pdf_skill_adapter.py`
- `backend/pipeline.py`
- `backend/master_excel_generator.py`
- `backend/validation_engine.py`
- `backend/excel_generator.py`
- `backend/review_flag_engine.py`
- `backend/schemas.py`

---

## Standing constraints (carried from Phase 3a)

- File-by-file delivery with complete drop-in replacements (no patches)
- Compile/render checks before any test run
- Backup before any session touching multi-section files
- Free rule-based regression test before PDF Skill test
- No GitHub push without explicit review

---

## Communication discipline for Session 1

When Session 1 starts:

1. **Implementer (Claude)** reads this entire document before generating code
2. **No re-deriving decisions from chat history** — every decision is captured here
3. **Color treatment** is the only deferred decision; flagged for live discussion
4. **Verification cycle** must complete before stopping (compile → static → free regression → PDF Skill regression)
5. **Stop after C1** — do NOT proceed to C1b in the same session. Session 1 ends with the dual-layout intermediate state.

---

## Phase 3b success criteria

Phase 3b is complete when:

- All three sessions (C1, C1b, polish) are done
- All six verification tests pass
- Master Workbook generation still works (verified by 2-PDF PDF Skill regression)
- `V1_2_STATUS.md` has a Phase 3b completion section
- `V1_3_RELEASE_NOTES.md` "What's New" section includes Phase 3b changes
- A side-by-side comparison (Phase 2 backup vs Phase 3b output) demonstrates the bookkeeping-first reframing has visibly succeeded

---

*This document is the source of truth for Phase 3b implementation. Save it alongside V1_3_PHASE3_REVISED_PLAN.md. When Phase 3b begins, this spec becomes the starting point — not chat history.*
