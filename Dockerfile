# PREPARE Reconciliation Workspace — container image
# -----------------------------------------------------------------------------
# The ai_assisted / multi_agent / pdf_skill engines use claude-agent-sdk, which
# drives the Claude Code CLI (a Node binary) as a subprocess. So the runtime
# needs BOTH Python (the app) and Node + the Claude Code CLI on PATH.
#
# Auth is headless via the ANTHROPIC_API_KEY env var (set as a secret on the
# host — Render dashboard / HF Spaces secret). Usage is billed by Anthropic.

FROM python:3.12-slim

# --- System + Node.js 20 (for the Claude Code CLI) ---------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python dependencies (cached layer) --------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- App code ----------------------------------------------------------------
COPY . .

# The Claude Code CLI writes config under $HOME; give it a writable home.
ENV HOME=/app

# Render/most PaaS inject $PORT; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands at runtime.
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT}
