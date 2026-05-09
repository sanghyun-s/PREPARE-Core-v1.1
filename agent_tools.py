"""
Agent Tools
-----------
Wraps the existing backend modules as Claude Agent SDK custom tools.

Each @tool decorator exposes a Python function to the Claude agent, so the
agent can call it autonomously during the agent loop. The agent decides
which tools to use and in what order based on the task description.

Design rule: these wrappers do NOT reimplement logic. They just adapt the
existing backend/ modules to the tool schema the SDK expects.
"""

import sys
from pathlib import Path

# Make the backend/ modules importable
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from claude_agent_sdk import tool, create_sdk_mcp_server

# Existing modules we built in Session 3
from pdf_extractor import extract_transactions
from vendor_normalizer import normalize_vendor
from transaction_aggregator import aggregate_by_vendor
from excel_generator import generate_excel_report


# ---------------------------------------------------------------------------
# State storage — session-isolated for multi-agent parallel runs
# ---------------------------------------------------------------------------
# Each agent in the multi-agent pipeline gets its own session_id so their
# states never collide. Single-agent mode uses session_id=None (default).

_sessions: dict[str, dict] = {}
_default_session: dict = {}


def _get_state(session_id: str | None = None) -> dict:
    """Return the state dict for this session."""
    if session_id is None:
        return _default_session
    if session_id not in _sessions:
        _sessions[session_id] = {}
    return _sessions[session_id]


def reset_session(session_id: str | None = None):
    """Clear state for this session."""
    global _default_session
    if session_id is None:
        _default_session = {}
    else:
        _sessions[session_id] = {}


def get_session_summaries(session_id: str | None = None) -> list:
    """Return vendor summaries from a completed session (for validation agent)."""
    state = _get_state(session_id)
    return state.get("summaries", [])


def get_session_transactions(session_id: str | None = None) -> list:
    """Return raw transactions from a completed session."""
    state = _get_state(session_id)
    return state.get("transactions", [])


def get_session_normalized(session_id: str | None = None) -> list:
    """Return normalized vendor objects from a completed session."""
    state = _get_state(session_id)
    return state.get("normalized", [])


def get_session_source_label(session_id: str | None = None) -> str:
    """Return the PDF label stored in session state."""
    state = _get_state(session_id)
    return state.get("source_label", "unknown")


# ---------------------------------------------------------------------------
# Tool 1: Extract transactions from a PDF
# ---------------------------------------------------------------------------

@tool(
    "extract_pdf_transactions",
    "Extract transactions from a bank or credit card statement PDF. "
    "Use this as the first step when given a PDF to process. "
    "Returns a count of transactions found and stores them in session state.",
    {"pdf_path": str, "session_id": str}
)
async def extract_pdf_transactions_tool(args):
    pdf_path = args["pdf_path"]
    session_id = args.get("session_id")
    state = _get_state(session_id)

    try:
        result = extract_transactions(pdf_path, source="bank")
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"Error: PDF not found at {pdf_path}"}]}

    state["transactions"] = result.transactions
    state["raw_text"] = result.raw_text
    state["source_label"] = Path(pdf_path).name

    summary = (
        f"Extracted {len(result.transactions)} transactions from {Path(pdf_path).name}.\n"
        f"Method: {result.extraction_method}, Pages: {result.pages_processed}.\n"
    )
    if result.warnings:
        summary += "Warnings: " + "; ".join(result.warnings) + "\n"
    if result.transactions:
        summary += (
            f"First: {result.transactions[0].date} | "
            f"{result.transactions[0].description} | ${result.transactions[0].amount:.2f}\n"
            f"Last:  {result.transactions[-1].date} | "
            f"{result.transactions[-1].description} | ${result.transactions[-1].amount:.2f}"
        )
    return {"content": [{"type": "text", "text": summary}]}


# ---------------------------------------------------------------------------
# Tool 2: Load a known-vendor list
# ---------------------------------------------------------------------------

@tool(
    "load_vendor_list",
    "Load a CSV of known canonical vendor names. Optional step — improves "
    "vendor matching accuracy. The CSV should have vendor names in the first column.",
    {"csv_path": str, "session_id": str}
)
async def load_vendor_list_tool(args):
    import csv
    csv_path = args["csv_path"]
    session_id = args.get("session_id")
    state = _get_state(session_id)

    try:
        vendors = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            first_row = next(reader, None)
            if first_row:
                if first_row[0].lower() not in ("vendor", "vendor name", "name", "canonical name"):
                    vendors.append(first_row[0].strip())
            for row in reader:
                if row and row[0].strip():
                    vendors.append(row[0].strip())
    except FileNotFoundError:
        return {"content": [{"type": "text", "text": f"Error: CSV not found at {csv_path}"}]}

    state["known_vendors"] = vendors
    return {
        "content": [{
            "type": "text",
            "text": f"Loaded {len(vendors)} known vendors: {', '.join(vendors[:5])}"
                    + ("..." if len(vendors) > 5 else "")
        }]
    }


# ---------------------------------------------------------------------------
# Tool 3: Normalize vendor names across all transactions
# ---------------------------------------------------------------------------

@tool(
    "normalize_vendors",
    "Normalize vendor names from all extracted transactions. Strips transaction noise, "
    "detects entity types (LLC/Inc/Corp), and fuzzy-matches against the known vendor list "
    "if one was loaded. Must be called after extract_pdf_transactions.",
    {"session_id": str}
)
async def normalize_vendors_tool(args):
    session_id = args.get("session_id")
    state = _get_state(session_id)

    if "transactions" not in state:
        return {"content": [{"type": "text", "text": "Error: No transactions. Call extract_pdf_transactions first."}]}

    known = state.get("known_vendors", [])
    normalized = [normalize_vendor(t.description, known) for t in state["transactions"]]
    state["normalized"] = normalized

    review_count = sum(1 for n in normalized if n.needs_review)
    unique_canonical = len({n.canonical_name for n in normalized})
    return {"content": [{"type": "text", "text": (
        f"Normalized {len(normalized)} vendor names.\n"
        f"Unique canonical vendors: {unique_canonical}.\n"
        f"Flagged for human review: {review_count}."
    )}]}


@tool(
    "aggregate_by_vendor",
    "Group transactions by canonical vendor, sum payment amounts, and compute statistics. "
    "Must be called after normalize_vendors. Returns a summary of total spend and "
    "identifies vendors crossing the $600 threshold (candidates for 1099 filing).",
    {"session_id": str}
)
async def aggregate_tool(args):
    session_id = args.get("session_id")
    state = _get_state(session_id)

    if "normalized" not in state:
        return {"content": [{"type": "text", "text": "Error: Call normalize_vendors first."}]}

    summaries = aggregate_by_vendor(state["transactions"], state["normalized"])
    state["summaries"] = summaries

    total = sum(s.total_amount for s in summaries)
    over_600 = [s for s in summaries if s.total_amount >= 600]

    lines = [
        f"Aggregated {len(state['transactions'])} transactions into {len(summaries)} vendors.",
        f"Total reconciled: ${total:,.2f}",
        f"Vendors over $600 (potential 1099): {len(over_600)}",
        "",
        "Top 5 vendors by spend:",
    ]
    for s in summaries[:5]:
        entity = s.entity_type or "?"
        flag = " [REVIEW]" if s.needs_review else ""
        lines.append(f"  {s.canonical_name} ({entity}): ${s.total_amount:,.2f} / {s.transaction_count} payments{flag}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "generate_excel_report",
    "Generate the final Excel workbook with three sheets: Vendor Summary, "
    "Transactions, and Summary Stats. This is the final step after aggregate_by_vendor. "
    "Returns the path to the saved Excel file.",
    {"output_path": str, "session_id": str}
)
async def generate_excel_tool(args):
    session_id = args.get("session_id")
    state = _get_state(session_id)

    if "summaries" not in state:
        return {"content": [{"type": "text", "text": "Error: Call aggregate_by_vendor first."}]}

    output_path = args["output_path"]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Import classifier for 1099 eligibility
    try:
        from vendor_classifier_1099 import classify_all_vendors
        eligibility = classify_all_vendors(state["summaries"])
    except ImportError:
        eligibility = None

    generate_excel_report(
        output_path=output_path,
        transactions=state["transactions"],
        normalized=state["normalized"],
        summaries=state["summaries"],
        eligibility=eligibility,
    )
    return {"content": [{"type": "text", "text": (
        f"Excel report generated at {output_path}. "
        f"Contains {len(state['summaries'])} vendors across 3 sheets."
    )}]}


@tool(
    "get_review_items",
    "Get a list of all vendors flagged for human review, with their raw name variants and "
    "confidence scores. Use this when the user asks 'what needs review' or 'explain the flags'.",
    {"session_id": str}
)
async def get_review_items_tool(args):
    session_id = args.get("session_id")
    state = _get_state(session_id)

    if "summaries" not in state:
        return {"content": [{"type": "text", "text": "Error: Run the pipeline first."}]}

    review_items = [s for s in state["summaries"] if s.needs_review]
    if not review_items:
        return {"content": [{"type": "text", "text": "No vendors flagged for review."}]}

    lines = [f"{len(review_items)} vendors need human review:\n"]
    for s in review_items:
        lines.append(f"• {s.canonical_name} — ${s.total_amount:,.2f} ({s.transaction_count} payments)")
        lines.append(f"  Confidence: {s.match_confidence:.0%}")
        lines.append(f"  Raw variants: {'; '.join(s.raw_name_variants)}")
        if s.review_reasons:
            lines.append(f"  Reasons: {'; '.join(s.review_reasons)}")
        lines.append("")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# ---------------------------------------------------------------------------
# Build the MCP server that exposes all tools
# ---------------------------------------------------------------------------

reconciliation_server = create_sdk_mcp_server(
    name="reconciliation-tools",
    version="1.0.0",
    tools=[
        extract_pdf_transactions_tool,
        load_vendor_list_tool,
        normalize_vendors_tool,
        aggregate_tool,
        generate_excel_tool,
        get_review_items_tool,
    ],
)
