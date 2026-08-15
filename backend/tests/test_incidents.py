from fastapi.testclient import TestClient
from backend.main import app
import pytest

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ghostops-backend"}

def test_incidents_summary():
    response = client.get("/api/incidents/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "passed" in data
    assert "failed" in data
    assert "pass_rate" in data
    assert "prs_created" in data

def test_incidents_recent():
    response = client.get("/api/incidents/recent")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "timestamp" in data[0]
        assert "alert_type" in data[0]

def test_incidents_timeseries():
    response = client.get("/api/incidents/timeseries")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
