#!/usr/bin/env python3
"""
Phase 4A — Reconciliation Extraction Spike: scoring harness (path-robust).

Runs pdf_skill_adapter.extract_from_pdf() on the three phase4_test PDFs,
N times each (stability), scores the returned reconciliation_snapshot against
the locked ground truth, and prints a per-criterion PASS/FAIL table + the
overall GO / NO-GO verdict.

PATH HANDLING
This script locates everything relative to the PROJECT ROOT, which it finds
by walking up from its own location until it sees a `backend/` dir containing
`pdf_skill_adapter.py`. So it works no matter where you run it from:

    project root layout assumed:
      PREPARE_app_v.1.0/
        backend/
          pdf_skill_adapter.py
          prototypes/pdf_skill_prompt.md   <- drop the v0.3 prompt here
        phase4_test/
          northgate_bank_clean.pdf
          summit_cu_transfer.pdf
          harbor_national_unbalanced.pdf
          score_4a.py                       <- this file (or anywhere)

PRE-FLIGHT
Before any paid run it verifies: adapter importable, ANTHROPIC_API_KEY set,
prompt file present and v0.3 (contains reconciliation_snapshot), and all
three test PDFs present. Any failure aborts BEFORE spending money.

USAGE (venv active, ANTHROPIC_API_KEY set):
    python phase4_test/score_4a.py --once          # 1 run/PDF, cheap sanity (~$0.6-2)
    python phase4_test/score_4a.py --runs 3        # full stability pass (~$2-5)
    python phase4_test/score_4a.py --model opus    # try Opus instead of Sonnet
    python phase4_test/score_4a.py --dry-run       # pre-flight only, no API calls

Cost: ~$0.20-0.60 per PDF-run. Default 3 PDFs x 3 runs = 9 runs ≈ $2-5.
Always do --once first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOLERANCE = 0.01  # dollars; matches RECONCILIATION_TOLERANCE in the Phase 4 spec

# ── Ground truth (mirrors phase4_test/GROUND_TRUTH_KEY.md — keep in sync) ──
GROUND_TRUTH = {
    "northgate_bank_clean.pdf": {
        "beginning_balance": 12000.00, "total_deposits": 8500.00,
        "total_withdrawals": 5747.50, "checks": 1200.00, "transfers": 0.00,
        "fees": 35.00, "reported_ending_balance": 13517.50,
        "expected_status": "balanced", "expected_difference": 0.00,
        "absent_sections": ["transfers"],
    },
    "summit_cu_transfer.pdf": {
        "beginning_balance": 5500.00, "total_deposits": 15200.00,
        "total_withdrawals": 6875.25, "checks": 2150.00, "transfers": 3000.00,
        "fees": 50.00, "reported_ending_balance": 8624.75,
        "expected_status": "balanced", "expected_difference": 0.00,
        "absent_sections": [],
    },
    "harbor_national_unbalanced.pdf": {
        "beginning_balance": 3000.00, "total_deposits": 6000.00,
        "total_withdrawals": 4820.00, "checks": 0.00, "transfers": 0.00,
        "fees": 30.00, "reported_ending_balance": 4000.00,
        "expected_status": "needs_review", "expected_difference": 150.00,
        "absent_sections": ["checks", "transfers"],
    },
}

INPUT_FIELDS = ["beginning_balance", "total_deposits", "total_withdrawals",
                "checks", "transfers", "fees", "reported_ending_balance"]


# ── Path resolution ────────────────────────────────────────────────────

def find_project_root(start: Path) -> Path | None:
    """Walk up from `start` until we find backend/pdf_skill_adapter.py."""
    cur = start.resolve()
    for _ in range(8):
        if (cur / "backend" / "pdf_skill_adapter.py").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _approx(a, b, tol=TOLERANCE):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def _compute_status(snap: dict):
    """Replicate the server-side computation the app will do in 4B."""
    needed = INPUT_FIELDS
    vals = {}
    for k in needed:
        v = snap.get(k)
        if v is None:
            return None, None, "unavailable"
        vals[k] = float(v)
    calc = round(vals["beginning_balance"] + vals["total_deposits"]
                 - vals["total_withdrawals"] - vals["checks"]
                 - vals["transfers"] - vals["fees"], 2)
    diff = round(calc - vals["reported_ending_balance"], 2)
    status = "balanced" if abs(diff) <= TOLERANCE else "needs_review"
    return calc, diff, status


def score_one(pdf_name: str, snap, gt: dict) -> dict:
    out = {"pdf": pdf_name, "field_results": {}, "issues": []}
    if not isinstance(snap, dict):
        out.update(field_accuracy=False, status_correct=False,
                   absent_correct=False, honesty_ok=False)
        out["issues"].append("no reconciliation_snapshot returned")
        return out

    fields_found = snap.get("fields_found", []) or []

    all_fields_ok = True
    for f in INPUT_FIELDS:
        got, want = snap.get(f), gt[f]
        ok = _approx(got, want)
        out["field_results"][f] = {"got": got, "want": want, "ok": ok}
        all_fields_ok = all_fields_ok and ok
    out["field_accuracy"] = all_fields_ok

    calc, diff, status = _compute_status(snap)
    out["computed"] = {"calculated_ending": calc, "difference": diff, "status": status}
    out["status_correct"] = (status == gt["expected_status"]
                             and _approx(diff, gt["expected_difference"]))

    absent_ok = True
    for sec in gt["absent_sections"]:
        if not _approx(snap.get(sec), 0.0):
            absent_ok = False
            out["issues"].append(f"{sec} should be 0.0 (absent), got {snap.get(sec)}")
        if sec in fields_found:
            absent_ok = False
            out["issues"].append(f"{sec} absent but claimed in fields_found")
    out["absent_correct"] = absent_ok

    # Honesty: catch genuine FABRICATION / FALSE CLAIMS only.
    # (fields_found is optional provenance metadata — a correct value that is
    # simply not listed in fields_found is NOT a trust problem, because
    # field-accuracy above already verified every value against ground truth.
    # The real honesty risks are: (a) claiming a field in fields_found that is
    # actually absent, and (b) a non-null value that is WRONG — but (b) is
    # already covered by field_accuracy, so here we only police false claims.)
    honesty_ok = True
    for claimed in fields_found:
        # A field claimed as "found" that ground truth says is absent → false claim.
        if claimed in gt["absent_sections"]:
            honesty_ok = False
            out["issues"].append(f"fields_found claims '{claimed}' but it is absent")
    out["honesty_ok"] = honesty_ok

    # fields_found is optional metadata — record a soft note if absent, but do
    # NOT fail the gate over it.
    if not fields_found:
        out["issues"].append("note: fields_found empty/missing (optional metadata; "
                             "not a gate failure)")

    return out


def preflight(root: Path, pdf_dir: Path, model: str) -> list[str]:
    """Return a list of blocking problems; empty list = ready."""
    problems = []

    # adapter import
    sys.path.insert(0, str(root / "backend"))
    try:
        import pdf_skill_adapter  # noqa
    except ImportError as e:
        problems.append(f"cannot import pdf_skill_adapter: {e}")

    # API key
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY not set in environment")

    # prompt present + v0.3
    prompt = root / "backend" / "prototypes" / "pdf_skill_prompt.md"
    if not prompt.exists():
        problems.append(f"prompt file missing: {prompt}")
    else:
        txt = prompt.read_text(encoding="utf-8", errors="replace")
        if "reconciliation_snapshot" not in txt:
            problems.append("prompt file present but lacks reconciliation_snapshot "
                            "(did you drop in the v0.3 prompt?)")

    # PDFs present
    for name in GROUND_TRUTH:
        if not (pdf_dir / name).exists():
            problems.append(f"test PDF missing: {pdf_dir / name}")

    return problems


def run(pdf_dir: Path, root: Path, runs: int, model: str, dry: bool):
    print("=== Phase 4A — reconciliation extraction gate ===")
    print(f"project root : {root}")
    print(f"pdf dir      : {pdf_dir}")
    print(f"prompt       : {root / 'backend' / 'prototypes' / 'pdf_skill_prompt.md'}")
    print(f"model        : {model}   runs/PDF: {runs}\n")

    problems = preflight(root, pdf_dir, model)
    if problems:
        print("PRE-FLIGHT FAILED — aborting before any API call:")
        for p in problems:
            print(f"  ✗ {p}")
        sys.exit(1)
    print("Pre-flight: OK (adapter, API key, v0.3 prompt, 3 PDFs all present)\n")

    if dry:
        print("--dry-run: pre-flight only, no API calls made. Ready to run for real.")
        return

    import pdf_skill_adapter as adapter

    # Monkeypatch _extract_json_object to capture the parsed JSON so we can read
    # reconciliation_snapshot without editing the adapter (spike-grade hook).
    _orig = adapter._extract_json_object
    _cap = {"last": None}

    def _wrapped(text):
        parsed = _orig(text)
        if isinstance(parsed, dict):
            _cap["last"] = parsed
        return parsed
    adapter._extract_json_object = _wrapped

    def get_snapshot(result):
        snap = getattr(result, "reconciliation_snapshot", None)
        if snap is not None:
            return snap
        parsed = getattr(result, "_parsed_json", None)
        if isinstance(parsed, dict):
            return parsed.get("reconciliation_snapshot")
        return None

    pdfs = sorted(p for p in pdf_dir.glob("*.pdf") if p.name in GROUND_TRUTH)
    stability = {p.name: [] for p in pdfs}
    per_run_pass = []
    total_cost = 0.0

    for run_i in range(1, runs + 1):
        print(f"----- RUN {run_i}/{runs} -----")
        for pdf in pdfs:
            _cap["last"] = None
            result = adapter.extract_from_pdf(pdf, model=model, enable_retry=False)
            try:
                setattr(result, "_parsed_json", _cap["last"])
            except Exception:
                pass
            cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
            total_cost += cost

            if not getattr(result, "success", False):
                # adapter itself failed (not a snapshot problem)
                print(f"  {pdf.name:<34} ADAPTER-FAIL  "
                      f"reason={getattr(result,'failure_reason','?')} "
                      f"(cost≈${cost:.4f})")
                print(f"        {getattr(result,'failure_details','')[:120]}")
                per_run_pass.append(False)
                continue

            snap = get_snapshot(result)
            gt = GROUND_TRUTH[pdf.name]
            sc = score_one(pdf.name, snap, gt)
            crit = [sc["field_accuracy"], sc["status_correct"],
                    sc["absent_correct"], sc["honesty_ok"]]
            passed = all(crit)
            per_run_pass.append(passed)

            print(f"  {pdf.name:<34} {'PASS' if passed else 'FAIL'}  "
                  f"fields={'Y' if sc['field_accuracy'] else 'N'} "
                  f"status={'Y' if sc['status_correct'] else 'N'} "
                  f"absent={'Y' if sc['absent_correct'] else 'N'} "
                  f"honest={'Y' if sc['honesty_ok'] else 'N'}  "
                  f"(cost≈${cost:.4f})")
            if sc.get("computed"):
                c = sc["computed"]
                print(f"        computed: calc={c['calculated_ending']} "
                      f"diff={c['difference']} status={c['status']}  "
                      f"(want status={gt['expected_status']}, diff={gt['expected_difference']})")
            if not sc["field_accuracy"]:
                for f, r in sc["field_results"].items():
                    if not r["ok"]:
                        print(f"        field {f}: got {r['got']} want {r['want']}")
            for issue in sc["issues"]:
                print(f"        ! {issue}")
            if snap:
                stability[pdf.name].append(tuple(snap.get(f) for f in INPUT_FIELDS))
        print()

    print("----- STABILITY (across runs) -----")
    stable_all = True
    if runs < 2:
        print("  (single run — stability not assessed; use --runs 3)")
    else:
        for name, sigs in stability.items():
            uniq = set(sigs)
            ok = len(uniq) == 1 and len(sigs) == runs
            stable_all = stable_all and ok
            print(f"  {name:<34} {'STABLE' if ok else 'UNSTABLE'} "
                  f"({len(uniq)} distinct over {len(sigs)} runs)")
    print()

    print(f"Total API cost this session: ≈${total_cost:.4f}\n")

    print("=" * 62)
    all_pass = all(per_run_pass)
    if all_pass and runs >= 2 and stable_all:
        print("VERDICT: GO — accurate, stable, discrepancy correctly detected.")
        print("         Proceed to Phase 4B (schema). Source B (row-sum")
        print("         cross-check) remains the planned follow-on.")
    elif all_pass and runs < 2:
        print("VERDICT: GO (provisional) — all criteria passed on one run.")
        print("         Re-run with --runs 3 to confirm stability before 4B.")
    elif all_pass and not stable_all:
        print("VERDICT: HOLD — criteria pass per-run but values drift across runs.")
        print("         Stability is required before building on these numbers.")
        print("         Inspect UNSTABLE rows; tighten the prompt; re-run.")
    else:
        print("VERDICT: NO-GO — one or more criteria failed. Do NOT build 4B-4E.")
        print("         Inspect FAIL rows. Refine the prompt and re-run, or shelf/")
        print("         gate the reconciliation snapshot per the Phase 4 spec.")
    print("=" * 62)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=None,
                    help="dir with test PDFs (default: <root>/phase4_test)")
    ap.add_argument("--runs", type=int, default=3, help="stability runs per PDF")
    ap.add_argument("--model", default="sonnet", help="sonnet | opus | full id")
    ap.add_argument("--once", action="store_true", help="shortcut for --runs 1")
    ap.add_argument("--dry-run", action="store_true",
                    help="pre-flight checks only, no API calls")
    args = ap.parse_args()

    root = find_project_root(Path(__file__).parent)
    if root is None:
        root = find_project_root(Path.cwd())
    if root is None:
        print("ERROR: could not locate project root (looked for "
              "backend/pdf_skill_adapter.py walking up from this script and CWD).")
        sys.exit(1)

    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else (root / "phase4_test")
    runs = 1 if args.once else args.runs
    run(pdf_dir, root, runs, args.model, args.dry_run)
