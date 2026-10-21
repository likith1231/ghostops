"""
Incident deduplication and correlation
======================================

Two levels:

* **Alerts.** Each alert gets a fingerprint: alert name + workload (the pod
  name with its ReplicaSet/pod hash stripped, so every restart of
  ``sample-app`` is the same workload). A repeat of an open incident is
  attached to it instead of starting another pipeline; a *different* alert
  on the same workload within ``CORRELATION_WINDOW_S`` joins the same
  incident (memory, errors and latency on one pod are usually one problem).
  A resolved alert closes its incident.
* **Pull requests.** Before opening a PR, ``open_pr_for()`` looks for an open
  PR with the same root cause on the same file; if there is one, GhostOps
  links to it instead of opening a duplicate.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents import knowledge_base

CORRELATION_WINDOW_S = 600
# Alertmanager re-notifies a firing alert at least hourly (repeat_interval), so an incident that has
# heard nothing for this long is dead: its "resolved" message was lost (backend down, restart).
STALE_AFTER_S = 7200

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS open_incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workload    TEXT NOT NULL,
    alerts      TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    run_ref     TEXT,
    state       TEXT NOT NULL DEFAULT 'open',
    resolved_at TEXT
);
"""

# ReplicaSet hash (pod-template-hash, 8–10 chars) + pod suffix (5 chars).
_POD_SUFFIX = re.compile(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$")


def workload(labels: dict[str, Any]) -> str:
    """The workload an alert is about: deployment-level, stable across restarts."""
    pod = labels.get("pod") or ""
    if pod:
        return _POD_SUFFIX.sub("", pod)
    return labels.get("component") or labels.get("deployment") or labels.get("job") or "cluster"


def fingerprint(labels: dict[str, Any]) -> str:
    return f"{labels.get('alertname', 'alert')}@{workload(labels)}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else knowledge_base._DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["alerts"] = json.loads(d["alerts"])
    return d


def classify_alert(labels: dict[str, Any], *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Decide what a firing alert is, and record it.

    Returns ``{"action": "new" | "duplicate" | "correlated", "incident": {...}}``.
    Only "new" should start a pipeline.
    """
    name, target, now = labels.get("alertname", "alert"), workload(labels), _now()
    conn = _connect(db_path)
    try:
        cutoff = (now - timedelta(seconds=STALE_AFTER_S)).isoformat()
        conn.execute("UPDATE open_incidents SET state = 'resolved', resolved_at = ? WHERE state = 'open' AND last_seen < ?", (now.isoformat(), cutoff))
        rows = [_row(r) for r in conn.execute("SELECT * FROM open_incidents WHERE workload = ? AND state = 'open' ORDER BY id DESC", (target,))]
        for inc in rows:
            if name in inc["alerts"]:
                conn.execute("UPDATE open_incidents SET occurrences = occurrences + 1, last_seen = ? WHERE id = ?", (now.isoformat(), inc["id"]))
                conn.commit()
                return {"action": "duplicate", "incident": _row(conn.execute("SELECT * FROM open_incidents WHERE id = ?", (inc["id"],)).fetchone())}
        for inc in rows:
            if now - datetime.fromisoformat(inc["last_seen"]) <= timedelta(seconds=CORRELATION_WINDOW_S):
                alerts = inc["alerts"] + [name]
                conn.execute(
                    "UPDATE open_incidents SET alerts = ?, occurrences = occurrences + 1, last_seen = ? WHERE id = ?",
                    (json.dumps(alerts), now.isoformat(), inc["id"]),
                )
                conn.commit()
                return {"action": "correlated", "incident": _row(conn.execute("SELECT * FROM open_incidents WHERE id = ?", (inc["id"],)).fetchone())}
        cur = conn.execute(
            "INSERT INTO open_incidents (workload, alerts, first_seen, last_seen) VALUES (?, ?, ?, ?)",
            (target, json.dumps([name]), now.isoformat(), now.isoformat()),
        )
        conn.commit()
        return {"action": "new", "incident": _row(conn.execute("SELECT * FROM open_incidents WHERE id = ?", (cur.lastrowid,)).fetchone())}
    finally:
        conn.close()


def attach_run(incident_id: int, run_ref: str, *, db_path: str | Path | None = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE open_incidents SET run_ref = ? WHERE id = ?", (run_ref, incident_id))
        conn.commit()
    finally:
        conn.close()


def close_open(*, workload_name: str | None = None, db_path: str | Path | None = None) -> int:
    """Close open incidents (all, or one workload's); returns how many. Used to start a demo clean."""
    conn = _connect(db_path)
    try:
        sql, args = "UPDATE open_incidents SET state = 'resolved', resolved_at = ? WHERE state = 'open'", [_now().isoformat()]
        if workload_name:
            sql, args = sql + " AND workload = ?", args + [workload_name]
        n = conn.execute(sql, args).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def resolve_alert(labels: dict[str, Any], *, db_path: str | Path | None = None) -> int | None:
    """An alert stopped firing: drop it from its incident; close the incident when none remain."""
    name, target = labels.get("alertname", "alert"), workload(labels)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM open_incidents WHERE workload = ? AND state = 'open' AND alerts LIKE ? ORDER BY id DESC LIMIT 1",
            (target, f'%"{name}"%'),
        ).fetchone()
        if row is None:
            return None
        inc = _row(row)
        remaining = [a for a in inc["alerts"] if a != name]
        if remaining:
            conn.execute("UPDATE open_incidents SET alerts = ? WHERE id = ?", (json.dumps(remaining), inc["id"]))
        else:
            conn.execute("UPDATE open_incidents SET state = 'resolved', resolved_at = ? WHERE id = ?", (_now().isoformat(), inc["id"]))
        conn.commit()
        return inc["id"]
    finally:
        conn.close()


def incidents(limit: int = 20, *, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM open_incidents ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def open_pr_for(root_cause: str, file_path: str | None, *, db_path: str | Path | None = None) -> str | None:
    """URL of an open GhostOps PR for the same root cause on the same file, if any."""
    path = Path(db_path) if db_path else knowledge_base._DB_PATH
    if not path.exists() or not root_cause:
        return None
    knowledge_base.init_db(path)
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT pr_url, patch_diff FROM incidents WHERE pr_created = 1 AND root_cause = ? "
            "AND (pr_state IS NULL OR pr_state = 'open') AND pr_url != '' ORDER BY id DESC",
            (root_cause,),
        ).fetchall()
    finally:
        conn.close()
    for url, diff in rows:
        if not file_path or file_path in (diff or ""):
            return url
    return None
