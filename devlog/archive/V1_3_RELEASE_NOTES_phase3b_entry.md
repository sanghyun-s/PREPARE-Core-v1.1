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
