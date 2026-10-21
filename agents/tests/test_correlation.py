"""Incident deduplication and correlation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agents import correlation, crew, knowledge_base


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "kb.db"


def mem(pod="sample-app-6594c596d4-h4289"):
    return {"alertname": "SampleAppHighMemory", "pod": pod}


ERR = {"alertname": "SampleAppHighErrorRate", "component": "sample-app"}


@pytest.mark.parametrize("labels,expected", [
    ({"pod": "sample-app-6594c596d4-h4289"}, "sample-app"),
    ({"pod": "checkout-api-7f9c8b6d5b-x2z9q"}, "checkout-api"),
    ({"pod": "standalone"}, "standalone"),
    ({"component": "sample-app"}, "sample-app"),
    ({}, "cluster"),
])
def test_workload_strips_pod_hashes(labels, expected):
    assert correlation.workload(labels) == expected


def test_first_alert_is_new(db):
    assert correlation.classify_alert(mem(), db_path=db)["action"] == "new"


def test_repeat_across_pod_restarts_is_a_duplicate(db):
    correlation.classify_alert(mem("sample-app-6594c596d4-aaaaa"), db_path=db)
    v = correlation.classify_alert(mem("sample-app-6594c596d4-bbbbb"), db_path=db)
    assert v["action"] == "duplicate" and v["incident"]["occurrences"] == 2


def test_related_alert_on_same_workload_is_correlated(db):
    first = correlation.classify_alert(mem(), db_path=db)["incident"]
    v = correlation.classify_alert(ERR, db_path=db)
    assert v["action"] == "correlated" and v["incident"]["id"] == first["id"]
    assert v["incident"]["alerts"] == ["SampleAppHighMemory", "SampleAppHighErrorRate"]


def test_related_alert_outside_window_is_new(db):
    correlation.classify_alert(mem(), db_path=db)
    later = datetime.now(timezone.utc) + timedelta(seconds=correlation.CORRELATION_WINDOW_S + 5)
    with patch.object(correlation, "_now", return_value=later):
        assert correlation.classify_alert(ERR, db_path=db)["action"] == "new"


def test_other_workload_is_new(db):
    correlation.classify_alert(mem(), db_path=db)
    assert correlation.classify_alert(mem("billing-5d8f7c6b4d-zzzzz"), db_path=db)["action"] == "new"


def test_resolving_last_alert_closes_incident(db):
    correlation.classify_alert(mem(), db_path=db)
    correlation.classify_alert(ERR, db_path=db)
    correlation.resolve_alert(ERR, db_path=db)
    assert correlation.incidents(db_path=db)[0]["state"] == "open"
    correlation.resolve_alert(mem(), db_path=db)
    inc = correlation.incidents(db_path=db)[0]
    assert inc["state"] == "resolved" and inc["resolved_at"]
    assert correlation.classify_alert(mem(), db_path=db)["action"] == "new", "after resolution a new incident starts"


def test_resolve_unknown_alert(db):
    assert correlation.resolve_alert(mem(), db_path=db) is None


def test_attach_run(db):
    inc = correlation.classify_alert(mem(), db_path=db)["incident"]
    correlation.attach_run(inc["id"], "run-1", db_path=db)
    assert correlation.incidents(db_path=db)[0]["run_ref"] == "run-1"


# ---- open PR dedupe -------------------------------------------------------------------
def _log_pr(db, root, diff, state=None):
    knowledge_base.init_db(db)
    knowledge_base.log_incident({"alert_type": "x"}, {"root_cause": root}, {"diff": diff}, {"passed": True},
                                {"pr_created": True, "pr_url": f"https://github.com/a/b/pull/{abs(hash(diff)) % 1000}"}, db_path=db)
    if state:
        import sqlite3
        c = sqlite3.connect(str(db))
        c.execute("UPDATE incidents SET pr_state = ? WHERE id = (SELECT max(id) FROM incidents)", (state,))
        c.commit()
        c.close()


def test_open_pr_found_for_same_cause_and_file(db):
    _log_pr(db, "memory_leak", "--- a/sample-app/app.py\n+++ b/sample-app/app.py\n")
    assert correlation.open_pr_for("memory_leak", "sample-app/app.py", db_path=db)


@pytest.mark.parametrize("state", ["merged", "closed", "superseded"])
def test_decided_prs_do_not_block(db, state):
    _log_pr(db, "memory_leak", "--- a/sample-app/app.py\n", state=state)
    assert correlation.open_pr_for("memory_leak", "sample-app/app.py", db_path=db) is None


def test_other_file_or_cause_does_not_block(db):
    _log_pr(db, "memory_leak", "--- a/sample-app/app.py\n")
    assert correlation.open_pr_for("memory_leak", "sample-app/other.py", db_path=db) is None
    assert correlation.open_pr_for("ci_test_failure", "sample-app/app.py", db_path=db) is None


def test_pipeline_links_existing_pr_instead_of_opening_another():
    source = {"sample-app/math_utils.py": "def multiply(a, b):\n    return a + b\n", "sample-app/test_math_utils.py": "..."}
    ctx = {"alert_type": "ci_failure", "logs": ["FAILED x"], "metrics": {}, "source_files": source}
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "MOCK_CREATE_PR", True), \
         patch.object(crew, "run_validation_checks", return_value={"exit_code": 0, "output": "1 passed"}), \
         patch("agents.knowledge_base.log_incident"), \
         patch.object(correlation, "open_pr_for", return_value="https://github.com/a/b/pull/4"), \
         patch("agents.deployer.create_pr") as create:
        result = crew.run_pipeline(ctx)
    create.assert_not_called()
    assert result["pr_result"] == {"pr_created": False, "reason": "duplicate_of_open_pr", "pr_url": "https://github.com/a/b/pull/4"}


def test_incident_whose_resolved_message_was_lost_expires(db):
    first = correlation.classify_alert(mem(), db_path=db)["incident"]
    later = datetime.now(timezone.utc) + timedelta(seconds=correlation.STALE_AFTER_S + 5)
    with patch.object(correlation, "_now", return_value=later):
        v = correlation.classify_alert(mem(), db_path=db)
    assert v["action"] == "new" and v["incident"]["id"] != first["id"]


def test_recent_repeat_is_still_a_duplicate(db):
    correlation.classify_alert(mem(), db_path=db)
    later = datetime.now(timezone.utc) + timedelta(seconds=correlation.STALE_AFTER_S - 60)
    with patch.object(correlation, "_now", return_value=later):
        assert correlation.classify_alert(mem(), db_path=db)["action"] == "duplicate"


def test_close_open_for_one_workload_or_all(db):
    correlation.classify_alert(mem(), db_path=db)
    correlation.classify_alert(mem("billing-5d8f7c6b4d-zzzzz"), db_path=db)
    assert correlation.close_open(workload_name="sample-app", db_path=db) == 1
    assert correlation.classify_alert(mem(), db_path=db)["action"] == "new"
    assert correlation.close_open(db_path=db) == 2
