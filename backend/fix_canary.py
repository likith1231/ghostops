"""
Canary rollout of the fix
=========================

Before a code fix reaches every pod, prove it on real infrastructure:

1. build an image of the patched sample app and load it into the kind cluster;
2. run it as a canary — one pod labelled ``track=canary`` next to the stable
   pod (it shares ``app=sample-app``, so the Service also routes real users to it);
3. drive the incident's own traffic at the canary only (the stable pod is
   already unhealthy: more of that traffic could OOM-kill it) and judge it
   with real Prometheus numbers: memory growth per request, and its error
   rate against the stable release's through the C1 canary detector;
4. promote (roll the fix image out to the stable deployment) only when the
   canary is clearly better, the risk is low and the kill switch allows;
   otherwise remove the canary. The result travels with the PR either way.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from agents import governance
from agents.detectors import detect_canary_regression
from agents.replay import extract_requests
from agents.validation_officer import _apply_patch
from backend import live

logger = logging.getLogger("ghostops.backend.fix_canary")

ROOT = Path(__file__).resolve().parent.parent
CANARY = f"{live.DEPLOYMENT}-canary"
KIND_CLUSTER = os.getenv("KIND_CLUSTER", "ghostops")
TRAFFIC_ROUNDS = int(os.getenv("FIX_CANARY_ROUNDS", "3"))  # per phase (two phases)
SETTLE_S = int(os.getenv("FIX_CANARY_SETTLE_S", "35"))  # two Prometheus scrapes
# Container memory is coarse (allocator, page cache), so a leak is judged against the incident's
# own leak rate (measured by the counterfactual replay): still growing at half that rate = still
# leaking. Without a measured rate, this absolute fallback applies.
LEAK_FRACTION = 0.5
FALLBACK_LEAK_KIB = 4096


def _sh(*cmd: str, cwd: Path | None = None, timeout: int = 300) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout


def build_image(file_path: str, diff: str, tag: str) -> str:
    """Build the patched sample app and load it into kind; returns the image name."""
    image = f"ghostops-sample-app:{tag}"
    with tempfile.TemporaryDirectory() as td:
        app_dir = Path(td) / "sample-app"
        shutil.copytree(ROOT / "sample-app", app_dir, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        error = _apply_patch(Path(td), file_path, diff)
        if error is not None:
            raise RuntimeError(f"patch did not apply to the image source: {error[:200]}")
        _sh("docker", "build", "-q", "-t", image, str(app_dir))
    _sh("kind", "load", "docker-image", image, "--name", KIND_CLUSTER)
    return image


def _manifest(image: str, release: str) -> dict[str, Any]:
    labels = {"app": live.DEPLOYMENT, "track": "canary"}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": CANARY, "namespace": live.NAMESPACE, "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [{
                        "name": live.DEPLOYMENT,
                        "image": image,
                        "imagePullPolicy": "Never",
                        "env": [{"name": "RELEASE", "value": release}],
                        "resources": {"limits": {"memory": "128Mi"}, "requests": {"memory": "64Mi"}},
                        "ports": [{"containerPort": 5000}],
                    }]
                },
            },
        },
    }


def deploy_canary(image: str, release: str, timeout_s: int = 120) -> str:
    client = live._core()
    apps = client.AppsV1Api()
    body = _manifest(image, release)
    try:
        apps.create_namespaced_deployment(live.NAMESPACE, body)
    except Exception as exc:  # noqa: BLE001 - already exists from an earlier run: replace it
        if getattr(exc, "status", None) != 409:
            raise
        apps.replace_namespaced_deployment(CANARY, live.NAMESPACE, body)
    core = client.CoreV1Api()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pods = core.list_namespaced_pod(live.NAMESPACE, label_selector=f"app={live.DEPLOYMENT},track=canary").items
        ready = [p for p in pods if p.status.phase == "Running" and not p.metadata.deletion_timestamp
                 and all(c.ready for c in (p.status.container_statuses or []))]
        if ready:
            return ready[0].metadata.name
        time.sleep(2)
    raise RuntimeError("the canary pod did not become ready")


def remove_canary() -> None:
    try:
        live._core().AppsV1Api().delete_namespaced_deployment(CANARY, live.NAMESPACE)
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status", None) != 404:
            logger.warning("Could not remove the canary: %s", exc)


def promote(image: str) -> None:
    body = {"spec": {"template": {"spec": {"containers": [{"name": live.DEPLOYMENT, "image": image}]}}}}
    live._core().AppsV1Api().patch_namespaced_deployment(live.DEPLOYMENT, live.NAMESPACE, body)


def _memory(pod: str) -> float | None:
    return live._prom(f'max(container_memory_working_set_bytes{{pod="{pod}", container="{live.DEPLOYMENT}"}})')


def _restarts(pod: str) -> int:
    try:
        status = live._core().CoreV1Api().read_namespaced_pod(pod, live.NAMESPACE).status
        return sum(c.restart_count for c in (status.container_statuses or []))
    except Exception:  # noqa: BLE001
        return 0


def _drive(pod: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """The incident's traffic at the canary in two phases, memory from Prometheus after each.

    A bounded cache levels off in the second phase; a leak keeps growing (or the pod gets
    OOM-killed and restarts), so the judgement uses the second phase only.
    """
    errors = total = 0
    memory = []
    for _phase in range(2):
        sent = 0
        for _ in range(TRAFFIC_ROUNDS):
            for r in requests:
                try:
                    live._pod_get(pod, r["path"].lstrip("/"))
                except Exception as exc:  # noqa: BLE001 - 5xx from the app
                    if getattr(exc, "status", 500) >= 500:
                        errors += 1
                total += 1
                sent += 1
        time.sleep(SETTLE_S)
        memory.append(_memory(pod))
    return {"pod": pod, "requests": total, "phase_requests": sent, "errors": errors, "memory": memory, "restarts": _restarts(pod)}


def leak_threshold_kib(incident_kib_per_request: float | None) -> float:
    if incident_kib_per_request and incident_kib_per_request > 0:
        return incident_kib_per_request * LEAK_FRACTION
    return FALLBACK_LEAK_KIB


def judge(canary: dict[str, Any], stable_error_rate: float, leak_kib: float = FALLBACK_LEAK_KIB) -> dict[str, Any]:
    """Did the canary hold up under the incident's traffic?"""
    findings = []
    growth_kib = None
    mem = canary.get("memory") or []
    if len(mem) == 2 and None not in mem and canary.get("phase_requests"):
        growth_kib = (mem[1] - mem[0]) / 1024 / canary["phase_requests"]
        findings.append(
            f"canary memory {mem[0] / 1048576:.0f} -> {mem[1] / 1048576:.0f} MiB in the second half of the traffic "
            f"({growth_kib:+.0f} KiB per request)"
        )
    error_rate = canary["errors"] / canary["requests"] if canary["requests"] else 0.0
    findings.append(f"canary error rate {error_rate:.0%} (stable release {stable_error_rate:.0%})")
    restarted = canary.get("restarts", 0) > 0
    if restarted:
        findings.append(f"the canary restarted {canary['restarts']} time(s) (OOM-killed?)")
    leaking = restarted or (growth_kib is not None and growth_kib >= leak_kib)
    regressed = bool(detect_canary_regression(error_rate, stable_error_rate))
    if leaking:
        findings.append("the canary still leaks")
    if regressed:
        findings.append("the canary fails more requests than the stable release")
    return {
        "healthy": not leaking and not regressed and growth_kib is not None,
        "leak_threshold_kib_per_request": round(leak_kib, 1),
        "memory_growth_kib_per_request": None if growth_kib is None else round(growth_kib, 1),
        "error_rate": round(error_rate, 3),
        "stable_error_rate": round(stable_error_rate, 3),
        "findings": findings,
    }


def run(
    file_path: str, diff: str, logs: list[str], *, run_ref: str | None, risk_level: str | None,
    incident_kib_per_request: float | None = None,
) -> dict[str, Any]:
    """Canary the fix on the cluster and decide. Never raises: failures are reported."""
    requests = extract_requests(logs)
    if not requests:
        return {"verdict": "not_applicable", "summary": "No recorded traffic to drive at a canary."}
    tag = f"fix-{int(time.time()) % 1000000}"
    image = None
    try:
        image = build_image(file_path, diff, tag)
        canary_pod = deploy_canary(image, tag)
        time.sleep(SETTLE_S)  # a first Prometheus sample of the fresh canary
        try:
            from backend import canary as c1

            stable_error_rate = c1.error_rates().get("stable", 0.0)
        except Exception:  # noqa: BLE001
            stable_error_rate = 0.0
        result = {**judge(_drive(canary_pod, requests), stable_error_rate, leak_threshold_kib(incident_kib_per_request)), "canary_pod": canary_pod}
        promote_ok = (
            result["healthy"] and risk_level == "low" and os.getenv("FIX_CANARY_PROMOTE", "") in ("1", "true", "yes")
            and governance.allow("promote_canary", run_ref=run_ref, evidence=result)
        )
        if promote_ok:
            promote(image)
            verdict, summary = "promoted", f"Canary {tag} held up under the incident's traffic and was promoted to all pods."
        elif result["healthy"]:
            verdict, summary = "passed", f"Canary {tag} held up under the incident's traffic; not promoted automatically."
        else:
            verdict, summary = "failed", f"Canary {tag} did not hold up: {'; '.join(result['findings'])}"
        governance.record("canary_fix", verdict, summary, run_ref=run_ref, evidence=result)
        return {"verdict": verdict, "summary": summary, "image": image, **result}
    except Exception as exc:  # noqa: BLE001
        logger.error("Fix canary failed: %s", exc, exc_info=True)
        governance.record("canary_fix", "error", str(exc)[:300], run_ref=run_ref)
        return {"verdict": "error", "summary": f"Canary could not run: {exc}", "image": image}
    finally:
        remove_canary()
