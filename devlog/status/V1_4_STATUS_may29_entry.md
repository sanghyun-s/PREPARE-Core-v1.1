# May 29, 2026 — Phase 5A: Per-Statement Card Information Architecture Cleanup

**Phase 5A status: COMPLETE.**

Phase 5A was an information architecture pass, not a feature build. After Phase 4 closed with Source A and Source B both functionally complete, the Per-Statement card had accumulated too many good features at the same visual level — it was trying to answer both "what happened with this one statement" and "how does this statement relate to others" at once, with full cross-statement data tables embedded inside each card. At 3 statements that meant the same name-variant data rendered four times across the UI; at 10 statements it would have rendered eleven times.

Phase 5A rebalances the surfaces around the conceptual split established in the recalibration discussion:

- **Per-Statement** = bookkeeping and statement-integrity lens
- **Consolidated Validation** = cross-statement vendor/name/1099 validation lens
- **Excel workbooks** = detailed accountant-ready audit trail

The per-statement card now answers exactly one question: *"Is this individual statement parsed, reconciled, and extraction-complete?"*

## What shipped

Five surgical edits, all frontend-only (`frontend/index.html`), all applied patch-by-patch via grep-anchored str_replace with verification after each:

**Patch 1 — Remove duplicate parsed/included/excluded breakdown line.** The "9 parsed · 7 included · 2 excluded" line directly duplicated the three headline tiles above it. Removed the 18-line `activityLine` builder and its render site. The headline tiles remain as the single source of truth for these counts.

**Patch 2 — Trim the green review summary line.** Removed the `name variants` and `near threshold` clauses from `renderReviewSentence`. Both were cross-statement findings that belong in Consolidated Validation. The line now reads "Review 7 flagged for review · 3 over $600 · 97% confidence" — only statement-local facts. The `discrepancies` clause was kept because it signals a possible extraction issue in *this specific statement* (actionable at the per-statement level).

**Patch 3 — Remove the STATEMENT ACTIVITY BREAKDOWN table.** The table inside Group A duplicated the inline Activity Classification line in the always-visible card body. Removed the 26-line `breakdownHtml` builder and its render site. The prose summary above ("9 rows identified. 7 included as vendor payments for 1099 aggregation. 2 excluded (1 deposit, 1 bank fee).") stays, and the inline Activity Classification line continues to show the per-type counts.

**Patch 4 — Add Source B one-line status indicator.** The `extraction_check` field has been on every PDF Skill statement's response since the May 28 build but had no web surface. Phase 5A wires it into the per-statement card with three render states:

- complete → green ✓ "Extraction complete"
- incomplete → amber ⚠ "Extraction incomplete — see workbook"
- unavailable → muted gray "Extraction check unavailable"

Positioned immediately after the Statement Reconciliation block, presenting Source A (reconciliation balance) and Source B (extraction completeness) as a paired statement-integrity lens. Bucket-level detail (stated / row_sum / delta per bucket) stays in Excel only — the card shows status, the workbook shows evidence.

**Patch 5 — Replace Group C tables with a one-line pointer.** The three full data tables inside the per-statement card's Group C (Near-Threshold Vendors, Possible Extraction Discrepancies, Name Variant Flags) collapsed into a single pointer line: *"Cross-statement signals involving this statement: X findings · View in Consolidated Validation."* The section header stays so users still see that cross-statement entanglements exist for this file, but the tables move to their architectural home in Consolidated Validation where they belong.

## File metrics

Live `frontend/index.html` size across the five patches:

| Stage | Bytes | Lines | Net change |
|---|---|---|---|
| Phase 4 end (May 28) | 153,768 | 3,599 | baseline |
| After Patch 1 | 153,209 | 3,582 | −559 / −17 |
| After Patch 2 | 153,332 | 3,583 | +123 / +1 |
| After Patch 3 | 152,578 | 3,561 | −754 / −22 |
| After Patch 4 | 158,030 | 3,654 | +5,452 / +93 |
| After Patch 5 (final) | 155,459 | 3,592 | −2,571 / −62 |

Net across all five patches: **+1,691 bytes / −7 lines**. The Source B addition roughly offset the duplicate-and-table removals — about what would be expected for a rebalance pass rather than a strip-down. The card is structurally slimmer (fewer distinct content blocks) but the source file gained the new component code.

## Verification

Live web UI test on all three real PDFs (Harbor + Northgate + Summit) after the apply:

**Workspace view:** unchanged. KPI tiles, Processing at a Glance, Validation Overview 4-tile row, Statement Reconciliation tile ("2 of 3 statements reconcile"), and Master Workbook CTA all identical to the May 28 baseline. Zero regressions on surfaces 5A was not supposed to touch.

**Per-Statement cards:** all three exercise the rebalance correctly.

- Harbor (the demanding case — needs_review reconciliation + 9 cross-statement findings + 3 Source B buckets): Source A waterfall shows amber Needs Review pill with $150 difference, Source B shows green ✓ Extraction complete, Group C collapsed to pointer reading "9 findings · View in Consolidated Validation"
- Northgate (balanced + with checks bucket): Source A balanced, Source B complete, pointer "7 findings"
- Summit (balanced + with transfers + 2 checks — exercises 5 of 5 Source B buckets): same pattern, pointer "3 findings"

**Consolidated Validation:** untouched, all four tables present with their full data. The 8 name variants that previously rendered four times across the UI (once per card + once here) now render once, in their architectural home. This is the most visible architectural payoff of Phase 5A — the duplication is gone, the cross-statement command center is the single source of truth.

**Per-statement and master Excel workbooks:** untouched.

## Discipline events

Two file-state slips caught before any edit landed:

**Stale upload, attempt 1.** The first `index.html` I received for diagnostic was 143,845 bytes (216 lines short of live). The 216-line gap turned out to be exactly the Phase 4C reconciliation waterfall (the `renderReconciliation` function plus its CSS block). Pattern: a backup file from before the May 27 Phase 4C restoration was sitting at a path that got picked up instead of the live file.

**Upload limit hit on retry.** The corrected file couldn't be re-uploaded because the chat session had hit the file drop limit. Switched to Option B — work with the file I had, plus targeted grep diagnostics run on the Mac to verify every anchor against ground truth before any edit was proposed.

The Option B approach turned out to be cleaner than full re-upload: the diagnostic pass produced a head-to-head anchor comparison that confirmed (a) which anchors existed in both files at consistent offsets, (b) which existed in live but not in my copy (renderReconciliation), and (c) which were unique enough to use as str_replace anchors regardless of the line-number drift.

**Patch-by-patch with verification.** Once the diagnostic was complete, each patch shipped as a separate `python3` apply block + grep verification block. The python3 pattern (using `content.count(anchor) == 1` as a precondition) provides built-in safety: if the anchor isn't found or isn't unique, the file isn't modified at all. Every one of the five patches verified clean before the next one was sent.

This was a slower workflow than the workspace-edit-then-ship pattern from Phases 2-4. It was also the right workflow given the file-state ambiguity. The discipline rule from yesterday's devlog held: diagnostic before edit, regardless of how recent the upload feels.

## Architectural payoff

The conceptual split is now visible in the UI:

| Surface | Question it answers | Phase 5A change |
|---|---|---|
| **Per-Statement card** | Is this individual statement parsed, reconciled, and extraction-complete? | Slimmed: 6–7 content blocks, statement-local data only, with Source A waterfall + Source B status as paired integrity signals |
| **Consolidated Validation** | Across all statements, what vendor/name/1099 issues need review? | Strengthened by absence of duplication — now the single home for cross-statement tables |
| **Master / per-statement Excel** | What is the full detailed evidence behind those summaries? | Unchanged — Source B bucket detail remains here, the audit-ready package |

The card density problem we set out to fix is fixed. The 8 name variants that used to render four times now render once. The same data lives in exactly one place per the new framing, with compact pointers connecting the surfaces.

## What this completes

**v1.4 frontend information architecture is settled.** Phase 4 functionality (Source A + Source B) was already complete; Phase 5A brings the information architecture into alignment with the original conceptual split (per-statement = bookkeeping, consolidated = cross-validation, Excel = audit trail).

Remaining v1.4 follow-ups are split into two buckets:

**Optional polish (Phase 5B / 5C):**

- **5B Consolidated Validation strengthening** — heading wording, subtitle clarification, possibly a "Statements analyzed: …" header line. Small textual work. Worth doing only if Consolidated feels weak relative to Per-Statement after live review.
- **5C Excel alignment check** — review-only pass to confirm the workbooks still make sense given the rebalance. No code changes expected.

**Non-code (Sunday targets):**

- README rewrite (this devlog + the May 27/28 entries feed it directly)
- GitHub push
- Demo materials (pitch / video / LinkedIn narrative)
- App 2A (IRS Form Processor) — separate planned app, post-v1.4

**Architectural backlog (post-launch, after demo feedback):**

- Source B `notes` field polish — the schema field exists and is unused; populating it on `incomplete` would give the per-statement Excel a plain-language explanation of which buckets diverged. ~30 lines total. Only meaningful when a real PDF triggers the incomplete code path.
- Per-statement card density second pass — depending on live feedback, there may be further trim opportunities (e.g., Group A's helper paragraph could potentially fold into the prose summary)
- Historical comment cleanup in `index.html` — two surviving "Statement Activity Breakdown" references from old change-log comments. Harmless, inert, low priority.
