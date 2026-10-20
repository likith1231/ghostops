"""Post-merge verification and real MTTR (Prometheus faked, clock controlled)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agents import governance, knowledge_base
from backend import verification
from backend.main import app

client = TestClient(app)
T0 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def db(tmp_path):
    with patch.object(knowledge_base, "_DB_PATH", tmp_path / "kb.db"):
        yield


def probes(*healthy):
    it = iter(list(healthy) + [healthy[-1]] * 50)
    return patch.object(verification, "_probe", side_effect=lambda a: {"ok": next(it), "value": 0.1, "alert": "inactive"})


def test_healthy_window_resolves_with_mttr():
    vid = verification.start(kind="rollback", alert_type="error_rate_high", started_at=T0, deployed_at=T0 + timedelta(seconds=80), window_s=120, run_ref="r1")
    with probes(True):
        assert verification.tick(T0 + timedelta(seconds=100)) == []  # still inside the window
        done = verification.tick(T0 + timedelta(seconds=201))
    assert done[0]["id"] == vid and done[0]["state"] == "resolved" and done[0]["mttr_s"] == 201
    assert verification.mttr_summary()["median_s"] == 201


def test_regression_reopens_after_two_unhealthy_samples():
    verification.start(kind="merge", alert_type="memory_high", started_at=T0, deployed_at=T0, window_s=600)
    with probes(True, False, False):
        verification.tick(T0 + timedelta(seconds=15))
        assert verification.tick(T0 + timedelta(seconds=30)) == [], "one bad sample can be metric lag"
        done = verification.tick(T0 + timedelta(seconds=45))
    assert done[0]["state"] == "reopened" and done[0]["mttr_s"] is None


def test_single_blip_does_not_reopen():
    verification.start(kind="merge", alert_type="memory_high", started_at=T0, deployed_at=T0, window_s=40)
    with probes(False, True, True):
        verification.tick(T0 + timedelta(seconds=10))
        verification.tick(T0 + timedelta(seconds=20))
        done = verification.tick(T0 + timedelta(seconds=41))
    assert done[0]["state"] == "resolved"


def test_error_rate_probe_measures_only_the_deployed_release():
    """After a rollback the bad release's errors linger in the 1m window: they must not count."""
    queries, alerts = [], []
    with patch.object(verification, "_deployed_release", return_value="stable"), \
         patch("backend.live._prom", side_effect=lambda q: queries.append(q) or 0.0), \
         patch("backend.live.alert_state", side_effect=lambda name, labels=None: alerts.append(labels) or "inactive"):
        assert verification._probe("error_rate_high")["ok"] is True
    assert 'release="stable"' in queries[0] and alerts == [{"release": "stable"}]
    assert verification.mttr_summary()["count"] == 0


def test_merge_waits_for_deployment_grace():
    verification.start(kind="merge", alert_type="memory_high", started_at=T0, deployed_at=T0 + timedelta(seconds=180), window_s=60)
    with probes(False) as probe:
        assert verification.tick(T0 + timedelta(seconds=100)) == []
    probe.assert_not_called()


def test_prometheus_down_is_not_a_verdict():
    verification.start(kind="rollback", alert_type="memory_high", started_at=T0, deployed_at=T0, window_s=60)
    with probes(None):
        assert verification.tick(T0 + timedelta(seconds=90)) == []


def test_unknown_signal_not_watched():
    assert verification.start(kind="merge", alert_type="ci_failure", started_at=T0) is None


def test_mttr_summary_percentiles():
    # Four incidents that started 60 s, 2, 4 and 10 min before the fix; all verified 2 s after it.
    for ago in (60, 120, 240, 600):
        verification.start(kind="rollback", alert_type="memory_high", started_at=T0 - timedelta(seconds=ago), deployed_at=T0, window_s=1)
    with probes(True):
        verification.tick(T0 + timedelta(seconds=2))
    s = verification.mttr_summary()
    assert s["count"] == 4 and s["median_s"] == 182 and s["p90_s"] == 602
    assert s["under_5_min_pct"] == 75.0 and s["states"] == {"resolved": 4}


def test_auto_rollback_on_regression(monkeypatch):
    monkeypatch.setenv("AUTO_ROLLBACK_ON_REGRESSION", "1")
    verification.start(kind="merge", alert_type="memory_high", started_at=T0, deployed_at=T0, window_s=600)
    with probes(False), patch("agents.deployer.rollback_deployment", return_value={"rolled_back": True, "output": "ok"}) as rb:
        verification.tick(T0 + timedelta(seconds=15))
        verification.tick(T0 + timedelta(seconds=30))
    rb.assert_called_once()


def test_auto_rollback_respects_kill_switch(monkeypatch):
    monkeypatch.setenv("AUTO_ROLLBACK_ON_REGRESSION", "1")
    governance.set_paused(True)
    verification.start(kind="merge", alert_type="memory_high", started_at=T0, deployed_at=T0, window_s=600)
    with probes(False), patch("agents.deployer.rollback_deployment") as rb:
        verification.tick(T0 + timedelta(seconds=15))
        verification.tick(T0 + timedelta(seconds=30))
    rb.assert_not_called()


def test_probe_reads_signal_and_alert():
    with patch("backend.live._prom", return_value=0.93), patch("backend.live.alert_state", return_value="firing"):
        assert verification._probe("memory_high") == {"ok": False, "value": 0.93, "alert": "firing"}
    with patch("backend.live._prom", return_value=0.3), patch("backend.live.alert_state", return_value="inactive"):
        assert verification._probe("memory_high")["ok"] is True


def test_api():
    verification.start(kind="rollback", alert_type="error_rate_high", started_at=T0, deployed_at=T0, window_s=60)
    assert client.get("/api/incidents/verifications").json()[0]["state"] == "watching"
    assert client.get("/api/incidents/mttr").json()["count"] == 0
