"""
Canary regression remediation (use case C1)
===========================================

When ``SampleAppHighErrorRate`` fires, decide from real cluster state whether a
recent release caused it, and if so roll it back:

* diagnosis   — the deployment's rollout history (ReplicaSets) and the error
                rate per release from Prometheus, through the C1 detector;
* remediation — ``kubectl rollout undo``; the "patch" shown is the real
                difference between the two releases' pod templates;
* validation  — wait for the rollback, then send real requests and require
                at most ``MAX_ERROR_RATE`` failures;
* no PR       — a rollback restores code that was already reviewed.

Without a recent rollout there is nothing safe to undo: it is left to a human.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from agents import governance
from agents.deployer import rollback_deployment
from agents.detectors import detect_canary_regression
from agents.mock_llm import unified_diff
from backend import live

logger = logging.getLogger("ghostops.backend.canary")

ALERT = "SampleAppHighErrorRate"
RECENT_ROLLOUT_S = 1800
MAX_ERROR_RATE = 0.05
VERIFY_REQUESTS = 30
ERROR_RATE_QUERY = (
    'sum by (release) (rate(sample_app_requests_total{status=~"5.."}[1m]))'
    " / sum by (release) (rate(sample_app_requests_total[1m]))"
)

StageCallback = Callable[[str, str, dict], None]


# ---------------------------------------------------------------------------
# Cluster state
# ---------------------------------------------------------------------------
def rollout_history() -> list[dict[str, Any]]:
    """The deployment's revisions (oldest first): revision, created, env, image."""
    client = live._core()
    sets = client.AppsV1Api().list_namespaced_replica_set(live.NAMESPACE, label_selector=f"app={live.DEPLOYMENT},track!=canary")
    out = []
    for rs in sets.items:
        revision = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
        if revision is None:
            continue
        container = rs.spec.template.spec.containers[0]
        out.append(
            {
                "revision": int(revision),
                "created": rs.metadata.creation_timestamp,
                "image": container.image,
                "env": {e.name: e.value for e in (container.env or [])},
            }
        )
    return sorted(out, key=lambda r: r["revision"])


def error_rates() -> dict[str, float]:
    """Current 5xx ratio per release label."""
    import requests

    resp = requests.get(f"{live.PROMETHEUS_URL}/api/v1/query", params={"query": ERROR_RATE_QUERY}, timeout=5)
    resp.raise_for_status()
    return {r["metric"].get("release", "?"): float(r["value"][1]) for r in resp.json()["data"]["result"]}


def checkout_ok(pod: str) -> bool:
    """One real request to the business endpoint; False on a 5xx."""
    try:
        live._pod_get(pod, "api/checkout")
        return True
    except Exception as exc:  # noqa: BLE001 - the k8s client raises ApiException for non-2xx
        return getattr(exc, "status", 500) < 500


def wait_rolled_out(timeout_s: float = 120) -> str:
    """Wait until the deployment's new pods are the only ones serving; return a pod name."""
    client = live._core()
    apps = client.AppsV1Api()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        d = apps.read_namespaced_deployment(live.DEPLOYMENT, live.NAMESPACE)
        s = d.status
        done = (
            (s.observed_generation or 0) >= (d.metadata.generation or 0)
            and (s.updated_replicas or 0) == d.spec.replicas
            and (s.available_replicas or 0) == d.spec.replicas
            and (s.replicas or 0) == d.spec.replicas
        )
        if done:
            pod = live._pod_or_none()
            if pod:
                return pod
        time.sleep(2)
    raise RuntimeError(f"{live.DEPLOYMENT} did not finish rolling out within {timeout_s:.0f}s")


def _describe(rev: dict[str, Any]) -> str:
    lines = [f"image: {rev['image']}"] + [f"env {k}={v}" for k, v in sorted(rev["env"].items())]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The remediation pipeline
# ---------------------------------------------------------------------------
def diagnose(alert: dict[str, Any]) -> dict[str, Any]:
    history = rollout_history()
    rates = error_rates()
    current = history[-1] if history else None
    previous = history[-2] if len(history) > 1 else None
    release = (alert.get("labels") or {}).get("release") or (current["env"].get("RELEASE", "stable") if current else "?")
    current_rate = rates.get(release, max(rates.values(), default=0.0))
    previous_release = previous["env"].get("RELEASE", "stable") if previous else None
    previous_rate = rates.get(previous_release, 0.0) if previous_release and previous_release != release else 0.0

    # Only the release that is running now can be rolled back. If the alert names a release that is
    # no longer deployed (already rolled back; Alertmanager re-sends firing alerts), undoing again
    # would re-deploy the bad release.
    deployed_release = current["env"].get("RELEASE", "stable") if current else None
    if current and release != deployed_release:
        return {
            "root_cause": "already_remediated",
            "confidence": "high",
            "summary": f"Release {release} is no longer deployed (running: {deployed_release}); nothing to roll back.",
            "evidence": [{"type": "log", "reference": f"deployed revision {current['revision']} runs release {deployed_release}", "reasoning": "Deployment history (ReplicaSets)."}],
            "release": release,
            "rollback": None,
        }
    age_s = (datetime.now(timezone.utc) - current["created"]).total_seconds() if current else None
    recent = age_s is not None and age_s < RECENT_ROLLOUT_S and previous is not None
    detection = detect_canary_regression(current_rate, previous_rate)

    evidence = [
        {"type": "metric", "reference": f"release {release}: {current_rate:.0%} of requests failing", "reasoning": "From Prometheus, per release."},
    ]
    if previous:
        evidence.append({"type": "metric", "reference": f"previous release {previous_release}: {previous_rate:.0%}", "reasoning": "Baseline before the rollout."})
    if current:
        when = f"{age_s / 60:.1f} min ago" if age_s is not None else "?"
        evidence.append({"type": "log", "reference": f"rollout to revision {current['revision']} {when}", "reasoning": "Deployment history (ReplicaSets)."})

    if recent and detection:
        return {
            "root_cause": "canary_regression",
            "confidence": "high",
            "summary": f"Release {release} (revision {current['revision']}) fails {current_rate:.0%} of requests; the previous release did not.",
            "evidence": evidence,
            "release": release,
            "rollback": {"from": current, "to": previous},
        }
    return {
        "root_cause": "error_rate_slo_breach",
        "confidence": "medium",
        "summary": f"Error rate {current_rate:.0%} with no recent rollout to blame: needs a human.",
        "evidence": evidence,
        "release": release,
        "rollback": None,
    }


def run_canary_pipeline(alert: dict[str, Any], on_stage: StageCallback | None = None, run_ref: str | None = None) -> dict[str, Any]:
    """Diagnose -> roll back -> verify, reported through the same stages as the LLM pipeline."""
    notify = on_stage or (lambda *a: None)

    notify("diagnosis", "running", {})
    diagnosis = diagnose(alert)
    governance.record("diagnose", diagnosis["root_cause"], diagnosis["summary"], run_ref=run_ref, evidence={"evidence": diagnosis["evidence"]})
    notify("diagnosis", "done", _public(diagnosis))

    notify("patch", "running", {})
    plan = diagnosis["rollback"]
    paused = bool(plan) and not governance.allow("rollback", run_ref=run_ref, evidence={"release": diagnosis.get("release")})
    if paused:
        patch = {"action": "none", "diff": "", "explanation": "A rollback was warranted, but the kill switch is on: nothing was changed."}
        outcome = {"rolled_back": False}
        plan = None
    elif plan:
        before, after = plan["from"], plan["to"]
        patch = {
            "action": "rollback",
            "file_path": f"deployment/{live.DEPLOYMENT}",
            "diff": unified_diff(f"deployment/{live.DEPLOYMENT}", _describe(before), _describe(after)).replace(
                f"a/deployment/{live.DEPLOYMENT}", f"a/deployment/{live.DEPLOYMENT} (revision {before['revision']})", 1
            ).replace(f"b/deployment/{live.DEPLOYMENT}", f"b/deployment/{live.DEPLOYMENT} (revision {after['revision']})", 1),
            "explanation": f"Roll {live.DEPLOYMENT} back from revision {before['revision']} to {after['revision']}, the last release that served traffic cleanly.",
        }
        outcome = rollback_deployment(live.DEPLOYMENT, live.NAMESPACE)
        patch["output"] = outcome["output"]
        if not outcome["rolled_back"]:
            patch["explanation"] += f" The rollback failed: {outcome['output']}"
    else:
        patch = {"action": "none", "diff": "", "explanation": "No recent rollout to undo; nothing is changed automatically."}
        outcome = {"rolled_back": False}
    if patch["action"] == "rollback":
        governance.record(
            "rollback", "done" if outcome["rolled_back"] else "failed", patch["explanation"], run_ref=run_ref,
            evidence={"output": outcome.get("output")},
        )
    notify("patch", "done", patch)

    notify("validation", "running", {})
    validation = verify() if outcome["rolled_back"] else {
        "passed": False,
        "summary": "Paused by the kill switch." if paused
        else diagnosis["summary"] if diagnosis["root_cause"] == "already_remediated"
        else "Not remediated automatically." if not plan else "The rollback did not run.",
        "failing_tests": [],
        "test_output": "",
    }
    notify("validation", "done", validation)

    governance.record("verify", "passed" if validation["passed"] else "failed", validation["summary"], run_ref=run_ref)
    if validation["passed"]:
        from backend import verification

        started = alert.get("startsAt")
        verification.start(
            kind="rollback", alert_type="error_rate_high", run_ref=run_ref, reference=diagnosis.get("release"),
            started_at=datetime.fromisoformat(started.replace("Z", "+00:00")) if started else datetime.now(timezone.utc),
            # Let the restored release collect fresh samples before judging it.
            deployed_at=datetime.now(timezone.utc) + timedelta(seconds=verification.ROLLBACK_SETTLE_S),
        )
    notify("deploy", "running", {})
    already = diagnosis["root_cause"] == "already_remediated"
    pr_result = {
        "pr_created": False,
        "reason": "rollback" if validation["passed"] else "already_remediated" if already else "autonomy_paused" if paused else "needs_human",
    }
    notify("deploy", "done", pr_result)

    try:
        from agents import postmortem

        postmortem.write(
            run_ref=run_ref, alert_type="error_rate_high", diagnosis=_public(diagnosis), patch=patch,
            validation=validation, outcome=pr_result, started_at=alert.get("startsAt"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not write the postmortem: %s", exc)

    try:
        from agents.knowledge_base import log_incident

        log_incident({"alert_type": "error_rate_high"}, diagnosis, patch, validation, pr_result)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to log incident: %s", exc)

    return {"diagnosis": _public(diagnosis), "patch": patch, "validation_result": validation, "pr_result": pr_result}


def verify() -> dict[str, Any]:
    """After the rollback: real requests to the restored release must succeed."""
    try:
        pod = wait_rolled_out()
    except RuntimeError as exc:
        return {"passed": False, "summary": str(exc), "failing_tests": [], "test_output": ""}
    time.sleep(2)  # let the new pod start serving
    results = [checkout_ok(pod) for _ in range(VERIFY_REQUESTS)]
    failures = results.count(False)
    rate = failures / len(results)
    passed = rate <= MAX_ERROR_RATE
    return {
        "passed": passed,
        "summary": f"{len(results) - failures}/{len(results)} real checkout requests succeeded on {pod} after the rollback ({rate:.0%} errors).",
        "failing_tests": [] if passed else [f"{failures} checkout requests failed"],
        "test_output": f"pod {pod}: {failures} of {len(results)} requests returned 5xx",
    }


def _public(diagnosis: dict[str, Any]) -> dict[str, Any]:
    """The diagnosis without the raw rollout objects (datetimes don't serialise)."""
    return {k: v for k, v in diagnosis.items() if k != "rollback"}
