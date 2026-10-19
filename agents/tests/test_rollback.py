"""Tests for the C1 canary rollback action."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from agents.deployer import rollback_deployment


def _proc(code=0, out="deployment.apps/sample-app rolled back", err=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


def test_rollback_runs_kubectl_undo():
    with patch("agents.deployer.subprocess.run", return_value=_proc()) as run:
        result = rollback_deployment("sample-app", "prod")
    assert run.call_args.args[0] == ["kubectl", "rollout", "undo", "deployment/sample-app", "-n", "prod"]
    assert result == {"rolled_back": True, "output": "deployment.apps/sample-app rolled back"}


def test_rollback_failure_reported():
    with patch("agents.deployer.subprocess.run", return_value=_proc(1, "", "no rollout history found")):
        result = rollback_deployment("sample-app")
    assert result == {"rolled_back": False, "output": "no rollout history found"}


@pytest.mark.parametrize("name", ["", "Sample-App", "app; rm -rf /", "a" * 64, "-leading-dash", "../x"])
def test_rollback_rejects_invalid_names(name):
    with patch("agents.deployer.subprocess.run") as run:
        assert rollback_deployment(name)["rolled_back"] is False
    run.assert_not_called()


def test_rollback_rejects_invalid_namespace():
    with patch("agents.deployer.subprocess.run") as run:
        assert rollback_deployment("sample-app", "kube system")["rolled_back"] is False
    run.assert_not_called()


def test_rollback_kubectl_missing():
    with patch("agents.deployer.subprocess.run", side_effect=FileNotFoundError("kubectl")):
        assert rollback_deployment("sample-app")["rolled_back"] is False


def test_rollback_timeout():
    with patch("agents.deployer.subprocess.run", side_effect=subprocess.TimeoutExpired("kubectl", 60)):
        assert rollback_deployment("sample-app")["rolled_back"] is False
