"""Use case C1: canary regression -> rollback -> verify (cluster, Prometheus and kubectl faked)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import canary, live
from backend.main import app
from backend.runs import RunStore, pipeline_lock

client = TestClient(app)
NOW = datetime.now(timezone.utc)


def rev(n, minutes_ago, release, faulty=None):
    env = {"RELEASE": release}
    if faulty:
        env["FAULTY_RATE"] = faulty
    return {"revision": n, "created": NOW - timedelta(minutes=minutes_ago), "image": "ghostops-sample-app:v4", "env": env}


BAD = [rev(4, 60, "stable"), rev(5, 2, "canary-1", "0.4")]
ALERT = {"labels": {"alertname": "SampleAppHighErrorRate", "release": "canary-1"}}


@pytest.fixture()
def cluster():
    state = {"history": BAD, "rates": {"canary-1": 0.4, "stable": 0.0}, "rollback": {"rolled_back": True, "output": "rolled back"}, "checkouts": [True] * 30}
    it = {"i": 0}

    def checkout(pod):
        ok = state["checkouts"][it["i"] % len(state["checkouts"])]
        it["i"] += 1
        return ok

    with patch.object(canary, "rollout_history", side_effect=lambda: state["history"]), \
         patch.object(canary, "error_rates", side_effect=lambda: state["rates"]), \
         patch.object(canary, "rollback_deployment", side_effect=lambda d, n: state["rollback"]) as rb, \
         patch.object(canary, "wait_rolled_out", return_value="sample-app-new"), \
         patch.object(canary, "checkout_ok", side_effect=checkout), \
         patch.object(canary.time, "sleep"), \
         patch("agents.knowledge_base.log_incident") as log:
        state["rb"], state["log"] = rb, log
        yield state


# ---- diagnosis ----------------------------------------------------------------------------
def test_recent_bad_release_is_a_canary_regression(cluster):
    d = canary.diagnose(ALERT)
    assert d["root_cause"] == "canary_regression" and d["confidence"] == "high"
    assert d["rollback"]["from"]["revision"] == 5 and d["rollback"]["to"]["revision"] == 4
    refs = " ".join(e["reference"] for e in d["evidence"])
    assert "canary-1: 40%" in refs and "revision 5" in refs


def test_old_rollout_is_left_to_a_human(cluster):
    cluster["history"] = [rev(4, 600, "stable"), rev(5, 120, "canary-1", "0.4")]
    d = canary.diagnose(ALERT)
    assert d["root_cause"] == "error_rate_slo_breach" and d["rollback"] is None


def test_previous_release_also_failing_is_not_blamed_on_rollout(cluster):
    cluster["rates"] = {"canary-1": 0.4, "stable": 0.38}
    assert canary.diagnose(ALERT)["rollback"] is None


def test_single_revision_cannot_roll_back(cluster):
    cluster["history"] = [rev(1, 2, "canary-1", "0.4")]
    assert canary.diagnose(ALERT)["rollback"] is None


# ---- full remediation ---------------------------------------------------------------------
def test_pipeline_rolls_back_and_verifies(cluster):
    events = []
    result = canary.run_canary_pipeline(ALERT, on_stage=lambda s, st, d: events.append((s, st)))
    cluster["rb"].assert_called_once_with("sample-app", "default")
    assert result["patch"]["action"] == "rollback"
    assert "-env FAULTY_RATE=0.4" in result["patch"]["diff"] and "(revision 5)" in result["patch"]["diff"]
    assert result["validation_result"]["passed"] is True and "30/30" in result["validation_result"]["summary"]
    assert result["pr_result"] == {"pr_created": False, "reason": "rollback"}
    assert [s for s, st in events if st == "done"] == ["diagnosis", "patch", "validation", "deploy"]
    assert "rollback" not in result["diagnosis"], "raw rollout objects must not reach the API"
    cluster["log"].assert_called_once()


def test_verification_fails_when_errors_persist(cluster):
    cluster["checkouts"] = [True, False]  # 50% still failing
    result = canary.run_canary_pipeline(ALERT)
    assert result["validation_result"]["passed"] is False
    assert result["pr_result"]["reason"] == "needs_human"


def test_failed_rollback_is_reported(cluster):
    cluster["rollback"] = {"rolled_back": False, "output": "no rollout history found"}
    result = canary.run_canary_pipeline(ALERT)
    assert result["validation_result"]["passed"] is False
    assert "no rollout history" in result["patch"]["explanation"]


def test_no_recent_rollout_changes_nothing(cluster):
    """The running (stable) release itself is failing and nothing was rolled out recently."""
    cluster["history"] = [rev(4, 600, "stable")]
    cluster["rates"] = {"stable": 0.4}
    result = canary.run_canary_pipeline({"labels": {"alertname": "SampleAppHighErrorRate", "release": "stable"}})
    cluster["rb"].assert_not_called()
    assert result["patch"]["action"] == "none" and result["pr_result"]["reason"] == "needs_human"


def test_checkout_ok_treats_5xx_as_failure():
    err = type("ApiException", (Exception,), {})()
    err.status = 500
    with patch.object(live, "_pod_get", side_effect=err):
        assert canary.checkout_ok("p") is False
    with patch.object(live, "_pod_get", return_value="{}"):
        assert canary.checkout_ok("p") is True


# ---- webhook routing ------------------------------------------------------------------------
@pytest.fixture()
def fresh_store():
    s = RunStore()
    with patch("backend.routers.runs.store", s), patch("backend.live.store", s), patch("backend.main.run_store", s):
        yield s


def test_error_rate_alert_runs_canary_remediation(fresh_store):
    from backend import main

    result = {"diagnosis": {}, "patch": {}, "validation_result": {"passed": True}, "pr_result": {"reason": "rollback"}}
    with patch.object(main.canary, "run_canary_pipeline", return_value=result) as run, patch.object(main, "_run_alert_pipeline") as llm:
        main._process_alert({"labels": {"alertname": "SampleAppHighErrorRate", "release": "canary-1"}})
    run.assert_called_once()
    llm.assert_not_called()
    assert fresh_store.list()[0]["status"] == "passed"


def test_webhook_accepts_error_rate_alert():
    with patch("backend.main._process_alert") as process:
        r = client.post("/webhook/alert", json={"alerts": [{"status": "firing", "labels": {"alertname": "SampleAppHighErrorRate"}}]})
    assert r.json()["action"] == "pipeline_started"
    process.assert_called_once()


# ---- live canary --------------------------------------------------------------------------
def test_live_canary_ships_bad_release_and_hands_over(fresh_store):
    run = fresh_store.create("live", "Live")
    live.live_lock.acquire()
    alerts = iter(["inactive", "firing"] + ["firing"] * 20)

    def fake_wait(predicate, timeout_s, on_poll=None):
        if on_poll:
            on_poll()
        if live._waiting_run is not None:
            live.claim_waiting_run()
        return predicate()

    with patch.object(live, "alert_state", side_effect=lambda name=live.ALERT: next(alerts)), \
         patch.object(live, "_set_release") as ship, patch.object(canary, "wait_rolled_out", return_value="sample-app-new"), \
         patch.object(canary, "error_rates", return_value={"x": 0.4}), patch.object(live, "_wait", side_effect=fake_wait), \
         patch.object(live, "_traffic"):
        live.run_live_canary(run["id"])
    release = ship.call_args.args[0]
    assert release.startswith("canary-") and ship.call_args.args[1] == live.FAULTY_RATE
    got = fresh_store.get(run["id"])
    assert got["live"]["kind"] == "canary" and got["live"]["step"] == "alert_firing"
    assert not live.live_lock.locked()


def test_live_canary_endpoint(fresh_store):
    with patch.object(live, "run_live_canary", side_effect=lambda run_id: live.live_lock.release()) as run:
        r = client.post("/api/live/canary")
    assert r.status_code == 202
    run.assert_called_once()


def test_live_canary_endpoint_busy(fresh_store):
    with pipeline_lock:
        assert client.post("/api/live/canary").status_code == 409


def test_kill_switch_stops_the_rollback(cluster):
    from agents import governance

    with patch.object(governance, "is_paused", return_value=True):
        result = canary.run_canary_pipeline(ALERT)
    cluster["rb"].assert_not_called()
    assert result["pr_result"]["reason"] == "autonomy_paused"
    assert "kill switch" in result["patch"]["explanation"]



def test_repeated_alert_after_rollback_does_not_undo_the_rollback(cluster):
    """Regression (seen on the kind cluster): Alertmanager re-sent the still-firing alert for
    release canary-1 after GhostOps had rolled it back; the pipeline saw its own rollback as 'a
    recent rollout' and undid it, re-deploying the bad release."""
    cluster["history"] = [rev(4, 60, "stable"), rev(5, 3, "canary-1", "0.4"), rev(6, 1, "stable")]  # 6 = the rollback
    cluster["rates"] = {"canary-1": 0.4, "stable": 0.0}  # the bad release's errors are still in the 1m window
    result = canary.run_canary_pipeline(ALERT)
    cluster["rb"].assert_not_called()
    assert result["diagnosis"]["root_cause"] == "already_remediated"
    assert result["pr_result"]["reason"] == "already_remediated"
