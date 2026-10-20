"""
GhostOps Backend — FastAPI Ingestion Service
=============================================

Receives Alertmanager webhook payloads, enriches them with live Kubernetes
logs and Prometheus metrics, builds a ``failure_context`` dict, and runs
it through the CrewAI agent pipeline.

Run with::

    cd ~/ghostops
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Path setup — ensure the project root is on sys.path so we can import the
# ``agents`` package (a sibling directory of ``backend/``).
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

# Now that the path is set up, import the agent pipeline.
from agents.crew import run_pipeline  # noqa: E402
from agents import correlation, governance  # noqa: E402
from agents.knowledge_base import init_db, past_incident_context  # noqa: E402

# Initialise the SQLite knowledge base (creates DB + table if needed).
init_db()

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------
PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
SAMPLE_APP_SOURCE: str = os.getenv(
    "SAMPLE_APP_SOURCE",
    str(_PROJECT_ROOT / "sample-app" / "app.py"),
)
# The app's tests: the Validation Officer runs them against the patched code.
SAMPLE_APP_TESTS: str = os.getenv(
    "SAMPLE_APP_TESTS",
    str(_PROJECT_ROOT / "sample-app" / "test_app.py"),
)
POD_LOG_LINES: int = int(os.getenv("POD_LOG_LINES", "20"))
# A deeper slice of the access log, for counterfactual replay of the incident's traffic.
REPLAY_LOG_LINES: int = int(os.getenv("REPLAY_LOG_LINES", "500"))
POD_NAMESPACE: str = os.getenv("POD_NAMESPACE", "default")
MEMORY_HISTORY_S: int = int(os.getenv("MEMORY_HISTORY_S", "300"))
# B1 (memory leak -> AI fix) and C1 (canary regression -> rollback).
HANDLED_ALERTS = {"SampleAppHighMemory", "SampleAppHighErrorRate"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("ghostops.backend")

# ---------------------------------------------------------------------------
# OpenTelemetry Tracing
# ---------------------------------------------------------------------------
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
# gRPC exporter expects host:port, not a URL with a scheme. Strip http(s):// if present.
if "://" in OTEL_ENDPOINT:
    OTEL_ENDPOINT = OTEL_ENDPOINT.split("://")[-1]

resource = Resource.create({SERVICE_NAME: "ghostops-backend"})
provider = TracerProvider(resource=resource)
try:
    # Set a short flush interval (1s) for local debugging so spans appear quickly
    processor = BatchSpanProcessor(
        OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True),
        schedule_delay_millis=1000,
    )
    provider.add_span_processor(processor)
    logger.info("OpenTelemetry exporter configured with gRPC endpoint: %s", OTEL_ENDPOINT)
except Exception as e:
    logger.warning("Failed to initialize OTLP exporter, tracing will be disabled. Error: %s", e)

trace.set_tracer_provider(provider)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start the watcher that verifies deployed fixes in production (post-merge verification, MTTR)."""
    if os.getenv("GHOSTOPS_DISABLE_WATCHER", "") not in ("1", "true", "yes"):
        from backend import verification

        verification.run_watcher()
    yield


app = FastAPI(
    title="GhostOps Ingestion Service",
    description="Receives Alertmanager webhooks and feeds the AI agent pipeline.",
    version="0.1.0",
    lifespan=_lifespan,
)

FastAPIInstrumentor.instrument_app(app)

# The web dashboard (web/) runs on its own origin; Grafana already owns :3000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("GHOSTOPS_CORS_ORIGINS", "http://localhost:3100,http://127.0.0.1:3100").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# Kubernetes log fetcher
# ---------------------------------------------------------------------------
def _fetch_pod_logs(pod_name: str, namespace: str = POD_NAMESPACE, tail: int | None = None) -> list[str]:
    """Fetch the last *POD_LOG_LINES* lines from *pod_name* via the
    Kubernetes Python client.

    Returns an empty list (with a logged warning) if the API call fails —
    partial context is better than crashing the whole pipeline.
    """
    try:
        from kubernetes import client, config

        # Try in-cluster config first (if this service ever runs as a pod),
        # then fall back to the local kubeconfig (~/.kube/config).
        try:
            config.load_incluster_config()
        except config.config_exception.ConfigException:
            config.load_kube_config()

        v1 = client.CoreV1Api()
        log_text: str = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail or POD_LOG_LINES,
        )
        lines = [line for line in log_text.strip().splitlines() if line]
        logger.info(
            "Fetched %d log lines from pod %s/%s", len(lines), namespace, pod_name
        )
        return lines

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to fetch logs for pod %s/%s: %s", namespace, pod_name, exc
        )
        return [f"[ERROR] Could not fetch pod logs: {exc}"]


# ---------------------------------------------------------------------------
# Prometheus metric fetcher
# ---------------------------------------------------------------------------
def _query_prometheus_memory(pod_name: str) -> dict[str, Any]:
    """Query Prometheus for ``container_memory_working_set_bytes`` for the
    given pod and return a metrics dict matching the ``failure_context``
    shape expected by ``run_pipeline()``.

    Uses an instant query by default.  Falls back gracefully on error.
    """
    metrics: dict[str, Any] = {}

    # --- Working-set bytes (instant) ---
    query_working_set = (
        f'container_memory_working_set_bytes{{pod="{pod_name}"}}'
    )
    # --- Memory limit bytes (instant) ---
    query_limit = (
        f'container_spec_memory_limit_bytes{{pod="{pod_name}"}}'
    )

    for metric_name, query in [
        ("container_memory_working_set_bytes", query_working_set),
        ("container_spec_memory_limit_bytes", query_limit),
    ]:
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    # Collect all matching time-series as a list of
                    # {ts, value} dicts (same shape as the sample_b1 in
                    # crew.py).
                    metric_values = []
                    for r in results:
                        ts, val = r.get("value", [None, None])
                        if ts is not None and val is not None:
                            metric_values.append(
                                {"ts": str(ts), "value": float(val)}
                            )
                    if metric_values:
                        metrics[metric_name] = metric_values
                        logger.info(
                            "Prometheus: %s → %d data point(s)",
                            metric_name,
                            len(metric_values),
                        )
                    else:
                        logger.warning("Prometheus: %s returned no values", metric_name)
                else:
                    logger.warning("Prometheus: %s returned empty result set", metric_name)
            else:
                logger.warning(
                    "Prometheus query failed for %s: %s",
                    metric_name,
                    data.get("error", "unknown error"),
                )

        except Exception as exc:  # noqa: BLE001
            logger.error("Prometheus query error for %s: %s", metric_name, exc)

    # The last few minutes of the working set, so the growth itself is evidence
    # (an instant query is a single point). Falls back to the instant value.
    try:
        now = time.time()
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={"query": query_working_set, "start": now - MEMORY_HISTORY_S, "end": now, "step": 15},
            timeout=10,
        )
        resp.raise_for_status()
        series = resp.json().get("data", {}).get("result", [])
        if series:
            values = [{"ts": str(ts), "value": float(v)} for ts, v in series[0].get("values", [])]
            if len(values) > 1:
                metrics["container_memory_working_set_bytes"] = values
    except Exception as exc:  # noqa: BLE001
        logger.warning("Prometheus range query failed, keeping the instant value: %s", exc)

    return metrics


# ---------------------------------------------------------------------------
# Source file reader
# ---------------------------------------------------------------------------
def _read_source_file(path: str) -> dict[str, str]:
    """Read the sample-app source file from disk.

    Returns a ``{path: content}`` dict matching the ``source_files`` shape in
    ``failure_context``.  The path is relative to the repo root (e.g.
    ``sample-app/app.py``) so the OPA allowlist and the PR target the real file.
    """
    try:
        resolved = Path(path).resolve()
        content = resolved.read_text(encoding="utf-8")
        try:
            key = str(resolved.relative_to(_PROJECT_ROOT))
        except ValueError:
            key = resolved.name
        logger.info("Read source file: %s (%d bytes)", key, len(content))
        return {key: content}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read source file %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Pipeline runner (background)
# ---------------------------------------------------------------------------
# One incident at a time (shared with /api/simulate): the deployer switches git branches to open the PR.
from backend import canary, live  # noqa: E402
from backend.runs import pipeline_lock as _pipeline_lock  # noqa: E402
from backend.runs import store as run_store  # noqa: E402


def _process_alert(alert: dict[str, Any]) -> None:
    """Gather context for one firing alert and run the agent pipeline.

    Runs as a background task after the webhook has answered, so a pipeline
    that takes minutes never makes Alertmanager time out and resend the alert.
    """
    labels = alert.get("labels", {})
    pod_name = labels.get("pod", "")
    if not _pipeline_lock.acquire(blocking=False):
        logger.warning("Pipeline already running — skipping alert for pod=%s", pod_name)
        return
    try:
        if alert.get("labels", {}).get("alertname") == canary.ALERT:
            _run_canary_alert(alert)
        else:
            _run_alert_pipeline(alert, pod_name)
    finally:
        _pipeline_lock.release()


def _run_canary_alert(alert: dict[str, Any]) -> None:
    """Use case C1: diagnose a release regression from cluster state, roll back, verify."""
    labels = alert.get("labels", {})
    run_id = live.claim_waiting_run() or run_store.create(
        "live", f"Live alert: {canary.ALERT} (release {labels.get('release', '?')})"
    )["id"]
    if alert.get("_incident"):
        correlation.attach_run(alert["_incident"], run_id)
    run_store.set_context(run_id, {"alert_type": "error_rate_high", "metrics": {}, "logs": [alert.get("annotations", {}).get("summary", "")]})
    try:
        result = canary.run_canary_pipeline(
            alert, on_stage=lambda stage, state, data: run_store.update_stage(run_id, stage, state, data), run_ref=run_id
        )
        passed = result["validation_result"].get("passed") is True
        run_store.finish(run_id, "passed" if passed else "failed", result=result)
    except Exception as exc:  # noqa: BLE001
        logger.error("Canary remediation failed: %s", exc, exc_info=True)
        run_store.finish(run_id, "error", error=f"Remediation failed: {exc}")


def _run_alert_pipeline(alert: dict[str, Any], pod_name: str) -> None:
    logger.info(
        "Processing SampleAppHighMemory alert for pod=%s (started=%s)",
        pod_name,
        alert.get("startsAt", "unknown"),
    )

    # ---- Step A: Fetch pod logs ------------------------------------
    logs = _fetch_pod_logs(pod_name) if pod_name else [
        "[WARN] No pod name in alert labels — skipping log fetch"
    ]

    # ---- Step B: Query Prometheus for memory metrics ---------------
    metrics = _query_prometheus_memory(pod_name) if pod_name else {}

    # ---- Step C: Build failure_context -----------------------------
    source_files = _read_source_file(SAMPLE_APP_SOURCE)
    source_files.update(_read_source_file(SAMPLE_APP_TESTS))

    failure_context: dict[str, Any] = {
        "alert_type": "memory_high",
        "logs": logs,
        "metrics": metrics,
        "source_files": source_files,
        # What happened last time: diagnoses, fixes and whether a human merged them.
        "past_incidents": past_incident_context("memory_high"),
    }

    logger.info(
        "Built failure_context: %d log lines, %d metric keys, %d source files",
        len(logs),
        len(metrics),
        len(source_files),
    )

    # ---- Step D: Run the agent pipeline, as a run the dashboard follows ----
    # A live run started from the dashboard is waiting for exactly this alert;
    # otherwise (Chaos Mesh, the demo script, a real leak) the alert gets its own run.
    run_id = live.claim_waiting_run() or run_store.create(
        "live", f"Live alert: {alert.get('labels', {}).get('alertname', 'alert')} on {pod_name or 'cluster'}"
    )["id"]
    failure_context["run_ref"] = run_id
    failure_context["started_at"] = alert.get("startsAt")
    if pod_name:
        failure_context["replay_logs"] = _fetch_pod_logs(pod_name, tail=REPLAY_LOG_LINES)
    if alert.get("_incident"):
        correlation.attach_run(alert["_incident"], run_id)
    run_store.set_context(
        run_id,
        {"alert_type": failure_context["alert_type"], "metrics": metrics, "logs": logs[-20:]},
    )
    try:
        from backend import fix_canary

        pipeline_result = run_pipeline(
            failure_context,
            on_stage=lambda stage, state, data: run_store.update_stage(run_id, stage, state, data),
            # Real alerts canary the validated fix on the cluster before it is delivered.
            fix_canary=fix_canary.run if os.getenv("FIX_CANARY", "1") in ("1", "true", "yes") else None,
        )
        passed = pipeline_result.get("validation_result", {}).get("passed") is True
        run_store.finish(run_id, "passed" if passed else "failed", result=pipeline_result)

        # ---- Step E: Log the result clearly ------------------------
        logger.info("=" * 72)
        logger.info("PIPELINE RESULT for pod=%s", pod_name)
        logger.info("=" * 72)
        logger.info(
            "Diagnosis: %s",
            json.dumps(pipeline_result.get("diagnosis", {}), indent=2),
        )
        logger.info(
            "Patch diff:\n%s",
            pipeline_result.get("patch", {}).get("diff", "(none)"),
        )
        logger.info(
            "Validation passed: %s",
            pipeline_result.get("validation_result", {}).get("passed"),
        )
        logger.info("=" * 72)

    except Exception as exc:  # noqa: BLE001
        logger.error("Pipeline failed for pod=%s: %s", pod_name, exc, exc_info=True)
        err_str = str(exc)

        # Detect Litellm / Gemini quota limits
        if "RateLimitError" in str(type(exc)) or "429" in err_str or "Quota" in err_str:
            clean_error = "API Quota Exhausted / Rate Limited"
        else:
            clean_error = f"Pipeline Execution Failed: {err_str}"
        run_store.finish(run_id, "error", error=clean_error)


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------
@app.post("/webhook/alert")
async def receive_alert(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Receive an Alertmanager webhook payload and process each firing
    ``SampleAppHighMemory`` alert through the agent pipeline.

    Alertmanager payload shape (v4)::

        {
          "version": "4",
          "status": "firing",
          "alerts": [
            {
              "status": "firing",
              "labels": {
                "alertname": "SampleAppHighMemory",
                "pod": "sample-app-xxx-yyy",
                "severity": "critical",
                "component": "sample-app"
              },
              "annotations": {
                "summary": "...",
                "description": "..."
              },
              "startsAt": "2026-08-08T...",
              "endsAt": "0001-01-01T00:00:00Z",
              ...
            }
          ]
        }
    """
    try:
        payload = await request.json()
    except Exception:
        logger.error("Failed to parse webhook JSON body")
        return JSONResponse(
            status_code=400,
            content={"received": False, "error": "invalid JSON"},
        )

    alerts = payload.get("alerts", [])
    logger.info(
        "Received Alertmanager webhook: %d alert(s), group status=%s",
        len(alerts),
        payload.get("status", "unknown"),
    )

    results: list[dict[str, Any]] = []

    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        alert_name = labels.get("alertname", "")
        alert_status = alert.get("status", "")
        pod_name = labels.get("pod", "")

        # ---- A resolved alert closes its incident -----------------------
        if alert_name in HANDLED_ALERTS and alert_status == "resolved":
            closed = correlation.resolve_alert(labels)
            results.append({"alert": alert_name, "status": alert_status, "action": "resolved", "incident": closed})
            continue

        # ---- Skip non-actionable alerts --------------------------------
        if alert_name not in HANDLED_ALERTS or alert_status != "firing":
            logger.info(
                "Ignoring alert: name=%s status=%s", alert_name, alert_status
            )
            results.append({
                "alert": alert_name,
                "status": alert_status,
                "action": "ignored",
            })
            continue

        # ---- Repeats and related alerts join the open incident ----------
        verdict = correlation.classify_alert(labels)
        if verdict["action"] != "new":
            governance.record(
                "dedupe", verdict["action"], f"{alert_name} joined incident #{verdict['incident']['id']}",
                run_ref=verdict["incident"].get("run_ref"), evidence={"incident": verdict["incident"]},
            )
            results.append({"alert": alert_name, "pod": pod_name, "action": verdict["action"], "incident": verdict["incident"]["id"]})
            continue

        alert["_incident"] = verdict["incident"]["id"]
        background_tasks.add_task(_process_alert, alert)
        results.append({"alert": alert_name, "pod": pod_name, "action": "pipeline_started", "incident": verdict["incident"]["id"]})

    # The pipeline runs after this response. If there was exactly one alert,
    # return it at the top level for convenience; otherwise wrap in a list.
    if len(results) == 1:
        return JSONResponse(
            status_code=200,
            content={"received": True, **results[0]},
        )

    return JSONResponse(
        status_code=200,
        content={"received": True, "results": results},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    """Simple health-check endpoint."""
    return {"status": "ok", "service": "ghostops-backend"}


# ---------------------------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------------------------
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST
import sqlite3

INCIDENTS_TOTAL = Gauge("ghostops_incidents_total", "Total number of incidents")
INCIDENTS_PASSED = Gauge("ghostops_incidents_passed", "Number of passed incidents")
INCIDENTS_FAILED = Gauge("ghostops_incidents_failed", "Number of failed incidents")
PRS_CREATED = Gauge("ghostops_prs_created", "Number of PRs created")

@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    from agents.knowledge_base import _DB_PATH
    if _DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            row = conn.execute(
                """\
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN validation_passed = 1 THEN 1 ELSE 0 END) AS passed,
                    SUM(CASE WHEN validation_passed = 0 THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN pr_created = 1 THEN 1 ELSE 0 END) AS prs_created
                FROM incidents
                """
            ).fetchone()
            conn.close()

            if row:
                total, passed, failed, prs_created = row
                INCIDENTS_TOTAL.set(total or 0)
                INCIDENTS_PASSED.set(passed or 0)
                INCIDENTS_FAILED.set(failed or 0)
                PRS_CREATED.set(prs_created or 0)
        except Exception as exc:
            logger.error("Failed to update Prometheus metrics: %s", exc)
            
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Incident API Router
# ---------------------------------------------------------------------------
from backend.routers import detect, governance as governance_router, incidents, runs

app.include_router(governance_router.router)

app.include_router(incidents.router)
app.include_router(runs.router)
app.include_router(detect.router)
