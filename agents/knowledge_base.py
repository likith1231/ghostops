"""
GhostOps Knowledge Base — SQLite Incident Logger
=================================================

Logs every pipeline run (pass or fail) into a local SQLite database so
past incidents can be queried for context in future diagnoses.

Public API
----------
``init_db()``
    Creates the database and table if they don't already exist.
    Call once at application startup.

``log_incident(failure_context, diagnosis, patch, validation_result, pr_result)``
    Inserts one row per pipeline run.  Never raises — logs a warning on error.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("ghostops.knowledge_base")

# Database lives alongside the agents code, in agents/data/
_DB_DIR = Path(__file__).resolve().parent / "data"
_DB_PATH = _DB_DIR / "incidents.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    alert_type       TEXT    NOT NULL,
    root_cause       TEXT,
    confidence       TEXT,
    patch_diff       TEXT,
    validation_passed INTEGER,
    pr_created       INTEGER,
    pr_url           TEXT,
    outcome_summary  TEXT
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: str | Path | None = None) -> None:
    """Create the incidents database and table if they don't exist.

    Parameters
    ----------
    db_path:
        Override the default database path.  Useful for testing.
    """
    path = Path(db_path) if db_path else _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(str(path))
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
        conn.close()
        logger.info("Knowledge base initialised: %s", path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to initialise knowledge base: %s", exc)


def log_incident(
    failure_context: dict[str, Any],
    diagnosis: dict[str, Any],
    patch: dict[str, Any],
    validation_result: dict[str, Any],
    pr_result: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> None:
    """Insert one row into the incidents table.

    Extracts fields from each dict using ``.get()`` with safe defaults,
    so malformed or partial dicts never crash the pipeline.

    Parameters
    ----------
    failure_context:
        The original failure context dict (needs ``alert_type``).
    diagnosis:
        Reasoner output (needs ``root_cause``, ``confidence``, ``summary``).
    patch:
        Patch Generator output (needs ``diff``).
    validation_result:
        Validation Officer output (needs ``passed``, ``summary``).
    pr_result:
        Deployer output (needs ``pr_created``, ``pr_url``).
    db_path:
        Override the default database path.  Useful for testing.
    """
    path = Path(db_path) if db_path else _DB_PATH

    # --- Extract fields safely -------------------------------------------
    now = datetime.now(timezone.utc).isoformat()
    alert_type = str(failure_context.get("alert_type", "unknown"))
    root_cause = str(diagnosis.get("root_cause", "unknown"))
    confidence = str(diagnosis.get("confidence", "unknown"))
    patch_diff = str(patch.get("diff", ""))
    validation_passed = 1 if validation_result.get("passed") is True else 0
    pr_created = 1 if pr_result.get("pr_created") is True else 0
    pr_url = str(pr_result.get("pr_url", ""))

    # Build a concise outcome summary
    diag_summary = str(diagnosis.get("summary", ""))
    val_summary = str(validation_result.get("summary", ""))
    pr_reason = str(pr_result.get("reason", ""))

    outcome_parts = []
    if diag_summary:
        outcome_parts.append(f"Diagnosis: {diag_summary}")
    outcome_parts.append(f"Validation: {'PASS' if validation_passed else 'FAIL'}")
    if val_summary:
        outcome_parts.append(f"({val_summary})")
    if pr_created:
        outcome_parts.append(f"PR: {pr_url}")
    elif pr_reason:
        outcome_parts.append(f"PR skipped: {pr_reason}")
    outcome_summary = " | ".join(outcome_parts)

    # --- Insert ----------------------------------------------------------
    try:
        conn = sqlite3.connect(str(path))
        conn.execute(
            """\
            INSERT INTO incidents
                (timestamp, alert_type, root_cause, confidence,
                 patch_diff, validation_passed, pr_created, pr_url,
                 outcome_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                alert_type,
                root_cause,
                confidence,
                patch_diff,
                validation_passed,
                pr_created,
                pr_url,
                outcome_summary,
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Incident logged: alert_type=%s root_cause=%s", alert_type, root_cause)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to log incident: %s", exc)


def get_past_incidents(
    alert_type: str | None = None,
    limit: int = 5,
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Query past incidents, optionally filtered by alert_type.

    Returns a list of dicts (most recent first), useful for populating
    the ``past_incidents`` field in future ``failure_context`` dicts.
    """
    path = Path(db_path) if db_path else _DB_PATH

    if not path.exists():
        return []

    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row

        if alert_type:
            rows = conn.execute(
                "SELECT * FROM incidents WHERE alert_type = ? ORDER BY id DESC LIMIT ?",
                (alert_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM incidents ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        conn.close()
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to query past incidents: %s", exc)
        return []
