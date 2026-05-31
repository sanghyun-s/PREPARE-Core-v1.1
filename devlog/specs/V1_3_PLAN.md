# V1_3_PLAN.md — PDF Skill Integration Plan

**Status:** Draft for mentor review. Architecture and risk documentation; implementation has not started.

**Owner:** Sang-Hyun Seong
**Drafted:** May 2026, following Track 2 prototype testing
**Predecessor docs:** `V1_2_STATUS.md` ("Track 2 — PDF Skill prototype outcome"), `backend/prototypes/README.md`

---

## 1. Purpose

v1.3 integrates Anthropic's pre-built `pdf` Agent Skill (invoked via the Claude Agent SDK) as PREPARE's canonical PDF ingestion path. Today's production runs two parallel extractors (rule-based pdfplumber + AI-assisted multi-agent), neither of which reliably produces row-level `transaction_type` and `include_for_1099` fields. The Track 2 prototype proved a single mechanism that does, on the test corpus.

v1.3 is the work that turns that prototype into production-grade ingestion: adapter layer, failure handling, observability, UX changes for long extractions, and the cost model.

v1.3 is NOT:
- A rewrite of the aggregator, validator, or workbook generators (those stay)
- A removal of the rule-based path (kept as fallback — see Fallback Strategy below)
- A frontend redesign (only the upload→processing→results loop changes)

---

## 2. Prototype outcome recap

Four tests run against the prototype at `backend/prototypes/sample_pdf_skill_test.py`. Detailed numbers in `V1_2_STATUS.md`. Summary:

| Test | Question | Result |
|---|---|---|
| Tier 1 + Tier 2 sweep | Does it work end-to-end? | PASS — Tier 1 row-by-row match to ground truth; Tier 2 sanity-clean across 4 layouts |
| Test 1 — Determinism | Same input → same output? | PASS — Identical counts and totals across multiple runs |
| Test 2 — Opus vs Sonnet | Is Opus meaningfully more accurate? | NEUTRAL — Identical accuracy. Opus is faster (~3×) and slightly more expensive (~15%). |
| Test 3 — Failure modes | What does the agent return on bad input? | PASS — Returns structured JSON with `detected_type: "unknown"` and empty transactions. No hallucination. |
| Test 4 — Batch behavior | Does latency drift under load? | INCONCLUSIVE — Agent SDK subprocess failed at startup on every PDF. Documented as a production risk below. |

Total prototype spend: ~$4.45. Useful data per dollar: high.

---

## 3. Architecture

### 3.1 Where the adapter sits

```
Frontend upload
      ↓
server.py /api/process
      ↓
pipeline.py
      ↓
   ┌──────────────────────────────────────────────────────┐
   │ Extraction layer (v1.3 changes this layer only)      │
   │                                                       │
   │  ┌────────────────────────┐                          │
   │  │ pdf_skill_adapter.py   │ ← NEW                    │
   │  │ (Agent SDK + pdf Skill)│                          │
   │  └────────────────────────┘                          │
   │            ↓ on failure / by user choice ↓           │
   │  ┌────────────────────────┐                          │
   │  │ pdf_extractor.py       │ ← UNCHANGED (fallback)   │
   │  │ (pdfplumber + regex)   │                          │
   │  └────────────────────────┘                          │
   └──────────────────────────────────────────────────────┘
      ↓
transaction_aggregator.py (UNCHANGED)
      ↓
validation, workbook generators (UNCHANGED)
      ↓
Frontend results
```

The adapter is a single new module. Everything downstream of extraction (aggregator, validator, workbook generators) stays the same — the adapter's output conforms to the existing shape that downstream code expects.

### 3.2 The adapter's contract

`pdf_skill_adapter.py` exposes one main function:

```python
def extract_from_pdf(
    pdf_path: Path,
    *,
    model: str = "claude-sonnet-4-6",
    max_turns: int = 15,
    timeout_seconds: int = 300,
) -> ExtractionResult: ...
```

`ExtractionResult` is one of:
- `ExtractionSuccess(transactions=[...], metadata={...}, raw_json={...}, cost_usd=...)`
- `ExtractionFailed(reason="agent_subprocess_failed" | "invalid_pdf" | "agent_returned_unknown" | "timeout" | "schema_violation", details="...", partial_data=...)`

The adapter handles:
1. Pre-flight validation (file size, magic bytes) — fail fast with no API cost
2. Agent SDK invocation
3. Subprocess crash handling with one retry
4. JSON extraction from agent output (defensive against malformed responses)
5. Schema validation of returned JSON
6. Result wrapping into `ExtractionSuccess` or `ExtractionFailed`

Downstream code in `pipeline.py` sees only the result types. It does not know or care what model ran, what tools were invoked, or whether retries happened.

### 3.3 Adapter logic — pseudocode

```python
def extract_from_pdf(pdf_path, *, model, max_turns, timeout_seconds):
    # Step 1: Pre-flight (free, fast)
    if not pdf_path.exists():
        return ExtractionFailed(reason="invalid_pdf", details="File not found")
    if pdf_path.stat().st_size < 1024:
        return ExtractionFailed(reason="invalid_pdf", details="File too small to be a real PDF")
    with open(pdf_path, "rb") as f:
        magic = f.read(4)
    if magic != b"%PDF":
        return ExtractionFailed(reason="invalid_pdf", details="Missing PDF magic bytes")

    # Step 2: Agent run with one retry on subprocess failure
    for attempt in (1, 2):
        try:
            result = _run_agent_once(pdf_path, model, max_turns, timeout_seconds)
            break
        except AgentSubprocessError as e:
            if attempt == 2:
                return ExtractionFailed(
                    reason="agent_subprocess_failed",
                    details=str(e),
                )
            time.sleep(2)  # brief backoff before retry

    # Step 3: Inspect agent result
    if result.parsed is None:
        return ExtractionFailed(reason="schema_violation", details="No JSON in response",
                                partial_data=result.raw_text)
    meta = result.parsed.get("document_metadata", {})
    if meta.get("detected_type") == "unknown" or meta.get("page_count", 0) == 0:
        return ExtractionFailed(reason="agent_returned_unknown",
                                details="Agent could not interpret PDF",
                                partial_data=result.parsed)

    # Step 4: Schema validation
    txns = result.parsed.get("transactions", [])
    if not _validate_transaction_schema(txns):
        return ExtractionFailed(reason="schema_violation",
                                details="Transactions missing required fields")

    # Step 5: Success
    return ExtractionSuccess(
        transactions=txns,
        metadata=meta,
        raw_json=result.parsed,
        cost_usd=result.cost,
    )
```

---

## 4. Production Risk Register

Real risks identified during Track 2 testing. Each has a documented mitigation in the adapter.

### Risk 1 — Agent SDK subprocess fails at startup

**Observed:** Test 4 batch run on May 11, 2026. The Agent SDK's underlying Claude Code subprocess died with `"Fatal error in message reader: Command failed with exit code 1"` for every PDF in the batch. The bare Claude Code CLI (`claude --print`) continued to work in the same shell. Earlier the same day, identical batch runs succeeded.

**Severity:** High when it occurs (0/6 PDFs succeeded); frequency unknown.

**Root cause:** Unconfirmed. Plausible candidates include local state conflict between Claude desktop app and Claude Code CLI, transient SDK or backend issues, or subprocess initialization race conditions.

**Mitigation:**
- One automatic retry with brief backoff in the adapter (Step 2 above). Many subprocess startup failures are transient.
- After two consecutive failures, return `ExtractionFailed(reason="agent_subprocess_failed")` with structured details.
- Frontend renders this as a clear per-statement error with a "Retry" button — user can retry without re-uploading.
- Fallback Strategy (Section 6) ensures user always has an extraction path even when agent fails repeatedly.
- Structured logging captures stderr, tool calls before failure, and full exception detail for post-mortem.

**What we don't know:** Whether the failure rate in production will be 0.1%, 1%, or 10%. The Phased Rollout (Section 7) is designed to measure this before broad release.

### Risk 2 — Agent runs longer than UI timeout

**Observed:** Per-PDF agent time ranges from 22s (failure cases) to 251s (BoA business checking, T2). Realistic worst case under load is unknown.

**Severity:** Medium. Slow extraction frustrates users but doesn't corrupt data.

**Mitigation:**
- Adapter enforces `timeout_seconds=300` (5 minutes) hard cap per PDF.
- Frontend shows "Processing statement X of Y..." progress with elapsed time per statement.
- Beyond 5 minutes, adapter returns `ExtractionFailed(reason="timeout")`.

### Risk 3 — Agent returns valid JSON but wrong schema

**Observed:** Not observed in testing. All 6 successful runs returned correct schema. But the prototype already includes defensive JSON extraction logic for a reason — agents can drift on instruction-following.

**Severity:** Medium. Bad schema breaks downstream code.

**Mitigation:**
- Schema validation in Step 4 of the adapter logic.
- Specific required fields enforced: `date`, `description`, `amount`, `transaction_type`, `include_for_1099`, `source_text`.
- Schema violation returns `ExtractionFailed(reason="schema_violation", partial_data=...)` so we can inspect what came back.

### Risk 4 — Cost spike from bad input or pathological PDFs

**Observed:** Test 3 showed an empty/corrupted PDF costs $0.08–0.15 (agent tries to make sense of nothing). A determined agent on a truncated PDF used 8 tool calls before giving up.

**Severity:** Low individually, medium at scale. 100 corrupted uploads = ~$10 wasted.

**Mitigation:**
- Pre-flight validation in Step 1 catches most bad input at $0 cost.
- `max_turns=15` (down from 30 in the prototype) bounds worst-case agent loops.
- Cost monitoring: adapter logs every run's cost. If average cost per PDF exceeds a threshold, alert.

### Risk 5 — Production users on the same machine as a Claude.app instance

**Observed:** During Track 2 testing, the developer machine had Claude.app running. The Agent SDK uses the same Claude Code CLI binary that Claude.app may share state with. Whether this contributes to Risk 1 is unconfirmed.

**Severity:** Unknown.

**Mitigation:**
- Documentation note in the user-facing install guide: "If you have Claude.app installed and notice extraction failures, try closing Claude.app and retrying."
- This is a workaround, not a fix. The real mitigation is Risk 1's retry logic.

---

## 5. UX implications

### 5.1 Long extraction wait

Per-PDF wall time: 1–4 minutes on Sonnet, 1–1.5 minutes on Opus. A user uploading 5 statements will wait 5–20 minutes total. Today's pdfplumber path takes ~30s for 5 statements.

**Frontend changes required:**

- **Per-PDF progress feedback.** Current loading spinner is fine for 10–60 seconds, not for 10–20 minutes. Need: "Processing statement 2 of 5 — sample_bank_multicolumn.pdf (45s elapsed)". Live update as each statement completes.
- **Per-statement status as it lands.** Once statement 1 finishes, show its card in the Per-Statement view immediately. Don't wait for all statements before showing anything. User stays engaged when they can see incremental progress.
- **Estimated time remaining.** Use observed per-PDF time average to give "Estimated 4 minutes remaining" feedback.
- **Cancel button.** Long extractions need an escape hatch. Cancellation should kill the agent processes cleanly and refund nothing (already paid for in-flight work).
- **Allow leaving and returning.** Long extractions shouldn't require keeping the browser tab open. Server-side processing with a "Check status" or notification when done.

### 5.2 Error display

Today's frontend shows extraction errors as banner text. Per-statement errors need:

- Per-PDF card in Per-Statement Review with clear "Extraction failed" status, the reason (subprocess failure, invalid PDF, timeout, etc.), and a "Retry this statement" button.
- Aggregate-level "X of Y statements extracted successfully" banner so user understands overall state.
- If all statements fail, suggest the rule-based fallback as a "try the rule-based engine" option.

### 5.3 Cost reporting

Current `Technical Details` view shows total cost. With PDF Skill at ~$0.30/PDF, a 10-PDF run is ~$3 and users should see that up front, not just in the post-run report.

- **Estimated cost preview** on the upload screen: "Estimated cost: $1.50–$2.50 for 5 statements (Sonnet)."
- **Per-statement cost** in the Per-Statement card.
- **Total cost** in the existing Technical Details view, broken down by statement.

---

## 6. Fallback Strategy

**Decision:** The rule-based pdfplumber path is kept as a fallback engine in v1.3. It is NOT removed.

**Rationale:**

1. **Reliability floor.** When the agent fails (Risk 1), users need an alternative that doesn't require API access or external state. The rule-based path runs locally with deterministic timing.
2. **Cost choice for users.** Some users will prefer free + fast over accurate + slow. Keep the option.
3. **Diagnostic value.** When v1.3 has bugs (and it will), comparing rule-based output vs agent output on the same PDF is the fastest diagnostic.
4. **Distribution flexibility.** Users without API keys or in restricted environments can still use PREPARE in rule-based mode.

**Frontend implementation:**

The existing engine dropdown becomes:
- **PDF Skill (recommended)** — new default. Uses Agent SDK + pdf Skill. ~$0.20–0.60/PDF, 1–4 min/PDF, highest accuracy.
- **Rule-based (fast, free)** — existing rule-based path. ~$0/PDF, ~5s/PDF, may miss rows on multi-column layouts.
- **Multi-agent (existing AI)** — kept temporarily through v1.3, deprecated for v1.4. Lets us A/B compare during transition.

**Automatic fallback:**

After two consecutive agent failures on the same PDF, the adapter MAY automatically retry with the rule-based path if the user has opted in. Default off — explicit retry is safer than silent fallback. Users who toggle "auto-fallback to rule-based on agent failure" trade accuracy for reliability.

---

## 7. Phased Rollout

v1.3 ships in three phases, not a single release.

### Phase 7.1 — Internal alpha (Week 1-2 post-implementation)

- PDF Skill engine available behind a feature flag, off by default
- Only enabled for the developer + 1-2 trusted internal testers
- Test against the existing sample corpus + 5-10 real (sanitized) PDFs with ground truth annotations
- Daily cost cap: $20 (~60-100 PDFs/day)
- Track every agent run: cost, time, success/failure, failure reason
- Acceptance gate to move to Phase 7.2: 100 successful extractions across at least 5 PDF types, ≤2% subprocess failure rate

### Phase 7.2 — Limited beta (Week 3-4)

- Feature flag on for 5-10 known users (recruited explicitly for testing)
- Default engine: rule-based; PDF Skill available via dropdown
- Daily cost cap: $100
- Monitor: failure rate by reason, per-user cost, per-PDF timing distribution, support requests
- Acceptance gate to move to Phase 7.3: ≥95% extraction success rate, no critical bugs unresolved, mentor sign-off

### Phase 7.3 — General availability (Week 5+)

- Feature flag removed; PDF Skill becomes default engine
- Rule-based and multi-agent kept as alternative options
- Daily cost monitoring continues; per-user cost reporting visible to user
- Documentation updated for end users

**Rollback plan:** If Phase 7.2 or 7.3 surfaces a critical issue, frontend can be reverted to default rule-based engine via a single config change. The adapter code stays in place; only the default routing flips.

---

## 8. Open questions for mentor review

1. **Cost pass-through.** Should per-extraction cost ($0.20–0.60) be passed to the user, absorbed by the project, or tiered (free below N PDFs/month, paid above)? Affects monetization model and frontend cost reporting.

2. **Default model.** Tests 1+2 showed Sonnet and Opus produce identical accuracy on our corpus. Opus is faster but 15% more expensive. Default Sonnet is the cost-conservative choice. Reasonable to default Opus? Or keep Sonnet default and offer Opus toggle?

3. **Test corpus expansion.** Before Phase 7.1, do we need 5-10 more ground-truth-annotated PDFs? If yes, are these sourced from your work, synthetic, or paid for from a sample dataset?

4. **Multi-agent deprecation timeline.** v1.3 keeps multi-agent as a third engine option for A/B comparison. v1.4 removes it. Is that the right schedule? Or remove sooner, keep longer?

5. **Auto-fallback default.** If the agent fails twice on a statement, should we silently fall back to rule-based, or always require explicit user retry? Trade-off: reliability vs. honesty.

6. **Subprocess crash mitigation.** Risk 1 is documented but not solved. Do we want a separate investigation before integration starts, or accept the retry-and-document path?

---

## 9. Implementation phases (estimated effort)

Order matters. Phase order is not the same as deployment order (Phase 7).

| # | Work | Why first | Estimate |
|---|---|---|---|
| 1 | Design `ExtractionResult` types and adapter contract | Locks the interface so downstream code can be updated in parallel | 0.5 day |
| 2 | Build `pdf_skill_adapter.py` (extraction logic, retry, schema validation) | Core new code | 2 days |
| 3 | Add structured logging for every agent invocation | Required for Phase 7.1 observability | 0.5 day |
| 4 | Update `pipeline.py` to route through the adapter | Wires the adapter into existing flow | 1 day |
| 5 | Frontend changes for long-extraction UX (progress, per-statement status, cancel) | Without this, users won't tolerate 4-min waits | 2-3 days |
| 6 | Cost preview and per-statement cost reporting | UX completeness | 1 day |
| 7 | Engine dropdown update (PDF Skill default, rule-based fallback) | Visible product change | 0.5 day |
| 8 | Error-state UI for per-statement failures | Required for production-quality error display | 1 day |
| 9 | Documentation: end-user, install guide, troubleshooting | Required for distribution | 1 day |
| 10 | Phase 7.1 internal alpha testing | Acceptance gate | 1-2 weeks |

**Total dev estimate:** ~10-12 working days plus ~2 weeks of Phase 7.1 testing.

---

## 10. What is explicitly NOT in v1.3

To avoid scope creep:

- **Phase 2B (Per-Statement bookkeeping KPI breakdown).** Once v1.3 ships and PDF Skill produces reliable `transaction_type` per row, Phase 2B becomes possible. Separate work cycle. Documented in `V1_2_STATUS.md`.
- **OCR support for scanned PDFs.** The pre-built pdf Skill has some OCR capability; we have not tested it. Out of scope for v1.3.
- **Encrypted/password-protected PDF support.** Skip in v1.3; document as known limitation.
- **Multi-page-spanning transaction handling.** Current samples don't exercise this. Defer.
- **Multi-language statement support.** Out of scope.
- **Real-time/streaming extraction.** Adapter is synchronous request/response. Streaming partial results is v1.4+.

---

## 11. Decision needed before implementation starts

Mentor review of:

- Section 4 (Production Risk Register) — does the retry+fallback approach to Risk 1 satisfy reliability concerns?
- Section 6 (Fallback Strategy) — keep rule-based path? Auto-fallback default?
- Section 7 (Phased Rollout) — gate criteria for moving between phases.
- Section 8 (Open Questions) — answers to questions 1, 2, 4 specifically (cost, default model, deprecation timeline).

Once these are decided, implementation phase 1 (adapter contract design) can start.
