"""Tests for the offline (mock LLM) diagnosis and patch templates.

The template tests apply each generated diff to the real sample-app files and
run their tests locally, so a broken template fails here, not in the demo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents import mock_llm
from agents.scenarios import ci_context, exception_context, leak_context
from agents.validation_officer import _apply_patch, _write_tree

SAMPLE_TESTS = {
    "sample-app/app.py": "test_app.py",
    "sample-app/math_utils.py": "test_math_utils.py",
    "sample-app/user_service.py": "test_user_service.py",
}


def _run_sample_tests(root: Path, test_file: str) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test_file],
        cwd=root / "sample-app", capture_output=True, text=True,
        # A one-character fix keeps the file size, so a cached .pyc would hide it.
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    ).returncode


# ---- diagnose -------------------------------------------------------------
def test_diagnose_memory_leak():
    d = mock_llm.diagnose(leak_context())
    assert d["root_cause"] == "memory_leak"
    assert d["confidence"] == "high"
    assert any(e["type"] == "metric" for e in d["evidence"])


def test_diagnose_memory_alert_without_growth_is_medium():
    ctx = {"alert_type": "memory_high", "logs": [], "metrics": {"container_memory_working_set_bytes": [{"value": 5}, {"value": 5}, {"value": 5}]}}
    d = mock_llm.diagnose(ctx)
    assert d["root_cause"] == "memory_leak" and d["confidence"] == "medium"


def test_diagnose_ci_failure():
    d = mock_llm.diagnose(ci_context())
    assert d["root_cause"] == "ci_test_failure"
    assert d["evidence"], "failing test lines should be cited"


def test_diagnose_null_reference():
    d = mock_llm.diagnose(exception_context())
    assert d["root_cause"] == "null_reference"
    assert "user_service.py" in d["summary"]


def test_diagnose_unknown():
    d = mock_llm.diagnose({"alert_type": "something", "logs": ["all fine"], "metrics": {}})
    assert d["root_cause"] == "unknown" and d["confidence"] == "low"


def test_diagnose_tolerates_missing_keys():
    assert mock_llm.diagnose({})["root_cause"] == "unknown"


def test_diagnosis_is_marked_mock():
    assert mock_llm.diagnose({})["mock"] is True


# ---- generate_patch -------------------------------------------------------
@pytest.mark.parametrize("build", [leak_context, ci_context, exception_context])
def test_template_fix_makes_failing_tests_pass(tmp_path, build):
    ctx = build()
    patch = mock_llm.generate_patch(mock_llm.diagnose(ctx), ctx["source_files"])
    path = patch["file_path"]
    test_file = SAMPLE_TESTS[path]

    _write_tree(tmp_path, ctx["source_files"])
    assert _run_sample_tests(tmp_path, test_file) != 0, "the sample bug should fail its tests before the fix"

    assert _apply_patch(tmp_path, path, patch["diff"]) is None
    assert _run_sample_tests(tmp_path, test_file) == 0


def test_patch_diff_has_repo_relative_headers():
    ctx = ci_context()
    diff = mock_llm.generate_patch({"root_cause": "ci_test_failure"}, ctx["source_files"])["diff"]
    assert diff.startswith("--- a/sample-app/math_utils.py\n+++ b/sample-app/math_utils.py\n")


def test_patch_does_not_touch_tests():
    ctx = ci_context()
    patch = mock_llm.generate_patch({"root_cause": "ci_test_failure"}, ctx["source_files"])
    assert "test_" not in patch["file_path"]


def test_no_template_for_unknown_root_cause():
    patch = mock_llm.generate_patch({"root_cause": "unknown"}, {"sample-app/app.py": "x = 1\n"})
    assert patch["diff"] == "" and "unknown" in patch["explanation"]


def test_no_template_when_code_already_fixed():
    patch = mock_llm.generate_patch({"root_cause": "ci_test_failure"}, {"sample-app/math_utils.py": "def multiply(a, b):\n    return a * b\n"})
    assert patch["diff"] == ""


def test_root_cause_must_match_file():
    patch = mock_llm.generate_patch({"root_cause": "memory_leak"}, {"sample-app/math_utils.py": "def multiply(a, b):\n    return a + b\n"})
    assert patch["diff"] == ""


def test_unified_diff_identical_is_empty():
    assert mock_llm.unified_diff("x.py", "a\n", "a\n") == ""


def test_memory_samples_picks_usage_and_limit():
    samples, limit = mock_llm._memory_samples({
        "container_memory_working_set_bytes": [{"value": 1}, {"value": 2}],
        "container_spec_memory_limit_bytes": [{"value": 10}],
    })
    assert samples == [1.0, 2.0] and limit == 10.0
