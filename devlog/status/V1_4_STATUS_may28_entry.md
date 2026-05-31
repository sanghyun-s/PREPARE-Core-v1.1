# May 28, 2026 — Source B (Extraction Cross-Check) Production Build

**Phase 4 status: COMPLETE.**

With Source B landed today, both reconciliation integrity signals are now live on every PDF Skill statement. Source A (reconciliation snapshot) and Source B (extraction completeness) ship together as the v1.4 reconciliation layer.

## Surface inventory (Phase 4 close)

**Source A — reconciliation snapshot:** per-statement Excel Block 5 (waterfall) · per-statement card waterfall · Workspace tile ("X of N reconcile") · Master Excel STATEMENT RECONCILIATION block (3 count rows).

**Source B — extraction cross-check:** per-statement Excel Block 5b (EXTRACTION CROSS-CHECK sub-block) · Master Excel one-line summary inside the STATEMENT RECONCILIATION block.

Source B's web surfaces were intentionally skipped per the May 26 spec lock. Extraction integrity is an auditor's signal — it answers "did we capture every row?" — and is meaningful when reviewing the Excel deliverable, not when scanning the workspace.

## What shipped

Source B is a five-file production change across the PDF Skill data flow:

**pipeline.py** — new `_compute_source_b(all_transactions, recon_snapshot)` helper (~120 lines), called immediately after `_compute_reconciliation`. Buckets every extracted Transaction by `transaction_type`, sums the buckets, compares against the snapshot's stated activity totals. Returns a dict with status (complete / incomplete / unavailable) + per-bucket {stated, row_sum, delta} triples. Wired into the return dict and passed to `generate_excel_report` as a new kwarg.

**schemas.py** — new `ExtractionCheck` Pydantic model (status Literal + 5 buckets × 3 fields, 15 explicit `Optional[float]` declarations + a notes string), plus `Statement.extraction_check: Optional[ExtractionCheck] = None`. Pattern mirrors `ReconciliationSnapshot` / `reconciliation_snapshot`.

**server.py** — three small edits in the PDF Skill carry path: add `ExtractionCheck` to the schemas import, add `"extraction_check": stmt_result.get("extraction_check")` to the `agent_outputs.append` dict right next to the snapshot-carry line, build `ec_model` from the dict and pass `extraction_check=ec_model` to the `Statement(...)` constructor. Same carry-or-drop pattern as yesterday's snapshot fix.

**excel_generator.py** — new "EXTRACTION CROSS-CHECK" sub-block (Block 5b) in `write_summary_stats_sheet`, between Statement Reconciliation (Block 5) and Bookkeeping Review Signals (Block 6). Renders only when status is complete or incomplete. Verdict line (green ✓ "Complete — extracted rows match stated totals" or amber ⚠ "Incomplete — row sums diverge from stated totals") + 4-column table (Bucket / Stated / Row Sum / Delta). Skips bucket rows where both stated is None and row_sum is ~$0 (keeps the table compact for statements without checks/transfers).

**master_excel_generator.py** — Source B tally added to the existing per-statement loop (parallel to Source A). One-line summary inside the STATEMENT RECONCILIATION block right before Top Vendors: "Extraction Cross-Check: X of N show complete extraction" (with " · Y incomplete" suffix when Y > 0). Amber row when any incomplete. Line omitted entirely when no statement produced a usable check.

## The bucketing design

The bucket map is the load-bearing decision in Source B. It maps PDF Skill's 11 `transaction_type` values onto the 5 stated-activity buckets that bank statements report:

```
deposits     ← deposit + interest + reimbursement
withdrawals  ← vendor_payment     (NOT checks, NOT fees)
checks       ← check_payment
transfers    ← transfer + owner_draw
fees         ← bank_fee
Skipped      ← balance_line, payroll_deposit, metadata, unknown
```

Two non-obvious choices worth surfacing:

**Withdrawals contains vendor_payment only, not checks or fees.** Bank statements summarize "Total Withdrawals" as the ACH/card column — checks and fees are itemized on separate summary lines. Folding checks into withdrawals would have flagged every statement that itemizes checks as "incomplete" with a non-zero checks delta. This was the single most likely source of false positives; the spike on May 26 confirmed every real test case behaved correctly with this mapping.

**owner_draw lands in transfers, not withdrawals.** Owner draws are non-deductible cash movements, semantically closer to inter-account transfers than to vendor payments. Bank statements don't always have an explicit owner-draw line, but when transfers and owner-draws appear together, the statement's Total Transfers usually captures both.

The skipped types don't represent activity flows: balance_line is the running ledger, payroll_deposit is a separate report, metadata and unknown are catch-alls. Including any would introduce drift unrelated to extraction completeness.

The May 26 spike ran this mapping against all three real test PDFs and produced 15-for-15 clean deltas (Harbor 3 buckets, Northgate 4, Summit 5). Today's productionized helper reproduced the same result.

## Verification trace

End-to-end testing covered six cases against the productionized `_compute_source_b`:

| Case | Expected | Got | Notes |
|---|---|---|---|
| Harbor (needs_review reconciliation) | complete | complete, all deltas $0.00 | Reconciliation status is independent of extraction status — a statement can fail Source A while passing Source B |
| Northgate (balanced + checks bucket) | complete | complete, all deltas $0.00 | Validates the checks bucket via `check_payment` |
| Summit (balanced + transfers bucket) | complete | complete, all deltas $0.00 | Validates the transfers bucket via `transfer` |
| Synthetic: drop a $400 vendor_payment row | incomplete | incomplete, withdrawals delta −$400.00 | Confirms Source B catches extraction loss; proves the check has real power |
| Unavailable: no recon_snapshot | unavailable | unavailable | Rule-based / multi-agent path |
| Unavailable: snapshot present but extraction_complete=False | unavailable | unavailable | PDF Skill where account summary couldn't be parsed |

Final live verification on the 3-PDF web test:

- Workspace tile unchanged: "2 of 3 statements reconcile · Balanced 2 / Needs Review 1 / Unavailable 0" (Source A only, by design)
- All three per-statement cards unchanged — no Source B render, by design
- Per-statement Excel × 3 all show the new EXTRACTION CROSS-CHECK section with green ✓ verdict + bucket table. Summit shows all 5 buckets (the most demanding case), Northgate 4, Harbor 3.
- Master Excel Executive Summary shows the new line "Extraction Cross-Check: 3 of 3 show complete extraction" under the STATEMENT RECONCILIATION block, no amber

## Discipline event: file-state caught pre-edit

The day's near-miss: when I asked for your live `server.py` to base the drop-in on, the file you uploaded was actually a copy from before yesterday's snapshot-carry fix (returned 2 matches for `reconciliation_snapshot` instead of the expected 3). Your local `grep -c` on the actual live file returned 3 as expected, but the upload mechanism pulled a different file — likely an older backup sitting at the path I asked you to copy from.

The diagnostic-first protocol from yesterday caught this before any code was written:

```
[Uploaded file]:  grep -c reconciliation_snapshot → 2
[Live file]:      grep -c reconciliation_snapshot → 3
[Live md5]:       d2c7ac87811332d149b3b50f4feacf47
```

The discrepancy surfaced in seconds. Instead of writing code against the stale file, I reconstructed the live state in workspace (by re-applying yesterday's known line-378 fix as a sync step), then built the three Source B edits on top of that, then verified the resulting file had the expected marker counts (3 / 4 / 2 / 800 lines) before shipping.

This is the same lesson from yesterday but caught one step earlier in the loop — diagnostic before edit, not diagnostic after the patch breaks something. Pattern is becoming load-bearing: any change touching live code begins with a grep against the actual current state, regardless of how recent the upload feels.

## Code hygiene notes

- `_compute_source_b` mirrors `_compute_reconciliation`'s shape in pipeline.py — same input contract (transactions + snapshot), same output discipline (dict ready for schema construction). They're a matched pair and should evolve together.
- The `ExtractionCheck` model fields are deliberately verbose (5 buckets × 3 fields = 15 explicit `Optional[float]` declarations) rather than condensed into `Dict[str, BucketResult]`. Pydantic introspection is cleaner, frontend serialization is predictable, the schema self-documents in the OpenAPI output.
- The Excel block skips bucket rows where stated is None AND row_sum is ~$0 — keeps the table compact for statements without checks/transfers. The skip happens at render time, not in `_compute_source_b`, so the data dict is always complete for downstream consumers.
- The master line only renders when `ec_assessable = ec_complete + ec_incomplete > 0`. If every statement returned "unavailable" (e.g., a rule_based-only run), the line is omitted rather than showing "0 of 0" which would imply coverage that doesn't exist.

## What Phase 4 closes

Source A and Source B now run together as two independent integrity signals per statement:

1. **Source A** asks: *does the statement's stated math balance?* — catches the bank's own reconciliation errors (Harbor's $150 case).
2. **Source B** asks: *did we extract every row the statement reported?* — catches PDF Skill extraction loss (would catch a row PDF Skill missed or miscounted).

These are genuinely orthogonal. Harbor demonstrates the orthogonality directly: failed Source A (statement doesn't reconcile), passed Source B (we extracted every row the statement had).

What this means for the bookkeeping workflow: a CPA reviewing the per-statement Excel can now distinguish between *"the bank made an error"* (Source A flags, Source B passes), *"the extraction missed something — go back and verify"* (Source A passes, Source B flags), and *"this statement needs a full re-look"* (both flag). Three possible diagnostic outcomes instead of one ambiguous warning.

## What's next

Phase 4 is closed. Remaining v1.4 work is non-code:

- README rewrite (this devlog feeds into it directly)
- GitHub push
- Demo materials (pitch / video / LinkedIn narrative)
- App 2A (IRS Form Processor) — separate planned app, post-v1.4

**Architectural backlog** (for post-launch refinement after demo feedback):

- Per-statement / Consolidated split: cross-statement Group C currently sits on the per-statement card with the same data as Consolidated view; original conceptual split was per-statement = bookkeeping, consolidated = cross-validation. Scales badly past 3-4 PDFs.
- Card density: 9 distinct content blocks when expanded (headline tiles, activity classification, 5-metric strip, activity line, review summary, Group A summary, reconciliation waterfall, Group B signals, Group C cross-statement signals). Some redundancy worth trimming.
- `index.html` older backup naming: Group A title "Statement-Level Bookkeeping Summary" is pre-Phase-3b-C2 and should be "Statement Processing Details."
