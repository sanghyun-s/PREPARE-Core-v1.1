# Phase 4D / v1.4 — Per-Statement Excel Reconciliation (Design Spec)

**Status**: Locked for build. Design is fully settled through prior discussion;
this document records the locked decisions and the exact edit anchors so the
build is mechanical.
**Target version**: v1.4
**Scope frame**: CONTINUATION of Phase 4. Phase 4C taught the *web Per-Statement
card* to display the reconciliation snapshot. Phase 4D teaches the *per-statement
Excel workbook* (`excel_generator.py`) to display the same snapshot — plus two
user-requested Transactions-sheet enhancements. No new backend logic, no schema
change: 4D consumes the exact `reconciliation_snapshot` already computed in 4B.

---

## 0. PREPARE directive (locked — unchanged)

> PREPARE is a bookkeeping and statement reconciliation aid... it is NOT a full
> 1099 filing processor.

4D is squarely in scope: it surfaces statement-integrity (does the statement
balance?) in the downloadable deliverable, mirroring what the app already shows.

---

## 1. Goal

Two coordinated outcomes:

1. **Mirror the Per-Statement card in the Excel.** Restructure the per-statement
   workbook so its landing sheet is a one-page statement overview that mirrors
   the web card's expansion — including the reconciliation waterfall. Someone
   opening the Excel sees the same at-a-glance picture they'd see in the app,
   then drills into the detail sheets.

2. **Two Transactions-sheet enhancements** (user-requested, from bookkeeping
   experience): per-type color-coding and native interactive sort/filter.

The value to a bookkeeper: the deliverable leads with "did this statement parse
completely and reconcile?" before the vendor/transaction detail, and the
transaction detail itself is scannable by type and re-orderable at will.

---

## 2. Two-pass build (locked)

4D ships in two isolated passes so a layout regression in one can't mask the
other:

- **4D-core** — the workbook restructure (Summary Stats becomes the leading
  sheet and gains five blocks including the reconciliation waterfall) + the
  `pipeline.py` plumbing to pass the computed snapshot and breakdown to the
  generator. Touches: `excel_generator.py` (Summary Stats sheet + entry point),
  `pipeline.py` (PDF Skill call site only).
- **4D-plus** — the Transactions-sheet enhancements (per-type cell coloring +
  AutoFilter). Touches: `excel_generator.py` (Transactions sheet only).

The two passes edit different functions; no overlap, no re-work. Build and
verify 4D-core first, then layer 4D-plus on a known-good base.

---

## 3. Sheet order (locked)

Current order → new order:

| Position | Current sheet (tab name)         | New sheet (tab name)             |
|----------|----------------------------------|----------------------------------|
| index 0  | `Vendor Summary` (opens first)   | **`Summary Stats`** (opens first)|
| index 1  | `Transactions`                   | `Vendor Summary`                 |
| index 2  | `Summary Stats`                  | `Transactions`                   |

**CRITICAL — tab names are preserved exactly.** `agent_app.py.run_single_agent`
(Excel-recovery fallback) and possibly other paths reference the three sheets by
hardcoded string: `"Vendor Summary"`, `"Transactions"`, `"Summary Stats"`.
Reordering changes only sheet *position*, which openpyxl tracks independently of
name. All three tab names stay byte-identical. Only positions change.

Implementation note: `create_sheet(name, index)` accepts an explicit position
argument. The generator will create `Summary Stats` at index 0, `Vendor Summary`
at index 1, `Transactions` at index 2 (or equivalently, create in any order and
set `wb.move_sheet` / pass the index arg). The simplest mechanical change is to
swap the index arguments in the three `create_sheet` calls and reorder the three
`write_*_sheet` calls in `generate_excel_report` accordingly.

---

## 4. The new Summary Stats sheet (locked — 4D-core)

Tab name: **`Summary Stats`** (unchanged — load-bearing, see §3).
Title cell A1 text: **`STATEMENT BOOKKEEPING SUMMARY`** (unchanged — user's
choice; this is a non-load-bearing visual heading, no code references it).

Top-to-bottom layout. Every block below the title is **auto-omitted gracefully**
when its data is absent (rule-based engine: no breakdown, no snapshot), exactly
like the existing `_has_classifier_data` pattern. For rule-based, the sheet
degrades to roughly its current content (extraction/review stats + scope notes).

1. **Title + generated timestamp** (existing rows 1–2, retained).

2. **Statement Processing Details** — natural-language line mirroring the card:
   > Statement parsed successfully with PDF Skill
   > 9 rows identified. 7 included as vendor payments for 1099 aggregation.
   > 2 excluded (1 deposit, 1 bank fee).

   - Counts from `transaction_count` + excluded count (derivable from breakdown
     or passed counts).
   - **Parenthetical "(1 deposit, 1 bank fee)" IS included** (locked Decision 2).
     Built from the `breakdown` dict, same logic as the frontend's
     `formatExcludedBreakdown` — enumerate non-`vendor_payment` types with
     count > 0, singular/plural aware.
   - Rule-based fallback: generic "Statement parsed successfully" line without
     the breakdown detail.

3. **Activity Classification** — type breakdown, mirroring the card:
   > Vendor payments 7 · Deposits 1 · Bank fees 1

   - From the `breakdown` dict, rendered in the canonical type order
     (vendor_payment, check_payment, deposit, payroll_deposit, balance_line,
     transfer, bank_fee, interest, reimbursement, owner_draw, metadata, unknown),
     only non-zero types shown, friendly labels via the existing
     `_friendly_transaction_type` mapping (pluralized for this context).
   - Omitted entirely when no breakdown (rule-based).

4. **Vendor / 1099 Review** — one-line summary, mirroring the card:
   > Included Total $4,820.00 · Vendors 7 · Review Needed 5 · Over $600 3 · Confidence 97%

   - All values derive from `summaries` (already in scope in the generator).
     Included Total = sum of summary totals; Vendors = len(summaries);
     Review Needed = count needs_review; Over $600 = count total ≥ 600;
     Confidence = representative/average extraction confidence.
   - This block renders for BOTH engines (summaries always exist).

5. **Statement Reconciliation** — the waterfall (the 4D centerpiece), mirroring
   the card exactly:

   ```
       Beginning balance                    $3,000.00
   +   Deposits & credits                   $6,000.00
   −   Withdrawals                          $4,820.00
   −   Checks                                   $0.00
   −   Transfers                                $0.00
   −   Fees & charges                          $30.00
   =   Calculated ending                   $4,150.00
       Reported ending (as stated)         $4,000.00
       Difference                            $150.00      ← amber when needs_review
       ⚠ Needs Review     (or  ✓ Balanced)
       <verbatim model note, italic, muted>
   ```

   - Reads the **computed** `reconciliation_snapshot` passed from the pipeline
     (see §6). Uses the snapshot's already-computed `calculated_ending_balance`,
     `difference`, `status` — **does NOT recompute** (arithmetic lives in exactly
     one place, `_compute_reconciliation`, per the Phase 4 spec).
   - Operator column (+ − = ) in a narrow left cell, label, right-aligned
     currency — same three-column shape as the web `.ps-recon-table`.
   - Status row: green "Balanced" when `status == "balanced"`, amber
     "Needs Review" when `status == "needs_review"`. Difference cell amber on
     needs_review, muted on balanced. (Reuse existing fill/font constants where
     possible — `REVIEW_FILL` yellow is too loud for the diff cell; use an amber
     font color matching the UI's moderate treatment. See §4.1.)
   - The verbatim `notes` string from the snapshot renders below in italic muted
     font (matches the card's `.ps-recon-notes`).
   - **Omitted entirely** when `status == "unavailable"`, snapshot is None, or
     `extraction_complete` is False (rule-based always hits this) — same gate as
     the frontend's `renderReconciliation`.

6. **Bookkeeping Review Signals** — mirroring the card's Group B:
   > Review needed 5 · Over $600 3

   - From `summaries`. Renders for both engines. Omit individual signals with
     count 0 (matches the card, which only shows tags with count > 0).

7. **Scope notes** (existing, retained at the bottom) — the "per-statement
   output supports statement-level bookkeeping review; cross-statement and 1099
   threshold review live in the Master Workbook" note.

### 4.1 Reconciliation cell styling (locked)

To match the card's MODERATE needs_review treatment (amber text + ⚠, no loud
band — the Phase 4C decision), the Excel uses **font color**, not heavy fills,
for the verdict and difference:

- Balanced: verdict text + check in green (`#16A34A`, matches `--ok`).
- Needs review: verdict text + ⚠ and the Difference value in amber
  (`#EA580C`, matches `--warn`). No row fill on the waterfall — keep it clean,
  like the card.
- Waterfall labels in body font; the Calculated ending row bold (matches the
  card's `.ps-recon-calc-row` font-weight:700); a thin top border above
  Calculated ending and above Difference (matches the card's rule lines).

---

## 5. Transactions sheet enhancements (locked — 4D-plus)

Two changes, both on the Transactions sheet only. Both apply only meaningfully
in skill-mode (the sheet only has the Transaction Type column and type data when
`use_skill_mode` is true); AutoFilter applies in both modes.

### 5.1 Per-type cell coloring — Option A (locked)

Color **only the "Transaction Type" cell**, not the whole row. This avoids
collision with the existing row-fill priority system (yellow `REVIEW_FILL` for
needs_review, subtle grey `EXCLUDED_FILL` for excluded). The row fills stay as
the review/exclusion layer; the type cell becomes a color key.

Palette — reuse the existing design-token families for consistency with the rest
of the workbook. Proposed associations (pending user's bookkeeping instinct, §8):

| transaction_type            | Cell fill (light tint)        | Rationale                |
|-----------------------------|-------------------------------|--------------------------|
| deposit, interest, reimbursement | light green (`E8F5E9`, `ELIGIBLE_FILL` family) | money in       |
| vendor_payment, check_payment    | light blue (`E3F2FD`, `MISC_FILL` family)      | money out, normal |
| bank_fee                    | light red/amber               | money out, cost          |
| transfer, owner_draw        | light purple                  | internal movement        |
| payroll_deposit, balance_line, metadata, unknown | light grey (`EXCLUDED_FILL`)   | non-vendor / structural  |

Only the Type cell gets the fill; the rest of the row obeys the existing
review/exclusion priority. When a row is both (e.g. an excluded deposit), the
Type cell is green-for-deposit while the row is grey-for-excluded — both signals
visible, no conflict. This is the whole point of Option A.

### 5.2 Interactive sort/filter — Option X: Excel AutoFilter (locked)

Add native Excel AutoFilter to the Transactions header row:

```python
ws.auto_filter.ref = f"A{header_row}:{last_col_letter}{last_data_row}"
```

This gives the user the filter-arrow dropdowns on every column. They sort/filter
themselves in Excel — by date, canonical vendor, transaction type, amount, any
column — live and reversible. One line, any column, the idiom accountants
already know. We do NOT pre-sort (rejected Option Y — fixed order, would need
multiple sheets to offer multiple orders).

Layout note: the Transactions sheet has title rows (1–2 area) and a header at
row 3, with `freeze_panes = "A4"`. AutoFilter `ref` starts at the header row (3)
and spans through the last data row. Freeze panes and AutoFilter coexist
cleanly. If there are zero data rows, guard against an empty range.

---

## 6. Pipeline plumbing (locked — 4D-core, `pipeline.py`)

Two call sites exist for `generate_excel_report`:
- **Line 283 — PDF Skill path** (inside `run_pipeline_pdf_skill`, def at line 184).
  Identified by `all_transactions=all_txns` and `pdf_skill_metadata=skill_result.metadata`.
  **This site gets the changes.**
- **Line 483 — rule-based path.** No snapshot data. **Untouched.** The
  generator's auto-fallback omits the reconciliation block — the correct
  graceful degradation (already verified in 4C's rule-based test).

The computed snapshot is currently built **inline in the return dict** at lines
374–375:
```python
"reconciliation_snapshot": _compute_reconciliation(
    skill_result.reconciliation_snapshot
),
```
This is BELOW the line-283 Excel call, so no local holds it at call time. The fix
lifts the computation into a local above the call, used in BOTH places, so
`_compute_reconciliation` is still invoked **exactly once** (preserves the
"arithmetic in one place" principle):

**Edit 1 — add local before the Excel call.** Anchor: after
`eligibility = classify_all_vendors(summaries)` (line 278), before the line-283
call:
```python
    # v1.4 Phase 4D: compute the reconciliation snapshot ONCE here so both the
    # Excel generator (below) and the response dict reference the same result.
    recon_snapshot = _compute_reconciliation(skill_result.reconciliation_snapshot)
```

**Edit 2 — two new kwargs on the line-283 call.** Anchor: after
`pdf_skill_metadata=skill_result.metadata,` (line 292):
```python
        reconciliation_snapshot=recon_snapshot,
        breakdown=skill_result.breakdown,
```

**Edit 3 — replace inline computation at 374–375 with the local:**
```python
        "reconciliation_snapshot": recon_snapshot,
```

Net: one new local, two new kwargs, one inline→local swap. `_compute_reconciliation`
called once. Excel waterfall and UI waterfall are guaranteed identical (same
dict, same source). The generator's `**kwargs` already swallows the two new
args; `breakdown` and `reconciliation_snapshot` become named params consumed by
the new Summary Stats logic.

---

## 7. Generator entry-point signature (locked — 4D-core, `excel_generator.py`)

`generate_excel_report` currently accepts `*, all_transactions=None,
pdf_skill_metadata=None, **kwargs`. Add two explicit keyword params so they're
first-class (not silently swallowed):

```python
def generate_excel_report(
    output_path, transactions, normalized, summaries, eligibility=None, *,
    all_transactions=None,
    pdf_skill_metadata=None,
    reconciliation_snapshot=None,   # v1.4 Phase 4D
    breakdown=None,                 # v1.4 Phase 4D
    **kwargs,
):
```

Both default to None → when absent (rule-based line-483 call, or any caller that
doesn't pass them), the Summary Stats sheet omits the dependent blocks. The
`write_summary_stats_sheet` signature gains `reconciliation_snapshot` and
`breakdown` (and the data needed for the review/processing lines — it already
receives `summaries` and `transactions`).

---

## 8. Open palette question (non-blocking)

§5.1's color associations are a proposal grounded in the existing tokens. The
user may override based on bookkeeping instinct (e.g. "fees red because money
out," "deposits green because money in"). This does not block 4D-core (which has
no type coloring); it can be settled before or during 4D-plus. Default to the
§5.1 table if no override.

---

## 9. Verification plan (unchanged discipline)

For each pass:
1. `py_compile` both changed files clean in place.
2. **Free rule-based 3-PDF regression** first: confirm Summary Stats now opens
   first, reconciliation + activity blocks are absent gracefully, Vendor Summary
   and Transactions intact, nothing regressed, tab names unchanged (so the
   `agent_app.py` lookup still resolves).
3. **One ~$0.12 Harbor PDF Skill run**: confirm the Excel waterfall renders
   matching the UI — 3,000 / +6,000 / −4,820 / −0 / −0 / −30 = 4,150 vs 4,000,
   Difference $150.00 amber, ⚠ Needs Review, verbatim note. Confirm a balanced
   statement (northgate/summit) shows ✓ Balanced. For 4D-plus, additionally
   confirm Type cells are colored and the filter arrows work in Excel.

Delivery: complete drop-in `excel_generator.py` + `pipeline.py` (4D-core), then
the Transactions changes (4D-plus). Backups before replacement, per convention.

---

## 10. Decisions locked (summary)

- A — type coloring on the Transaction Type CELL only (not whole row).
- X — Excel AutoFilter for sort/filter (interactive, any column; not pre-sorted).
- Sheet order: Summary Stats (0) → Vendor Summary (1) → Transactions (2).
- Summary Stats tab name unchanged (`Summary Stats`); title cell unchanged
  (`STATEMENT BOOKKEEPING SUMMARY`).
- Decision 2: include the "(1 deposit, 1 bank fee)" parenthetical.
- Decision 4: 4D-core first, then 4D-plus.
- Pipeline: edit line-283 (PDF Skill) call only; line-483 (rule-based) untouched;
  lift `_compute_reconciliation` to a local so it's called exactly once.
