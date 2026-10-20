"""Automatic postmortems."""

from __future__ import annotations

import pytest

from agents import governance, postmortem


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "kb.db"


DIAG = {"root_cause": "memory_leak", "confidence": "high", "summary": "leak_store grows forever", "evidence": [{"reference": "memory 27 -> 118 MB"}]}
PATCH = {"file_path": "sample-app/app.py", "diff": "--- a/x\n+++ b/x\n-a\n+b\n", "explanation": "bound the store", "evidence": {"key_fingerprint": "abc123"}}
VAL = {
    "passed": True, "summary": "All 6 tests passed in the sandbox.",
    "regression_test": {"path": "sample-app/test_r.py", "failed_before": True, "passed_after": True},
    "replay": {"verdict": "reproduced_and_fixed", "summary": "unpatched grew, patched flat"},
}


def test_sections_and_content(db):
    pm = postmortem.write(run_ref="r1", alert_type="memory_high", diagnosis=DIAG, patch=PATCH, validation=VAL,
                          outcome={"pr_url": "https://github.com/a/b/pull/9"}, db_path=db)
    md = pm["markdown"]
    for heading in ("## Summary", "## Impact", "## Timeline", "## Root cause", "## The change", "## Why we trust the fix", "## Prevention"):
        assert heading in md
    assert "resolved — fix validated" in md and "https://github.com/a/b/pull/9" in md
    assert "memory 27 -> 118 MB" in md and "```diff" in md
    assert "failed before the fix = True" in md and "reproduced_and_fixed" in md and "abc123" in md
    assert "growth rate" in md, "leak-specific prevention item"


def test_timeline_comes_from_the_audit_trail(db):
    governance.record("diagnose", "memory_leak", "grows", run_ref="r2", db_path=db)
    governance.record("validate", "passed", "6 passed", run_ref="r2", db_path=db)
    governance.record("diagnose", "other", "x", run_ref="other-run", db_path=db)
    md = postmortem.write(run_ref="r2", alert_type="memory_high", diagnosis=DIAG, patch=PATCH, validation=VAL, outcome={}, db_path=db)["markdown"]
    rows = [l for l in md.splitlines() if l.startswith("| ") and "Time (UTC)" not in l]
    assert [r.split(" | ")[1] for r in rows] == ["Diagnosis", "Sandbox validation"]


def test_unresolved_incident(db):
    md = postmortem.write(run_ref=None, alert_type="error_rate_high", diagnosis={"root_cause": "error_rate_slo_breach"},
                          patch={"diff": ""}, validation={"passed": False}, outcome={"reason": "needs_human"}, db_path=db)["markdown"]
    assert "needs a human" in md and "No change was made." in md and "needs human" in md


def test_duration_from_alert_start(db):
    md = postmortem.write(run_ref="r", alert_type="x", diagnosis=DIAG, patch=PATCH, validation=VAL, outcome={},
                          started_at="2020-01-01T00:00:00Z", db_path=db)["markdown"]
    assert "min from detection to verified fix" in md


def test_stored_listed_and_fetched(db):
    pm = postmortem.write(run_ref="r3", alert_type="x", diagnosis=DIAG, patch=PATCH, validation=VAL, outcome={}, db_path=db)
    assert postmortem.listing(db_path=db)[0]["title"] == pm["title"]
    assert postmortem.get("r3", db_path=db)["markdown"] == pm["markdown"]
    assert postmortem.get("missing", db_path=db) is None


def test_postmortem_is_posted_on_the_pr():
    from unittest.mock import patch

    from agents import crew, deployer

    source = {"sample-app/math_utils.py": "def multiply(a, b):\n    return a + b\n", "sample-app/test_math_utils.py": "..."}
    ctx = {"alert_type": "ci_failure", "logs": ["FAILED x"], "metrics": {}, "source_files": source}
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "MOCK_CREATE_PR", True), \
         patch.object(crew, "run_validation_checks", return_value={"exit_code": 0, "output": "3 passed"}), \
         patch("agents.knowledge_base.log_incident"), \
         patch("agents.deployer.create_pr", return_value={"pr_created": True, "pr_number": 12, "pr_url": "u"}), \
         patch.object(deployer, "comment_pr", return_value=True) as comment:
        result = crew.run_pipeline(ctx)
    comment.assert_called_once()
    assert comment.call_args.args[0] == 12 and comment.call_args.args[1].startswith("# Postmortem: ci test failure")
    assert result["postmortem"]["title"].startswith("Postmortem")
