"""Runs the Rego unit tests (``*_test.rego``) for the Gatekeeper policies with ``conftest verify``."""

from __future__ import annotations

from pathlib import Path

import docker
import pytest

POLICIES = Path(__file__).resolve().parents[2] / "infra" / "k8s" / "gatekeeper" / "policies"
CONFTEST = "openpolicyagent/conftest:v0.48.0"


@pytest.fixture(scope="module")
def client():
    try:
        c = docker.from_env()
        c.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Docker is not available")
    try:
        c.images.get(CONFTEST)
    except docker.errors.ImageNotFound:
        c.images.pull(CONFTEST)
    return c


def test_rego_unit_tests_pass(client):
    out = client.containers.run(
        CONFTEST, ["verify", "-p", "/policies", "--no-color"],
        volumes={str(POLICIES): {"bind": "/policies", "mode": "ro"}},
        remove=True,
    ).decode()
    assert "0 failures" in out
    assert "10 tests, 10 passed" in out
