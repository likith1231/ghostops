"""End to end, offline: each demo scenario through the whole pipeline with the real OPA
check and the real Docker sandbox (mock LLM, no PR). Skipped when Docker is unavailable."""

from __future__ import annotations

from unittest.mock import patch

import docker
import pytest

from agents import crew
from agents.knowledge_base import get_past_incidents, init_db
from agents.scenarios import ci_context, exception_context, leak_context


@pytest.fixture(scope="module", autouse=True)
def needs_docker():
    try:
        docker.from_env().ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Docker is not available")


@pytest.mark.parametrize(
    "build,root_cause,file",
    [
        (leak_context, "memory_leak", "sample-app/app.py"),
        (ci_context, "ci_test_failure", "sample-app/math_utils.py"),
        (exception_context, "null_reference", "sample-app/user_service.py"),
    ],
)
def test_scenario_is_fixed_and_validated_for_real(tmp_path, build, root_cause, file):
    db = tmp_path / "incidents.db"
    init_db(db)
    from agents import knowledge_base

    # The knowledge base reads its path at call time: point it at a temp DB.
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "MOCK_CREATE_PR", False), \
         patch.object(knowledge_base, "_DB_PATH", db):
        result = crew.run_pipeline(build())

    assert result["diagnosis"]["root_cause"] == root_cause
    assert result["patch"]["file_path"] == file
    assert result["validation_result"]["passed"] is True, result["validation_result"]["test_output"]
    assert result["pr_result"] == {"pr_created": False, "reason": "mock_mode"}
    logged = get_past_incidents(db_path=db)
    assert len(logged) == 1 and logged[0]["validation_passed"] == 1 and logged[0]["root_cause"] == root_cause


def test_broken_patch_is_rejected_by_the_real_sandbox(tmp_path):
    """A patch that applies but does not fix the bug must fail validation."""
    from agents import mock_llm

    bad = {"diff": mock_llm.unified_diff(
        "sample-app/math_utils.py",
        open("sample-app/math_utils.py").read(),
        open("sample-app/math_utils.py").read().replace("return a + b\n\n\ndef multiply", "return a - b\n\n\ndef multiply"),
    ), "explanation": "wrong", "file_path": "sample-app/math_utils.py"}
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew.mock_llm, "generate_patch", return_value=bad), \
         patch("agents.knowledge_base.log_incident"):
        result = crew.run_pipeline(ci_context())
    assert result["validation_result"]["passed"] is False
    assert result["pr_result"]["reason"] == "validation_failed"
