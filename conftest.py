"""Test-wide setup: no test may touch the real knowledge base.

Runs before any test module imports ``agents.knowledge_base``, which reads
GHOSTOPS_DB_PATH at import time.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ghostops-tests-")
os.environ["GHOSTOPS_DB_PATH"] = os.path.join(_tmp, "incidents.db")
os.environ["GHOSTOPS_DISABLE_WATCHER"] = "1"  # tests drive verification.tick() themselves


import pytest  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "real_sandbox: let the pipeline run regression tests in the real Docker sandbox")


@pytest.fixture(autouse=True)
def _no_regression_sandbox(request, monkeypatch):
    """Unit tests fake the sandbox; the regression-test proof would run it for real. Opt in with
    @pytest.mark.real_sandbox (the end-to-end tests do)."""
    if request.node.get_closest_marker("real_sandbox"):
        return
    try:
        from agents import crew
    except Exception:  # noqa: BLE001 - modules that never import the pipeline
        return
    monkeypatch.setattr(crew, "run_replay", lambda *a, **k: {"verdict": "not_applicable", "summary": "faked in unit tests"})
    monkeypatch.setattr(
        crew,
        "verify_regression_test",
        lambda *a, **k: {"path": "x", "content": "", "source": "fake", "failed_before": False, "passed_after": False, "verified": False},
    )
