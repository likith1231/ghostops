"""The feedback loop: PR outcomes from GitHub into the knowledge base, and back into diagnoses."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from agents import feedback, knowledge_base


def _gh(state="open", merged=False, status=200, labels=()):
    r = MagicMock(status_code=status)
    r.json.return_value = {"state": state, "merged_at": "2026-10-01T00:00:00Z" if merged else None, "labels": [{"name": n} for n in labels]}
    return r


def _log(db, pr_url="", passed=True, root="memory_leak"):
    knowledge_base.log_incident(
        {"alert_type": "memory_high"}, {"root_cause": root, "confidence": "high", "summary": "s"}, {"diff": "d"},
        {"passed": passed}, {"pr_created": bool(pr_url), "pr_url": pr_url}, db_path=db,
    )


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "kb.db"
    knowledge_base.init_db(path)
    return path


# ---- pr_state -------------------------------------------------------------------------------
@pytest.mark.parametrize("resp,expected", [(_gh("open"), "open"), (_gh("closed", merged=True), "merged"), (_gh("closed"), "closed")])
def test_pr_state(resp, expected):
    with patch.object(feedback.requests, "get", return_value=resp) as get:
        assert feedback.pr_state("https://github.com/likith1231/ghostops/pull/3") == expected
    assert get.call_args.args[0] == "https://api.github.com/repos/likith1231/ghostops/pulls/3"


def test_pr_state_sends_token_when_set():
    with patch.object(feedback.requests, "get", return_value=_gh()) as get:
        feedback.pr_state("https://github.com/a/b/pull/1", token="tok")
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.parametrize("url", ["", "https://gitlab.com/a/b/pull/1", "https://github.com/a/b/issues/1", "not a url"])
def test_pr_state_ignores_non_pr_urls(url):
    with patch.object(feedback.requests, "get") as get:
        assert feedback.pr_state(url) is None
    get.assert_not_called()


def test_pr_state_github_error_or_down():
    with patch.object(feedback.requests, "get", return_value=_gh(status=404)):
        assert feedback.pr_state("https://github.com/a/b/pull/1") is None
    with patch.object(feedback.requests, "get", side_effect=feedback.requests.ConnectionError()):
        assert feedback.pr_state("https://github.com/a/b/pull/1") is None


# ---- sync + acceptance ------------------------------------------------------------------------
def test_sync_records_states_and_skips_decided(db):
    _log(db, "https://github.com/a/b/pull/1")
    _log(db, "https://github.com/a/b/pull/2")
    _log(db, "")  # no PR: never checked
    states = {"1": _gh(merged=True, state="closed"), "2": _gh("open")}
    with patch.object(feedback.requests, "get", side_effect=lambda url, **kw: states[url.rsplit("/", 1)[1]]):
        result = feedback.sync_pr_outcomes(db_path=db)
    assert {k: result[k] for k in ("checked", "updated", "failed")} == {"checked": 2, "updated": 2, "failed": 0}
    assert [m["pr_url"] for m in result["newly_merged"]] == ["https://github.com/a/b/pull/1"]
    # #1 is merged (final): only #2 is asked again.
    with patch.object(feedback.requests, "get", return_value=_gh("closed")) as get:
        assert feedback.sync_pr_outcomes(db_path=db)["checked"] == 1
    assert get.call_count == 1


def test_sync_failure_is_counted_not_fatal(db):
    _log(db, "https://github.com/a/b/pull/1")
    with patch.object(feedback.requests, "get", side_effect=feedback.requests.Timeout()):
        result = feedback.sync_pr_outcomes(db_path=db)
    assert {k: result[k] for k in ("checked", "updated", "failed")} == {"checked": 1, "updated": 0, "failed": 1}


def test_sync_missing_db(tmp_path):
    assert feedback.sync_pr_outcomes(db_path=tmp_path / "none.db")["checked"] == 0


def test_acceptance_rate_counts_only_decided_prs(db):
    for n in (1, 2, 3, 4):
        _log(db, f"https://github.com/a/b/pull/{n}")
    _log(db, "")
    states = {"1": _gh(merged=True, state="closed"), "2": _gh(merged=True, state="closed"), "3": _gh("closed"), "4": _gh("open")}
    with patch.object(feedback.requests, "get", side_effect=lambda url, **kw: states[url.rsplit("/", 1)[1]]):
        feedback.sync_pr_outcomes(db_path=db)
    a = feedback.acceptance(db_path=db)
    assert (a["prs"], a["merged"], a["closed"], a["open"]) == (4, 2, 1, 1)
    assert a["acceptance_rate"] == 66.7 and a["last_checked"]


def test_acceptance_without_decisions(db):
    _log(db, "https://github.com/a/b/pull/1")
    a = feedback.acceptance(db_path=db)
    assert a["acceptance_rate"] is None and a["unknown"] == 1


# ---- schema migration -----------------------------------------------------------------------
def test_init_db_adds_feedback_columns_to_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, alert_type TEXT NOT NULL, "
        "root_cause TEXT, confidence TEXT, patch_diff TEXT, validation_passed INTEGER, pr_created INTEGER, pr_url TEXT, outcome_summary TEXT)"
    )
    conn.execute("INSERT INTO incidents (timestamp, alert_type) VALUES ('t', 'memory_high')")
    conn.commit()
    conn.close()
    knowledge_base.init_db(path)
    knowledge_base.init_db(path)  # idempotent
    cols = {r[1] for r in sqlite3.connect(str(path)).execute("PRAGMA table_info(incidents)")}
    assert {"pr_state", "pr_checked_at"} <= cols
    assert len(knowledge_base.get_past_incidents(db_path=path)) == 1, "existing rows survive"


# ---- learning context -------------------------------------------------------------------------
def test_past_incident_context_carries_human_review(db):
    _log(db, "https://github.com/a/b/pull/1")
    _log(db, "", passed=False)
    with patch.object(feedback.requests, "get", return_value=_gh(merged=True, state="closed")):
        feedback.sync_pr_outcomes(db_path=db)
    ctx = knowledge_base.past_incident_context("memory_high", db_path=db)
    assert [c["human_review"] for c in ctx] == ["no PR", "merged"]  # newest first
    assert ctx[1]["validated"] is True and ctx[1]["root_cause"] == "memory_leak"


@pytest.mark.parametrize("label", ["duplicate", "Duplicate", "superseded"])
def test_closed_duplicate_is_superseded(label):
    with patch.object(feedback.requests, "get", return_value=_gh("closed", labels=[label])):
        assert feedback.pr_state("https://github.com/a/b/pull/1") == "superseded"


def test_superseded_excluded_from_acceptance_rate(db):
    for n in (1, 2, 3):
        _log(db, f"https://github.com/a/b/pull/{n}")
    states = {"1": _gh(merged=True, state="closed"), "2": _gh("closed", labels=["duplicate"]), "3": _gh("closed", labels=["duplicate"])}
    with patch.object(feedback.requests, "get", side_effect=lambda url, **kw: states[url.rsplit("/", 1)[1]]):
        feedback.sync_pr_outcomes(db_path=db)
    a = feedback.acceptance(db_path=db)
    assert (a["merged"], a["superseded"], a["closed"]) == (1, 2, 0) and a["acceptance_rate"] == 100.0


def test_closed_pr_rechecked_in_case_it_gets_labelled_later(db):
    _log(db, "https://github.com/a/b/pull/1")
    with patch.object(feedback.requests, "get", return_value=_gh("closed")):
        feedback.sync_pr_outcomes(db_path=db)
    with patch.object(feedback.requests, "get", return_value=_gh("closed", labels=["duplicate"])):
        assert feedback.sync_pr_outcomes(db_path=db)["updated"] == 1
    assert feedback.acceptance(db_path=db)["superseded"] == 1
