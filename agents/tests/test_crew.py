"""Tests for the pipeline orchestration in agents/crew.py (no LLM, no Docker)."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from agents import crew

SOURCE = {"sample-app/math_utils.py": "def multiply(a, b):\n    return a + b\n", "sample-app/test_math_utils.py": "..."}
CONTEXT = {"alert_type": "ci_failure", "logs": ["FAILED test_math_utils.py::test_multiply"], "metrics": {}, "source_files": SOURCE}
PASS = {"exit_code": 0, "output": "2 passed"}
FAIL = {"exit_code": 1, "output": "FAILED test_math_utils.py::test_multiply"}


def _run(sandbox=PASS, mock=True, create_pr=False, context=CONTEXT, **kwargs):
    """Run the pipeline with the sandbox, knowledge base and deployer stubbed."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(crew, "MOCK_LLM", mock))
        stack.enter_context(patch.object(crew, "MOCK_CREATE_PR", create_pr))
        checks = stack.enter_context(patch.object(crew, "run_validation_checks", return_value=sandbox))
        log = stack.enter_context(patch("agents.knowledge_base.log_incident"))
        pr = stack.enter_context(patch("agents.deployer.create_pr", return_value={"pr_created": True, "pr_url": "u"}))
        result = crew.run_pipeline(context, **kwargs)
    return result, checks, log, pr


# ---- _safe_json_loads -------------------------------------------------------
def test_safe_json_plain():
    assert crew._safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_strips_fences():
    assert crew._safe_json_loads('```json\n{"a": 1}\n```') == {"a": 1}


def test_safe_json_invalid_returns_raw():
    assert crew._safe_json_loads("not json") == {"raw": "not json"}


# ---- mock-mode pipeline -------------------------------------------------------
def test_mock_pipeline_result_shape():
    result, *_ = _run()
    assert set(result) == {"diagnosis", "patch", "validation_result", "pr_result", "shield"}
    assert result["diagnosis"]["root_cause"] == "ci_test_failure"
    assert result["validation_result"]["passed"] is True


def test_mock_mode_never_opens_real_pr_by_default():
    result, _, _, pr = _run()
    pr.assert_not_called()
    assert result["pr_result"] == {"pr_created": False, "reason": "mock_mode"}


def test_mock_mode_opens_pr_when_allowed():
    result, _, _, pr = _run(create_pr=True)
    pr.assert_called_once()
    assert result["pr_result"]["pr_created"] is True


def test_failed_validation_skips_pr():
    result, _, _, pr = _run(sandbox=FAIL, create_pr=True)
    pr.assert_not_called()
    assert result["pr_result"]["reason"] == "validation_failed"
    assert result["validation_result"]["failing_tests"] == ["test_math_utils.py::test_multiply"]


def test_every_run_is_logged_pass_or_fail():
    _, _, log_pass, _ = _run()
    _, _, log_fail, _ = _run(sandbox=FAIL)
    log_pass.assert_called_once()
    log_fail.assert_called_once()


def test_knowledge_base_failure_does_not_break_pipeline():
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "run_validation_checks", return_value=PASS), \
         patch("agents.knowledge_base.log_incident", side_effect=RuntimeError("disk full")):
        assert crew.run_pipeline(CONTEXT)["validation_result"]["passed"] is True


def test_deployer_exception_is_reported_not_raised():
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "MOCK_CREATE_PR", True), \
         patch.object(crew, "run_validation_checks", return_value=PASS), patch("agents.knowledge_base.log_incident"), \
         patch("agents.deployer.create_pr", side_effect=RuntimeError("GitHub down")):
        result = crew.run_pipeline(CONTEXT)
    assert result["pr_result"] == {"pr_created": False, "reason": "GitHub down"}


def test_validation_targets_patched_file_with_tests_alongside():
    _, checks, _, _ = _run()
    file_path, original, diff, extra = checks.call_args.args
    assert file_path == "sample-app/math_utils.py"
    assert original == SOURCE["sample-app/math_utils.py"]
    assert "a * b" in diff
    assert extra == {"sample-app/test_math_utils.py": "..."}


def test_patch_file_path_outside_source_files_is_ignored():
    with patch.object(crew.mock_llm, "generate_patch", return_value={"diff": "x", "file_path": "backend/main.py"}):
        _, checks, _, _ = _run()
    assert checks.call_args.args[0] == "sample-app/math_utils.py"


def test_stage_callbacks_in_order():
    events = []
    _run(on_stage=lambda stage, status, data: events.append((stage, status)))
    assert events == [
        ("diagnosis", "running"), ("diagnosis", "done"),
        ("patch", "running"), ("patch", "done"),
        ("validation", "running"), ("validation", "done"),
        ("deploy", "running"), ("deploy", "done"),
    ]


def test_stage_callback_receives_stage_output():
    seen = {}
    _run(on_stage=lambda stage, status, data: seen.__setitem__(stage, data) if status == "done" else None)
    assert seen["diagnosis"]["root_cause"] == "ci_test_failure"
    assert seen["deploy"]["reason"] == "mock_mode"


# ---- LLM-mode pipeline, with CrewAI stubbed ------------------------------------
def test_llm_mode_parses_each_agent_output():
    outputs = iter([
        json.dumps({"root_cause": "ci_test_failure", "confidence": "high"}),
        "```json\n" + json.dumps({"diff": "--- a/x\n+++ b/x\n", "explanation": "fix"}) + "\n```",
        json.dumps({"passed": False, "summary": "still failing"}),
    ])
    fake_crew = MagicMock()
    fake_crew.return_value.kickoff.side_effect = lambda: next(outputs)
    with patch.object(crew, "Crew", fake_crew), \
         patch.object(crew, "build_reasoner_agent"), patch.object(crew, "build_reasoner_task"), \
         patch.object(crew, "build_patch_agent"), patch.object(crew, "build_patch_task"), \
         patch.object(crew, "build_validation_agent"), patch.object(crew, "build_validation_task") as vtask, \
         patch("agents.knowledge_base.log_incident"):
        with patch.object(crew, "MOCK_LLM", False):
            result = crew.run_pipeline(CONTEXT)
    assert result["diagnosis"]["root_cause"] == "ci_test_failure"
    assert result["patch"]["explanation"] == "fix"
    assert result["pr_result"]["reason"] == "validation_failed"
    assert vtask.call_args.args[1] == "sample-app/math_utils.py"
