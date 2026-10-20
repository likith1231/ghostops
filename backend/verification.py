"""
Post-merge verification and real MTTR
=====================================

A fix isn't done when the sandbox passes; it's done when production stays
healthy. Once a fix is deployed (a rollback immediately, a merged PR after a
deployment grace period), a background watcher queries the same Prometheus
signal that fired the alert every ``POLL_S`` seconds for ``WINDOW_S``:

* healthy throughout   → "resolved", with MTTR = alert start → verified healthy;
* unhealthy (or firing) → "reopened"; with ``AUTO_ROLLBACK_ON_REGRESSION=1``
  the deployment is rolled back (the kill switch still applies).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import statistics
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents import governance, knowledge_base

logger = logging.getLogger("ghostops.backend.verification")

WINDOW_S = int(os.getenv("VERIFY_WINDOW_S", "600"))
MERGE_GRACE_S = int(os.getenv("VERIFY_MERGE_GRACE_S", "180"))
POLL_S = int(os.getenv("VERIFY_POLL_S", "15"))
SYNC_EVERY_S = int(os.getenv("FEEDBACK_SYNC_S", "600"))

# alert type -> (Prometheus query, unhealthy at or above, alert name)
SIGNALS = {
    "memory_high": (
        'max(container_memory_working_set_bytes{pod=~"sample-app.*", container="sample-app"}'
        ' / container_spec_memory_limit_bytes{pod=~"sample-app.*", container="sample-app"})',
        0.8,
        "SampleAppHighMemory",
    ),
    # Only the release deployed now: after a rollback the bad release's errors linger in the
    # 1-minute rate window (and its alert keeps firing for a while).
    "error_rate_high": (
        'sum(rate(sample_app_requests_total{{status=~"5..", release="{release}"}}[1m]))'
        ' / sum(rate(sample_app_requests_total{{release="{release}"}}[1m]))',
        0.2,
        "SampleAppHighErrorRate",
    ),
}
ROLLBACK_SETTLE_S = int(os.getenv("VERIFY_ROLLBACK_SETTLE_S", "30"))
UNHEALTHY_TO_REOPEN = 2  # consecutive unhealthy samples; one can be metric lag

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ref     TEXT,
    kind        TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    reference   TEXT,
    started_at  TEXT NOT NULL,
    deployed_at TEXT NOT NULL,
    window_s    INTEGER NOT NULL,
    state       TEXT NOT NULL DEFAULT 'watching',
    samples     TEXT NOT NULL DEFAULT '[]',
    chaos       TEXT,
    verdict_at  TEXT,
    mttr_s      REAL,
    note        TEXT
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> sqlite3.Connection:
    path = Path(knowledge_base._DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    if "chaos" not in {r[1] for r in conn.execute("PRAGMA table_info(verifications)")}:
        conn.execute("ALTER TABLE verifications ADD COLUMN chaos TEXT")  # databases from before chaos verification
    conn.row_factory = sqlite3.Row
    return conn


def start(*, kind: str, alert_type: str, started_at: datetime, deployed_at: datetime | None = None,
          run_ref: str | None = None, reference: str | None = None, window_s: int | None = None) -> int | None:
    """Begin watching production after a fix is deployed. Unknown signals are not watched."""
    if alert_type not in SIGNALS:
        return None
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO verifications (run_ref, kind, alert_type, reference, started_at, deployed_at, window_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_ref, kind, alert_type, reference, started_at.isoformat(), (deployed_at or _now()).isoformat(), window_s or WINDOW_S),
        )
        conn.commit()
        governance.record("verify_production", "watching", f"{kind}: watching {alert_type} for {window_s or WINDOW_S}s", run_ref=run_ref)
        return cur.lastrowid
    finally:
        conn.close()


def _deployed_release() -> str:
    from backend import canary

    history = canary.rollout_history()
    return history[-1]["env"].get("RELEASE", "stable") if history else "stable"


def _probe(alert_type: str) -> dict[str, Any]:
    from backend import live

    query, threshold, alert = SIGNALS[alert_type]
    try:
        labels = None
        if "{release}" in query:
            release = _deployed_release()
            query, labels = query.format(release=release), {"release": release}
        value = live._prom(query)
        state = live.alert_state(alert, labels)
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": str(exc)}
    healthy = (value is None or value < threshold) and state != "firing"
    return {"ok": healthy, "value": value, "alert": state}


def tick(now: datetime | None = None) -> list[dict[str, Any]]:
    """One pass over everything being watched; returns the verifications that reached a verdict."""
    now = now or _now()
    conn = _connect()
    done = []
    try:
        for row in conn.execute("SELECT * FROM verifications WHERE state = 'watching'").fetchall():
            deployed = datetime.fromisoformat(row["deployed_at"])
            if now < deployed:
                continue  # merged, waiting for the deployment grace period
            sample = {"at": now.isoformat(), **_probe(row["alert_type"])}
            samples = json.loads(row["samples"]) + [sample]
            recent = [s["ok"] for s in samples[-UNHEALTHY_TO_REOPEN:]]
            if len(recent) == UNHEALTHY_TO_REOPEN and all(ok is False for ok in recent):
                state, note = "reopened", f"{row['alert_type']} unhealthy again (value {sample.get('value')}, alert {sample.get('alert')})"
            elif now >= deployed + timedelta(seconds=row["window_s"]) and any(s["ok"] for s in samples):
                state, note = "resolved", f"healthy for {row['window_s']}s after the fix"
                # Before calling it resolved, attack it (opt-in): pod-kill + re-trigger the fault.
                from backend import chaos_verify

                if chaos_verify.enabled():
                    chaos = chaos_verify.run(row["alert_type"], row["run_ref"])
                    conn.execute("UPDATE verifications SET chaos = ? WHERE id = ?", (json.dumps(chaos), row["id"]))
                    if chaos["verdict"] == "survived":
                        note += "; chaos-verified (pod-kill + re-triggered fault)"
                    elif chaos["verdict"] in ("failed", "error"):
                        state, note = "reopened", f"failed chaos verification: {chaos['summary']}"
            else:
                conn.execute("UPDATE verifications SET samples = ? WHERE id = ?", (json.dumps(samples[-100:]), row["id"]))
                continue
            mttr = (now - datetime.fromisoformat(row["started_at"])).total_seconds() if state == "resolved" else None
            conn.execute(
                "UPDATE verifications SET state = ?, samples = ?, verdict_at = ?, mttr_s = ?, note = ? WHERE id = ?",
                (state, json.dumps(samples[-100:]), now.isoformat(), mttr, note, row["id"]),
            )
            done.append({"id": row["id"], "run_ref": row["run_ref"], "kind": row["kind"], "state": state, "mttr_s": mttr, "note": note})
        conn.commit()
    finally:
        conn.close()
    for v in done:
        governance.record("verify_production", v["state"], v["note"], run_ref=v["run_ref"], evidence={"mttr_s": v["mttr_s"]})
        if v["state"] == "reopened":
            _maybe_roll_back(v)
    return done


def _maybe_roll_back(v: dict[str, Any]) -> None:
    if os.getenv("AUTO_ROLLBACK_ON_REGRESSION", "") not in ("1", "true", "yes") or v["kind"] != "merge":
        return
    if not governance.allow("rollback", run_ref=v["run_ref"]):
        return
    from agents.deployer import rollback_deployment
    from backend import live

    outcome = rollback_deployment(live.DEPLOYMENT, live.NAMESPACE)
    governance.record("rollback", "done" if outcome["rolled_back"] else "failed", "fix did not hold in production", run_ref=v["run_ref"], evidence=outcome)


def listing(limit: int = 20) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM verifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["samples"] = json.loads(d["samples"])[-20:]
        d["chaos"] = json.loads(d["chaos"]) if d.get("chaos") else None
        out.append(d)
    return out


def mttr_summary() -> dict[str, Any]:
    conn = _connect()
    try:
        values = [r[0] for r in conn.execute("SELECT mttr_s FROM verifications WHERE state = 'resolved' AND mttr_s IS NOT NULL")]
        states = dict(conn.execute("SELECT state, count(*) FROM verifications GROUP BY state").fetchall())
    finally:
        conn.close()
    if not values:
        return {"count": 0, "median_s": None, "p90_s": None, "under_5_min_pct": None, "states": states}
    ordered = sorted(values)
    return {
        "count": len(values),
        "median_s": round(statistics.median(ordered), 1),
        "p90_s": round(ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))], 1),
        "under_5_min_pct": round(sum(1 for v in values if v <= 300) / len(values) * 100, 1),
        "states": states,
    }


# ---------------------------------------------------------------------------
# Background watcher
# ---------------------------------------------------------------------------
_stop = threading.Event()


def _loop() -> None:
    from agents import feedback

    last_sync = 0.0
    while not _stop.wait(POLL_S):
        try:
            tick()
            if datetime.now().timestamp() - last_sync >= SYNC_EVERY_S:
                last_sync = datetime.now().timestamp()
                for merged in feedback.sync_pr_outcomes().get("newly_merged", []):
                    start_for_merge(merged)
        except Exception as exc:  # noqa: BLE001
            logger.error("Verification watcher pass failed: %s", exc)


def start_for_merge(merged: dict[str, Any]) -> int | None:
    """A PR was merged: watch production once the deployment has had time to roll out."""
    return start(
        kind="merge",
        alert_type=merged.get("alert_type", ""),
        started_at=datetime.fromisoformat(merged["timestamp"]),
        deployed_at=_now() + timedelta(seconds=MERGE_GRACE_S),
        reference=merged.get("pr_url"),
    )


def run_watcher() -> threading.Thread:
    _stop.clear()
    thread = threading.Thread(target=_loop, daemon=True, name="ghostops-verification")
    thread.start()
    return thread
