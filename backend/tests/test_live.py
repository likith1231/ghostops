"""Tests for live cluster incidents, webhook runs and the activity feed (cluster and Prometheus faked)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import live
from backend.main import app
from backend.runs import RunStore, pipeline_lock

client = TestClient(app)


@pytest.fixture()
def fresh_store():
    s = RunStore()
    with patch("backend.routers.runs.store", s), patch("backend.live.store", s), patch("backend.main.run_store", s):
        yield s


def _fake_wait(handover=True):
    """Stand-in for live._wait: polls once, and (optionally) lets the 'webhook' claim the run."""
    def wait(predicate, timeout_s, on_poll=None):
        if on_poll:
            on_poll()
        if handover and live._waiting_run is not None:
            live.claim_waiting_run()
        return predicate()
    return wait


def _leak_env(leaked=0, alerts=("firing",), handover=True):
    calls = {"get": [], "restart": 0}
    alert_iter = iter(list(alerts) + [alerts[-1]] * 50)

    def pod_get(pod, path):
        calls["get"].append(path)
        return json.dumps({"leaked_objects": leaked if not calls["restart"] else 0})

    def restart():
        calls["restart"] += 1

    patches = [
        patch.object(live, "_pod_name", return_value="sample-app-1"),
        patch.object(live, "_pod_or_none", return_value="sample-app-2"),
        patch.object(live, "_pod_get", side_effect=pod_get),
        patch.object(live, "alert_state", side_effect=lambda: next(alert_iter)),
        patch.object(live, "_prom", side_effect=lambda q: 134217728.0 if "limit" in q else 110_000_000.0),
        patch.object(live, "_restart_sample_app", side_effect=restart),
        patch.object(live, "_wait", side_effect=_fake_wait(handover)),
        patch.object(live.time, "sleep"),
    ]
    return calls, patches


def _run_leak(store, **env):
    calls, patches = _leak_env(**env)
    run = store.create("live", "Live")
    live.live_lock.acquire()
    for p in patches:
        p.start()
    try:
        live.run_live_leak(run["id"])
    finally:
        for p in patches:
            p.stop()
    return store.get(run["id"]), calls


# ---- run_live_leak ------------------------------------------------------------------
def test_live_leak_leaks_and_hands_over_to_webhook(fresh_store):
    run, calls = _run_leak(fresh_store, alerts=("inactive", "firing"))
    assert calls["get"].count("leak") == live.LEAK_REQUESTS
    assert run["live"]["leaked_mib"] == live.LEAK_REQUESTS * 10
    assert run["live"]["step"] == "alert_firing"
    assert run["live"]["memory"] and run["live"]["limit"] == 134217728.0
    assert run["status"] == "running", "the webhook's pipeline finishes the run, not the leaker"
    assert not live.live_lock.locked()


def test_live_leak_resets_dirty_pod_first(fresh_store):
    run, calls = _run_leak(fresh_store, leaked=3, alerts=("inactive", "firing"))
    assert calls["restart"] == 1 and run["live"]["reset"] is True


def test_live_leak_clean_pod_is_not_reset(fresh_store):
    run, calls = _run_leak(fresh_store, alerts=("inactive", "firing"))
    assert calls["restart"] == 0 and "reset" not in run["live"]


def test_live_leak_alert_never_fires(fresh_store):
    run, _ = _run_leak(fresh_store, alerts=("inactive",))
    assert run["status"] == "error" and "did not fire" in run["error"]
    assert not live.live_lock.locked()


def test_live_leak_webhook_never_arrives(fresh_store):
    run, _ = _run_leak(fresh_store, alerts=("inactive", "firing"), handover=False)
    assert run["status"] == "error" and "0.0.0.0" in run["error"]
    assert live._waiting_run is None


def test_live_leak_cluster_unreachable(fresh_store):
    run = fresh_store.create("live", "Live")
    live.live_lock.acquire()
    with patch.object(live, "_pod_name", side_effect=RuntimeError("no running sample-app pod")):
        live.run_live_leak(run["id"])
    got = fresh_store.get(run["id"])
    assert got["status"] == "error" and "no running" in got["error"]
    assert not live.live_lock.locked()


def test_claim_waiting_run_is_one_shot():
    live._waiting_run = "abc"
    assert live.claim_waiting_run() == "abc"
    assert live.claim_waiting_run() is None


# ---- /api/live -------------------------------------------------------------------------
def test_live_leak_endpoint_starts_thread(fresh_store):
    with patch.object(live, "run_live_leak", side_effect=lambda run_id: live.live_lock.release()) as run:
        response = client.post("/api/live/leak")
    assert response.status_code == 202
    run.assert_called_once()
    assert fresh_store.get(response.json()["run_id"])["scenario"] == "live"


def test_live_leak_endpoint_busy_pipeline(fresh_store):
    with pipeline_lock:
        assert client.post("/api/live/leak").status_code == 409


def test_live_leak_endpoint_already_running(fresh_store):
    with live.live_lock:
        assert client.post("/api/live/leak").status_code == 409


def test_live_status_ready():
    with patch.object(live, "_pod_name", return_value="sample-app-1"), patch.object(live, "alert_state", return_value="inactive"):
        data = client.get("/api/live/status").json()
    assert data == {"ready": True, "pod": "sample-app-1", "alert": "inactive", "busy": False}


def test_live_status_unreachable():
    with patch.object(live, "_pod_name", side_effect=RuntimeError("kubeconfig missing")):
        data = client.get("/api/live/status").json()
    assert data["ready"] is False and "kubeconfig" in data["error"]


# ---- webhook runs ----------------------------------------------------------------------
RESULT = {"diagnosis": {}, "patch": {}, "validation_result": {"passed": True}, "pr_result": {}}


def _alert_pipeline(store, result=RESULT, error=None):
    from backend import main

    def fake_pipeline(ctx, on_stage=None):
        if error:
            raise error
        on_stage("diagnosis", "running", {})
        return result

    with patch.object(main, "_fetch_pod_logs", return_value=["log"]), \
         patch.object(main, "_query_prometheus_memory", return_value={"container_memory_working_set_bytes": [{"value": 1}]}), \
         patch.object(main, "run_pipeline", side_effect=fake_pipeline):
        main._run_alert_pipeline({"labels": {"alertname": "SampleAppHighMemory", "pod": "sample-app-1"}}, "sample-app-1")
    return store.list()


def test_webhook_attaches_to_waiting_live_run(fresh_store):
    run = fresh_store.create("live", "Live cluster")
    live._waiting_run = run["id"]
    runs = _alert_pipeline(fresh_store)
    assert len(runs) == 1
    assert runs[0]["status"] == "passed"
    assert runs[0]["context"]["logs"] == ["log"]
    assert runs[0]["stages"]["diagnosis"]["status"] == "done"  # streamed live, closed on finish


def test_webhook_without_live_run_creates_its_own(fresh_store):
    live._waiting_run = None
    runs = _alert_pipeline(fresh_store)
    assert len(runs) == 1 and runs[0]["title"].startswith("Live alert: SampleAppHighMemory")


def test_webhook_pipeline_error_finishes_run(fresh_store):
    runs = _alert_pipeline(fresh_store, error=RuntimeError("429 rate limit"))
    assert runs[0]["status"] == "error" and "Rate Limited" in runs[0]["error"]


# ---- /api/activity ---------------------------------------------------------------------
def test_activity_idle_without_runs(fresh_store):
    assert client.get("/api/activity").json() == {"state": "idle"}


@pytest.mark.parametrize("stage,state", [("diagnosis", "diagnosing"), ("patch", "patching"), ("validation", "validating"), ("deploy", "validating")])
def test_activity_follows_running_stage(fresh_store, stage, state):
    run = fresh_store.create("b1", "Leak")
    fresh_store.update_stage(run["id"], stage, "running")
    assert client.get("/api/activity").json()["state"] == state


def test_activity_preparing_while_leaking(fresh_store):
    run = fresh_store.create("live", "Live")
    fresh_store.set_live(run["id"], {"step": "leaking"})
    assert client.get("/api/activity").json()["state"] == "preparing"


def test_activity_incident_once_alert_fires(fresh_store):
    run = fresh_store.create("live", "Live")
    fresh_store.set_live(run["id"], {"step": "alert_firing"})
    assert client.get("/api/activity").json()["state"] == "incident"


def test_activity_healed_then_idle(fresh_store):
    run = fresh_store.create("b1", "Leak")
    fresh_store.finish(run["id"], "passed")
    assert client.get("/api/activity").json()["state"] == "healed"
    old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    fresh_store._runs[run["id"]]["updated_at"] = old
    assert client.get("/api/activity").json()["state"] == "idle"


def test_activity_failed(fresh_store):
    run = fresh_store.create("b1", "Leak")
    fresh_store.finish(run["id"], "failed")
    assert client.get("/api/activity").json()["state"] == "failed"


def test_pod_get_reads_raw_body():
    """The k8s client turns JSON into a dict repr unless asked for the raw body."""
    from unittest.mock import MagicMock

    api = MagicMock()
    api.connect_get_namespaced_pod_proxy_with_path.return_value.data = b'{"leaked_objects":0}'
    k8s = MagicMock()
    k8s.CoreV1Api.return_value = api
    with patch.object(live, "_core", return_value=k8s):
        body = live._pod_get("sample-app-1", "health")
    assert json.loads(body) == {"leaked_objects": 0}
    assert api.connect_get_namespaced_pod_proxy_with_path.call_args.kwargs["_preload_content"] is False


def test_memory_queries_scoped_to_current_pod(fresh_store):
    seen = []
    calls, patches = _leak_env(alerts=("inactive", "firing"))
    run = fresh_store.create("live", "Live")
    live.live_lock.acquire()
    for p in patches:
        p.start()
    try:
        with patch.object(live, "_prom", side_effect=lambda q: seen.append(q) or 1.0):
            live.run_live_leak(run["id"])
    finally:
        for p in patches:
            p.stop()
    assert seen and all('pod="sample-app-1"' in q for q in seen)


def test_live_run_closes_stale_incident_first(fresh_store, tmp_path):
    from agents import correlation, knowledge_base

    with patch.object(knowledge_base, "_DB_PATH", tmp_path / "kb.db"):
        correlation.classify_alert({"alertname": live.ALERT, "pod": "sample-app-6594c596d4-aaaaa"})
        _run_leak(fresh_store, alerts=("inactive", "firing"))
        assert correlation.incidents()[0]["state"] == "resolved"
        assert correlation.classify_alert({"alertname": live.ALERT, "pod": "sample-app-6594c596d4-bbbbb"})["action"] == "new"
