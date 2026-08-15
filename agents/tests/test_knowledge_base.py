"""
Tests for the GhostOps Knowledge Base (SQLite incident logger).

Validates that:
1. init_db() creates the database file and table.
2. log_incident() inserts a row with correct field extraction.
3. get_past_incidents() retrieves logged incidents.
4. Partial/malformed dicts don't crash log_incident().
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agents.knowledge_base import get_past_incidents, init_db, log_incident


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Return a path for a temporary SQLite database."""
    db_path = tmp_path / "test_incidents.db"
    init_db(db_path=db_path)
    return db_path


@pytest.fixture()
def sample_data() -> dict:
    """Return sample pipeline data matching the real shapes from crew.py."""
    return {
        "failure_context": {
            "alert_type": "memory_high",
            "logs": [
                "2026-08-09T08:00:12Z app WARNING: handler allocated 2.3 GB",
                "2026-08-09T08:01:45Z kernel: OOM killed process 4321",
            ],
            "metrics": {"container_memory_usage_bytes": [{"ts": "08:00", "value": 1_900_000_000}]},
            "source_files": {"app.py": "from flask import Flask\napp = Flask(__name__)\n"},
            "past_incidents": [],
        },
        "diagnosis": {
            "root_cause": "memory_leak",
            "confidence": "high",
            "summary": "Unbounded cache in /api/data endpoint",
            "evidence": [{"type": "log", "reference": "cache size: 148201", "reasoning": "grows without bound"}],
        },
        "patch": {
            "diff": "--- a/app.py\n+++ b/app.py\n@@ -5 +5,2 @@\n-cache = []\n+from collections import deque\n+cache = deque(maxlen=1000)\n",
            "explanation": "Added max-size limit to the cache.",
        },
        "validation_result": {
            "passed": True,
            "summary": "All 3 tests passed.",
            "failing_tests": [],
            "test_output": "...",
        },
        "pr_result": {
            "pr_created": True,
            "pr_url": "https://github.com/likith1231/ghostops/pull/1",
            "pr_number": 1,
            "branch": "fix/memory-leak-20260809-120000",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInitDb:
    """init_db() should create the database and table."""

    def test_creates_database_file(self, tmp_path: Path):
        db_path = tmp_path / "new.db"
        assert not db_path.exists()
        init_db(db_path=db_path)
        assert db_path.exists()

    def test_creates_incidents_table(self, tmp_db: Path):
        conn = sqlite3.connect(str(tmp_db))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_idempotent(self, tmp_db: Path):
        """Calling init_db() twice should not error or duplicate the table."""
        init_db(db_path=tmp_db)  # second call
        conn = sqlite3.connect(str(tmp_db))
        tables = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='incidents'"
        ).fetchone()
        assert tables[0] == 1
        conn.close()


class TestLogIncident:
    """log_incident() should insert a correct row."""

    def test_inserts_row(self, tmp_db: Path, sample_data: dict):
        log_incident(**sample_data, db_path=tmp_db)

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM incidents WHERE id = 1").fetchone()
        conn.close()

        assert row is not None
        assert row["alert_type"] == "memory_high"
        assert row["root_cause"] == "memory_leak"
        assert row["confidence"] == "high"
        assert "cache" in row["patch_diff"].lower() or "deque" in row["patch_diff"].lower()
        assert row["validation_passed"] == 1
        assert row["pr_created"] == 1
        assert "github.com" in row["pr_url"]
        assert row["outcome_summary"]  # not empty

    def test_logs_failed_validation(self, tmp_db: Path, sample_data: dict):
        """A failed validation should still be logged with validation_passed=0."""
        sample_data["validation_result"] = {
            "passed": False,
            "summary": "1 test failed.",
            "failing_tests": ["test_memory"],
        }
        sample_data["pr_result"] = {"pr_created": False, "reason": "validation_failed"}

        log_incident(**sample_data, db_path=tmp_db)

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM incidents WHERE id = 1").fetchone()
        conn.close()

        assert row["validation_passed"] == 0
        assert row["pr_created"] == 0

    def test_handles_empty_dicts(self, tmp_db: Path):
        """Passing empty dicts should not crash — just use defaults."""
        log_incident(
            failure_context={},
            diagnosis={},
            patch={},
            validation_result={},
            pr_result={},
            db_path=tmp_db,
        )

        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT count(*) FROM incidents").fetchone()
        conn.close()
        assert row[0] == 1  # row was inserted


class TestGetPastIncidents:
    """get_past_incidents() should retrieve logged rows."""

    def test_returns_logged_incidents(self, tmp_db: Path, sample_data: dict):
        log_incident(**sample_data, db_path=tmp_db)

        incidents = get_past_incidents(db_path=tmp_db)
        assert len(incidents) == 1
        assert incidents[0]["alert_type"] == "memory_high"

    def test_filters_by_alert_type(self, tmp_db: Path, sample_data: dict):
        # Log two incidents with different alert types
        log_incident(**sample_data, db_path=tmp_db)

        sample_data["failure_context"]["alert_type"] = "ci_failure"
        log_incident(**sample_data, db_path=tmp_db)

        memory_incidents = get_past_incidents(alert_type="memory_high", db_path=tmp_db)
        assert len(memory_incidents) == 1
        assert memory_incidents[0]["alert_type"] == "memory_high"

    def test_returns_empty_list_for_missing_db(self, tmp_path: Path):
        incidents = get_past_incidents(db_path=tmp_path / "nonexistent.db")
        assert incidents == []
