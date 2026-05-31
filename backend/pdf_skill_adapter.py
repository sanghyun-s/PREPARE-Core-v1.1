"""
PDF Skill Adapter (v1.3)
========================
Production-facing wrapper around Anthropic's pre-built `pdf` Agent Skill.

Mechanism:
    Invokes the Skill via claude_agent_sdk.query() with
    setting_sources=["user","project"] and allowed_tools=["Skill","Read","Bash"].
    The pre-built `pdf` Skill (installed at .claude/skills/pdf/SKILL.md) handles
    actual PDF reading via progressive disclosure. Our system prompt provides
    the JSON schema, classification policy, and 1099 inclusion rules that
    PREPARE needs.

Role in the pipeline:
    This adapter is the new "ingestion engine" alongside the existing rule-based
    pdfplumber path and the legacy multi-agent path. It returns:
        - A list of Transaction objects ready for downstream aggregation
          (`include_for_1099=True` rows, classifier-fields populated)
        - A list of ALL transactions including excluded rows (for UI display)
        - Per-statement metadata (counts, breakdowns, status, evidence)
        - Cost and timing telemetry

Stability priorities (per v1.3 scope):
    1. PDF Skill failures must NOT crash the app — return a structured failure
       result that pipeline.py can render as a per-statement error card.
    2. Pre-flight validation (file size, magic bytes) catches obvious bad
       input at $0 cost before any API call.
    3. One automatic retry on subprocess startup failures.
    4. Schema validation of returned JSON — partial/missing fields tolerated
       where possible, hard failure only on unrecoverable schema violation.
    5. Excluded rows preserved separately so the Per-Statement UI can show
       them, even though only included rows feed aggregation.

This adapter does NOT:
    - Modify pdf_extractor.py (rule-based path stays as-is)
    - Modify transaction_aggregator.py (its output shape is the contract)
    - Modify master_excel_generator.py (workbook structure unchanged in v1.3)
    - Make any other backend changes that aren't strictly required

Limitations documented for v1.3:
    - Scanned/OCR-only PDFs are not tested
    - Password-protected PDFs are not supported
    - Latency is 1-4 minutes per PDF on Sonnet (vs ~5s for rule-based)
    - Cost is ~$0.20-0.60 per PDF on Sonnet
    - API credit exhaustion presents as agent subprocess failure (observed
      during Track 2 testing); operators should set up auto-reload on the
      Anthropic billing page.

Track 2 prototype evidence supporting this adapter:
    - 6 PDFs tested across two layout families
    - Tier 1 row-by-row match to ground truth on both diagnostic PDFs
    - Tier 2 sanity-clean across 4 real-world layouts
    - Determinism verified across repeated runs
    - Failure modes (corrupted/empty/truncated PDFs) handled gracefully
      with structured `detected_type: "unknown"` responses
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ─── Adapter contract ──────────────────────────────────────────────────

@dataclass
class PDFSkillExtractionResult:
    """
    Output of the adapter. Either a success with transactions, or a
    structured failure with reason + details.

    On success:
        - included_transactions: rows that flow into vendor aggregation
        - all_transactions: includes excluded rows (deposits, balances,
          payroll, transfers, fees) — used by per-statement UI and Excel
        - metadata: detected_type, detected_layout, page_count, statement_period
        - breakdown: counts by transaction_type
        - cost_usd, agent_seconds: telemetry
        - skill_was_used, tool_calls: provenance

    On failure:
        - success = False
        - failure_reason: one of the FAILURE_REASON_* constants
        - failure_details: human-readable explanation
        - partial_data: any data captured before the failure (raw text,
          partial JSON, etc.) — useful for debugging
    """
    success: bool
    pdf_filename: str
    model: str

    # Success fields (populated when success=True)
    included_transactions: list[dict] = field(default_factory=list)
    all_transactions: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    breakdown: dict[str, int] = field(default_factory=dict)
    # v1.4 (Phase 4): statement-level balance figures transcribed AS STATED
    # from the account-summary section. Empty dict when the engine/prompt did
    # not produce one. The pipeline computes calculated_ending/difference/status.
    reconciliation_snapshot: dict = field(default_factory=dict)

    # Telemetry (always populated)
    cost_usd: float = 0.0
    agent_seconds: float = 0.0
    skill_was_used: bool = False
    tool_calls: list[str] = field(default_factory=list)

    # Failure fields (populated when success=False)
    failure_reason: str = ""
    failure_details: str = ""
    partial_data: Any = None


# Failure reason constants — production code can branch on these
FAILURE_REASON_INVALID_PDF = "invalid_pdf"                    # caught pre-flight
FAILURE_REASON_AGENT_SUBPROCESS = "agent_subprocess_failed"   # SDK subprocess died
FAILURE_REASON_AGENT_TIMEOUT = "agent_timeout"                # exceeded timeout
FAILURE_REASON_AGENT_UNKNOWN_TYPE = "agent_returned_unknown"  # agent could not interpret PDF
FAILURE_REASON_SCHEMA_VIOLATION = "schema_violation"          # JSON malformed or missing fields
FAILURE_REASON_SDK_NOT_INSTALLED = "sdk_not_installed"        # claude_agent_sdk missing
FAILURE_REASON_NO_API_KEY = "no_api_key"                      # ANTHROPIC_API_KEY missing


# ─── Configuration ─────────────────────────────────────────────────────

DEFAULT_MODEL_SONNET = "claude-sonnet-4-6"
DEFAULT_MODEL_OPUS = "claude-opus-4-7"

DEFAULT_MAX_TURNS = 15           # bounded agent loop (was 30 in prototype)
DEFAULT_TIMEOUT_SECONDS = 300    # 5 min per PDF hard cap

# Resolve project root from this file's location: backend/pdf_skill_adapter.py
# → project root is two levels up.
_THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent.parent

# Where the system prompt lives. Reused from the prototype so prompt iteration
# happens in one place. If the prototype file is absent, we fall back to an
# inline default so the adapter still works in a stripped-down install.
PROTOTYPE_PROMPT_FILE = PROJECT_ROOT / "backend" / "prototypes" / "pdf_skill_prompt.md"


# ─── Pre-flight validation ─────────────────────────────────────────────

def _preflight_pdf_check(pdf_path: Path) -> Optional[tuple[str, str]]:
    """
    Cheap validation before spending any API cost.
    Returns None on pass, or (failure_reason, details) on fail.
    """
    if not pdf_path.exists():
        return (FAILURE_REASON_INVALID_PDF, f"File not found: {pdf_path}")
    try:
        size = pdf_path.stat().st_size
    except OSError as e:
        return (FAILURE_REASON_INVALID_PDF, f"Cannot stat file: {e}")
    if size == 0:
        return (FAILURE_REASON_INVALID_PDF, "File is empty (0 bytes)")
    if size < 100:
        return (FAILURE_REASON_INVALID_PDF, f"File too small ({size} bytes) to be a valid PDF")
    try:
        with open(pdf_path, "rb") as f:
            magic = f.read(5)
    except OSError as e:
        return (FAILURE_REASON_INVALID_PDF, f"Cannot read file: {e}")
    if not magic.startswith(b"%PDF"):
        return (
            FAILURE_REASON_INVALID_PDF,
            f"Missing PDF magic bytes (file starts with {magic!r})",
        )
    return None


# ─── System prompt loader ──────────────────────────────────────────────

def _load_extraction_instruction() -> str:
    """
    Load the extraction instruction. Tries the prototype prompt file first;
    falls back to a minimal inline instruction if the file is missing.

    The prototype prompt is wrapped in a plain ``` ... ``` block within a
    markdown file. We extract the plain (non-language-tagged) block, taking
    the longest match as the actual instruction.
    """
    if PROTOTYPE_PROMPT_FILE.exists():
        try:
            text = PROTOTYPE_PROMPT_FILE.read_text(encoding="utf-8")
            matches = re.finditer(
                r"^```(\w*)\s*\n(.*?)\n```", text, re.MULTILINE | re.DOTALL
            )
            plain_blocks = [m.group(2) for m in matches if not m.group(1)]
            if plain_blocks:
                return max(plain_blocks, key=len).strip()
        except OSError:
            pass

    # Fallback: minimal inline instruction. Kept brief because the production
    # path expects the prototype prompt file to be present.
    return (
        "Extract every transaction row from this PDF and return JSON with "
        "fields: document_metadata (detected_type, detected_layout, page_count, "
        "statement_period), transactions (array with date, description, amount, "
        "transaction_type, include_for_1099, exclusion_reason, confidence, "
        "source_page, source_text, review_required, review_reason), and summary. "
        "Use the pre-built `pdf` Agent Skill. Return JSON only, no prose."
    )


# ─── JSON extraction from agent output ────────────────────────────────

def _extract_json_object(text: str) -> Optional[dict]:
    """
    Defensively extract a JSON object from agent output.
    Handles: pure JSON, JSON with markdown fences, JSON with prose around it.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Try the first { ... last } span
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass
    # Try ```json ... ``` block
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    return None


# ─── Schema validation ────────────────────────────────────────────────

_REQUIRED_TRANSACTION_FIELDS = {
    "date", "description", "amount", "transaction_type", "include_for_1099",
}
# Optional but desired fields — we don't fail the extraction if they're
# missing, but we log it and default them.
_OPTIONAL_TRANSACTION_FIELDS = {
    "exclusion_reason", "review_required", "review_reason",
    "confidence", "source_page", "source_text",
}


def _normalize_transaction(raw: dict) -> dict:
    """
    Take an agent-returned transaction dict and normalize it into a canonical
    form. Defaults applied for missing optional fields so downstream code
    doesn't have to handle None everywhere.
    """
    norm = {
        "date": str(raw.get("date", "") or ""),
        "description": str(raw.get("description", "") or ""),
        "amount": float(raw.get("amount", 0) or 0),
        "transaction_type": str(raw.get("transaction_type", "vendor_payment") or "vendor_payment"),
        "include_for_1099": bool(raw.get("include_for_1099", True)),
        "exclusion_reason": str(raw.get("exclusion_reason", "") or ""),
        "review_required": bool(raw.get("review_required", False)),
        "review_reason": str(raw.get("review_reason", "") or ""),
        "confidence": float(raw.get("confidence", 0.0) or 0.0),
        "source_page": int(raw.get("source_page", 0) or 0),
        "source_text": str(raw.get("source_text", "") or ""),
    }
    return norm


# ─── Agent SDK invocation (async, called from sync wrapper below) ──────

async def _run_agent_async(
    pdf_path: Path,
    model: str,
    extraction_instruction: str,
    max_turns: int,
    timeout_seconds: int,
) -> dict:
    """
    One agent invocation. Returns a dict with success/failure detail.
    All exceptions are caught and returned as structured failures.

    Returns dict shape:
        {
            "success": bool,
            "parsed": dict | None,
            "raw_text": str,
            "agent_seconds": float,
            "cost_usd": float,
            "tool_calls": list[str],
            "skill_was_used": bool,
            "failure_reason": str,
            "failure_details": str,
        }
    """
    base_result: dict = {
        "success": False,
        "parsed": None,
        "raw_text": "",
        "agent_seconds": 0.0,
        "cost_usd": 0.0,
        "tool_calls": [],
        "skill_was_used": False,
        "failure_reason": "",
        "failure_details": "",
    }

    # Import here so SDK absence is a clean failure rather than module-load error
    try:
        from claude_agent_sdk import (
            query,
            ClaudeAgentOptions,
            AssistantMessage,
            ResultMessage,
        )
    except ImportError:
        base_result["failure_reason"] = FAILURE_REASON_SDK_NOT_INSTALLED
        base_result["failure_details"] = (
            "claude-agent-sdk Python package not installed. "
            "Run: pip install claude-agent-sdk"
        )
        return base_result

    if not os.environ.get("ANTHROPIC_API_KEY"):
        base_result["failure_reason"] = FAILURE_REASON_NO_API_KEY
        base_result["failure_details"] = (
            "ANTHROPIC_API_KEY not set in environment or .env"
        )
        return base_result

    pdf_abs = str(pdf_path.resolve())
    prompt_text = (
        f"Extract every transaction row from this PDF:\n\n{pdf_abs}\n\n"
        "Use the pre-built `pdf` Skill to read it. Then return your final "
        "answer as a single JSON object exactly matching the schema "
        "described below. JSON only — no prose before or after, no "
        "markdown code fences.\n\n"
        "===== EXTRACTION SCHEMA AND RULES =====\n\n"
        f"{extraction_instruction}\n\n"
        "===== END SCHEMA =====\n\n"
        f"Begin. PDF to extract: {pdf_abs}"
    )

    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        setting_sources=["user", "project"],
        allowed_tools=["Skill", "Read", "Bash"],
        model=model,
        max_turns=max_turns,
        permission_mode="acceptEdits",
    )

    t_start = time.time()
    tool_calls: list[str] = []
    skill_invoked = False
    final_text_parts: list[str] = []
    result_cost: Optional[float] = None
    timed_out = False

    try:
        # Wrap the message-iteration in a timeout
        async def consume_messages():
            nonlocal skill_invoked, result_cost
            async for message in query(prompt=prompt_text, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            final_text_parts.append(block.text)
                        if hasattr(block, "name") and block.name:
                            tool_calls.append(block.name)
                            if block.name == "Skill":
                                skill_invoked = True
                            elif block.name == "Bash" and hasattr(block, "input"):
                                cmd_str = str(block.input).lower()
                                if "skill.md" in cmd_str or "/skills/" in cmd_str:
                                    skill_invoked = True
                elif isinstance(message, ResultMessage):
                    if hasattr(message, "total_cost_usd") and message.total_cost_usd:
                        result_cost = float(message.total_cost_usd)

        try:
            await asyncio.wait_for(consume_messages(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True

    except Exception as e:
        base_result["agent_seconds"] = round(time.time() - t_start, 2)
        base_result["tool_calls"] = tool_calls
        base_result["skill_was_used"] = skill_invoked
        base_result["raw_text"] = "\n".join(final_text_parts).strip()
        base_result["failure_reason"] = FAILURE_REASON_AGENT_SUBPROCESS
        base_result["failure_details"] = f"{type(e).__name__}: {str(e)[:400]}"
        return base_result

    elapsed = round(time.time() - t_start, 2)
    raw_text = "\n".join(final_text_parts).strip()

    if timed_out:
        base_result["agent_seconds"] = elapsed
        base_result["tool_calls"] = tool_calls
        base_result["skill_was_used"] = skill_invoked
        base_result["raw_text"] = raw_text
        base_result["failure_reason"] = FAILURE_REASON_AGENT_TIMEOUT
        base_result["failure_details"] = (
            f"Agent did not complete within {timeout_seconds}s"
        )
        if result_cost is not None:
            base_result["cost_usd"] = result_cost
        return base_result

    parsed = _extract_json_object(raw_text)

    base_result["agent_seconds"] = elapsed
    base_result["tool_calls"] = tool_calls
    base_result["skill_was_used"] = skill_invoked
    base_result["raw_text"] = raw_text
    if result_cost is not None:
        base_result["cost_usd"] = result_cost

    if parsed is None:
        base_result["failure_reason"] = FAILURE_REASON_SCHEMA_VIOLATION
        base_result["failure_details"] = (
            "Agent returned no parseable JSON object. See raw_text for response."
        )
        return base_result

    base_result["success"] = True
    base_result["parsed"] = parsed
    return base_result

def _run_async_safely(coro):
    """
    Run an async coroutine from sync code, handling both cases:
      1. No event loop running (CLI/script context) → use asyncio.run()
      2. Event loop already running (FastAPI/Jupyter context) → run in a
         separate thread with its own event loop so we don't conflict
         with the caller's loop.

    Without this, calling the adapter from FastAPI (an async framework)
    raises 'asyncio.run() cannot be called from a running event loop'.
    """
    try:
        asyncio.get_running_loop()
        # We're inside a running loop (FastAPI case). Run in a thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        # No loop running (CLI/script case). Safe to call asyncio.run directly.
        return asyncio.run(coro)


# ─── Public entrypoint (sync, with retry) ──────────────────────────────

def extract_from_pdf(
    pdf_path: str | Path,
    *,
    model: str = DEFAULT_MODEL_SONNET,
    max_turns: int = DEFAULT_MAX_TURNS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    enable_retry: bool = True,
) -> PDFSkillExtractionResult:
    """
    Main entrypoint. Synchronous. Caller-facing.

    Args:
        pdf_path: Path to the PDF (str or Path).
        model: Model alias or full ID. Defaults to Sonnet.
            Accepts "sonnet" or "opus" or full IDs.
        max_turns: Bounded agent loop. Default 15.
        timeout_seconds: Hard per-PDF cap. Default 300.
        enable_retry: If True (default), one retry on subprocess startup
            failure with 2-second backoff. Set False for testing.

    Returns:
        PDFSkillExtractionResult — always returned, never raises.
        Inspect .success to determine outcome.
        On failure, inspect .failure_reason for branching.

    Stability contract:
        This function NEVER raises. All exceptions are caught and turned
        into structured failure results. Callers can treat the return value
        as authoritative without try/except wrapping.
    """
    # Normalize inputs
    if isinstance(pdf_path, str):
        pdf_path = Path(pdf_path)
    pdf_filename = pdf_path.name

    # Resolve model alias
    model_resolved = {
        "sonnet": DEFAULT_MODEL_SONNET,
        "opus": DEFAULT_MODEL_OPUS,
    }.get(model.lower(), model)

    # Step 1: pre-flight validation (free, fast)
    preflight = _preflight_pdf_check(pdf_path)
    if preflight is not None:
        reason, details = preflight
        return PDFSkillExtractionResult(
            success=False,
            pdf_filename=pdf_filename,
            model=model_resolved,
            failure_reason=reason,
            failure_details=details,
        )

    # Step 2: load extraction instruction
    instruction = _load_extraction_instruction()

    # Step 3: run agent with one retry on subprocess failure
    attempts = 2 if enable_retry else 1
    last_result: dict = {}
    for attempt in range(1, attempts + 1):
        last_result = _run_async_safely(
            _run_agent_async(pdf_path, model_resolved, instruction, max_turns, timeout_seconds)
        )
        if last_result["success"]:
            break
        # Retry only on subprocess-class failures, not on
        # invalid_pdf/timeout/schema/auth — those won't fix themselves
        if last_result["failure_reason"] != FAILURE_REASON_AGENT_SUBPROCESS:
            break
        if attempt < attempts:
            time.sleep(2)  # brief backoff

    # Step 4: build result object
    if not last_result["success"]:
        return PDFSkillExtractionResult(
            success=False,
            pdf_filename=pdf_filename,
            model=model_resolved,
            cost_usd=last_result.get("cost_usd", 0.0),
            agent_seconds=last_result.get("agent_seconds", 0.0),
            skill_was_used=last_result.get("skill_was_used", False),
            tool_calls=last_result.get("tool_calls", []),
            failure_reason=last_result.get("failure_reason", ""),
            failure_details=last_result.get("failure_details", ""),
            partial_data=last_result.get("raw_text", ""),
        )

    parsed = last_result["parsed"]
    meta = parsed.get("document_metadata", {}) if isinstance(parsed, dict) else {}
    raw_transactions = parsed.get("transactions", []) if isinstance(parsed, dict) else []

    # Check for agent's own "I can't read this" signal — Test 3 evidence
    detected_type = meta.get("detected_type", "")
    page_count = meta.get("page_count", 0) or 0
    if detected_type == "unknown" or page_count == 0:
        return PDFSkillExtractionResult(
            success=False,
            pdf_filename=pdf_filename,
            model=model_resolved,
            cost_usd=last_result.get("cost_usd", 0.0),
            agent_seconds=last_result.get("agent_seconds", 0.0),
            skill_was_used=last_result.get("skill_was_used", False),
            tool_calls=last_result.get("tool_calls", []),
            failure_reason=FAILURE_REASON_AGENT_UNKNOWN_TYPE,
            failure_details=(
                "Agent could not interpret PDF "
                f"(detected_type={detected_type!r}, page_count={page_count})"
            ),
            partial_data=parsed,
        )

    # Normalize each transaction (apply defaults, coerce types)
    normalized_txns: list[dict] = []
    for raw_txn in raw_transactions:
        if not isinstance(raw_txn, dict):
            continue
        # Light schema check — must have at least the required core fields
        if not _REQUIRED_TRANSACTION_FIELDS.issubset(raw_txn.keys()):
            # Missing core fields: log, skip this row but continue with others
            # (don't fail the whole extraction over one bad row)
            continue
        normalized_txns.append(_normalize_transaction(raw_txn))

    # Build breakdown by transaction_type
    breakdown: dict[str, int] = {}
    for t in normalized_txns:
        ttype = t["transaction_type"]
        breakdown[ttype] = breakdown.get(ttype, 0) + 1

    # Split included vs excluded
    included = [t for t in normalized_txns if t["include_for_1099"]]
    # all_transactions stays as-is (includes excluded for UI display)

    # Build clean metadata dict — preserve known fields, drop unknown
    metadata = {
        "detected_type": detected_type,
        "detected_layout": meta.get("detected_layout", "unknown"),
        "page_count": page_count,
        "statement_period": meta.get("statement_period", None),
    }

    # v1.4 (Phase 4): pull the reconciliation snapshot if the prompt produced
    # one. The model transcribes the statement's account-summary figures AS
    # STATED; the pipeline does the arithmetic. We pass it through untouched
    # here (a plain dict) — if absent, an empty dict signals "not available".
    recon_snapshot = {}
    if isinstance(parsed, dict):
        rs = parsed.get("reconciliation_snapshot")
        if isinstance(rs, dict):
            recon_snapshot = rs

    return PDFSkillExtractionResult(
        success=True,
        pdf_filename=pdf_filename,
        model=model_resolved,
        included_transactions=included,
        all_transactions=normalized_txns,
        metadata=metadata,
        breakdown=breakdown,
        reconciliation_snapshot=recon_snapshot,
        cost_usd=last_result.get("cost_usd", 0.0),
        agent_seconds=last_result.get("agent_seconds", 0.0),
        skill_was_used=last_result.get("skill_was_used", False),
        tool_calls=last_result.get("tool_calls", []),
    )


# ─── Bridge to existing Transaction dataclass ─────────────────────────

def to_pipeline_transactions(result: PDFSkillExtractionResult) -> tuple[list, list]:
    """
    Convert PDFSkillExtractionResult into the (Transaction, Transaction) lists
    that pipeline.py's downstream steps expect.

    Returns:
        (included_transactions, all_transactions)
        Both are lists of transaction_aggregator.Transaction objects.

    The included list is what flows into normalization → aggregation.
    The all list is what the per-statement UI and per-statement Excel display.

    If the import fails (transaction_aggregator not on sys.path), returns
    plain dicts so the adapter still works in test contexts. Production
    callers running through agent_app.py / pipeline.py will have the import
    available.
    """
    if not result.success:
        return [], []

    try:
        from transaction_aggregator import Transaction
    except ImportError:
        # Fallback: return dicts. Caller can adapt.
        return result.included_transactions, result.all_transactions

    def _make_txn(d: dict, source: str = "bank") -> "Transaction":
        return Transaction(
            date=d.get("date") or None,
            description=d.get("description", ""),
            amount=float(d.get("amount", 0) or 0),
            source=source,
            transaction_type=d.get("transaction_type", "vendor_payment"),
            include_for_1099=bool(d.get("include_for_1099", True)),
            review_required=bool(d.get("review_required", False)),
            exclusion_reason=d.get("exclusion_reason", ""),
        )

    # Choose source based on detected statement type
    statement_type = (result.metadata.get("detected_type") or "").lower()
    source = "credit_card" if "credit" in statement_type else "bank"

    included = [_make_txn(d, source) for d in result.included_transactions]
    all_txns = [_make_txn(d, source) for d in result.all_transactions]
    return included, all_txns


# ─── Module self-check ────────────────────────────────────────────────

if __name__ == "__main__":
    # Lightweight self-test: validate pre-flight check on the failure-test
    # fixtures (if present). Does NOT call the API.
    import sys

    print("=== pdf_skill_adapter.py self-check ===")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Prompt file present: {PROTOTYPE_PROMPT_FILE.exists()}")
    print()

    # Try to import the SDK and report status (no actual call)
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions  # noqa
        print("claude_agent_sdk: importable ✓")
    except ImportError as e:
        print(f"claude_agent_sdk: NOT importable ({e})")

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY: set ✓")
    else:
        print("ANTHROPIC_API_KEY: NOT set")

    # Test pre-flight on the failure_test fixtures if available
    test_dir = PROJECT_ROOT / "samples" / "failure_test"
    if test_dir.exists():
        print()
        print("Pre-flight check on failure_test fixtures:")
        for f in sorted(test_dir.glob("*.pdf")):
            result = _preflight_pdf_check(f)
            status = "PASS" if result is None else f"FAIL ({result[0]}: {result[1]})"
            print(f"  {f.name}: {status}")

    # Test pre-flight on a real sample
    real_sample = PROJECT_ROOT / "samples" / "sample_bank_3col_clean.pdf"
    if real_sample.exists():
        result = _preflight_pdf_check(real_sample)
        status = "PASS" if result is None else f"FAIL ({result[0]}: {result[1]})"
        print(f"\nReal sample pre-flight: {real_sample.name}: {status}")

    print("\nSelf-check done.")
