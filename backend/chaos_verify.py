"""
Chaos-verified fixes
====================

Before an incident is marked resolved, GhostOps attacks the fix:

1. **pod-kill** with Chaos Mesh (a ``PodChaos`` object) — the workload must
   come back healthy;
2. **re-trigger** the original fault against the fixed workload — the leak
   traffic for a memory incident (enough requests to break a still-leaky
   app), a checkout burst for an error-rate incident;
3. the same Prometheus signal must stay healthy.

Deliberately breaking production is opt-in (``CHAOS_VERIFY=1``) and the
kill switch blocks it.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from agents import governance
from backend import live

logger = logging.getLogger("ghostops.backend.chaos_verify")

LEAK_REQUESTS = int(os.getenv("CHAOS_LEAK_REQUESTS", "10"))  # 100 MiB vs a 128 MiB limit: fatal for a leaky app
CHECKOUT_REQUESTS = int(os.getenv("CHAOS_CHECKOUT_REQUESTS", "40"))
SETTLE_S = int(os.getenv("CHAOS_SETTLE_S", "35"))
MAX_ERROR_RATE = 0.05


def enabled() -> bool:
    return os.getenv("CHAOS_VERIFY", "") in ("1", "true", "yes")


def _pod_kill() -> str:
    """Create a one-shot Chaos Mesh pod-kill for the stable workload; returns its name."""
    name = f"ghostops-verify-{uuid.uuid4().hex[:6]}"
    body = {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": "PodChaos",
        "metadata": {"name": name, "namespace": live.NAMESPACE, "labels": {"app.kubernetes.io/managed-by": "ghostops"}},
        "spec": {
            "action": "pod-kill",
            "mode": "one",
            "selector": {
                "namespaces": [live.NAMESPACE],
                "labelSelectors": {"app": live.DEPLOYMENT},
                "expressionSelectors": [{"key": "track", "operator": "DoesNotExist"}],
            },
        },
    }
    live._core().CustomObjectsApi().create_namespaced_custom_object("chaos-mesh.org", "v1alpha1", live.NAMESPACE, "podchaos", body)
    return name


def _delete_chaos(name: str) -> None:
    try:
        live._core().CustomObjectsApi().delete_namespaced_custom_object("chaos-mesh.org", "v1alpha1", live.NAMESPACE, "podchaos", name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not delete PodChaos %s: %s", name, exc)


def _restarts(pod: str) -> int:
    from backend import fix_canary

    return fix_canary._restarts(pod)


def _wait_replaced(old_pod: str, timeout_s: int = 120) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pod = live._pod_or_none()
        if pod and pod != old_pod:
            from backend import canary

            return canary.wait_rolled_out(timeout_s=max(10, int(deadline - time.monotonic())))
        time.sleep(2)
    raise RuntimeError(f"{old_pod} was not replaced after the pod-kill")


def _retrigger(alert_type: str, pod: str) -> dict[str, Any]:
    from backend import canary

    if alert_type == "memory_high":
        for _ in range(LEAK_REQUESTS):
            try:
                live._pod_get(pod, "leak")
            except Exception:  # noqa: BLE001 - a dying pod; judged below
                pass
        time.sleep(SETTLE_S)
        used_bytes = live._prom(live.memory_query(pod))
        limit = live._prom(live.limit_query(pod))
        restarted = _restarts(pod) > 0
        used = (used_bytes / limit) if used_bytes and limit else None
        ok = not restarted and used is not None and used < 0.8
        detail = f"{LEAK_REQUESTS} leak requests: memory at {used:.0%} of limit" if used is not None else "memory unavailable"
        if restarted:
            detail += ", pod restarted (OOM-killed)"
        return {"ok": ok, "detail": detail}
    results = [canary.checkout_ok(pod) for _ in range(CHECKOUT_REQUESTS)]
    rate = results.count(False) / len(results)
    return {"ok": rate <= MAX_ERROR_RATE, "detail": f"{CHECKOUT_REQUESTS} checkout requests: {rate:.0%} errors"}


def run(alert_type: str, run_ref: str | None = None) -> dict[str, Any]:
    """Attack the fixed workload; ``verdict`` is survived | failed | skipped | error. Never raises."""
    if not enabled():
        return {"verdict": "skipped", "summary": "Chaos verification is off (CHAOS_VERIFY=1 enables it)."}
    if not governance.allow("chaos", run_ref=run_ref):
        return {"verdict": "skipped", "summary": "Kill switch is on: no chaos experiments."}
    steps, chaos = [], None
    try:
        before = live._pod_name()
        chaos = _pod_kill()
        pod = _wait_replaced(before)
        steps.append({"step": "pod-kill", "ok": True, "detail": f"{before} killed by Chaos Mesh, {pod} took over"})
        retrigger = _retrigger(alert_type, pod)
        steps.append({"step": "re-trigger", **retrigger})
        from backend import verification

        signal = verification._probe(alert_type)
        steps.append({"step": "signal", "ok": signal["ok"] is not False, "detail": f"Prometheus value {signal.get('value')}, alert {signal.get('alert')}"})
        survived = all(s["ok"] for s in steps)
        verdict = "survived" if survived else "failed"
        summary = "; ".join(s["detail"] for s in steps)
    except Exception as exc:  # noqa: BLE001
        logger.error("Chaos verification failed to run: %s", exc, exc_info=True)
        verdict, summary = "error", str(exc)
    finally:
        if chaos:
            _delete_chaos(chaos)
    governance.record("chaos", verdict, summary[:300], run_ref=run_ref, evidence={"steps": steps})
    return {"verdict": verdict, "summary": summary, "steps": steps}
