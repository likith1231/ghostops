"""Run the deterministic detectors on supplied telemetry, and list the use-case catalogue."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agents import detectors as d
from agents import shield

router = APIRouter(prefix="/api", tags=["detect"])

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class AuthEvent(BaseModel):
    ts: float
    service: str
    line: str


class Pod(BaseModel):
    waiting_reason: str = ""
    last_terminated_reason: str = ""
    image: str = ""


class DetectRequest(BaseModel):
    """Every field is optional: each detector runs only when its inputs are present."""

    logs: list[str] = Field(default_factory=list)
    auth_events: list[AuthEvent] = Field(default_factory=list)
    log_rate_history: list[float] = Field(default_factory=list)
    log_rate_current: float | None = None
    memory_samples: list[float] = Field(default_factory=list)
    memory_limit: float | None = None
    cpu_throttled_ratio: float | None = None
    cpu_usage_ratio: float | None = None
    disk_io_utilisation: list[float] = Field(default_factory=list)
    p99_seconds: float | None = None
    slo_seconds: float = 0.5
    errors: float | None = None
    requests: float | None = None
    canary_error_rate: float | None = None
    stable_error_rate: float | None = None
    declared_config: dict[str, str] | None = None
    live_config: dict[str, str] | None = None
    pods: list[Pod] = Field(default_factory=list)
    replicas: list[int] = Field(default_factory=list)


@router.post("/detect")
async def detect(body: DetectRequest) -> dict:
    found = list(d.run_log_detectors(body.logs)) if body.logs else []
    if body.auth_events:
        found.append(d.detect_auth_burst([(e.ts, e.service, e.line) for e in body.auth_events]))
    if body.log_rate_current is not None:
        found.append(d.detect_log_spike(body.log_rate_history, body.log_rate_current))
    if body.memory_samples:
        found.append(d.detect_memory_leak(body.memory_samples, body.memory_limit))
    if body.cpu_throttled_ratio is not None and body.cpu_usage_ratio is not None:
        found.append(d.detect_cpu_throttling(body.cpu_throttled_ratio, body.cpu_usage_ratio))
    if body.disk_io_utilisation:
        found.append(d.detect_disk_saturation(body.disk_io_utilisation))
    if body.p99_seconds is not None:
        found.append(d.detect_latency_slo(body.p99_seconds, body.slo_seconds))
    if body.errors is not None and body.requests is not None:
        found.append(d.detect_error_rate_slo(body.errors, body.requests))
    if body.canary_error_rate is not None and body.stable_error_rate is not None:
        found.append(d.detect_canary_regression(body.canary_error_rate, body.stable_error_rate))
    if body.declared_config is not None and body.live_config is not None:
        found.append(d.detect_config_drift(body.declared_config, body.live_config))
    if body.pods:
        pods = [p.model_dump() for p in body.pods]
        found += [d.detect_crashloop(pods), d.detect_image_pull_failure(pods)]
    if body.replicas:
        found.append(d.detect_hpa_flapping(body.replicas))

    detections = sorted((x for x in found if x), key=lambda x: (_SEVERITY_ORDER[x.severity], -x.confidence))
    _, report = shield.sanitize({"logs": body.logs})
    return {"detections": [x.to_dict() for x in detections], "shield": report}


@router.get("/usecases")
async def usecases() -> list[dict]:
    return list(d.USE_CASES)
