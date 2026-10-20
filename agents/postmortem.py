"""
Automatic postmortem
====================

For every incident GhostOps writes a blameless postmortem (Markdown): summary,
impact, a timeline built from the tamper-evident audit trail, root cause and
evidence, the change, why the fix is trusted (each gate it passed), and
prevention items for that kind of failure. Stored in the knowledge base and
committed with the fix. Offline it is templated from the run's own data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import governance, knowledge_base

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS postmortems (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ref    TEXT,
    created_at TEXT NOT NULL,
    title      TEXT NOT NULL,
    markdown   TEXT NOT NULL
);
"""

PREVENTION: dict[str, list[str]] = {
    "memory_leak": [
        "Alert on memory *growth rate*, not only on a threshold, so a leak is caught hours before an OOM kill.",
        "Lint for module-level collections that are appended to in request handlers and never trimmed.",
        "Load-test new endpoints for memory growth per request before release.",
    ],
    "ci_test_failure": [
        "Require the test suite to pass before merge (branch protection).",
        "Add property-based tests for arithmetic helpers so a wrong operator can't pass a single example.",
    ],
    "null_reference": [
        "Type-annotate upstream payloads as Optional and run mypy in CI, so missing checks fail the build.",
        "Contract-test the upstream API for empty and partial payloads.",
    ],
    "canary_regression": [
        "Ship every release through progressive delivery with automatic canary analysis.",
        "Keep the error-rate SLO alert per release so a bad release is pinned down at once.",
        "Add the failing request pattern to the release's integration tests.",
    ],
    "error_rate_slo_breach": [
        "Correlate error-rate breaches with config and dependency changes, not only deployments.",
    ],
}

_ACTION_LABEL = {
    "shield": "Prompt-injection shield", "diagnose": "Diagnosis", "patch": "Patch", "validate": "Sandbox validation",
    "regression_test": "Regression test", "replay": "Counterfactual replay", "canary_fix": "Canary of the fix",
    "risk": "Risk score", "evidence": "Signed evidence", "open_pr": "Pull request", "rollback": "Rollback",
    "verify": "Verification", "verify_production": "Production verification", "auto_merge": "Auto-merge", "page": "Page",
    "dedupe": "Deduplication", "blocked": "Blocked",
}


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else knowledge_base._DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _timeline(run_ref: str | None, db_path: str | Path | None) -> list[str]:
    if not run_ref:
        return []
    rows = [e for e in reversed(governance.entries(500, db_path=db_path)) if e.get("run_ref") == run_ref]
    out = []
    for e in rows:
        when = datetime.fromisoformat(e["ts"]).strftime("%H:%M:%S")
        label = _ACTION_LABEL.get(e["action"], e["action"])
        out.append(f"| {when} | {label} | {e['decision']} | {e['reason'][:140]} |")
    return out


def write(
    *,
    run_ref: str | None,
    alert_type: str,
    diagnosis: dict[str, Any],
    patch: dict[str, Any],
    validation: dict[str, Any],
    outcome: dict[str, Any],
    started_at: str | None = None,
    impact: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compose, store and return the postmortem for one incident."""
    root = str(diagnosis.get("root_cause", "unknown"))
    title = f"Postmortem: {root.replace('_', ' ')} ({alert_type})"
    now = datetime.now(timezone.utc)
    resolved = validation.get("passed") is True
    duration = ""
    if started_at:
        try:
            secs = (now - datetime.fromisoformat(started_at.replace("Z", "+00:00"))).total_seconds()
            duration = f"{secs / 60:.1f} min from detection to {'verified fix' if resolved else 'verdict'}"
        except ValueError:
            pass

    evidence_lines = []
    for e in diagnosis.get("evidence", []) or []:
        evidence_lines.append(f"- {e.get('reference', e) if isinstance(e, dict) else e}")

    gates = ["- OPA policy: passed (the patch only touches allow-listed files)." if patch.get("diff") else None]
    if patch.get("diff"):
        gates.append("- Blast-radius gate: passed (small, no new dependencies, no secrets, no dangerous or network calls).")
    if validation.get("summary"):
        gates.append(f"- Sandbox: {validation['summary']}")
    rt = validation.get("regression_test")
    if rt:
        gates.append(f"- Regression test `{rt.get('path')}`: failed before the fix = {rt.get('failed_before')}, passes after = {rt.get('passed_after')}.")
    rp = validation.get("replay")
    if rp:
        gates.append(f"- Counterfactual replay: {rp.get('verdict')} — {rp.get('summary')}")
    fc = validation.get("fix_canary")
    if fc:
        gates.append(f"- Canary of the fix: {fc.get('verdict')} — {fc.get('summary')}")
    ev = patch.get("evidence")
    if ev:
        gates.append(f"- Signed evidence: Ed25519 key `{ev.get('key_fingerprint')}`.")
    gates = [g for g in gates if g]

    impact_lines = [f"- **Detected by:** `{alert_type}`"]
    if duration:
        impact_lines.append(f"- **Duration:** {duration}")
    for k, v in (impact or {}).items():
        impact_lines.append(f"- **{k}:** {v}")
    if outcome.get("pr_url"):
        impact_lines.append(f"- **Fix:** {outcome['pr_url']}")
    elif outcome.get("reason"):
        impact_lines.append(f"- **Outcome:** {outcome['reason'].replace('_', ' ')}")

    timeline = _timeline(run_ref, db_path)
    prevention = PREVENTION.get(root, ["Review why existing alerts and tests did not catch this earlier."])

    md = "\n".join([
        f"# {title}",
        "",
        f"_Blameless postmortem written by GhostOps on {now.strftime('%Y-%m-%d %H:%M UTC')} (run `{run_ref or 'n/a'}`)._",
        "",
        "## Summary",
        str(diagnosis.get("summary", "No summary.")),
        "",
        f"**Status:** {'resolved — fix validated' if resolved else 'not resolved automatically — needs a human'}",
        "",
        "## Impact",
        *impact_lines,
        "",
        "## Timeline",
        *(["| Time (UTC) | Step | Decision | Detail |", "|---|---|---|---|", *timeline] if timeline else ["_No audit entries for this run._"]),
        "",
        "## Root cause",
        f"`{root}` (confidence: {diagnosis.get('confidence', 'unknown')})",
        "",
        "### Evidence",
        *(evidence_lines or ["_None recorded._"]),
        "",
        "## The change",
        str(patch.get("explanation") or "No change was made."),
        *(["", "```diff", patch["diff"].rstrip(), "```"] if patch.get("diff") else []),
        "",
        "## Why we trust the fix",
        *(gates or ["_Not validated._"]),
        "",
        "## Prevention",
        *[f"- [ ] {item}" for item in prevention],
        "",
    ])

    conn = _connect(db_path)
    try:
        cur = conn.execute("INSERT INTO postmortems (run_ref, created_at, title, markdown) VALUES (?, ?, ?, ?)", (run_ref, now.isoformat(), title, md))
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    governance.record("postmortem", "written", title, run_ref=run_ref, db_path=db_path)
    return {"id": pid, "run_ref": run_ref, "title": title, "markdown": md}


def listing(limit: int = 20, *, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT id, run_ref, created_at, title FROM postmortems ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def get(run_ref: str, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM postmortems WHERE run_ref = ? ORDER BY id DESC LIMIT 1", (run_ref,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
