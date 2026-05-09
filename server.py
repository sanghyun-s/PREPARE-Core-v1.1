"""
Server v1.0 — PREPARE Reconciliation Workspace
-----------------------------------------------
Single-page workflow with one main endpoint that dispatches to the chosen
engine, runs deterministic validation, generates the master Excel, and
returns one consolidated response built against the schemas.py contract.

v1.0 changes from v0.6:
    * AI Validation Narrative call removed. `run_validation_narrative` no
      longer imported or called.
    * `validation_model` form parameter dropped (clean cut). The dropdown is
      gone from the upload screen and the value is no longer carried.
    * `validation_models_available` field dropped from /api/health.
    * Per-agent `agent_narrative` field dropped from the response.
    * Response payload now conforms to schemas.py (ProcessResponse) with a
      formal contract — schema_version, summary, statements, validation,
      workbook, technical, errors. Frontend renders strictly against this
      shape.
    * Original filename preservation: uploads now retain `file.filename` and
      a per-run `filename_map` resolves uuid-based file IDs to readable
      filenames at the response boundary and inside the workbook.
    * `language` parameter retained as a no-op pass-through (carried for
      v1.1 workbook localization).

Endpoints:
  POST /api/process              — Main workflow. Returns ProcessResponse.
  GET  /api/download/{file_id}   — Download a generated Excel file.
  GET  /api/health               — Health check.
"""

import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, NamedTuple

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Path setup
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

load_dotenv(ROOT / ".env")

from agent_app import run_engine, EXTRACTION_MODEL_DEFAULT
from review_flag_engine import build_flags_for_statement
from validation_engine import run_deterministic_validation
from master_excel_generator import generate_master_workbook
from vendor_classifier_1099 import classify_all_vendors

from schemas import (
    SCHEMA_VERSION,
    ProcessResponse,
    Summary,
    Statement,
    Validation,
    CrossMatch,
    CrossMatchAppearance,
    NameVariant,
    DiscrepancyAlert,
    NearThresholdVendor,
    StatementExclusion,
    WorkbookInfo,
    Technical,
    RunError,
    HealthResponse,
)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="PREPARE Reconciliation Workspace v1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = ROOT / "uploads_tmp"
OUTPUTS_DIR = ROOT / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

FRONTEND = ROOT / "frontend"

VERSION = "1.0.0"


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the unified workspace UI."""
    index_path = FRONTEND / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend not yet built</h1>")


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        version=VERSION,
        api_key_set=bool(os.getenv("ANTHROPIC_API_KEY")),
        extraction_model=EXTRACTION_MODEL_DEFAULT,
    )


# ---------------------------------------------------------------------------
# Upload helper — preserves original filename
# ---------------------------------------------------------------------------

class UploadInfo(NamedTuple):
    saved_path: Path
    original_filename: str
    file_id: str           # uuid stem (no extension), the canonical internal ID


def _save_upload(file: UploadFile, suffix: str) -> Optional[UploadInfo]:
    """
    Save uploaded file to uploads_tmp/ with a unique uuid-based name.
    Returns UploadInfo carrying the saved path, the user's original filename,
    and a stable file_id (uuid stem) for backend keying.
    """
    if not file:
        return None
    file_id = uuid.uuid4().hex
    saved_path = UPLOADS_DIR / f"{file_id}{suffix}"
    with open(saved_path, "wb") as f:
        f.write(file.file.read())
    return UploadInfo(
        saved_path=saved_path,
        original_filename=file.filename or saved_path.name,
        file_id=file_id,
    )


# ---------------------------------------------------------------------------
# Eligibility helper (unchanged from v0.6, just relocated)
# ---------------------------------------------------------------------------

def _build_eligibility_for_statement(vendors_dicts: list[dict]) -> dict:
    """Run the 1099 classifier on vendor dicts. Returns name -> EligibilityResult."""
    from dataclasses import dataclass, field

    @dataclass
    class _MinimalSummary:
        canonical_name: str
        entity_type: Optional[str]
        total_amount: float
        transaction_count: int = 1
        first_payment_date: Optional[str] = None
        last_payment_date: Optional[str] = None
        match_confidence: float = 1.0
        needs_review: bool = False
        review_reasons: list = field(default_factory=list)
        raw_name_variants: list = field(default_factory=list)

    mocks = []
    for v in vendors_dicts:
        mocks.append(_MinimalSummary(
            canonical_name=v["canonical_name"],
            entity_type=v.get("entity_type"),
            total_amount=v.get("total_amount", 0.0),
            transaction_count=v.get("transaction_count", 1),
            match_confidence=v.get("match_confidence", 1.0),
            needs_review=v.get("needs_review", False),
        ))
    return classify_all_vendors(mocks)


# ---------------------------------------------------------------------------
# Filename resolution
# ---------------------------------------------------------------------------

def _make_resolver(filename_map: dict[str, str]):
    """
    Build a label-resolution function. Given an internal label like
    "<file_id>.pdf" or just "<file_id>", returns the original filename.
    Passes through "Combined" and any unknown labels unchanged.
    """
    def resolve(label: str) -> str:
        if not label or label == "Combined":
            return label
        if label.endswith(".pdf"):
            stem = label[:-4]
        else:
            stem = label
        return filename_map.get(stem, label)
    return resolve


# ---------------------------------------------------------------------------
# Status mapping — collapse granular failure codes to schema's three-state status
# ---------------------------------------------------------------------------

def _map_status(raw_status: str) -> tuple[str, Optional[str]]:
    """
    Map agent_app's granular status to (schema_status, failure_reason).

    schema_status ∈ {"success", "partial", "failed"}
    failure_reason ∈ {"rate_limit", "extraction", "other", None}
    """
    if raw_status == "success":
        return "success", None
    if raw_status == "partial":
        return "partial", None
    if raw_status == "failed_rate_limit":
        return "failed", "rate_limit"
    if raw_status == "failed_extraction":
        return "failed", "extraction"
    return "failed", "other"


# ---------------------------------------------------------------------------
# Main endpoint — unified workflow
# ---------------------------------------------------------------------------

@app.post("/api/process", response_model=ProcessResponse)
async def process(
    pdf_files: list[UploadFile] = File(...),
    vendor_list: Optional[UploadFile] = File(None),
    engine: str = Form("multi_agent"),
    extraction_model: str = Form(EXTRACTION_MODEL_DEFAULT),
    language: str = Form("English"),
):
    """
    Unified workflow endpoint.

    engine:           "rule_based" | "ai_assisted" | "multi_agent"
    extraction_model: Default Haiku. Advanced override available.
    language:         Reserved for v1.1 workbook localization. No-op in v1.0.
    """
    # ── Validate inputs ──
    if not pdf_files or len(pdf_files) == 0:
        raise HTTPException(status_code=422, detail="At least one PDF required.")
    if len(pdf_files) > 10:
        raise HTTPException(status_code=422, detail="Maximum 10 PDFs per run.")

    if engine not in ("rule_based", "ai_assisted", "multi_agent"):
        raise HTTPException(status_code=422, detail=f"Unknown engine: {engine}")

    if engine != "rule_based" and not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="ANTHROPIC_API_KEY not set. Use rule_based engine or set the key in .env.",
        )

    t_start = time.time()

    # ── Save uploads, build filename_map ──
    upload_infos = [_save_upload(f, ".pdf") for f in pdf_files]
    pdf_paths = [str(u.saved_path) for u in upload_infos]
    filename_map: dict[str, str] = {u.file_id: u.original_filename for u in upload_infos}

    csv_info = _save_upload(vendor_list, ".csv") if vendor_list else None
    csv_path = str(csv_info.saved_path) if csv_info else None

    # ── Per-run output directory ──
    run_id = uuid.uuid4().hex[:12]
    run_dir = OUTPUTS_DIR / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    run_errors: list[RunError] = []
    resolve = _make_resolver(filename_map)

    # ── Run the chosen engine ──
    try:
        engine_result = await run_engine(
            engine=engine,
            pdf_paths=pdf_paths,
            output_dir=str(run_dir),
            vendor_list_path=csv_path,
            extraction_model=extraction_model,
            language=language,
        )
    except Exception as e:
        # Engine startup failure — top-level error, return minimal response
        raise HTTPException(status_code=500, detail=f"Engine failed: {e}")

    if not engine_result.get("success"):
        raise HTTPException(status_code=500, detail=engine_result.get("error", "Engine failed"))

    agent_outputs = engine_result.get("agent_outputs", [])

    # ── Build per-statement review flags + 1099 eligibility ──
    flags_by_statement: dict[str, dict] = {}
    eligibility_by_statement: dict[str, dict] = {}

    for out in agent_outputs:
        statement_label = out["statement_label"]   # internal uuid-based label

        if out.get("status") not in ("success", "partial"):
            flags_by_statement[statement_label] = {}
            eligibility_by_statement[statement_label] = {}
            continue

        from dataclasses import dataclass, field

        @dataclass
        class _MinimalSummary:
            canonical_name: str
            entity_type: Optional[str]
            total_amount: float
            transaction_count: int = 1
            first_payment_date: Optional[str] = None
            last_payment_date: Optional[str] = None
            match_confidence: float = 1.0
            needs_review: bool = False
            review_reasons: list = field(default_factory=list)
            raw_name_variants: list = field(default_factory=list)

        summaries = [
            _MinimalSummary(
                canonical_name=v["canonical_name"],
                entity_type=v.get("entity_type"),
                total_amount=v.get("total_amount", 0.0),
                transaction_count=v.get("transaction_count", 1),
                match_confidence=v.get("match_confidence", 1.0),
                needs_review=v.get("needs_review", False),
                review_reasons=v.get("review_reasons", []),
            )
            for v in out["vendors"]
        ]

        flags_by_statement[statement_label] = build_flags_for_statement(
            summaries,
            extraction_confidence=out.get("extraction_confidence", 1.0),
        )
        eligibility_by_statement[statement_label] = _build_eligibility_for_statement(
            out["vendors"]
        )

    # ── Run deterministic validation ──
    validation_raw = run_deterministic_validation(agent_outputs, flags_by_statement)

    # ── Generate master Excel workbook ──
    workbook_info: Optional[WorkbookInfo] = None
    workbook_filename = f"prepare_master_{datetime.now().strftime('%Y-%m-%d')}_{run_id}.xlsx"
    master_path = run_dir / workbook_filename

    try:
        generate_master_workbook(
            output_path=str(master_path),
            agent_outputs=agent_outputs,
            flags_by_statement=flags_by_statement,
            eligibility_by_statement=eligibility_by_statement,
            validation=validation_raw,
            filename_map=filename_map,
        )
        # Read actual sheet count from the generated workbook so the response
        # always reflects reality. v1.1 produces 5 sheets (Executive Summary
        # added at index 0); future sheet additions will be reflected
        # automatically without server.py edits.
        from openpyxl import load_workbook
        _wb_for_count = load_workbook(master_path, read_only=True)
        _actual_sheet_count = len(_wb_for_count.sheetnames)
        _wb_for_count.close()

        workbook_info = WorkbookInfo(
            file_id=f"run_{run_id}/{workbook_filename}",
            filename=workbook_filename,
            sheet_count=_actual_sheet_count,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as e:
        run_errors.append(RunError(
            file_id=None,
            stage="workbook_generation",
            message=f"Master workbook generation failed: {e}",
            fatal=False,
        ))
        
    # ── Build per-agent file IDs for individual downloads ──
    for out in agent_outputs:
        if out.get("output_path"):
            p = Path(out["output_path"])
            if p.exists():
                out["_excel_file_id"] = f"run_{run_id}/{p.name}"
            else:
                out["_excel_file_id"] = None
        else:
            out["_excel_file_id"] = None

    # ── Build the response per ProcessResponse contract ──

    # Statements
    statements: list[Statement] = []
    successful_count = 0
    failed_count = 0
    total_transactions = 0
    total_vendors = 0
    total_amount = 0.0
    total_over_threshold = 0
    total_review_needed = 0

    for out in agent_outputs:
        raw_status = out.get("status", "failed_other")
        schema_status, failure_reason = _map_status(raw_status)

        is_ok = schema_status in ("success", "partial")
        if is_ok:
            successful_count += 1
        else:
            failed_count += 1

        vendors = out.get("vendors", []) if is_ok else []
        txn_count = len(out.get("transactions", [])) if is_ok else 0
        amt = round(sum(v.get("total_amount", 0) for v in vendors), 2)
        over_thresh = sum(1 for v in vendors if v.get("total_amount", 0) >= 600)
        review_count = sum(
            1 for f in flags_by_statement.get(out["statement_label"], {}).values()
            if f.needs_review
        )

        total_transactions += txn_count
        total_vendors += len(vendors)
        total_amount += amt
        total_over_threshold += over_thresh
        total_review_needed += review_count

        # file_id from internal label (strip .pdf)
        internal_label = out["statement_label"]
        file_id_stem = internal_label[:-4] if internal_label.endswith(".pdf") else internal_label

        statements.append(Statement(
            file_id=file_id_stem,
            original_filename=resolve(internal_label),
            status=schema_status,
            failure_reason=failure_reason,
            error_message=out.get("error_message"),
            transaction_count=txn_count,
            vendor_count=len(vendors),
            total_amount=amt,
            vendors_over_threshold=over_thresh,
            review_needed=review_count,
            extraction_confidence=out.get("extraction_confidence", 0.0),
            excel_file_id=out.get("_excel_file_id"),
        ))

    # Summary
    # Summary — unique_vendors is the deduplicated count of canonical names
    # across all successful statements (matches Executive Summary workbook sheet).
    # Without dedup, vendors appearing in multiple statements get double-counted.
    _unique_vendor_names: set[str] = set()
    for _o in agent_outputs:
        if _o.get("status") in ("success", "partial"):
            for _v in _o.get("vendors", []):
                _unique_vendor_names.add(_v["canonical_name"])

    summary = Summary(
        total_transactions=total_transactions,
        unique_vendors=len(_unique_vendor_names),
        total_reconciled=round(total_amount, 2),
        vendors_over_threshold=total_over_threshold,
        review_needed_count=total_review_needed,
        successful_count=successful_count,
        failed_count=failed_count,
    )

    # Validation — resolve all statement labels to original filenames
    validation = Validation(
        cross_statement_matches=[
            CrossMatch(
                vendor=cm.canonical_name,
                statement_count=len(cm.appearances),
                combined_total=round(cm.combined_total, 2),
                crosses_threshold_combined_only=cm.crosses_threshold_combined_only,
                appearances=[
                    CrossMatchAppearance(
                        statement=resolve(a["statement"]),
                        amount=round(a["amount"], 2),
                        count=a["count"],
                    )
                    for a in cm.appearances
                ],
            )
            for cm in validation_raw.cross_matches
        ],
        name_variants=[
            NameVariant(
                statement_a=resolve(nv.statement_a),
                name_a=nv.name_a,
                statement_b=resolve(nv.statement_b),
                name_b=nv.name_b,
                similarity=nv.similarity,
                amount_a=round(nv.amount_a, 2),
                amount_b=round(nv.amount_b, 2),
            )
            for nv in validation_raw.name_variants
        ],
        discrepancy_alerts=[
            DiscrepancyAlert(
                vendor=am.canonical_name,
                statement_a=resolve(am.statement_a),
                amount_a=round(am.amount_a, 2),
                statement_b=resolve(am.statement_b),
                amount_b=round(am.amount_b, 2),
                ratio=am.ratio,
                abs_diff=round(am.abs_diff, 2),
            )
            for am in validation_raw.amount_mismatches
        ],
        near_threshold_vendors=[
            NearThresholdVendor(
                vendor=nt.canonical_name,
                source=resolve(nt.statement),    # "Combined" passes through
                total_amount=round(nt.total_amount, 2),
                distance_to_threshold=round(nt.distance_to_threshold, 2),
            )
            for nt in validation_raw.near_threshold
        ],
        statements_processed=[resolve(s) for s in validation_raw.statements_processed],
        statements_excluded=[
            StatementExclusion(
                statement=resolve(ex["statement"]),
                status=ex["status"],
                reason=ex["reason"],
            )
            for ex in validation_raw.statements_excluded
        ],
    )

    # Technical
    total_tool_calls = sum(o.get("tool_calls", 0) for o in agent_outputs)
    total_cost = engine_result.get("total_cost_usd", 0.0)

    technical = Technical(
        engine=engine,
        extraction_model=extraction_model if engine != "rule_based" else None,
        language=language,
        processing_time_seconds=round(time.time() - t_start, 2),
        total_cost_usd=round(total_cost, 4),
        total_tool_calls=total_tool_calls,
    )

    return ProcessResponse(
        schema_version=SCHEMA_VERSION,
        success=True,
        run_id=run_id,
        summary=summary,
        statements=statements,
        validation=validation,
        workbook=workbook_info,
        technical=technical,
        errors=run_errors,
    )


# ---------------------------------------------------------------------------
# Download endpoint — supports nested run_*/file.xlsx paths
# ---------------------------------------------------------------------------

@app.get("/api/download/{file_id:path}")
async def download(file_id: str):
    """Download an Excel file by its file_id (which may include run subdir)."""
    safe = Path(file_id).as_posix()
    if ".." in safe or safe.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file_id")

    full_path = OUTPUTS_DIR / safe
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

    return FileResponse(
        full_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=full_path.name,
    )


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
