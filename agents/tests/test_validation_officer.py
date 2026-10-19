"""Tests for the Validation Officer's sandbox plumbing and verdict logic (no LLM)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents import validation_officer as vo

ORIGINAL = "def multiply(a, b):\n    return a + b\n"
GOOD_DIFF = (
    "--- a/sample-app/math_utils.py\n+++ b/sample-app/math_utils.py\n"
    "@@ -1,2 +1,2 @@\n def multiply(a, b):\n-    return a + b\n+    return a * b\n"
)


# ---- _write_tree / _apply_patch -----------------------------------------
def test_write_tree_creates_nested_dirs(tmp_path):
    vo._write_tree(tmp_path, {"a/b/c.py": "x = 1\n"})
    assert (tmp_path / "a/b/c.py").read_text() == "x = 1\n"


def test_apply_patch_repo_relative(tmp_path):
    vo._write_tree(tmp_path, {"sample-app/math_utils.py": ORIGINAL})
    assert vo._apply_patch(tmp_path, "sample-app/math_utils.py", GOOD_DIFF) is None
    assert "a * b" in (tmp_path / "sample-app/math_utils.py").read_text()


def test_apply_patch_basename_headers(tmp_path):
    diff = GOOD_DIFF.replace("sample-app/", "")
    vo._write_tree(tmp_path, {"sample-app/math_utils.py": ORIGINAL})
    assert vo._apply_patch(tmp_path, "sample-app/math_utils.py", diff) is None
    assert "a * b" in (tmp_path / "sample-app/math_utils.py").read_text()


def test_apply_patch_mismatch_returns_error_and_leaves_file(tmp_path):
    vo._write_tree(tmp_path, {"sample-app/math_utils.py": "something else\n"})
    assert vo._apply_patch(tmp_path, "sample-app/math_utils.py", GOOD_DIFF) is not None
    assert (tmp_path / "sample-app/math_utils.py").read_text() == "something else\n"


def test_apply_patch_garbage(tmp_path):
    vo._write_tree(tmp_path, {"sample-app/math_utils.py": ORIGINAL})
    assert vo._apply_patch(tmp_path, "sample-app/math_utils.py", "not a diff at all") is not None


# ---- _run_tests_in_sandbox -------------------------------------------------
def test_sandbox_reports_unappliable_patch_without_docker():
    with patch("agents.validation_officer.docker.from_env") as from_env:
        result = vo._run_tests_in_sandbox("sample-app/math_utils.py", "other\n", GOOD_DIFF)
    assert result["exit_code"] == 1
    assert result["output"].startswith("Patch could not be applied")
    from_env.assert_not_called()


def test_sandbox_docker_unavailable_fails_closed():
    with patch("agents.validation_officer.docker.from_env", side_effect=RuntimeError("no daemon")):
        result = vo._run_tests_in_sandbox("sample-app/math_utils.py", ORIGINAL, GOOD_DIFF)
    assert result["exit_code"] == -1 and "Docker error" in result["output"]


def test_sandbox_container_is_isolated_and_removed():
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"2 passed"
    client = MagicMock()
    client.containers.create.return_value = container
    with patch("agents.validation_officer.docker.from_env", return_value=client):
        result = vo._run_tests_in_sandbox("sample-app/math_utils.py", ORIGINAL, GOOD_DIFF)
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["network_mode"] == "none"
    assert kwargs["mem_limit"] == "256m"
    assert "volumes" not in kwargs, "the host filesystem must never be mounted"
    container.remove.assert_called_once_with(force=True)
    assert result == {"exit_code": 0, "output": "2 passed"}


def test_sandbox_container_removed_even_on_timeout():
    container = MagicMock()
    container.wait.side_effect = TimeoutError("too slow")
    client = MagicMock()
    client.containers.create.return_value = container
    with patch("agents.validation_officer.docker.from_env", return_value=client):
        result = vo._run_tests_in_sandbox("sample-app/math_utils.py", ORIGINAL, GOOD_DIFF)
    container.remove.assert_called_once_with(force=True)
    assert result["exit_code"] == -1


def test_sandbox_output_truncated():
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 1}
    container.logs.return_value = b"x" * 10_000
    client = MagicMock()
    client.containers.create.return_value = container
    with patch("agents.validation_officer.docker.from_env", return_value=client):
        result = vo._run_tests_in_sandbox("sample-app/math_utils.py", ORIGINAL, GOOD_DIFF)
    assert len(result["output"]) == 4000


# ---- run_validation_checks -------------------------------------------------
def test_empty_diff_never_passes():
    with patch.object(vo, "_run_opa_checks") as opa, patch.object(vo, "_run_tests_in_sandbox") as sandbox:
        result = vo.run_validation_checks("sample-app/app.py", "x", "   \n")
    assert result["exit_code"] == 1
    opa.assert_not_called()
    sandbox.assert_not_called()


def test_policy_violation_skips_sandbox():
    violation = {"exit_code": 1, "output": "OPA Policy Violation (File Path): nope"}
    with patch.object(vo, "_run_opa_checks", return_value=violation), patch.object(vo, "_run_tests_in_sandbox") as sandbox:
        assert vo.run_validation_checks("backend/main.py", "x", GOOD_DIFF) == violation
    sandbox.assert_not_called()


def test_policy_pass_runs_sandbox_with_extra_files():
    with patch.object(vo, "_run_opa_checks", return_value=None), \
         patch.object(vo, "_run_tests_in_sandbox", return_value={"exit_code": 0, "output": "ok"}) as sandbox:
        result = vo.run_validation_checks("sample-app/math_utils.py", ORIGINAL, GOOD_DIFF, {"t.py": "x"})
    sandbox.assert_called_once_with("sample-app/math_utils.py", ORIGINAL, GOOD_DIFF, {"t.py": "x"})
    assert result["exit_code"] == 0


# ---- interpret_sandbox_result ----------------------------------------------
def test_verdict_pass_counts_tests():
    v = vo.interpret_sandbox_result({"exit_code": 0, "output": "===== 5 passed in 0.1s ====="})
    assert v["passed"] is True and v["summary"] == "All 5 tests passed in the sandbox."
    assert v["failing_tests"] == []


def test_verdict_failing_tests_listed_once():
    out = (
        "test_math_utils.py::test_multiply FAILED\n"
        "FAILED test_math_utils.py::test_multiply - assert 6 == 8\n"
        "FAILED test_math_utils.py::test_add\n"
    )
    v = vo.interpret_sandbox_result({"exit_code": 1, "output": out})
    assert v["passed"] is False
    assert v["failing_tests"] == ["test_math_utils.py::test_multiply", "test_math_utils.py::test_add"]


def test_verdict_policy_violation():
    v = vo.interpret_sandbox_result({"exit_code": 1, "output": "OPA Policy Violation (File Path):\n..."})
    assert v["passed"] is False and "OPA" in v["summary"]


def test_verdict_patch_not_applied():
    v = vo.interpret_sandbox_result({"exit_code": 1, "output": "Patch could not be applied:\nhunk failed"})
    assert "did not apply" in v["summary"]


def test_verdict_docker_error():
    v = vo.interpret_sandbox_result({"exit_code": -1, "output": "Docker error: boom"})
    assert v["passed"] is False and "-1" in v["summary"]


@pytest.mark.parametrize("result", [{}, {"exit_code": None}, {"output": "5 passed"}])
def test_verdict_missing_or_bad_exit_code_fails_closed(result):
    assert vo.interpret_sandbox_result(result)["passed"] is False


def test_verdict_output_truncated():
    v = vo.interpret_sandbox_result({"exit_code": 0, "output": "y" * 9000})
    assert len(v["test_output"]) == 4000
