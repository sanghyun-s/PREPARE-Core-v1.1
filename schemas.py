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


# ---------------------------------------------------------------------------
# Per-statement result (drives the Per-Statement compact cards)
# ---------------------------------------------------------------------------

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
    engine: Literal["rule_based", "ai_assisted", "multi_agent"]
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
