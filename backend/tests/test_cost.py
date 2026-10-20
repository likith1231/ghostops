"""Incident cost meter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agents import knowledge_base
from backend import cost
from backend.main import app

client = TestClient(app)
GiB = 1073741824
T0 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def db(tmp_path):
    with patch.object(knowledge_base, "_DB_PATH", tmp_path / "kb.db"):
        yield


def fake_prom(failed=40.0, avg_mem=0.5 * GiB, request=0.0625 * GiB, peak=0.93):
    def q(query):
        if "increase(sample_app_requests_total" in query:
            return failed
        if "avg_over_time" in query:
            return avg_mem
        if "resource_requests" in query:
            return request
        if "max_over_time" in query:
            return peak
        return None
    return patch.object(cost.live, "_prom", side_effect=q)


def test_measure_reads_the_window_from_prometheus():
    with fake_prom() as prom:
        m = cost.measure(T0, T0 + timedelta(minutes=3))
    assert m == {"degraded_minutes": 3.0, "failed_requests": 40, "wasted_gib_hours": round(0.4375 * 180 / 3600, 4), "peak_memory_pct": 93.0}
    assert any("[180s]" in c.args[0] for c in prom.call_args_list)


def test_price_with_default_rates(monkeypatch):
    for k in ("COST_PER_FAILED_REQUEST", "COST_PER_DEGRADED_MINUTE", "COST_PER_GIB_HOUR", "MANUAL_MTTR_MINUTES"):
        monkeypatch.delenv(k, raising=False)
    p = cost.price({"degraded_minutes": 3.0, "failed_requests": 40, "wasted_gib_hours": 0.02})
    assert p["lines_usd"] == {"failed_requests": 10.0, "degradation": 60.0, "compute": 0.0}
    assert p["cost_usd"] == 70.0 and p["saved_minutes"] == 42.0 and p["saved_usd"] == 840.0


def test_rates_are_configurable(monkeypatch):
    monkeypatch.setenv("COST_PER_DEGRADED_MINUTE", "100")
    monkeypatch.setenv("MANUAL_MTTR_MINUTES", "30")
    p = cost.price({"degraded_minutes": 10.0, "failed_requests": 0, "wasted_gib_hours": 0})
    assert p["cost_usd"] == 1000.0 and p["saved_usd"] == 2000.0


def test_slower_than_manual_saves_nothing():
    assert cost.price({"degraded_minutes": 90.0, "failed_requests": 0, "wasted_gib_hours": 0})["saved_usd"] == 0


def test_prometheus_down_still_prices_duration():
    with patch.object(cost.live, "_prom", side_effect=ConnectionError()):
        m = cost.measure(T0, T0 + timedelta(minutes=2))
    assert m["failed_requests"] is None and m["degraded_minutes"] == 2.0


def test_record_and_summary_api():
    with fake_prom():
        out = cost.record(run_ref="r1", alert_type="error_rate_high", started_at="2026-10-20T10:00:00Z", ended_at=T0 + timedelta(minutes=2))
    assert out["failed_requests"] == 40
    data = client.get("/api/incidents/costs").json()
    assert data["incidents"] == 1 and data["total_cost_usd"] == out["cost_usd"] and data["items"][0]["run_ref"] == "r1"
    assert "manual_mttr_minutes" in data["rates"]


def test_no_start_time_no_cost():
    assert cost.record(run_ref="r", alert_type="x", started_at=None) is None
