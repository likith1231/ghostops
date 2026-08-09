"""
Tests for the GhostOps Deployer safety gates.

Validates that:
1. No PR is created when validation fails (``passed != True``).
2. No PR is created when ``GITHUB_TOKEN`` is empty.
3. No PR is created when the patch diff is empty.
4. No git operations are attempted in any of these cases.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures — sample data matching the real pipeline output shapes
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_failure_context() -> dict:
    return {
        "alert_type": "memory_high",
        "logs": [
            "2026-08-09T08:00:12Z app WARNING: /api/data handler allocated 2.3 GB",
            "2026-08-09T08:01:45Z kernel: Out of memory: Killed process 4321",
        ],
        "metrics": {
            "container_memory_usage_bytes": [
                {"ts": "08:00", "value": 1_900_000_000},
            ],
        },
        "source_files": {
            "app.py": "from flask import Flask\napp = Flask(__name__)\n",
        },
        "past_incidents": [],
    }


@pytest.fixture()
def sample_diagnosis() -> dict:
    return {
        "root_cause": "memory_leak",
        "confidence": "high",
        "summary": "Unbounded cache in /api/data endpoint",
        "evidence": [
            {
                "type": "log",
                "reference": "request_cache size: 148201 entries",
                "reasoning": "Cache grows without bound",
            }
        ],
    }


@pytest.fixture()
def sample_patch() -> dict:
    return {
        "diff": (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -5,1 +5,2 @@\n"
            "-request_cache = []\n"
            "+from collections import deque\n"
            "+request_cache = deque(maxlen=1000)\n"
        ),
        "explanation": "Added a max-size limit to the request cache.",
    }


def _make_pipeline_result(
    diagnosis: dict,
    patch_data: dict,
    passed: bool | None,
) -> dict:
    """Build a pipeline_result dict with the given validation status."""
    validation_result = {
        "passed": passed,
        "summary": "All tests passed." if passed else "1 test failed.",
        "failing_tests": [] if passed else ["test_memory"],
        "test_output": "...",
    }
    return {
        "diagnosis": diagnosis,
        "patch": patch_data,
        "validation_result": validation_result,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSafetyGate:
    """The deployer should NEVER create a PR unless validation passed."""

    def test_no_pr_when_validation_failed(
        self, sample_failure_context, sample_diagnosis, sample_patch
    ):
        """If validation_result.passed is False, no PR and no git ops."""
        from agents.deployer import create_pr

        pipeline_result = _make_pipeline_result(
            sample_diagnosis, sample_patch, passed=False
        )

        # Patch subprocess.run so if any git command is called, the test fails
        with patch("agents.deployer.subprocess.run") as mock_run:
            result = create_pr(
                pipeline_result=pipeline_result,
                failure_context=sample_failure_context,
            )

        assert result["pr_created"] is False
        assert result["reason"] == "validation_failed"
        mock_run.assert_not_called()  # NO git operations at all

    def test_no_pr_when_validation_none(
        self, sample_failure_context, sample_diagnosis, sample_patch
    ):
        """If validation_result.passed is None, no PR."""
        from agents.deployer import create_pr

        pipeline_result = _make_pipeline_result(
            sample_diagnosis, sample_patch, passed=None
        )

        with patch("agents.deployer.subprocess.run") as mock_run:
            result = create_pr(
                pipeline_result=pipeline_result,
                failure_context=sample_failure_context,
            )

        assert result["pr_created"] is False
        assert result["reason"] == "validation_failed"
        mock_run.assert_not_called()

    def test_no_pr_when_validation_missing(
        self, sample_failure_context, sample_diagnosis, sample_patch
    ):
        """If validation_result key is missing entirely, no PR."""
        from agents.deployer import create_pr

        pipeline_result = {
            "diagnosis": sample_diagnosis,
            "patch": sample_patch,
            # No "validation_result" key at all
        }

        with patch("agents.deployer.subprocess.run") as mock_run:
            result = create_pr(
                pipeline_result=pipeline_result,
                failure_context=sample_failure_context,
            )

        assert result["pr_created"] is False
        assert result["reason"] == "validation_failed"
        mock_run.assert_not_called()


class TestAuthGate:
    """The deployer should refuse to create a PR without a GitHub token."""

    def test_no_pr_when_token_empty(
        self, sample_failure_context, sample_diagnosis, sample_patch
    ):
        """If GITHUB_TOKEN is empty, no PR and no git ops."""
        from agents.deployer import create_pr

        pipeline_result = _make_pipeline_result(
            sample_diagnosis, sample_patch, passed=True
        )

        with patch("agents.deployer.GITHUB_TOKEN", ""), \
             patch("agents.deployer.subprocess.run") as mock_run:
            result = create_pr(
                pipeline_result=pipeline_result,
                failure_context=sample_failure_context,
            )

        assert result["pr_created"] is False
        assert "GITHUB_TOKEN" in result["reason"]
        mock_run.assert_not_called()


class TestEmptyDiff:
    """No PR should be created if the patch diff is empty."""

    def test_no_pr_when_diff_empty(
        self, sample_failure_context, sample_diagnosis
    ):
        """If the patch diff string is empty, no PR."""
        from agents.deployer import create_pr

        empty_patch = {"diff": "", "explanation": "nothing to fix"}
        pipeline_result = _make_pipeline_result(
            sample_diagnosis, empty_patch, passed=True
        )

        with patch("agents.deployer.GITHUB_TOKEN", "fake-token-for-test"), \
             patch("agents.deployer.subprocess.run") as mock_run:
            result = create_pr(
                pipeline_result=pipeline_result,
                failure_context=sample_failure_context,
            )

        assert result["pr_created"] is False
        assert result["reason"] == "empty_diff"
        mock_run.assert_not_called()
