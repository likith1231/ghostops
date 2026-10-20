"""
Live incidents on the real cluster
==================================

Drives scenario B1 for real: leak memory in the running sample-app pod
(through the Kubernetes API proxy, no port-forward needed), watch Prometheus
until ``SampleAppHighMemory`` fires, and let Alertmanager's real webhook start
the pipeline. Progress is written to the run store so the dashboard can show
every step; the webhook handler attaches the pipeline to the same run.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from agents import correlation
from backend.runs import store

logger = logging.getLogger("ghostops.backend.live")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
NAMESPACE = os.getenv("POD_NAMESPACE", "default")
DEPLOYMENT = os.getenv("SAMPLE_APP_DEPLOYMENT", "sample-app")
ALERT = "SampleAppHighMemory"
LEAK_REQUESTS = 8  # 10 MiB each against a 128 MiB limit: well past the 80% alert threshold
ALERT_TIMEOUT_S = 300
WEBHOOK_TIMEOUT_S = 90
POLL_S = 5

# Scoped to the current pod: after a restart Prometheus still holds the old pod's series.
def memory_query(pod: str) -> str:
    return f'max(container_memory_working_set_bytes{{pod="{pod}", container="sample-app"}})'


def limit_query(pod: str) -> str:
    return f'max(container_spec_memory_limit_bytes{{pod="{pod}", container="sample-app"}})'

live_lock = threading.Lock()
_waiting_run: str | None = None  # the live run waiting for its alert's webhook
_waiting_guard = threading.Lock()


def claim_waiting_run() -> str | None:
    """Called by the webhook handler: the live run waiting for this alert, if any."""
    global _waiting_run
    with _waiting_guard:
        run_id, _waiting_run = _waiting_run, None
        return run_id


# ---------------------------------------------------------------------------
# Cluster and Prometheus helpers
# ---------------------------------------------------------------------------
def _core():
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()
    return client


def _pod_name() -> str:
    client = _core()
    # The stable workload only: a fix canary (track=canary) shares the app label for traffic.
    pods = client.CoreV1Api().list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT},track!=canary")
    running = [p for p in pods.items if p.status.phase == "Running" and not p.metadata.deletion_timestamp]
    if not running:
        raise RuntimeError(f"no running {DEPLOYMENT} pod in namespace {NAMESPACE}")
    return running[0].metadata.name


def _pod_or_none() -> str | None:
    try:
        return _pod_name()
    except Exception:  # noqa: BLE001
        return None


def _pod_get(pod: str, path: str) -> str:
    """GET a path on the pod's port 5000 through the API server proxy."""
    client = _core()
    # _preload_content=False: the raw body. Otherwise the client turns the pod's JSON
    # into a Python dict's repr, which is not JSON any more.
    resp = client.CoreV1Api().connect_get_namespaced_pod_proxy_with_path(
        f"{pod}:5000", NAMESPACE, path, _preload_content=False
    )
    return resp.data.decode("utf-8")


def _prom(query: str) -> float | None:
    resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
    resp.raise_for_status()
    result = resp.json()["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def alert_state(name: str = ALERT, labels: dict[str, str] | None = None) -> str:
    """inactive | pending | firing (optionally only alerts carrying these labels)."""
    resp = requests.get(f"{PROMETHEUS_URL}/api/v1/alerts", timeout=5)
    resp.raise_for_status()
    states = {
        a["state"] for a in resp.json()["data"]["alerts"]
        if a["labels"].get("alertname") == name and all(a["labels"].get(k) == v for k, v in (labels or {}).items())
    }
    return "firing" if "firing" in states else "pending" if "pending" in states else "inactive"


def _restart_sample_app() -> None:
    client = _core()
    now = datetime.now(timezone.utc).isoformat()
    body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
    client.AppsV1Api().patch_namespaced_deployment(DEPLOYMENT, NAMESPACE, body)


# ---------------------------------------------------------------------------
# The live run
# ---------------------------------------------------------------------------
def _step(run_id: str, live: dict[str, Any], **changes: Any) -> None:
    live.update(changes)
    store.set_live(run_id, live)


def _wait(predicate, timeout_s: float, on_poll=None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if on_poll:
            on_poll()
        if predicate():
            return True
        time.sleep(POLL_S)
    return False


def run_live_leak(run_id: str) -> None:
    """Leak memory for real and follow the alert into the pipeline. Releases ``live_lock``."""
    global _waiting_run
    live: dict[str, Any] = {"step": "starting", "leaked_mib": 0, "memory": [], "limit": None, "alert": "inactive"}
    started = time.monotonic()
    pod = ""

    def sample() -> None:
        try:
            used, limit = _prom(memory_query(pod)), _prom(limit_query(pod))
            alert = alert_state()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Prometheus poll failed: %s", exc)
            return
        if used is not None:
            live["memory"].append({"ts": round(time.monotonic() - started, 1), "value": used})
        _step(run_id, live, limit=limit, alert=alert)

    try:
        # A deliberate new incident: close any stale one whose "resolved" notice hasn't arrived yet,
        # or the new alert would be treated as its duplicate.
        correlation.resolve_alert({"alertname": ALERT, "component": DEPLOYMENT})
        # 0. Start clean: leaked memory or a still-firing alert would spoil the run.
        pod = _pod_name()
        leaked = int(json.loads(_pod_get(pod, "health")).get("leaked_objects", 0))
        if leaked or alert_state() != "inactive":
            _step(run_id, live, step="resetting", reset=True)
            _restart_sample_app()
            time.sleep(8)
            if not _wait(lambda: alert_state() == "inactive", 180):
                raise RuntimeError("the previous alert did not clear within 3 minutes")
            fresh = _wait(lambda: _pod_or_none() not in (None, pod), 120)
            if not fresh:
                raise RuntimeError("sample-app did not come back after the restart")
            pod = _pod_name()
            time.sleep(3)  # let the new pod start serving

        # 1. Leak memory, one real request at a time.
        _step(run_id, live, step="leaking", pod=pod)
        for i in range(LEAK_REQUESTS):
            _pod_get(pod, "leak")
            _step(run_id, live, leaked_mib=(i + 1) * 10)
            time.sleep(0.6)

        # 2. Wait for Prometheus to fire the alert (memory > 80% for 30 s).
        _step(run_id, live, step="waiting_alert")
        if not _wait(lambda: live["alert"] == "firing", ALERT_TIMEOUT_S, on_poll=sample):
            raise RuntimeError("Prometheus did not fire SampleAppHighMemory within 5 minutes")

        # 3. Hand over to Alertmanager: its webhook starts the pipeline on this run.
        with _waiting_guard:
            _waiting_run = run_id
        _step(run_id, live, step="alert_firing")
        handed_over = _wait(lambda: _waiting_run != run_id, WEBHOOK_TIMEOUT_S, on_poll=sample)
        if not handed_over:
            with _waiting_guard:
                _waiting_run = None
            raise RuntimeError(
                "the alert fired but Alertmanager's webhook never reached the backend "
                "(it must listen on 0.0.0.0:8000 so the cluster can reach it)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Live run %s failed: %s", run_id, exc, exc_info=True)
        store.finish(run_id, "error", error=str(exc))
    finally:
        live_lock.release()


# ---------------------------------------------------------------------------
# Live canary regression (use case C1)
# ---------------------------------------------------------------------------
CANARY_ALERT = "SampleAppHighErrorRate"
FAULTY_RATE = "0.5"  # the bad release fails every 2nd checkout
TRAFFIC_INTERVAL_S = 0.4
TRAFFIC_MAX_S = 420


def _set_release(release: str, faulty_rate: str | None) -> None:
    """Roll out a new revision with these env vars (a strategic merge on the container env)."""
    client = _core()
    env = [{"name": "RELEASE", "value": release}]
    env.append({"name": "FAULTY_RATE", "value": faulty_rate} if faulty_rate else {"name": "FAULTY_RATE", "$patch": "delete"})
    body = {"spec": {"template": {"spec": {"containers": [{"name": DEPLOYMENT, "env": env}]}}}}
    client.AppsV1Api().patch_namespaced_deployment(DEPLOYMENT, NAMESPACE, body)


def _traffic(run_id: str, stop: threading.Event, stats: dict[str, int]) -> None:
    """Steady real requests to the checkout endpoint until the run ends."""
    from backend import canary

    deadline = time.monotonic() + TRAFFIC_MAX_S
    while not stop.is_set() and time.monotonic() < deadline:
        run = store.get(run_id)
        if run is None or run["status"] in ("passed", "failed", "error"):
            return
        pod = _pod_or_none()
        if pod:
            ok = canary.checkout_ok(pod)
            stats["ok" if ok else "failed"] += 1
        time.sleep(TRAFFIC_INTERVAL_S)


def run_live_canary(run_id: str) -> None:
    """Ship a bad release, drive real traffic, follow the real alert into remediation. Releases ``live_lock``."""
    global _waiting_run
    from backend import canary

    release = f"canary-{int(time.time()) % 100000}"
    live: dict[str, Any] = {"kind": "canary", "step": "starting", "release": release, "error_rate": [], "alert": "inactive", "requests": {}}
    started = time.monotonic()
    stop = threading.Event()
    stats = {"ok": 0, "failed": 0}

    def sample() -> None:
        try:
            rates = canary.error_rates()
            alert = alert_state(CANARY_ALERT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Prometheus poll failed: %s", exc)
            return
        live["error_rate"].append({"ts": round(time.monotonic() - started, 1), "value": rates.get(release, 0.0)})
        _step(run_id, live, alert=alert, requests=dict(stats))

    try:
        correlation.resolve_alert({"alertname": CANARY_ALERT, "component": DEPLOYMENT})
        # 0. Start from a healthy release with no alert in flight.
        if alert_state(CANARY_ALERT) != "inactive":
            _step(run_id, live, step="resetting", reset=True)
            if not _wait(lambda: alert_state(CANARY_ALERT) == "inactive", 180):
                raise RuntimeError("the previous error-rate alert did not clear within 3 minutes")

        # 1. Ship the bad release and wait for it to replace the old pod.
        _step(run_id, live, step="deploying")
        _set_release(release, FAULTY_RATE)
        live["pod"] = canary.wait_rolled_out()

        # 2. Real traffic; Prometheus sees the new release failing.
        threading.Thread(target=_traffic, args=(run_id, stop, stats), daemon=True).start()
        _step(run_id, live, step="waiting_alert")
        if not _wait(lambda: live["alert"] == "firing", ALERT_TIMEOUT_S, on_poll=sample):
            raise RuntimeError("Prometheus did not fire SampleAppHighErrorRate within 5 minutes")

        # 3. Alertmanager's webhook starts the remediation on this run.
        with _waiting_guard:
            _waiting_run = run_id
        _step(run_id, live, step="alert_firing")
        if not _wait(lambda: _waiting_run != run_id, WEBHOOK_TIMEOUT_S, on_poll=sample):
            with _waiting_guard:
                _waiting_run = None
            raise RuntimeError(
                "the alert fired but Alertmanager's webhook never reached the backend "
                "(it must listen on 0.0.0.0:8000 so the cluster can reach it)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Live canary run %s failed: %s", run_id, exc, exc_info=True)
        stop.set()
        store.finish(run_id, "error", error=str(exc))
    finally:
        live_lock.release()
