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
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
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
from agents.knowledge_base import init_db  # noqa: E402

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
POD_LOG_LINES: int = int(os.getenv("POD_LOG_LINES", "20"))
POD_NAMESPACE: str = os.getenv("POD_NAMESPACE", "default")

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
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="GhostOps Ingestion Service",
    description="Receives Alertmanager webhooks and feeds the AI agent pipeline.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Kubernetes log fetcher
# ---------------------------------------------------------------------------
def _fetch_pod_logs(pod_name: str, namespace: str = POD_NAMESPACE) -> list[str]:
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
            tail_lines=POD_LOG_LINES,
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

    return metrics


# ---------------------------------------------------------------------------
# Source file reader
# ---------------------------------------------------------------------------
def _read_source_file(path: str) -> dict[str, str]:
    """Read the sample-app source file from disk.

    Returns a ``{filename: content}`` dict matching the ``source_files``
    shape in ``failure_context``.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
        basename = Path(path).name
        logger.info("Read source file: %s (%d bytes)", path, len(content))
        return {basename: content}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read source file %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------
@app.post("/webhook/alert")
async def receive_alert(request: Request) -> JSONResponse:
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

        # ---- Skip non-actionable alerts --------------------------------
        if alert_name != "SampleAppHighMemory" or alert_status != "firing":
            logger.info(
                "Ignoring alert: name=%s status=%s", alert_name, alert_status
            )
            results.append({
                "alert": alert_name,
                "status": alert_status,
                "action": "ignored",
            })
            continue

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

        failure_context: dict[str, Any] = {
            "alert_type": "memory_high",
            "logs": logs,
            "metrics": metrics,
            "source_files": source_files,
            "past_incidents": [],  # knowledge base integration comes later
        }

        logger.info(
            "Built failure_context: %d log lines, %d metric keys, %d source files",
            len(logs),
            len(metrics),
            len(source_files),
        )

        # ---- Step D: Run the agent pipeline ----------------------------
        try:
            pipeline_result = run_pipeline(failure_context)

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
            pipeline_result = {"error": str(exc)}

        # ---- Step F: Build response ------------------------------------
        results.append({
            "alert": alert_name,
            "pod": pod_name,
            "pipeline_result": pipeline_result,
        })

    # If there was exactly one actionable alert, return it directly at the
    # top level for convenience; otherwise wrap in a list.
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
# Incident API Router
# ---------------------------------------------------------------------------
from backend.routers import incidents

app.include_router(incidents.router)
