"""/api/governance: kill switch and audit trail over HTTP."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agents import governance
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def db(tmp_path):
    with patch.object(governance.knowledge_base, "_DB_PATH", tmp_path / "gov.db"):
        yield


def test_status_defaults_to_running():
    assert client.get("/api/governance").json()["paused"] is False


def test_pause_and_resume_round_trip():
    assert client.post("/api/governance/pause", json={"reason": "demo"}).json()["paused"] is True
    assert client.get("/api/governance").json()["reason"] == "demo"
    assert client.post("/api/governance/resume", json={}).json()["paused"] is False


def test_audit_lists_toggles_and_verifies_chain():
    client.post("/api/governance/pause", json={"reason": "r"})
    client.post("/api/governance/resume", json={})
    data = client.get("/api/governance/audit").json()
    assert [e["decision"] for e in data["entries"]] == ["resumed", "paused"]
    assert data["chain"]["valid"] is True and data["chain"]["entries"] == 2
