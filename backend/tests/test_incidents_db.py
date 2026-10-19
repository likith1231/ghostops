"""/api/incidents/* against a real, temporary knowledge base."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agents.knowledge_base import init_db, log_incident
from backend.main import app

client = TestClient(app)


def _log(db, passed, pr=False, alert="memory_high"):
    log_incident(
        {"alert_type": alert},
        {"root_cause": "memory_leak", "confidence": "high", "summary": "leak"},
        {"diff": "--- a\n+++ b\n"},
        {"passed": passed, "summary": "ok" if passed else "bad"},
        {"pr_created": pr, "pr_url": "https://github.com/x/y/pull/1" if pr else ""},
        db_path=db,
    )


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "incidents.db"
    init_db(path)
    with patch("backend.routers.incidents._DB_PATH", path):
        yield path


@pytest.fixture()
def no_db(tmp_path):
    with patch("backend.routers.incidents._DB_PATH", tmp_path / "missing.db"):
        yield


def test_summary_counts(db):
    _log(db, True, pr=True)
    _log(db, True)
    _log(db, False)
    assert client.get("/api/incidents/summary").json() == {
        "total": 3, "passed": 2, "failed": 1, "pass_rate": 66.7, "prs_created": 1,
    }


def test_summary_empty_table(db):
    assert client.get("/api/incidents/summary").json()["pass_rate"] == 0.0


def test_summary_missing_db(no_db):
    assert client.get("/api/incidents/summary").json()["total"] == 0


def test_summary_corrupt_db(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite")
    with patch("backend.routers.incidents._DB_PATH", bad):
        response = client.get("/api/incidents/summary")
    assert response.status_code == 200 and response.json()["total"] == 0


def test_timeseries_groups_by_day(db):
    _log(db, True)
    _log(db, False)
    rows = client.get("/api/incidents/timeseries").json()
    assert len(rows) == 1 and rows[0]["passed"] == 1 and rows[0]["failed"] == 1


def test_timeseries_missing_db(no_db):
    assert client.get("/api/incidents/timeseries").json() == []


def test_recent_limit_capped():
    with patch("backend.routers.incidents.get_past_incidents", return_value=[]) as get:
        client.get("/api/incidents/recent?limit=5000")
    get.assert_called_once_with(limit=100)


def test_recent_rejects_non_integer_limit():
    assert client.get("/api/incidents/recent?limit=abc").status_code == 422


def test_detail(db):
    _log(db, True, pr=True)
    data = client.get("/api/incidents/1").json()
    assert data["id"] == 1 and data["pr_url"].endswith("/pull/1") and data["patch_diff"].startswith("---")


def test_detail_not_found(db):
    assert client.get("/api/incidents/999").status_code == 404


def test_detail_missing_db(no_db):
    assert client.get("/api/incidents/1").status_code == 404


def test_detail_non_integer_id():
    assert client.get("/api/incidents/abc").status_code == 422


def test_cors_allows_dashboard_origin():
    response = client.get("/health", headers={"Origin": "http://localhost:3100"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3100"


def test_cors_rejects_other_origin():
    response = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_metrics_endpoint_exposes_gauges():
    body = client.get("/metrics").text
    assert "ghostops_incidents_total" in body and "ghostops_prs_created" in body
