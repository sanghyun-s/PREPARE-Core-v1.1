"""
PREPARE v1.0 — Response Contract Schema
----------------------------------------
Single source of truth for the /api/process response payload.

Design goals:
  * Stable top-level structure so the frontend can render against a fixed
    contract instead of reaching into backend internals.
  * Optional/advanced fields are nullable; the top-level shape never changes
    based on engine choice or partial failures.
  * One lightweight schema_version field for forward compatibility — this
    refactor's contract is "1.0".

Conventions:
  * file_id      — internal stable identifier (uuid stem). Backend-only.
  * original_filename — user-facing filename. Frontend renders this.
  * statement labels inside validation findings are resolved to original
    filenames before serialization (server.py applies the filename_map).
  * status taxonomy at top level: "success" | "partial" | "failed"
    Granular reason carried in `failure_reason` ("rate_limit" | "extraction"
    | "other" | None).
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Run-level summary (drives the five Reconciliation Summary KPI cards)
# ---------------------------------------------------------------------------

class Summary(BaseModel):
    total_transactions: int
    unique_vendors: int
    total_reconciled: float
    vendors_over_threshold: int
    review_needed_count: int
    successful_count: int
    failed_count: int

    # v1.4 Phase 4E (web tile) — cross-statement reconciliation roll-up.
    # Counts across SUCCESSFUL statements only (so failed statements don't
    # muddy the denominator). A missing snapshot — rule_based / multi_agent
    # engines, or PDF Skill that found no balance summary — folds into
    # "unavailable". Defaults to zero for backward compatibility with any
    # caller that doesn't set them.
    reconciliation_balanced: int = 0
    reconciliation_needs_review: int = 0
    reconciliation_unavailable: int = 0


# ---------------------------------------------------------------------------
# Per-statement result (drives the Per-Statement compact cards)
# ---------------------------------------------------------------------------

class ReconciliationSnapshot(BaseModel):
    """
    v1.4 (Phase 4) — statement-level balance reconciliation.

    The seven extracted fields are transcribed from the statement's account-
    summary section by the PDF Skill engine (AS STATED — never computed by the
    model). The three computed fields are derived server-side in
    pipeline.run_pipeline_pdf_skill via the locked balance equation:

        calculated_ending = beginning + deposits - withdrawals - checks
                            - transfers - fees
        difference        = calculated_ending - reported_ending
        status            = "balanced" if |difference| <= 0.01
                            else "needs_review"
                            ("unavailable" when extraction incomplete)

    Engines that don't extract balance figures (rule_based, multi_agent) omit
    this object entirely (None on the Statement), which the frontend renders as
    "reconciliation not available for this statement" — never a fabricated
    balance.
    """
    # Extracted (AS STATED on the statement); None when not found
    beginning_balance: Optional[float] = None
    total_deposits: Optional[float] = None
    total_withdrawals: Optional[float] = None
    checks: Optional[float] = None
    transfers: Optional[float] = None
    fees: Optional[float] = None
    reported_ending_balance: Optional[float] = None

    # Computed server-side
    calculated_ending_balance: Optional[float] = None
    difference: Optional[float] = None
    status: Literal["balanced", "needs_review", "unavailable"] = "unavailable"

    # Provenance
    extraction_complete: bool = False
    fields_found: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class ExtractionCheck(BaseModel):
    """
    v1.4 (Phase 4 — Source B) — extraction-completeness cross-check.

    Companion to ReconciliationSnapshot (Source A). Where Source A checks
    whether the statement's stated math balances, Source B checks whether
    every row the statement reported was actually extracted. Computed in
    pipeline._compute_source_b by bucketing the extracted transactions and
    comparing per-bucket row sums against the snapshot's stated activity
    totals.

    Bucketing (from the May 26 spike, verified GO across three real test PDFs):
        deposits     ← deposit + interest + reimbursement
        withdrawals  ← vendor_payment    (NOT checks, NOT fees)
        checks       ← check_payment
        transfers    ← transfer + owner_draw
        fees         ← bank_fee

    Status taxonomy:
        complete    — every bucket's delta within RECONCILIATION_TOLERANCE
                      (0.01). Strong signal extraction captured every row.
        incomplete  — at least one bucket's delta exceeds tolerance. Likely
                      missed or miscounted rows during extraction.
        unavailable — no usable snapshot to compare against (rule_based /
                      multi_agent engines, or PDF Skill found no balance
                      summary). Source B can't run without stated totals.
    """
    status: Literal["complete", "incomplete", "unavailable"] = "unavailable"

    # Per-bucket comparison: stated (from snapshot), row_sum (from extracted
    # transactions), and delta (row_sum - stated, or row_sum alone when
    # stated is None and row_sum > 0). delta is None for genuinely-absent
    # sections where both stated is None AND row_sum is 0.
    deposits_stated:     Optional[float] = None
    deposits_row_sum:    Optional[float] = None
    deposits_delta:      Optional[float] = None

    withdrawals_stated:  Optional[float] = None
    withdrawals_row_sum: Optional[float] = None
    withdrawals_delta:   Optional[float] = None

    checks_stated:       Optional[float] = None
    checks_row_sum:      Optional[float] = None
    checks_delta:        Optional[float] = None

    transfers_stated:    Optional[float] = None
    transfers_row_sum:   Optional[float] = None
    transfers_delta:     Optional[float] = None

    fees_stated:         Optional[float] = None
    fees_row_sum:        Optional[float] = None
    fees_delta:          Optional[float] = None

    notes: Optional[str] = None


class Statement(BaseModel):
    file_id: str
    original_filename: str
    status: Literal["success", "partial", "failed"]
    failure_reason: Optional[Literal["rate_limit", "extraction", "other"]] = None
    error_message: Optional[str] = None

    transaction_count: int = 0
    vendor_count: int = 0
    total_amount: float = 0.0
    vendors_over_threshold: int = 0
    review_needed: int = 0
    extraction_confidence: float = 0.0

    excel_file_id: Optional[str] = None  # for per-statement download

    # v1.3 — PDF Skill engine fields. Optional so older engines (rule_based,
    # multi_agent) that don't produce them still validate. Frontend reads
    # bookkeeping_breakdown to render the transaction-type table in
    # Per-Statement card Group A.
    engine_used: Optional[str] = None
    bookkeeping_breakdown: Optional[dict] = None
    excluded_count: int = 0

    # v1.4 (Phase 4) — statement reconciliation snapshot. None for engines
    # that don't extract balance figures (rule_based, multi_agent).
    reconciliation_snapshot: Optional[ReconciliationSnapshot] = None

    # v1.4 (Phase 4 — Source B) — extraction-completeness check. Independent
    # of Source A: tells whether the extracted rows sum to the snapshot's
    # stated activity totals. None for engines that don't produce one
    # (rule_based, multi_agent).
    extraction_check: Optional[ExtractionCheck] = None

# ---------------------------------------------------------------------------
# Consolidated Validation findings (drives the four collapsible sections)
# ---------------------------------------------------------------------------

class CrossMatchAppearance(BaseModel):
    statement: str          # original filename
    amount: float
    count: int


class CrossMatch(BaseModel):
    vendor: str
    statement_count: int
    combined_total: float
    crosses_threshold_combined_only: bool
    appearances: list[CrossMatchAppearance]   # detail breakdown (UI may collapse)


class NameVariant(BaseModel):
    statement_a: str        # original filename
    name_a: str
    statement_b: str        # original filename
    name_b: str
    similarity: float
    amount_a: float
    amount_b: float


class DiscrepancyAlert(BaseModel):
    vendor: str
    statement_a: str        # original filename
    amount_a: float
    statement_b: str        # original filename
    amount_b: float
    ratio: float
    abs_diff: float


class NearThresholdVendor(BaseModel):
    vendor: str
    source: str             # original filename, or the literal "Combined"
    total_amount: float
    distance_to_threshold: float


class StatementExclusion(BaseModel):
    statement: str          # original filename
    status: str
    reason: str


class Validation(BaseModel):
    cross_statement_matches: list[CrossMatch] = Field(default_factory=list)
    name_variants: list[NameVariant] = Field(default_factory=list)
    discrepancy_alerts: list[DiscrepancyAlert] = Field(default_factory=list)
    near_threshold_vendors: list[NearThresholdVendor] = Field(default_factory=list)
    statements_processed: list[str] = Field(default_factory=list)   # original filenames
    statements_excluded: list[StatementExclusion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Master workbook (single download area)
# ---------------------------------------------------------------------------

class WorkbookInfo(BaseModel):
    file_id: str
    filename: str           # readable name (e.g., "prepare_master_2026-05-07_a1b2c3.xlsx")
    sheet_count: int
    generated_at: str       # ISO 8601


# ---------------------------------------------------------------------------
# Technical details (collapsed panel)
# ---------------------------------------------------------------------------

class Technical(BaseModel):
    engine: Literal['rule_based', 'ai_assisted', 'multi_agent', 'pdf_skill']
    extraction_model: Optional[str] = None      # None for rule_based
    language: str = "English"                    # carried for v1.1 translation
    processing_time_seconds: Optional[float] = None
    total_cost_usd: float = 0.0
    total_tool_calls: int = 0


# ---------------------------------------------------------------------------
# Run-level errors (NOT per-statement — those live on Statement objects)
# ---------------------------------------------------------------------------

class RunError(BaseModel):
    file_id: Optional[str] = None
    stage: str              # "engine_startup" | "workbook_generation" | etc.
    message: str
    fatal: bool = False


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------

class ProcessResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    success: bool
    run_id: str

    summary: Summary
    statements: list[Statement]
    validation: Validation
    workbook: Optional[WorkbookInfo] = None     # None if workbook generation failed
    technical: Technical
    errors: list[RunError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Health endpoint response (small, separate)
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    schema_version: str = SCHEMA_VERSION
    api_key_set: bool
    extraction_model: str
