"""Start demo pipeline runs and follow them live (polling or Server-Sent Events)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.config import MOCK_LLM
from agents.crew import run_pipeline
from agents.scenarios import SCENARIOS
from backend import live
from backend.runs import TERMINAL, pipeline_lock, store

router = APIRouter(prefix="/api", tags=["runs"])
logger = logging.getLogger("ghostops.backend.routers.runs")

SSE_POLL_SECONDS = 0.3


class SimulateRequest(BaseModel):
    scenario: str


@router.get("/status")
async def status() -> dict:
    """What the dashboard needs to know before offering the simulate button."""
    return {"mock_llm": MOCK_LLM, "pipeline_busy": pipeline_lock.locked()}


@router.get("/scenarios")
async def scenarios() -> list[dict]:
    return [{"id": key, "title": title} for key, (title, _) in SCENARIOS.items()]


def _execute(run_id: str, scenario: str) -> None:
    """Run one scenario through the pipeline; always releases the pipeline lock."""
    try:
        _, build_context = SCENARIOS[scenario]
        context = build_context()
        # Source files stay server-side; the dashboard only needs the telemetry.
        store.set_context(
            run_id,
            {
                "alert_type": context.get("alert_type"),
                "metrics": context.get("metrics", {}),
                "logs": list(context.get("logs", []))[-20:],
            },
        )
        result = run_pipeline(
            context,
            on_stage=lambda stage, state, data: store.update_stage(run_id, stage, state, data),
        )
        passed = result.get("validation_result", {}).get("passed") is True
        store.finish(run_id, "passed" if passed else "failed", result=result)
    except Exception as exc:  # noqa: BLE001
        logger.error("Simulated run %s failed: %s", run_id, exc, exc_info=True)
        store.finish(run_id, "error", error=str(exc))
    finally:
        pipeline_lock.release()


@router.post("/simulate", status_code=202)
async def simulate(body: SimulateRequest) -> dict:
    """Start a demo scenario in the background and return its run id."""
    if body.scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario: {body.scenario}")
    if not pipeline_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a pipeline is already running")
    try:
        run = store.create(body.scenario, SCENARIOS[body.scenario][0])
        threading.Thread(target=_execute, args=(run["id"], body.scenario), daemon=True).start()
    except Exception:
        pipeline_lock.release()
        raise
    return {"run_id": run["id"], "mock_llm": MOCK_LLM}


@router.get("/live/status")
def live_status() -> dict:
    """Whether a live cluster incident can be started (sample-app pod and Prometheus reachable)."""
    try:
        pod = live._pod_name()
        alert = live.alert_state()
        return {"ready": True, "pod": pod, "alert": alert, "busy": live.live_lock.locked()}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "error": str(exc), "busy": live.live_lock.locked()}


@router.post("/live/leak", status_code=202)
async def live_leak() -> dict:
    """Leak memory in the real sample-app and follow the real alert into the pipeline."""
    if pipeline_lock.locked():
        raise HTTPException(status_code=409, detail="a pipeline is already running")
    if not live.live_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a live incident is already in progress")
    try:
        run = store.create("live", "Live cluster: memory leak in sample-app")
        threading.Thread(target=live.run_live_leak, args=(run["id"],), daemon=True).start()
    except Exception:
        live.live_lock.release()
        raise
    return {"run_id": run["id"]}


ACTIVITY_LINGER_S = 10
STAGE_ACTIVITY = {"diagnosis": "diagnosing", "patch": "patching", "validation": "validating", "deploy": "validating"}


@router.get("/activity")
async def activity() -> dict:
    """What GhostOps is doing right now, for the hero hologram and the background."""
    run = store.latest()
    if run is None:
        return {"state": "idle"}
    base = {"run_id": run["id"], "title": run["title"], "status": run["status"]}
    if run["status"] == "running":
        running = next((s for s, v in run["stages"].items() if v["status"] == "running"), None)
        started = any(v["status"] != "pending" for v in run["stages"].values())
        if running:
            return {**base, "state": STAGE_ACTIVITY[running]}
        live_step = (run.get("live") or {}).get("step")
        if not started and live_step in (None, "starting", "resetting", "leaking"):
            return {**base, "state": "preparing"}
        return {**base, "state": "incident"}
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(run["updated_at"])).total_seconds()
    if age < ACTIVITY_LINGER_S:
        return {**base, "state": "healed" if run["status"] == "passed" else "failed"}
    return {**base, "state": "idle"}


@router.get("/runs")
async def list_runs() -> list[dict]:
    return store.list()


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


async def _run_events(run_id: str) -> AsyncIterator[str]:
    """Send every version of the run in order, so no stage is ever skipped."""
    sent = 0
    while True:
        versions = store.history(run_id, since=sent)
        if not versions and store.get(run_id) is None:
            return
        for run in versions:
            sent += 1
            yield f"data: {json.dumps(run)}\n\n"
            if run["status"] in TERMINAL:
                return
        await asyncio.sleep(SSE_POLL_SECONDS)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """Server-Sent Events: one message per change, closing when the run ends."""
    if store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return StreamingResponse(
        _run_events(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
