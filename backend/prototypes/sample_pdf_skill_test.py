"""
sample_pdf_skill_test.py — PDF Skill Prototype (v0.2)
=====================================================

Standalone prototype that tests whether Anthropic's pre-built `pdf` Agent
Skill can replace the pdfplumber + regex extraction path in PREPARE.

Mechanism (per Anthropic Agent SDK docs):
    options = ClaudeAgentOptions(
        cwd="<project root>",
        setting_sources=["user", "project"],
        allowed_tools=["Skill", "Read", "Bash"],
    )
    async for message in query(prompt="...", options=options):
        process(message)

The agent autonomously invokes the pre-built `pdf` Skill when the prompt
asks for PDF extraction. Skill is loaded via progressive disclosure:
agent reads SKILL.md first, pulls supplemental files only as needed.

Lives outside production. Does NOT modify:
    - pdf_extractor.py
    - pipeline.py
    - agent_tools.py
    - server.py
    - frontend/index.html
    - any workbook generator

Usage:
    # Default: run Tier 1 PDFs with Sonnet
    python backend/prototypes/sample_pdf_skill_test.py

    # Use Opus instead
    python backend/prototypes/sample_pdf_skill_test.py --model opus

    # Run just one PDF
    python backend/prototypes/sample_pdf_skill_test.py --single samples/sample_bank_3col_clean.pdf

    # Include Tier 2 sanity checks
    python backend/prototypes/sample_pdf_skill_test.py --tier2

    # Skip the delay between API calls
    python backend/prototypes/sample_pdf_skill_test.py --delay 0

Acceptance criteria for prototype viability:
    Tier 1 — strict ground truth match (row-by-row):
        sample_bank_3col_clean.pdf:    37 included, 0 excluded, $14,582.61
        sample_bank_multicolumn.pdf:   31 included, 8 excluded, $13,688.33

    Tier 2 — sanity checks only:
        valid JSON, statement_type detected, reasonable totals,
        no obvious misclassifications.

If Tier 1 passes, prototype is "viable" — v1.3 integration planning can begin.

Environment:
    Requires ANTHROPIC_API_KEY in environment or .env file at project root.
    Requires: pip install claude-agent-sdk
    Requires Node.js + Claude Code CLI on PATH (Agent SDK uses Claude Code
    binary under the hood per Anthropic docs).

    If the pre-built `pdf` Skill is NOT auto-bundled with the SDK, install
    it from the open-source skills repo:
        https://github.com/anthropics/skills
    and place it at .claude/skills/pdf/SKILL.md under PROJECT_ROOT.

    The script will warn at startup if the Skill is not discoverable.
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── Path resolution ───────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

# Load .env if present so ANTHROPIC_API_KEY is available
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# ── Configuration ─────────────────────────────────────────────────────

MODEL_DEFAULTS = {
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-7",
}

# Tier 1 — strict ground truth comparison
TIER_1_PDFS = [
    {
        "filename": "sample_bank_3col_clean.pdf",
        "ground_truth_csv": "docs/extraction_policy/debug_3col_clean.csv",
        "expected_included": 37,
        "expected_excluded": 0,
        "expected_included_total": 14582.61,
    },
    {
        "filename": "sample_bank_multicolumn.pdf",
        "ground_truth_csv": "docs/extraction_policy/debug_multicolumn.csv",
        "expected_included": 31,
        "expected_excluded": 8,
        "expected_included_total": 13688.33,
    },
]

# Tier 2 — sanity check only, no row-by-row ground truth
TIER_2_PDFS = [
    {"filename": "sample_credit_card_chase.pdf"},
    {"filename": "sample_wells_fargo_style.pdf"},
    {"filename": "boa_business_checking_2024.pdf"},
    {"filename": "chase_sapphire_business_2024.pdf"},
]

PROMPT_FILE = SCRIPT_DIR / "pdf_skill_prompt.md"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "pdf_skill_tests"
SAMPLES_DIR = PROJECT_ROOT / "samples"


# ── Result types ──────────────────────────────────────────────────────

@dataclass
class PDFSkillResult:
    """Result of a single PDF Skill agent run."""
    pdf_filename: str
    success: bool
    model: str
    error: Optional[str] = None
    raw_final_text: Optional[str] = None
    parsed: Optional[dict] = None
    agent_seconds: float = 0.0
    tool_calls: list[str] = field(default_factory=list)
    skill_was_used: bool = False
    cost_estimate: float = 0.0


@dataclass
class GroundTruthRow:
    date: str
    description: str
    amount: float
    transaction_type: str
    include_for_1099: bool
    exclusion_reason: str = ""


@dataclass
class ComparisonReport:
    pdf_filename: str
    ground_truth_included_count: int = 0
    ground_truth_excluded_count: int = 0
    ground_truth_included_total: float = 0.0
    extracted_included_count: int = 0
    extracted_excluded_count: int = 0
    extracted_included_total: float = 0.0
    counts_match: bool = False
    totals_match: bool = False
    total_delta: float = 0.0
    missing_rows: list[dict] = field(default_factory=list)
    spurious_rows: list[dict] = field(default_factory=list)
    misclassified_rows: list[dict] = field(default_factory=list)


# ── Skill discovery ──────────────────────────────────────────────────

def discover_pdf_skill() -> dict[str, Any]:
    """
    Check whether a `pdf` Agent Skill is discoverable in the standard
    filesystem locations. Returns a status dict for startup logging.

    Project-level: <PROJECT_ROOT>/.claude/skills/pdf/SKILL.md
    User-level:    ~/.claude/skills/pdf/SKILL.md
    """
    status: dict[str, Any] = {
        "project_skill_present": False,
        "user_skill_present": False,
        "any_pdf_skill_found": False,
        "details": [],
    }

    project_skill = PROJECT_ROOT / ".claude" / "skills" / "pdf" / "SKILL.md"
    user_skill = Path.home() / ".claude" / "skills" / "pdf" / "SKILL.md"

    if project_skill.exists():
        status["project_skill_present"] = True
        status["details"].append(f"Found project Skill: {project_skill}")
    if user_skill.exists():
        status["user_skill_present"] = True
        status["details"].append(f"Found user Skill: {user_skill}")

    status["any_pdf_skill_found"] = (
        status["project_skill_present"] or status["user_skill_present"]
    )

    if not status["any_pdf_skill_found"]:
        status["details"].append(
            "No `pdf` Skill found at .claude/skills/pdf/SKILL.md "
            "(project or user level). If the pre-built Skill is NOT "
            "auto-bundled with claude-agent-sdk, install from "
            "https://github.com/anthropics/skills and place at "
            f"{project_skill.relative_to(PROJECT_ROOT)}"
        )

    return status


# ── Prompt loader ─────────────────────────────────────────────────────

def load_extraction_instruction() -> str:
    """
    Read the extraction instruction from pdf_skill_prompt.md, extracting
    a plain ``` ... ``` block (not ```python, ```json, etc.).

    The markdown contains multiple fenced blocks:
        - ```python ...```   — illustrative SDK snippets
        - ``` ... ```        — the actual extraction instruction

    We capture the language tag and filter for plain (empty-language) blocks,
    then return the longest one. This is robust to the prose markdown around
    the instruction block.
    """
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_FILE}")

    text = PROMPT_FILE.read_text(encoding="utf-8")
    # Capture language tag in group(1), content in group(2).
    # \w* matches python/json/etc.; plain ``` blocks have empty group(1).
    all_blocks = re.finditer(
        r"^```(\w*)\s*\n(.*?)\n```", text, re.MULTILINE | re.DOTALL
    )
    plain_blocks = [m.group(2) for m in all_blocks if not m.group(1)]
    if plain_blocks:
        return max(plain_blocks, key=len).strip()
    return text


# ── Agent SDK call ────────────────────────────────────────────────────

async def call_pdf_skill_agent(
    pdf_path: Path,
    model: str,
    extraction_instruction: str,
    cwd: Path,
) -> PDFSkillResult:
    """
    Invoke the Claude Agent SDK with the pre-built `pdf` Skill enabled.

    The agent reads the PDF via the Skill (which uses Bash + Read under
    progressive disclosure), then returns a JSON-shaped final message
    per the extraction_instruction's schema.

    Returns a PDFSkillResult with parsed JSON or error info.
    """
    try:
        from claude_agent_sdk import (
            query,
            ClaudeAgentOptions,
            AssistantMessage,
            ResultMessage,
        )
    except ImportError:
        return PDFSkillResult(
            pdf_filename=pdf_path.name,
            success=False,
            model=model,
            error=(
                "claude-agent-sdk not installed. "
                "Run: pip install claude-agent-sdk"
            ),
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return PDFSkillResult(
            pdf_filename=pdf_path.name,
            success=False,
            model=model,
            error="ANTHROPIC_API_KEY not set in environment or .env",
        )

    if not pdf_path.exists():
        return PDFSkillResult(
            pdf_filename=pdf_path.name,
            success=False,
            model=model,
            error=f"PDF not found: {pdf_path}",
        )

    # Build the prompt: tell the agent which PDF to read, then paste in
    # our extraction schema instruction.
    # We use an absolute path so the agent can read it regardless of cwd.
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
        cwd=str(cwd),
        setting_sources=["user", "project"],
        allowed_tools=["Skill", "Read", "Bash"],
        model=model,
        # Note: max_turns guards against agent loops. PDF extraction
        # typically needs ~5-15 turns (read SKILL.md, read pdf, possibly
        # read supplemental skill files, then answer).
        max_turns=30,
        # Auto-approve tool use so we don't block on interactive prompts.
        # Acceptable for read-only tools (Skill, Read, Bash limited to reads).
        permission_mode="acceptEdits",
    )

    t_start = time.time()
    tool_calls: list[str] = []
    skill_invoked = False
    final_text_parts: list[str] = []
    result_cost: Optional[float] = None
    api_error: Optional[str] = None

    try:
        async for message in query(prompt=prompt_text, options=options):
            # AssistantMessage = streamed text + tool use blocks
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    # Text blocks: capture for final-answer parsing
                    if hasattr(block, "text") and block.text:
                        final_text_parts.append(block.text)
                    # Tool use blocks: record name for diagnostics
                    if hasattr(block, "name") and block.name:
                        tool_calls.append(block.name)
                        if block.name == "Skill":
                            skill_invoked = True
                        # Also detect skill use by Bash reading SKILL.md
                        if block.name == "Bash" and hasattr(block, "input"):
                            cmd = str(block.input).lower()
                            if "skill.md" in cmd or "/skills/" in cmd:
                                skill_invoked = True

            # ResultMessage = final result + cost/usage summary
            elif isinstance(message, ResultMessage):
                if hasattr(message, "total_cost_usd") and message.total_cost_usd:
                    result_cost = float(message.total_cost_usd)
                if hasattr(message, "subtype") and message.subtype == "error":
                    api_error = f"Agent ended with error subtype: {message.subtype}"
    except Exception as e:
        return PDFSkillResult(
            pdf_filename=pdf_path.name,
            success=False,
            model=model,
            error=f"Agent run failed: {type(e).__name__}: {str(e)[:400]}",
            agent_seconds=time.time() - t_start,
            tool_calls=tool_calls,
            skill_was_used=skill_invoked,
        )

    agent_seconds = time.time() - t_start

    if api_error:
        return PDFSkillResult(
            pdf_filename=pdf_path.name,
            success=False,
            model=model,
            error=api_error,
            agent_seconds=agent_seconds,
            tool_calls=tool_calls,
            skill_was_used=skill_invoked,
        )

    # Combine all assistant text; the final JSON should be in the last
    # significant text block. Try to find a JSON object in the combined
    # output, defensive against any conversational text the agent may add.
    raw = "\n".join(final_text_parts).strip()

    parsed = _extract_json_object(raw)
    parse_error = None if parsed else "Could not extract a valid JSON object from agent output"

    return PDFSkillResult(
        pdf_filename=pdf_path.name,
        success=(parsed is not None),
        model=model,
        error=parse_error,
        raw_final_text=raw,
        parsed=parsed,
        agent_seconds=agent_seconds,
        tool_calls=tool_calls,
        skill_was_used=skill_invoked,
        cost_estimate=result_cost or 0.0,
    )


def _extract_json_object(text: str) -> Optional[dict]:
    """
    Defensively extract a JSON object from agent output. Handles:
    - Pure JSON
    - JSON wrapped in markdown fences
    - JSON preceded or followed by conversational text
    """
    if not text:
        return None

    # Try direct parse first
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find the first { and last } and try that span
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Look for ```json ... ``` block
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    return None


# ── Ground truth loader ───────────────────────────────────────────────

def load_ground_truth(csv_path: Path) -> list[GroundTruthRow]:
    """Load row-level ground truth from a debug CSV."""
    if not csv_path.exists():
        return []

    rows: list[GroundTruthRow] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        reader.fieldnames = fieldnames

        for row in reader:
            def get(*keys, default=""):
                for k in keys:
                    if k in row and row[k] is not None and str(row[k]).strip() != "":
                        return str(row[k]).strip()
                return default

            amt_str = get("amount", "amt").replace("$", "").replace(",", "")
            try:
                amount = float(amt_str) if amt_str else 0.0
            except ValueError:
                amount = 0.0

            inc_str = get("include_for_1099", "include", "included").lower()
            include = inc_str in ("true", "yes", "y", "1", "include")

            rows.append(GroundTruthRow(
                date=get("date"),
                description=get("description", "desc"),
                amount=amount,
                transaction_type=get("transaction_type", "type"),
                include_for_1099=include,
                exclusion_reason=get("exclusion_reason", "reason"),
            ))
    return rows


# ── Comparison logic ──────────────────────────────────────────────────

def compare_to_ground_truth(
    result: PDFSkillResult,
    ground_truth: list[GroundTruthRow],
    pdf_filename: str,
) -> ComparisonReport:
    """Row-by-row comparison of extracted transactions vs ground truth."""
    report = ComparisonReport(pdf_filename=pdf_filename)

    gt_included = [r for r in ground_truth if r.include_for_1099]
    gt_excluded = [r for r in ground_truth if not r.include_for_1099]
    report.ground_truth_included_count = len(gt_included)
    report.ground_truth_excluded_count = len(gt_excluded)
    report.ground_truth_included_total = round(sum(r.amount for r in gt_included), 2)

    if not result.success or not result.parsed:
        return report

    txns = result.parsed.get("transactions", [])
    extracted_included = [t for t in txns if t.get("include_for_1099") is True]
    extracted_excluded = [t for t in txns if t.get("include_for_1099") is False]
    report.extracted_included_count = len(extracted_included)
    report.extracted_excluded_count = len(extracted_excluded)
    report.extracted_included_total = round(
        sum(float(t.get("amount", 0) or 0) for t in extracted_included), 2
    )

    report.counts_match = (
        report.ground_truth_included_count == report.extracted_included_count
        and report.ground_truth_excluded_count == report.extracted_excluded_count
    )
    report.total_delta = round(
        report.extracted_included_total - report.ground_truth_included_total, 2
    )
    report.totals_match = abs(report.total_delta) < 0.05

    def row_key(amount: float, desc: str) -> tuple:
        return (round(amount, 2), desc[:20].upper().strip())

    gt_keys = {row_key(r.amount, r.description): r for r in ground_truth}
    ex_keys = {
        row_key(float(t.get("amount", 0) or 0), str(t.get("description", ""))): t
        for t in txns
    }

    for k, gt_row in gt_keys.items():
        if k not in ex_keys:
            report.missing_rows.append({
                "date": gt_row.date,
                "description": gt_row.description,
                "amount": gt_row.amount,
                "transaction_type": gt_row.transaction_type,
                "include_for_1099": gt_row.include_for_1099,
            })

    for k, ex_row in ex_keys.items():
        if k not in gt_keys:
            report.spurious_rows.append({
                "date": ex_row.get("date", ""),
                "description": ex_row.get("description", ""),
                "amount": ex_row.get("amount", 0),
                "transaction_type": ex_row.get("transaction_type", ""),
                "include_for_1099": ex_row.get("include_for_1099"),
            })

    for k, gt_row in gt_keys.items():
        if k in ex_keys:
            ex_row = ex_keys[k]
            if bool(ex_row.get("include_for_1099")) != gt_row.include_for_1099:
                report.misclassified_rows.append({
                    "description": gt_row.description,
                    "amount": gt_row.amount,
                    "ground_truth_include": gt_row.include_for_1099,
                    "extracted_include": bool(ex_row.get("include_for_1099")),
                    "extracted_type": ex_row.get("transaction_type", ""),
                    "extracted_reason": ex_row.get("exclusion_reason", ""),
                })

    return report


# ── Tier 2 sanity checks ──────────────────────────────────────────────

def sanity_check_tier2(result: PDFSkillResult) -> dict:
    """Light validation for Tier 2 PDFs — no row-by-row ground truth."""
    out: dict[str, Any] = {
        "valid_json": False,
        "statement_type": "—",
        "layout": "—",
        "total_rows": 0,
        "included_count": 0,
        "excluded_count": 0,
        "review_required_count": 0,
        "included_total": 0.0,
        "evidence_provided": False,
        "obvious_risks": [],
    }

    if not result.success or not result.parsed:
        out["obvious_risks"].append("JSON parse failed or agent error")
        return out

    out["valid_json"] = True
    p = result.parsed
    meta = p.get("document_metadata", {})
    out["statement_type"] = meta.get("detected_type", "—")
    out["layout"] = meta.get("detected_layout", "—")

    txns = p.get("transactions", [])
    out["total_rows"] = len(txns)
    included = [t for t in txns if t.get("include_for_1099") is True]
    excluded = [t for t in txns if t.get("include_for_1099") is False]
    review = [t for t in txns if t.get("review_required") is True]
    out["included_count"] = len(included)
    out["excluded_count"] = len(excluded)
    out["review_required_count"] = len(review)
    out["included_total"] = round(
        sum(float(t.get("amount", 0) or 0) for t in included), 2
    )

    if txns:
        with_evidence = sum(
            1 for t in txns if str(t.get("source_text", "")).strip()
        )
        out["evidence_provided"] = (with_evidence / len(txns)) >= 0.5

    if out["total_rows"] == 0:
        out["obvious_risks"].append("Zero transactions extracted")
    if out["included_total"] < 0:
        out["obvious_risks"].append("Negative included total — sign-handling issue")
    if out["statement_type"] == "unknown":
        out["obvious_risks"].append("Statement type not detected")
    for t in txns:
        desc = str(t.get("description", "")).upper()
        ttype = t.get("transaction_type", "")
        if "PAYROLL" in desc and ttype == "vendor_payment":
            out["obvious_risks"].append(
                f"'{t.get('description')}' classified as vendor_payment despite PAYROLL in description"
            )
            break
    for t in txns:
        desc = str(t.get("description", "")).upper()
        ttype = t.get("transaction_type", "")
        if ("OPENING BALANCE" in desc or "ENDING BALANCE" in desc) and ttype == "vendor_payment":
            out["obvious_risks"].append(
                f"'{t.get('description')}' classified as vendor_payment despite being a balance line"
            )
            break

    return out


# ── Markdown report writer ────────────────────────────────────────────

def write_summary_report(
    output_dir: Path,
    model: str,
    tier1_results: list[tuple[PDFSkillResult, ComparisonReport]],
    tier2_results: list[tuple[PDFSkillResult, dict]],
    skill_discovery: dict,
    total_cost: float,
    total_seconds: float,
):
    """Write outputs/pdf_skill_tests/pdf_skill_test_summary.md"""
    report_path = output_dir / "pdf_skill_test_summary.md"

    lines: list[str] = []
    lines.append("# PDF Skill Prototype — Test Summary (Agent SDK + pre-built `pdf` Skill)")
    lines.append("")
    lines.append(f"**Model:** `{model}`")
    lines.append(f"**Run timestamp:** {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"**Mechanism:** `claude_agent_sdk.query()` with `setting_sources=['user','project']` and `allowed_tools=['Skill','Read','Bash']`")
    lines.append(f"**Total agent time:** {total_seconds:.1f}s")
    lines.append(f"**Reported cost:** ${total_cost:.4f}")
    lines.append("")

    # Skill discovery section
    lines.append("## Skill Discovery")
    lines.append("")
    if skill_discovery["any_pdf_skill_found"]:
        if skill_discovery["project_skill_present"]:
            lines.append("- ✓ Project-level `pdf` Skill found at `.claude/skills/pdf/SKILL.md`")
        if skill_discovery["user_skill_present"]:
            lines.append("- ✓ User-level `pdf` Skill found at `~/.claude/skills/pdf/SKILL.md`")
    else:
        lines.append("- ⚠ No `pdf` Skill found at standard filesystem locations.")
        lines.append("  Agent may have used a bundled Skill (if any) or fallen back to")
        lines.append("  generic file reading. Check `skill_was_used` per-PDF below.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Tier 1 ──
    lines.append("## Tier 1 — Strict Ground Truth Comparison")
    lines.append("")
    lines.append("Both PDFs must match ground truth row-by-row for the prototype")
    lines.append("to be considered viable for v1.3 integration.")
    lines.append("")

    if not tier1_results:
        lines.append("_No Tier 1 results._")
    else:
        lines.append("| PDF | Agent OK | Skill Used | Counts | Totals | Δ Total | Missing | Spurious | Misclass |")
        lines.append("|---|:---:|:---:|:---:|:---:|---:|---:|---:|---:|")
        for (res, cmp) in tier1_results:
            agent_ok = "✓" if res.success else "✗"
            skill = "✓" if res.skill_was_used else "✗"
            counts = "✓" if cmp.counts_match else "✗"
            totals = "✓" if cmp.totals_match else "✗"
            delta_str = f"${cmp.total_delta:+,.2f}"
            lines.append(
                f"| `{res.pdf_filename}` | {agent_ok} | {skill} | {counts} | {totals} | "
                f"{delta_str} | {len(cmp.missing_rows)} | {len(cmp.spurious_rows)} | "
                f"{len(cmp.misclassified_rows)} |"
            )
        lines.append("")

        for (res, cmp) in tier1_results:
            lines.append(f"### `{res.pdf_filename}`")
            lines.append("")
            if not res.success:
                lines.append(f"**Agent run failed:** {res.error}")
                lines.append("")
                if res.tool_calls:
                    lines.append(f"Tools invoked before failure: `{', '.join(res.tool_calls)}`")
                    lines.append("")
                continue
            lines.append(f"- Agent time: {res.agent_seconds:.1f}s · ${res.cost_estimate:.4f}")
            lines.append(f"- Skill used: {res.skill_was_used} · Tool calls: `{', '.join(res.tool_calls) or '(none)'}`")
            lines.append(f"- Ground truth: {cmp.ground_truth_included_count} included, "
                        f"{cmp.ground_truth_excluded_count} excluded, "
                        f"${cmp.ground_truth_included_total:,.2f}")
            lines.append(f"- Extracted: {cmp.extracted_included_count} included, "
                        f"{cmp.extracted_excluded_count} excluded, "
                        f"${cmp.extracted_included_total:,.2f}")
            lines.append("")

            if cmp.missing_rows:
                lines.append("**Missing rows** (in ground truth, not in extraction):")
                for r in cmp.missing_rows[:10]:
                    lines.append(f"- {r['date']} · {r['description']} · "
                                f"${r['amount']:,.2f} · {r['transaction_type']}")
                if len(cmp.missing_rows) > 10:
                    lines.append(f"- ...and {len(cmp.missing_rows) - 10} more")
                lines.append("")

            if cmp.spurious_rows:
                lines.append("**Spurious rows** (in extraction, not in ground truth):")
                for r in cmp.spurious_rows[:10]:
                    lines.append(f"- {r['date']} · {r['description']} · "
                                f"${r['amount']} · {r['transaction_type']}")
                if len(cmp.spurious_rows) > 10:
                    lines.append(f"- ...and {len(cmp.spurious_rows) - 10} more")
                lines.append("")

            if cmp.misclassified_rows:
                lines.append("**Misclassified rows** (matched amount+desc, include differs):")
                for r in cmp.misclassified_rows[:10]:
                    lines.append(
                        f"- {r['description']} · ${r['amount']:,.2f} · "
                        f"GT={r['ground_truth_include']} / Extract={r['extracted_include']} "
                        f"(type={r['extracted_type']})"
                    )
                if len(cmp.misclassified_rows) > 10:
                    lines.append(f"- ...and {len(cmp.misclassified_rows) - 10} more")
                lines.append("")

    # ── Tier 2 ──
    if tier2_results:
        lines.append("---")
        lines.append("")
        lines.append("## Tier 2 — Layout Robustness Sanity Checks")
        lines.append("")
        lines.append("| PDF | OK | Skill | Type | Layout | Rows | Incl | Excl | Review | Incl Total | Evid | Risks |")
        lines.append("|---|:---:|:---:|---|---|---:|---:|---:|---:|---:|:---:|---:|")
        for (res, sc) in tier2_results:
            agent_ok = "✓" if res.success else "✗"
            skill = "✓" if res.skill_was_used else "✗"
            evidence = "✓" if sc["evidence_provided"] else "✗"
            risk_count = len(sc["obvious_risks"])
            lines.append(
                f"| `{res.pdf_filename}` | {agent_ok} | {skill} | {sc['statement_type']} | "
                f"{sc['layout']} | {sc['total_rows']} | {sc['included_count']} | "
                f"{sc['excluded_count']} | {sc['review_required_count']} | "
                f"${sc['included_total']:,.2f} | {evidence} | {risk_count} |"
            )
        lines.append("")

        for (res, sc) in tier2_results:
            if sc["obvious_risks"] or not res.success:
                lines.append(f"### `{res.pdf_filename}` — notes")
                lines.append("")
                if not res.success:
                    lines.append(f"- Agent/parse error: {res.error}")
                for risk in sc["obvious_risks"]:
                    lines.append(f"- {risk}")
                lines.append("")

    # ── Bottom line ──
    lines.append("---")
    lines.append("")
    lines.append("## Bottom line")
    lines.append("")

    if tier1_results:
        all_t1_match = all(
            cmp.counts_match and cmp.totals_match
            for (_, cmp) in tier1_results
        )
        if all_t1_match:
            lines.append("**Tier 1: PASS.** Both core PDFs match ground truth exactly.")
            lines.append("Pre-built `pdf` Agent Skill is viable as a v1.3 ingestion path.")
        else:
            lines.append("**Tier 1: needs review.** See per-PDF detail above.")
            lines.append("Possible causes: prompt iteration needed, Skill not auto-invoked,")
            lines.append("or schema mismatch between Skill output and our expected JSON.")
    else:
        lines.append("_No Tier 1 results to evaluate._")
    lines.append("")

    if tier2_results:
        clean_count = sum(
            1 for (res, sc) in tier2_results
            if res.success and not sc["obvious_risks"]
        )
        lines.append(f"**Tier 2:** {clean_count} of {len(tier2_results)} PDFs ran cleanly.")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("This prototype is **standalone**. It does not affect the production app.")
    lines.append("For v1.3 integration planning, see `V1_2_STATUS.md`.")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ── Main runner ───────────────────────────────────────────────────────

async def run_single_pdf(
    pdf_path: Path, model: str, instruction: str,
    output_dir: Path, cwd: Path
) -> PDFSkillResult:
    """Run PDF Skill on one PDF, save its JSON output."""
    print(f"  → Agent run: {model} on {pdf_path.name}...", flush=True)
    result = await call_pdf_skill_agent(pdf_path, model, instruction, cwd)

    if result.success:
        skill_str = "skill used" if result.skill_was_used else "no skill"
        print(f"     OK · {result.agent_seconds:.1f}s · {skill_str} · "
              f"${result.cost_estimate:.4f}", flush=True)
        if result.parsed:
            txns = result.parsed.get("transactions", [])
            included = sum(1 for t in txns if t.get("include_for_1099") is True)
            excluded = sum(1 for t in txns if t.get("include_for_1099") is False)
            total = sum(float(t.get("amount", 0) or 0)
                       for t in txns if t.get("include_for_1099") is True)
            print(f"     {len(txns)} total · {included} included · {excluded} excluded · "
                  f"${total:,.2f} included total", flush=True)
        if result.tool_calls:
            unique_tools = list(dict.fromkeys(result.tool_calls))
            print(f"     tools: {', '.join(unique_tools)}", flush=True)
    else:
        print(f"     FAIL · {result.error}", flush=True)
        if result.tool_calls:
            print(f"     tools before fail: {', '.join(result.tool_calls)}", flush=True)

    # Save JSON output
    out_path = output_dir / f"{pdf_path.stem}_pdf_skill.json"
    out_dict = {
        "pdf_filename": result.pdf_filename,
        "model": result.model,
        "success": result.success,
        "error": result.error,
        "agent_seconds": round(result.agent_seconds, 2),
        "tool_calls": result.tool_calls,
        "skill_was_used": result.skill_was_used,
        "cost_estimate": round(result.cost_estimate, 6),
        "parsed": result.parsed,
        "raw_final_text": result.raw_final_text if not result.parsed else None,
    }
    out_path.write_text(json.dumps(out_dict, indent=2, default=str), encoding="utf-8")
    try:
        print(f"     saved → {out_path.relative_to(PROJECT_ROOT)}", flush=True)
    except ValueError:
        print(f"     saved → {out_path}", flush=True)

    return result


async def amain(args):
    model = MODEL_DEFAULTS.get(args.model.lower(), args.model)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load extraction instruction
    try:
        instruction = load_extraction_instruction()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Check Skill discovery
    skill_status = discover_pdf_skill()

    print(f"== PDF Skill Prototype (Agent SDK) ==")
    print(f"Loaded instruction from {PROMPT_FILE.relative_to(PROJECT_ROOT)} "
          f"({len(instruction)} chars)")
    try:
        print(f"Output directory: {output_dir.relative_to(PROJECT_ROOT)}")
    except ValueError:
        print(f"Output directory: {output_dir}")
    print(f"Model: {model}")
    print(f"Delay between calls: {args.delay}s")
    print(f"Working directory (cwd for SDK): {PROJECT_ROOT}")
    print()
    print("Skill discovery:")
    for line in skill_status["details"]:
        print(f"  {line}")
    if not skill_status["any_pdf_skill_found"]:
        print("  (Continuing — Skill may still be auto-bundled with SDK.)")
    print()

    # ── Single-PDF mode ──
    if args.single:
        pdf_path = Path(args.single)
        if not pdf_path.is_absolute():
            pdf_path = PROJECT_ROOT / pdf_path
        if not pdf_path.exists():
            print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
            return 1
        print("== Single PDF mode ==")
        await run_single_pdf(pdf_path, model, instruction, output_dir, PROJECT_ROOT)
        return 0

    # ── Tier 1 ──
    print("== Tier 1 — strict ground truth comparison ==")
    tier1_results: list[tuple[PDFSkillResult, ComparisonReport]] = []
    total_cost = 0.0
    total_seconds = 0.0

    for i, spec in enumerate(TIER_1_PDFS):
        pdf_path = SAMPLES_DIR / spec["filename"]
        if not pdf_path.exists():
            print(f"  ⚠ Skipping {spec['filename']} — not in samples/")
            continue

        if i > 0:
            print(f"  (waiting {args.delay}s...)")
            await asyncio.sleep(args.delay)

        result = await run_single_pdf(pdf_path, model, instruction, output_dir, PROJECT_ROOT)
        total_cost += result.cost_estimate
        total_seconds += result.agent_seconds

        gt_csv = PROJECT_ROOT / spec["ground_truth_csv"]
        gt_rows = load_ground_truth(gt_csv)
        if not gt_rows:
            print(f"     ⚠ Ground truth CSV missing or empty: {gt_csv}")
            cmp = ComparisonReport(pdf_filename=spec["filename"])
        else:
            cmp = compare_to_ground_truth(result, gt_rows, spec["filename"])
            print(f"     vs GT: {'✓' if cmp.counts_match else '✗'} counts · "
                  f"{'✓' if cmp.totals_match else '✗'} totals · "
                  f"Δ ${cmp.total_delta:+.2f} · "
                  f"{len(cmp.missing_rows)} missing · "
                  f"{len(cmp.spurious_rows)} spurious · "
                  f"{len(cmp.misclassified_rows)} misclassified")
        tier1_results.append((result, cmp))
        print()

    # ── Tier 2 ──
    tier2_results: list[tuple[PDFSkillResult, dict]] = []
    if args.tier2:
        print("== Tier 2 — layout robustness sanity checks ==")
        for spec in TIER_2_PDFS:
            pdf_path = SAMPLES_DIR / spec["filename"]
            if not pdf_path.exists():
                print(f"  ⚠ Skipping {spec['filename']} — not in samples/")
                continue

            print(f"  (waiting {args.delay}s...)")
            await asyncio.sleep(args.delay)

            result = await run_single_pdf(pdf_path, model, instruction, output_dir, PROJECT_ROOT)
            total_cost += result.cost_estimate
            total_seconds += result.agent_seconds

            sc = sanity_check_tier2(result)
            risk_str = (f"{len(sc['obvious_risks'])} risks"
                       if sc['obvious_risks'] else "no risks flagged")
            print(f"     sanity: type={sc['statement_type']} · "
                  f"layout={sc['layout']} · "
                  f"{sc['total_rows']} rows · "
                  f"{risk_str}")
            tier2_results.append((result, sc))
            print()

    # Write summary
    report_path = write_summary_report(
        output_dir, model, tier1_results, tier2_results,
        skill_status, total_cost, total_seconds,
    )
    try:
        print(f"Summary written: {report_path.relative_to(PROJECT_ROOT)}")
    except ValueError:
        print(f"Summary written: {report_path}")
    print(f"Total reported cost: ${total_cost:.4f}")
    print(f"Total agent time:    {total_seconds:.1f}s")
    print()

    if tier1_results and all(
        cmp.counts_match and cmp.totals_match
        for (_, cmp) in tier1_results
    ):
        print("Tier 1 PASS — pre-built `pdf` Skill is viable.")
        return 0
    else:
        print("Tier 1 did not fully pass. See report for detail.")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="PDF Skill prototype — Agent SDK + pre-built `pdf` Skill"
    )
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--single", type=str, default=None)
    parser.add_argument("--tier2", action="store_true")
    parser.add_argument("--delay", type=int, default=5)
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
