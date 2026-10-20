"""Kill switch and tamper-evident audit trail."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from agents import crew, governance


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gov.db"


# ---- audit trail -----------------------------------------------------------------------
def test_entries_newest_first_with_evidence(db):
    governance.record("diagnose", "memory_leak", "grows", run_ref="r1", evidence={"confidence": "high"}, db_path=db)
    governance.record("open_pr", "opened", "url", db_path=db)
    out = governance.entries(db_path=db)
    assert [e["action"] for e in out] == ["open_pr", "diagnose"]
    assert out[1]["evidence"] == {"confidence": "high"} and out[1]["run_ref"] == "r1"


def test_chain_links_each_entry_to_the_previous(db):
    a = governance.record("a", "x", "r", db_path=db)
    b = governance.record("b", "y", "r", db_path=db)
    assert a["prev_hash"] == governance.GENESIS and b["prev_hash"] == a["hash"]
    assert governance.verify_chain(db_path=db) == {"valid": True, "entries": 2, "broken_at": None}


def test_editing_an_entry_breaks_the_chain(db):
    for i in range(3):
        governance.record("validate", "failed", f"run {i}", db_path=db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_log SET decision = 'passed' WHERE id = 2")
    conn.commit()
    conn.close()
    assert governance.verify_chain(db_path=db) == {"valid": False, "entries": 3, "broken_at": 2}


def test_deleting_an_entry_breaks_the_chain(db):
    for i in range(3):
        governance.record("rollback", "done", str(i), db_path=db)
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM audit_log WHERE id = 2")
    conn.commit()
    conn.close()
    assert governance.verify_chain(db_path=db)["broken_at"] == 3


def test_record_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert governance.record("a", "b", "c", db_path=blocker / "sub" / "db.sqlite") is None


# ---- kill switch -------------------------------------------------------------------------
def test_running_by_default(db):
    assert governance.is_paused(db_path=db) is False and governance.allow("open_pr", db_path=db) is True


def test_pause_blocks_and_audits(db):
    governance.set_paused(True, by="alice", reason="investigating", db_path=db)
    assert governance.status(db_path=db)["paused"] is True
    assert governance.allow("rollback", run_ref="r9", db_path=db) is False
    actions = [(e["action"], e["decision"], e["actor"]) for e in governance.entries(db_path=db)]
    assert actions[:2] == [("rollback", "blocked", "ghostops"), ("autonomy", "paused", "alice")]


def test_resume_restores(db):
    governance.set_paused(True, db_path=db)
    governance.set_paused(False, db_path=db)
    assert governance.allow("open_pr", db_path=db) is True


def test_unreadable_state_fails_safe():
    with patch.object(governance, "status", side_effect=RuntimeError("db locked")):
        assert governance.is_paused() is True


# ---- in the pipeline ---------------------------------------------------------------------
SOURCE = {"sample-app/math_utils.py": "def multiply(a, b):\n    return a + b\n", "sample-app/test_math_utils.py": "..."}
CONTEXT = {"alert_type": "ci_failure", "logs": ["FAILED x"], "metrics": {}, "source_files": SOURCE, "run_ref": "run-7"}


def _pipeline(paused: bool, db):
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "MOCK_CREATE_PR", True), \
         patch.object(crew, "run_validation_checks", return_value={"exit_code": 0, "output": "2 passed"}), \
         patch("agents.knowledge_base.log_incident"), \
         patch("agents.deployer.create_pr", return_value={"pr_created": True, "pr_url": "u"}) as pr, \
         patch.object(governance, "is_paused", return_value=paused):
        with patch.object(governance.knowledge_base, "_DB_PATH", db):
            result = crew.run_pipeline(CONTEXT)
    return result, pr


def test_kill_switch_stops_pr_creation(db):
    result, pr = _pipeline(True, db)
    pr.assert_not_called()
    assert result["pr_result"] == {"pr_created": False, "reason": "autonomy_paused"}


def test_pipeline_decisions_are_audited_with_run_ref(db):
    _pipeline(False, db)
    trail = governance.entries(db_path=db)
    assert [e["action"] for e in reversed(trail) if e["action"] not in ("regression_test", "replay", "postmortem")] == ["diagnose", "patch", "validate", "risk", "evidence", "open_pr"]
    assert {e["run_ref"] for e in trail} == {"run-7"}
    assert governance.verify_chain(db_path=db)["valid"] is True
