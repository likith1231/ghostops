from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


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


def test_webhook_ignores_resolved_alerts():
    with patch("backend.main._process_alert") as process:
        response = client.post("/webhook/alert", json={"alerts": [_alert(status="resolved")]})
    assert response.json()["action"] == "ignored"
    process.assert_not_called()


def test_webhook_starts_pipeline_for_firing_alert():
    alert = _alert()
    with patch("backend.main._process_alert") as process:
        response = client.post("/webhook/alert", json={"alerts": [alert]})
    assert response.status_code == 200
    assert response.json()["action"] == "pipeline_started"
    process.assert_called_once_with(alert)


def test_only_one_pipeline_runs_at_a_time():
    from backend import main

    with patch("backend.main._run_alert_pipeline") as run:
        with main._pipeline_lock:  # a pipeline is already running
            main._process_alert(_alert())
        run.assert_not_called()
        main._process_alert(_alert())
        run.assert_called_once()
