"""
GhostOps Governance — kill switch and tamper-evident audit trail
================================================================

* **Kill switch.** ``is_paused()`` is checked before every automatic action
  (opening a PR, rolling back, auto-merging, promoting a canary). Pausing and
  resuming are themselves audited.
* **Audit trail.** Every agent decision is appended to ``audit_log`` with what
  was done, why, the evidence, and who (``ghostops`` or a human). Each entry
  stores the SHA-256 of the previous entry, so editing or deleting any past
  entry breaks the chain and ``verify_chain()`` reports where.

Both live in the knowledge-base SQLite file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import knowledge_base

logger = logging.getLogger("ghostops.governance")

GENESIS = "0" * 64

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    decision  TEXT NOT NULL,
    reason    TEXT NOT NULL,
    run_ref   TEXT,
    evidence  TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path else knowledge_base._DB_PATH


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = _path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def _digest(prev_hash: str, entry: dict[str, Any]) -> str:
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
def record(
    action: str,
    decision: str,
    reason: str,
    *,
    actor: str = "ghostops",
    run_ref: str | None = None,
    evidence: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Append one decision to the audit trail. Never raises (auditing must not break remediation)."""
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "actor": actor,
                "action": action,
                "decision": decision,
                "reason": reason,
                "run_ref": run_ref,
                "evidence": json.dumps(evidence or {}, sort_keys=True, default=str),
            }
            digest = _digest(prev, entry)
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, decision, reason, run_ref, evidence, prev_hash, hash)"
                " VALUES (:ts, :actor, :action, :decision, :reason, :run_ref, :evidence, :prev, :hash)",
                {**entry, "prev": prev, "hash": digest},
            )
            conn.commit()
            return {**entry, "prev_hash": prev, "hash": digest}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("Audit write failed for %s/%s: %s", action, decision, exc)
        return None


def entries(limit: int = 50, *, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Most recent audit entries first."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        item = dict(r)
        item["evidence"] = json.loads(item["evidence"] or "{}")
        out.append(item)
    return out


def verify_chain(*, db_path: str | Path | None = None) -> dict[str, Any]:
    """Recompute every hash. Returns ``{"valid": bool, "entries": n, "broken_at": id | None}``."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, ts, actor, action, decision, reason, run_ref, evidence, prev_hash, hash FROM audit_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    prev = GENESIS
    for rid, ts, actor, action, decision, reason, run_ref, evidence, prev_hash, digest in rows:
        entry = {"ts": ts, "actor": actor, "action": action, "decision": decision, "reason": reason, "run_ref": run_ref, "evidence": evidence}
        if prev_hash != prev or _digest(prev, entry) != digest:
            return {"valid": False, "entries": len(rows), "broken_at": rid}
        prev = digest
    return {"valid": True, "entries": len(rows), "broken_at": None}


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------
def _get(key: str, db_path: str | Path | None) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set(key: str, value: str, db_path: str | Path | None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()
    finally:
        conn.close()


def status(*, db_path: str | Path | None = None) -> dict[str, Any]:
    raw = _get("autonomy", db_path)
    data = json.loads(raw) if raw else {}
    return {"paused": bool(data.get("paused")), "by": data.get("by"), "at": data.get("at"), "reason": data.get("reason")}


def is_paused(*, db_path: str | Path | None = None) -> bool:
    """Fails safe: if the setting can't be read, automatic actions are treated as paused."""
    try:
        return status(db_path=db_path)["paused"]
    except Exception as exc:  # noqa: BLE001
        logger.error("Kill-switch state unreadable, treating autonomy as paused: %s", exc)
        return True


def set_paused(paused: bool, *, by: str = "human", reason: str = "", db_path: str | Path | None = None) -> dict[str, Any]:
    data = {"paused": paused, "by": by, "at": datetime.now(timezone.utc).isoformat(), "reason": reason}
    _set("autonomy", json.dumps(data), db_path)
    record("autonomy", "paused" if paused else "resumed", reason or ("kill switch engaged" if paused else "autonomy restored"), actor=by, db_path=db_path)
    return status(db_path=db_path)


def allow(action: str, *, run_ref: str | None = None, evidence: dict[str, Any] | None = None, db_path: str | Path | None = None) -> bool:
    """Gate for an automatic action: False (and audited as blocked) while the kill switch is on."""
    if is_paused(db_path=db_path):
        record(action, "blocked", "kill switch is on: automatic actions are paused", run_ref=run_ref, evidence=evidence, db_path=db_path)
        return False
    return True
