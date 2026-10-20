"""Chaos-verified fixes (Chaos Mesh, pods and Prometheus faked)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agents import governance, knowledge_base
from backend import chaos_verify as cv
from backend import verification

MiB = 1048576


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "SETTLE_S", 0)
    with patch.object(knowledge_base, "_DB_PATH", tmp_path / "kb.db"):
        yield


@pytest.fixture()
def cluster(monkeypatch):
    monkeypatch.setenv("CHAOS_VERIFY", "1")
    state = {"memory": 40 * MiB, "restarts": 0, "deleted": [], "signal": {"ok": True, "value": 0.3, "alert": "inactive"}, "checkout": True}
    api = MagicMock()
    with patch.object(cv.live, "_core", return_value=MagicMock(CustomObjectsApi=MagicMock(return_value=api))), \
         patch.object(cv.live, "_pod_name", return_value="sample-app-old"), \
         patch.object(cv, "_wait_replaced", return_value="sample-app-new"), \
         patch.object(cv.live, "_pod_get", return_value="{}"), \
         patch.object(cv.live, "_prom", side_effect=lambda q: 128 * MiB if "limit" in q else state["memory"]), \
         patch.object(cv, "_restarts", side_effect=lambda pod: state["restarts"]), \
         patch.object(cv, "_delete_chaos", side_effect=lambda name: state["deleted"].append(name)), \
         patch("backend.canary.checkout_ok", side_effect=lambda pod: state["checkout"]), \
         patch.object(verification, "_probe", side_effect=lambda a: state["signal"]):
        state["api"] = api
        yield state


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("CHAOS_VERIFY", raising=False)
    assert cv.run("memory_high")["verdict"] == "skipped"


def test_fixed_memory_workload_survives(cluster):
    result = cv.run("memory_high", run_ref="r")
    assert result["verdict"] == "survived"
    assert [s["step"] for s in result["steps"]] == ["pod-kill", "re-trigger", "signal"]
    body = cluster["api"].create_namespaced_custom_object.call_args.args[4]
    assert body["kind"] == "PodChaos" and body["spec"]["action"] == "pod-kill"
    assert body["spec"]["selector"]["expressionSelectors"] == [{"key": "track", "operator": "DoesNotExist"}], "never the canary"
    assert cluster["deleted"], "the PodChaos object is cleaned up"


def test_still_leaking_workload_fails(cluster):
    cluster["memory"] = 115 * MiB
    result = cv.run("memory_high")
    assert result["verdict"] == "failed" and "90% of limit" in result["summary"]


def test_oom_restart_fails(cluster):
    cluster["restarts"] = 1
    assert cv.run("memory_high")["verdict"] == "failed"


def test_error_workload_checked_with_checkout_burst(cluster):
    assert cv.run("error_rate_high")["verdict"] == "survived"
    cluster["checkout"] = False
    assert cv.run("error_rate_high")["verdict"] == "failed"


def test_kill_switch_skips_chaos(cluster):
    with patch.object(governance, "is_paused", return_value=True):
        assert cv.run("memory_high")["verdict"] == "skipped"
    cluster["api"].create_namespaced_custom_object.assert_not_called()


def test_errors_are_reported_and_cleaned(cluster):
    with patch.object(cv, "_wait_replaced", side_effect=RuntimeError("pod not replaced")):
        result = cv.run("memory_high")
    assert result["verdict"] == "error" and "not replaced" in result["summary"] and cluster["deleted"]


# ---- in post-merge verification ------------------------------------------------------------
T0 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def _resolve_window(chaos_result):
    verification.start(kind="rollback", alert_type="memory_high", started_at=T0, deployed_at=T0, window_s=60)
    with patch.object(verification, "_probe", return_value={"ok": True}), patch.object(cv, "enabled", return_value=True), \
         patch.object(cv, "run", return_value=chaos_result):
        return verification.tick(T0 + timedelta(seconds=61))[0]


def test_resolution_requires_surviving_chaos():
    done = _resolve_window({"verdict": "survived", "summary": "ok"})
    assert done["state"] == "resolved" and "chaos-verified" in done["note"]
    assert verification.listing()[0]["chaos"]["verdict"] == "survived"


def test_failed_chaos_reopens():
    done = _resolve_window({"verdict": "failed", "summary": "memory at 90% of limit"})
    assert done["state"] == "reopened" and "failed chaos verification" in done["note"]


def test_old_verifications_table_gets_chaos_column(tmp_path):
    path = knowledge_base._DB_PATH
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE verifications (id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT, kind TEXT NOT NULL, alert_type TEXT NOT NULL, "
              "reference TEXT, started_at TEXT NOT NULL, deployed_at TEXT NOT NULL, window_s INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'watching', "
              "samples TEXT NOT NULL DEFAULT '[]', verdict_at TEXT, mttr_s REAL, note TEXT)")
    c.commit()
    c.close()
    verification.listing()
    assert "chaos" in {r[1] for r in sqlite3.connect(str(path)).execute("PRAGMA table_info(verifications)")}
