"""
Incident cost meter
===================

What each real incident cost, measured from Prometheus over the incident
window (alert start -> resolution), and what fixing it automatically saved:

* failed requests: real 5xx count in the window;
* degraded minutes: alert start -> resolution;
* wasted memory: GiB-hours above the pod's memory request;
* saved: (typical manual repair time - GhostOps' time) x cost per minute.

Money uses configurable rates, shown with the numbers as assumptions:
COST_PER_FAILED_REQUEST, COST_PER_DEGRADED_MINUTE, COST_PER_GIB_HOUR and
MANUAL_MTTR_MINUTES (default 45: the middle of the 30-90 min industry range
the project synopsis cites).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import knowledge_base
from backend import live

logger = logging.getLogger("ghostops.backend.cost")

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS incident_costs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ref    TEXT,
    alert_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT NOT NULL,
    measures   TEXT NOT NULL,
    cost_usd   REAL NOT NULL,
    saved_usd  REAL NOT NULL
);
"""


def rates() -> dict[str, float]:
    return {
        "per_failed_request": float(os.getenv("COST_PER_FAILED_REQUEST", "0.25")),
        "per_degraded_minute": float(os.getenv("COST_PER_DEGRADED_MINUTE", "20")),
        "per_gib_hour": float(os.getenv("COST_PER_GIB_HOUR", "0.05")),
        "manual_mttr_minutes": float(os.getenv("MANUAL_MTTR_MINUTES", "45")),
    }


def _connect() -> sqlite3.Connection:
    path = Path(knowledge_base._DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def measure(started: datetime, ended: datetime) -> dict[str, Any]:
    """Real numbers for the window, from Prometheus (missing ones stay None)."""
    window = max(60, int((ended - started).total_seconds()))
    sel = f'{{pod=~"{live.DEPLOYMENT}.*", container="{live.DEPLOYMENT}"}}'

    def q(query: str) -> float | None:
        try:
            return live._prom(query)
        except Exception:  # noqa: BLE001
            return None

    failed = q(f'sum(increase(sample_app_requests_total{{status=~"5.."}}[{window}s]))')
    avg_mem = q(f"max(avg_over_time(container_memory_working_set_bytes{sel}[{window}s]))")
    request = q(f'max(kube_pod_container_resource_requests{{pod=~"{live.DEPLOYMENT}.*", resource="memory"}})') or 64 * 1048576
    peak_pct = q(f"max(max_over_time((container_memory_working_set_bytes{sel} / container_spec_memory_limit_bytes{sel})[{window}s:15s]))")
    over_gib = max(0.0, (avg_mem or 0) - request) / 1073741824
    return {
        "degraded_minutes": round((ended - started).total_seconds() / 60, 2),
        "failed_requests": None if failed is None else round(failed),
        "wasted_gib_hours": round(over_gib * window / 3600, 4),
        "peak_memory_pct": None if peak_pct is None else round(peak_pct * 100, 1),
    }


def price(measures: dict[str, Any], r: dict[str, float] | None = None) -> dict[str, Any]:
    r = r or rates()
    lines = {
        "failed_requests": (measures.get("failed_requests") or 0) * r["per_failed_request"],
        "degradation": measures["degraded_minutes"] * r["per_degraded_minute"],
        "compute": measures.get("wasted_gib_hours", 0) * r["per_gib_hour"],
    }
    cost = sum(lines.values())
    saved_minutes = max(0.0, r["manual_mttr_minutes"] - measures["degraded_minutes"])
    return {
        "lines_usd": {k: round(v, 2) for k, v in lines.items()},
        "cost_usd": round(cost, 2),
        "saved_minutes": round(saved_minutes, 1),
        "saved_usd": round(saved_minutes * r["per_degraded_minute"], 2),
        "rates": r,
    }


def record(*, run_ref: str | None, alert_type: str, started_at: str | None, ended_at: datetime | None = None) -> dict[str, Any] | None:
    """Measure and price one real incident, store it, and return it. Never raises."""
    started = _parse(started_at)
    if started is None:
        return None
    ended = ended_at or datetime.now(timezone.utc)
    try:
        measures = measure(started, ended)
        priced = price(measures)
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO incident_costs (run_ref, alert_type, started_at, ended_at, measures, cost_usd, saved_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_ref, alert_type, started.isoformat(), ended.isoformat(), json.dumps({**measures, **priced}), priced["cost_usd"], priced["saved_usd"]),
            )
            conn.commit()
        finally:
            conn.close()
        return {**measures, **priced}
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not price incident %s: %s", run_ref, exc)
        return None


def summary(limit: int = 20) -> dict[str, Any]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM incident_costs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        totals = conn.execute("SELECT count(*), coalesce(sum(cost_usd), 0), coalesce(sum(saved_usd), 0) FROM incident_costs").fetchone()
    finally:
        conn.close()
    items = []
    for r in rows:
        d = dict(r)
        d["measures"] = json.loads(d["measures"])
        items.append(d)
    return {"incidents": totals[0], "total_cost_usd": round(totals[1], 2), "total_saved_usd": round(totals[2], 2), "rates": rates(), "items": items}
