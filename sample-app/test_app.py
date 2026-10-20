"""Tests for the sample app. GhostOps runs these in its sandbox to validate a fix."""

import app as sample

client = sample.app.test_client()


def test_index():
    assert client.get("/").status_code == 200


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_metrics_endpoint():
    assert client.get("/metrics").status_code == 200


def test_leak_endpoint_responds():
    assert client.get("/leak").status_code == 200


def test_leak_does_not_retain_memory():
    """Memory held by the app must not grow with the number of /leak requests."""
    for _ in range(6):
        client.get("/leak")
    held = sum(len(chunk) for chunk in getattr(sample, "leak_store", []))
    assert held < 30 * 1024 * 1024, f"{held // (1024 * 1024)} MiB still held after repeated /leak calls"


def test_checkout_healthy_by_default():
    assert all(client.get("/api/checkout").status_code == 200 for _ in range(10))


def test_requests_counted_by_status_and_release():
    client.get("/api/checkout")
    body = client.get("/metrics").get_data(as_text=True)
    assert 'sample_app_requests_total{release="stable",status="200"}' in body
