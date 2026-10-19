"""Tests for the run registry and the /api/simulate, /api/runs and SSE endpoints."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.runs import RunStore, pipeline_lock, store

client = TestClient(app)

RESULT_PASS = {"diagnosis": {}, "patch": {}, "validation_result": {"passed": True}, "pr_result": {}}
RESULT_FAIL = {"diagnosis": {}, "patch": {}, "validation_result": {"passed": False}, "pr_result": {}}


def _fake_pipeline(result):
    def run(context, on_stage=None):
        for stage in ("diagnosis", "patch", "validation", "deploy"):
            on_stage(stage, "running", {})
            on_stage(stage, "done", {"stage": stage})
        return result
    return run


def _wait_for_end(run_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"passed", "failed", "error"}:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


@pytest.fixture(autouse=True)
def _scenarios():
    scenarios = {"c4": ("CI test failure", lambda: {"alert_type": "ci_failure"})}
    with patch("backend.routers.runs.SCENARIOS", scenarios):
        yield
    # A test must never leave the shared lock held.
    deadline = time.time() + 5
    while pipeline_lock.locked() and time.time() < deadline:
        time.sleep(0.02)
    assert not pipeline_lock.locked()


# ---- RunStore -----------------------------------------------------------------
def test_store_create_has_pending_stages():
    run = RunStore().create("c4", "CI")
    assert run["status"] == "running"
    assert all(s["status"] == "pending" for s in run["stages"].values())


def test_store_update_bumps_version():
    s = RunStore()
    run = s.create("c4", "CI")
    s.update_stage(run["id"], "patch", "done", {"diff": "x"})
    got = s.get(run["id"])
    assert got["version"] == 1 and got["stages"]["patch"] == {"status": "done", "data": {"diff": "x"}}


def test_store_ignores_unknown_stage_and_run():
    s = RunStore()
    run = s.create("c4", "CI")
    s.update_stage(run["id"], "bogus", "done")
    s.update_stage("missing", "patch", "done")
    assert s.get(run["id"])["version"] == 0


def test_store_finish_closes_running_stages():
    s = RunStore()
    run = s.create("c4", "CI")
    s.update_stage(run["id"], "validation", "running")
    s.finish(run["id"], "error", error="boom")
    got = s.get(run["id"])
    assert got["status"] == "error" and got["error"] == "boom"
    assert got["stages"]["validation"]["status"] == "error"


def test_store_finish_rejects_non_terminal():
    s = RunStore()
    with pytest.raises(ValueError):
        s.finish(s.create("c4", "CI")["id"], "running")


def test_store_evicts_oldest():
    s = RunStore(max_runs=2)
    first = s.create("a", "A")
    s.create("b", "B")
    s.create("c", "C")
    assert s.get(first["id"]) is None
    assert [r["scenario"] for r in s.list()] == ["c", "b"]


def test_store_returns_copies():
    s = RunStore()
    run = s.create("c4", "CI")
    s.get(run["id"])["status"] = "tampered"
    assert s.get(run["id"])["status"] == "running"


# ---- endpoints ------------------------------------------------------------------
def test_status_endpoint():
    data = client.get("/api/status").json()
    assert set(data) == {"mock_llm", "pipeline_busy"}


def test_scenarios_endpoint():
    assert client.get("/api/scenarios").json() == [{"id": "c4", "title": "CI test failure"}]


def test_simulate_unknown_scenario_404():
    assert client.post("/api/simulate", json={"scenario": "zz"}).status_code == 404


def test_simulate_requires_scenario():
    assert client.post("/api/simulate", json={}).status_code == 422


def test_simulate_busy_409():
    with pipeline_lock:
        response = client.post("/api/simulate", json={"scenario": "c4"})
    assert response.status_code == 409


def test_simulate_runs_to_passed():
    with patch("backend.routers.runs.run_pipeline", side_effect=_fake_pipeline(RESULT_PASS)):
        response = client.post("/api/simulate", json={"scenario": "c4"})
        assert response.status_code == 202
        run = _wait_for_end(response.json()["run_id"])
    assert run["status"] == "passed"
    assert all(s["status"] == "done" for s in run["stages"].values())
    assert run["result"] == RESULT_PASS


def test_simulate_failed_validation():
    with patch("backend.routers.runs.run_pipeline", side_effect=_fake_pipeline(RESULT_FAIL)):
        run = _wait_for_end(client.post("/api/simulate", json={"scenario": "c4"}).json()["run_id"])
    assert run["status"] == "failed"


def test_simulate_pipeline_exception_releases_lock():
    with patch("backend.routers.runs.run_pipeline", side_effect=RuntimeError("429 rate limited")):
        run = _wait_for_end(client.post("/api/simulate", json={"scenario": "c4"}).json()["run_id"])
    assert run["status"] == "error" and "429" in run["error"]


def test_runs_list_includes_new_run():
    with patch("backend.routers.runs.run_pipeline", side_effect=_fake_pipeline(RESULT_PASS)):
        run_id = client.post("/api/simulate", json={"scenario": "c4"}).json()["run_id"]
        _wait_for_end(run_id)
    assert client.get("/api/runs").json()[0]["id"] == run_id


def test_run_not_found():
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/events").status_code == 404


def test_events_stream_ends_with_final_state():
    with patch("backend.routers.runs.run_pipeline", side_effect=_fake_pipeline(RESULT_PASS)):
        run_id = client.post("/api/simulate", json={"scenario": "c4"}).json()["run_id"]
        _wait_for_end(run_id)
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        messages = [json.loads(line[len("data: "):]) for line in response.iter_lines() if line.startswith("data: ")]
    assert messages[-1]["status"] == "passed"


def test_events_stream_follows_live_run():
    run = store.create("c4", "CI")
    store.finish(run["id"], "failed", result=RESULT_FAIL)
    with client.stream("GET", f"/api/runs/{run['id']}/events") as response:
        lines = [line for line in response.iter_lines() if line.startswith("data: ")]
    assert len(lines) == 1 and json.loads(lines[0][6:])["status"] == "failed"
