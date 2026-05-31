# `backend/prototypes/` — exploratory scripts

This directory holds **standalone prototypes** that are NOT part of the
production PREPARE app. Scripts here are for testing potential architecture
directions before committing them to production code.

Scripts in this directory:
- Do not modify production code paths (`pdf_extractor.py`, `pipeline.py`,
  `agent_tools.py`, `server.py`, `frontend/index.html`, or any workbook
  generator)
- Can be run independently from the project root
- Save outputs to `outputs/<prototype_name>/`
- Are reviewed for promotion to production in a separate work cycle

## Current prototypes

### `sample_pdf_skill_test.py` — v0.2 (Agent SDK + pre-built `pdf` Skill)

Tests whether Anthropic's pre-built `pdf` Agent Skill (invoked via the
Claude Agent SDK with `setting_sources=["user","project"]` and
`allowed_tools=["Skill","Read","Bash"]`) can replace the pdfplumber + regex
extraction path used in production.

**Mechanism:**
```python
options = ClaudeAgentOptions(
    cwd=PROJECT_ROOT,
    setting_sources=["user", "project"],
    allowed_tools=["Skill", "Read", "Bash"],
)
async for message in query(prompt="...", options=options):
    process(message)
```

The agent autonomously invokes the pre-built `pdf` Skill when the prompt
asks for PDF extraction. Skill loads via progressive disclosure — agent
reads `SKILL.md` first, then supplemental files only as needed.

See `V1_2_STATUS.md` for context on why this prototype exists and what
"viable" means for v1.3 integration planning.

**Run from project root:**

```bash
# Tier 1 with Sonnet (default)
python backend/prototypes/sample_pdf_skill_test.py

# Tier 1 + Tier 2 sanity checks
python backend/prototypes/sample_pdf_skill_test.py --tier2

# Opus instead of Sonnet
python backend/prototypes/sample_pdf_skill_test.py --model opus

# Single PDF for one-off testing
python backend/prototypes/sample_pdf_skill_test.py --single samples/sample_bank_3col_clean.pdf

# Skip API call delay (default 5s between calls)
python backend/prototypes/sample_pdf_skill_test.py --delay 0
```

**Outputs land in:** `outputs/pdf_skill_tests/`

**Files in this prototype:**
- `sample_pdf_skill_test.py` — the runner
- `pdf_skill_prompt.md` — the extraction instruction (edited separately so
  prompt iteration doesn't require touching the runner)

**Prerequisites:**

1. `pip install claude-agent-sdk python-dotenv` (in your venv)
2. Node.js + Claude Code CLI on PATH (Agent SDK uses Claude Code under
   the hood per Anthropic docs)
3. `ANTHROPIC_API_KEY` in your `.env` or environment
4. Pre-built `pdf` Skill discoverable at one of:
   - `<PROJECT_ROOT>/.claude/skills/pdf/SKILL.md` (project-level)
   - `~/.claude/skills/pdf/SKILL.md` (user-level)

   The script reports Skill discovery status at startup. If the Skill is
   not found at the filesystem locations and is not auto-bundled with the
   SDK, install it from [anthropics/skills](https://github.com/anthropics/skills)
   and place it at `.claude/skills/pdf/`.

**Cost estimate:** Tier 1 only on Sonnet: ~$0.10–0.30. Tier 1 + Tier 2:
~$0.30–0.80. Opus runs roughly 5× cost. Costs reported by the Agent SDK
itself in `ResultMessage.total_cost_usd`.

**Exit codes:**
- `0` — Tier 1 fully passed (counts and totals match ground truth)
- `1` — Tier 1 had differences (see `outputs/pdf_skill_tests/pdf_skill_test_summary.md`)

## Version history

- **v0.1** — Initial prototype using Messages API directly with PDF as
  `document` content block. Replaced by v0.2 to match mentor's recommended
  architecture (Agent SDK + pre-built Skill, progressive disclosure).
  The classification rules and JSON schema carried over unchanged from
  v0.1 to v0.2.
- **v0.2** — Current. Uses `claude_agent_sdk.query()` with the pre-built
  `pdf` Agent Skill. Matches Anthropic's documented Agent Skill pattern.
