from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Alerts are remembered for deduplication: give every test its own knowledge base."""
    from agents import knowledge_base

    with patch.object(knowledge_base, "_DB_PATH", tmp_path / "kb.db"):
        yield


def _alert(name="SampleAppHighMemory", status="firing", pod="sample-app-abc"):
    return {"status": status, "labels": {"alertname": name, "pod": pod}}


def test_webhook_rejects_invalid_json():
    response = client.post("/webhook/alert", content="not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["received"] is False


def test_webhook_ignores_other_alerts():
    with patch("backend.main._process_alert") as process:
        response = client.post("/webhook/alert", json={"alerts": [_alert(name="SomethingElse")]})
    assert response.status_code == 200
    assert response.json()["action"] == "ignored"
    process.assert_not_called()


def test_webhook_resolved_alert_closes_incident_without_pipeline():
    with patch("backend.main._process_alert"):
        client.post("/webhook/alert", json={"alerts": [_alert()]})
    with patch("backend.main._process_alert") as process:
        response = client.post("/webhook/alert", json={"alerts": [_alert(status="resolved")]})
    assert response.json()["action"] == "resolved" and response.json()["incident"] == 1
    process.assert_not_called()


def test_webhook_starts_pipeline_for_firing_alert():
    alert = _alert()
    with patch("backend.main._process_alert") as process:
        response = client.post("/webhook/alert", json={"alerts": [alert]})
    assert response.status_code == 200
    assert response.json()["action"] == "pipeline_started"
    process.assert_called_once()
    sent = process.call_args.args[0]
    assert sent["labels"] == alert["labels"] and sent["_incident"] == response.json()["incident"]


def test_only_one_pipeline_runs_at_a_time():
    from backend import main

    with patch("backend.main._run_alert_pipeline") as run:
        with main._pipeline_lock:  # a pipeline is already running
            main._process_alert(_alert())
        run.assert_not_called()
        main._process_alert(_alert())
        run.assert_called_once()


def test_webhook_empty_alerts_list():
    response = client.post("/webhook/alert", json={"alerts": []})
    assert response.status_code == 200
    assert response.json() == {"received": True, "results": []}


def test_webhook_missing_alerts_key():
    assert client.post("/webhook/alert", json={"status": "firing"}).json()["received"] is True


def test_webhook_multiple_alerts_reported_individually():
    alerts = [_alert(), _alert(name="Other"), _alert(pod="sample-app-2")]
    with patch("backend.main._process_alert") as process:
        data = client.post("/webhook/alert", json={"alerts": alerts}).json()
    assert [r["action"] for r in data["results"]] == ["pipeline_started", "ignored", "pipeline_started"]
    assert process.call_count == 2


def test_webhook_alert_without_labels_ignored():
    with patch("backend.main._process_alert") as process:
        assert client.post("/webhook/alert", json={"alerts": [{"status": "firing"}]}).json()["action"] == "ignored"
    process.assert_not_called()


def test_webhook_and_simulate_share_one_lock():
    from backend import main
    from backend.runs import pipeline_lock

    assert main._pipeline_lock is pipeline_lock


def test_repeat_alert_does_not_start_another_pipeline():
    with patch("backend.main._process_alert") as process:
        first = client.post("/webhook/alert", json={"alerts": [_alert(pod="sample-app-6594c596d4-aaaaa")]}).json()
        again = client.post("/webhook/alert", json={"alerts": [_alert(pod="sample-app-6594c596d4-bbbbb")]}).json()
    assert first["action"] == "pipeline_started" and again["action"] == "duplicate"
    assert again["incident"] == first["incident"]
    assert process.call_count == 1


def test_related_alert_is_correlated_not_processed():
    with patch("backend.main._process_alert") as process:
        client.post("/webhook/alert", json={"alerts": [_alert(pod="sample-app-6594c596d4-aaaaa")]})
        r = client.post("/webhook/alert", json={"alerts": [{"status": "firing", "labels": {"alertname": "SampleAppHighErrorRate", "component": "sample-app"}}]}).json()
    assert r["action"] == "correlated"
    assert process.call_count == 1


def test_correlated_incidents_endpoint():
    with patch("backend.main._process_alert"):
        client.post("/webhook/alert", json={"alerts": [_alert(pod="sample-app-6594c596d4-aaaaa")]})
    from agents import knowledge_base

    with patch("backend.routers.incidents._DB_PATH", knowledge_base._DB_PATH):
        data = client.get("/api/incidents/correlated").json()
    assert data[0]["workload"] == "sample-app" and data[0]["state"] == "open"
