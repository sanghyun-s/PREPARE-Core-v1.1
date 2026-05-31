# Phase 4C — Per-Statement Reconciliation Waterfall · Drop-in Snippets

Four surgical edits to `frontend/index.html` (your live C2 file, 3131 lines).
No backend/schema changes — all four read the `reconciliation_snapshot` field
already delivered by Phase 4B.

**Back up first** (you already keep timestamped backups in `frontend/backup/` —
make a `phase3b_c2_pre_4C` snapshot before editing).

Apply in order. Each says FIND (exact text already in your file) and what to do.

---

## Snippet 1 of 4 — CSS (the reconciliation block styles)

**FIND** this exact text (end of the Phase 3b CSS block):

```css
  .ps-vendor-review-row .vr-segment.review.zero .vr-value {
    color: var(--text-primary);
  }
  /* ───────────────── end Phase 3b CSS ───────────────── */
```

**INSERT immediately AFTER it** (after the `end Phase 3b CSS` comment line):

```css

  /* ─────────────────────────────────────────────────────────────────
   * v1.4 Phase 4C — per-statement reconciliation waterfall (Source A)
   *
   * Lives inside the Group A expansion. Three states: balanced (green check),
   * needs_review (amber text + icon, moderate — NO full band), unavailable
   * (muted note). Styled to match the existing .ps-mini-table / .ps-group look.
   * ───────────────────────────────────────────────────────────────── */
  .ps-recon {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.85rem 1rem;
  }
  .ps-recon-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.84rem;
  }
  .ps-recon-table td {
    padding: 0.3rem 0.5rem;
    border-bottom: none;
  }
  .ps-recon-op {
    width: 1.2rem;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    text-align: center;
    font-weight: 600;
  }
  .ps-recon-label {
    color: var(--text-secondary);
  }
  .ps-recon-amt {
    text-align: right;
    font-variant-numeric: tabular-nums;
    color: var(--text-primary);
    white-space: nowrap;
  }
  .ps-recon-calc-row td {
    border-top: 1px solid var(--border-strong);
    padding-top: 0.5rem;
    font-weight: 700;
  }
  .ps-recon-calc-row .ps-recon-label { color: var(--text-primary); }
  .ps-recon-reported-row .ps-recon-label { color: var(--text-secondary); }
  .ps-recon-diff-row td {
    border-top: 1px solid var(--border);
    padding-top: 0.45rem;
    font-weight: 700;
  }
  .ps-recon-diff-zero { color: var(--text-muted); }
  .ps-recon-diff-flag { color: var(--warn); }
  .ps-recon-verdict {
    display: flex; align-items: center; gap: 0.45rem;
    margin-top: 0.7rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--border);
    font-weight: 700;
    font-size: 0.88rem;
  }
  .ps-recon-verdict-icon { width: 16px; height: 16px; flex-shrink: 0; }
  .ps-recon-verdict-icon svg { width: 16px; height: 16px; }
  .ps-recon-verdict.ps-recon-balanced,
  .ps-recon-verdict.ps-recon-balanced .ps-recon-verdict-icon { color: var(--ok); }
  .ps-recon-verdict.ps-recon-needs-review,
  .ps-recon-verdict.ps-recon-needs-review .ps-recon-verdict-icon { color: var(--warn); }
  .ps-recon-notes {
    margin-top: 0.6rem;
    font-size: 0.78rem;
    color: var(--text-muted);
    font-style: italic;
    line-height: 1.45;
  }
  .ps-recon-unavailable {
    display: flex; align-items: flex-start; gap: 0.5rem;
    font-size: 0.83rem;
    color: var(--text-muted);
  }
  .ps-recon-unavailable-icon { width: 15px; height: 15px; flex-shrink: 0; margin-top: 1px; }
  .ps-recon-unavailable-icon svg { width: 15px; height: 15px; }
```

---

## Snippet 2 of 4 — JS function `renderReconciliation`

**FIND** this exact text (the end of the Phase 3b render helpers):

```javascript
  /* ───────────────── end Phase 3b helpers ───────────────── */
```

**INSERT immediately AFTER it**:

```javascript

  /* ─────────────────────────────────────────────────────────────────
   * v1.4 Phase 4C — per-statement reconciliation waterfall (Source A)
   *
   * renderReconciliation(s) — full balance waterfall, rendered inside the
   * Group A expansion. Reads s.reconciliation_snapshot (added in Phase 4B).
   *
   * Three states:
   *   - unavailable : muted note. Rule-based/multi-agent engines (no snapshot),
   *                   or PDF Skill that found no account-summary section.
   *   - balanced    : green check, difference $0.00 in muted text.
   *   - needs_review: amber text + ⚠ icon, difference shown in amber (moderate,
   *                   no full band — per Phase 4C design decision).
   *
   * Transcribe-don't-compute: the seven figures are AS STATED by the model;
   * calculated_ending / difference / status were computed server-side (4B).
   * ───────────────────────────────────────────────────────────────── */
  function fmtReconAmount(n) {
    if (n == null) return '—';
    const v = Number(n);
    const sign = v < 0 ? '-' : '';
    return sign + '$' + Math.abs(v).toLocaleString('en-US',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function renderReconciliation(s) {
    const snap = s.reconciliation_snapshot;

    if (!snap || typeof snap !== 'object' || snap.status === 'unavailable'
        || !snap.extraction_complete) {
      return `
        <div class="ps-group">
          <h4 class="ps-group-title">Statement Reconciliation</h4>
          <div class="ps-recon ps-recon-unavailable">
            <span class="ps-recon-unavailable-icon">${ICONS.info}</span>
            <span>Reconciliation not available for this statement${
              (!snap || snap.status === 'unavailable')
                ? ' (balance summary not extracted).'
                : '.'}</span>
          </div>
        </div>`;
    }

    const isBalanced = snap.status === 'balanced';
    const stateClass = isBalanced ? 'ps-recon-balanced' : 'ps-recon-needs-review';
    const verdictIcon = isBalanced ? ICONS.checkSimple : ICONS.warnSimple;
    const verdictLabel = isBalanced ? 'Balanced' : 'Needs Review';

    const rows = [
      { label: 'Beginning balance',  value: snap.beginning_balance,  op: '' },
      { label: 'Deposits & credits', value: snap.total_deposits,     op: '+' },
      { label: 'Withdrawals',        value: snap.total_withdrawals,  op: '−' },
      { label: 'Checks',             value: snap.checks,             op: '−' },
      { label: 'Transfers',          value: snap.transfers,          op: '−' },
      { label: 'Fees & charges',     value: snap.fees,               op: '−' },
    ];

    const rowHtml = rows.map(r => `
      <tr>
        <td class="ps-recon-op">${r.op}</td>
        <td class="ps-recon-label">${r.label}</td>
        <td class="ps-recon-amt num">${fmtReconAmount(r.value)}</td>
      </tr>`).join('');

    const diffClass = isBalanced ? 'ps-recon-diff-zero' : 'ps-recon-diff-flag';

    return `
      <div class="ps-group">
        <h4 class="ps-group-title">Statement Reconciliation</h4>
        <div class="ps-recon ${stateClass}">
          <table class="ps-recon-table">
            <tbody>
              ${rowHtml}
              <tr class="ps-recon-calc-row">
                <td class="ps-recon-op">=</td>
                <td class="ps-recon-label">Calculated ending</td>
                <td class="ps-recon-amt num">${fmtReconAmount(snap.calculated_ending_balance)}</td>
              </tr>
              <tr class="ps-recon-reported-row">
                <td class="ps-recon-op"></td>
                <td class="ps-recon-label">Reported ending (as stated)</td>
                <td class="ps-recon-amt num">${fmtReconAmount(snap.reported_ending_balance)}</td>
              </tr>
              <tr class="ps-recon-diff-row">
                <td class="ps-recon-op"></td>
                <td class="ps-recon-label">Difference</td>
                <td class="ps-recon-amt num ${diffClass}">${fmtReconAmount(snap.difference)}</td>
              </tr>
            </tbody>
          </table>
          <div class="ps-recon-verdict ${stateClass}">
            <span class="ps-recon-verdict-icon">${verdictIcon}</span>
            <span class="ps-recon-verdict-label">${verdictLabel}</span>
          </div>
          ${snap.notes ? `<div class="ps-recon-notes">${escapeHtml(snap.notes)}</div>` : ''}
        </div>
      </div>`;
  }
```

---

## Snippet 3 of 4 — Wire into Group A (buildExpansionContent)

**FIND** this exact text (the Group A push in `buildExpansionContent`):

```javascript
    parts.push(`
      <div class="ps-group">
        <h4 class="ps-group-title">Statement Processing Details</h4>
        <div class="ps-group-status">
          <div class="status-line ${statusClass}">
            ${statusIcon}
            <span>${escapeHtml(statusLabel)}</span>
          </div>
          <div class="status-detail">${statusDetail}</div>
        </div>
      </div>`);
```

**INSERT immediately AFTER it** (a new line that appends the reconciliation block
right after the Group A "Statement Processing Details" block):

```javascript

    // v1.4 Phase 4C — append the reconciliation waterfall after the Group A
    // status block. Renders the unavailable state gracefully for engines
    // without a snapshot (rule_based / multi_agent).
    parts.push(renderReconciliation(s));
```

---

## Snippet 4 of 4 — needs_review breadcrumb (renderReviewSentence)

**FIND** this exact text (end of `renderReviewSentence`):

```javascript
    parts.push(`${conf}% confidence`);
    return parts.join(' · ');
```

**REPLACE it with** (adds a subtle "reconciliation needs review" breadcrumb to
the always-visible review strip when the statement doesn't balance):

```javascript
    parts.push(`${conf}% confidence`);
    // v1.4 Phase 4C — subtle always-visible breadcrumb: surface a reconciliation
    // imbalance so an unbalanced statement hints at it without expanding the card.
    const reconSnap = s.reconciliation_snapshot;
    if (reconSnap && reconSnap.status === 'needs_review') {
      parts.push('reconciliation needs review');
    }
    return parts.join(' · ');
```

---

## After applying

1. Hard-refresh the browser (Cmd-Shift-R) to bust the cached HTML.
2. Free check: run a **rule-based** 2-PDF process → expand a card → Group A should
   show "Statement Reconciliation — not available (balance summary not extracted)".
   The breadcrumb should NOT appear (no snapshot). Nothing else changes.
3. PDF Skill check (~$0.12): process `harbor_national_unbalanced.pdf` →
   - the card's always-visible review strip should end with "· reconciliation needs review"
   - expand the card → Group A shows the full waterfall: 3000 / +6000 / −4820 / −0 /
     −0 / −30 = 4150 calculated vs 4000 reported, Difference −$150.00 in amber,
     verdict "⚠ Needs Review", and the model's note in italics.
   - process a balanced statement (northgate/summit) → verdict "✓ Balanced",
     difference $0.00 muted, no breadcrumb.
