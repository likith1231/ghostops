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


@pytest.mark.real_sandbox
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
    # The generated regression test was proven in the sandbox: fails before the fix, passes after.
    proof = result["validation_result"]["regression_test"]
    assert proof["failed_before"] and proof["passed_after"], proof
    assert result["patch"]["regression_test"]["path"].startswith("sample-app/test_ghostops_regression_")
    assert result["risk"]["level"] in ("low", "medium") and any(f["factor"] == "regression test" for f in result["risk"]["factors"])
    # Counterfactual replay: the leak's recorded traffic reproduces it unpatched and not patched;
    # the CI failure has no HTTP traffic to replay.
    expected_replay = {"memory_leak": "reproduced_and_fixed"}.get(root_cause, "not_applicable")
    assert result["validation_result"]["replay"]["verdict"] == expected_replay, result["validation_result"]["replay"]
    # The fix carries signed evidence that verifies, and binds the exact validated diff.
    from agents import evidence

    signed = result["patch"]["evidence"]
    key = evidence.signing_key(db.parent / "evidence.key")  # the run signed with the key beside its database
    check = evidence.verify(signed, key.public_key(), diff=result["patch"]["diff"])
    assert check == {"signature_valid": True, "patch_matches": True, "valid": True}
    assert signed["bundle"]["gates"]["regression_test"]["failed_before"] is True
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
