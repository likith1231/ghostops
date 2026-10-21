"""Canary rollout of the fix (cluster, image build and Prometheus faked)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents import crew, governance
from backend import fix_canary as fc

LOGS = ['10.244.0.1 - - "GET /leak HTTP/1.1" 200 -'] * 2
MiB = 1048576


@pytest.fixture()
def cluster(monkeypatch):
    monkeypatch.setattr(fc, "SETTLE_S", 0)
    state = {"memory": iter([40 * MiB, 41 * MiB]), "errors": 0, "removed": 0, "promoted": None, "restarts": 0}

    def pod_get(pod, path):
        if state["errors"]:
            state["errors"] -= 1
            e = Exception("500")
            e.status = 500
            raise e
        return "{}"

    with patch.object(fc, "build_image", return_value="ghostops-sample-app:fix-1"), \
         patch.object(fc, "deploy_canary", return_value="sample-app-canary-x"), \
         patch.object(fc, "_memory", side_effect=lambda pod: next(state["memory"])), \
         patch.object(fc.live, "_pod_get", side_effect=pod_get), \
         patch.object(fc, "remove_canary", side_effect=lambda: state.__setitem__("removed", state["removed"] + 1)), \
         patch.object(fc, "promote", side_effect=lambda image: state.__setitem__("promoted", image)), \
         patch.object(fc, "_restarts", side_effect=lambda pod: state["restarts"]), \
         patch("backend.canary.error_rates", return_value={"stable": 0.0}):
        yield state


def test_healthy_canary_passes_and_is_removed(cluster):
    result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="medium")
    assert result["verdict"] == "passed" and result["healthy"] is True
    assert result["memory_growth_kib_per_request"] < 256
    assert cluster["removed"] == 1 and cluster["promoted"] is None


def test_leaking_canary_fails(cluster):
    cluster["memory"] = iter([40 * MiB, 120 * MiB])
    result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")
    assert result["verdict"] == "failed" and "still leaks" in result["summary"]
    assert cluster["promoted"] is None and cluster["removed"] == 1


def test_erroring_canary_fails_by_c1_detector(cluster):
    cluster["errors"] = 6
    result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")
    assert result["verdict"] == "failed" and result["error_rate"] == 0.5


def test_bounded_cache_in_first_phase_is_not_a_leak():
    """Growth that levels off (a bounded cache filling up) must not fail the canary."""
    canary = {"memory": [60 * MiB, 61 * MiB], "phase_requests": 6, "requests": 12, "errors": 0, "restarts": 0}
    assert fc.judge(canary, 0.0)["healthy"] is True


def test_real_cluster_numbers_are_judged_against_the_incident_rate():
    """Measured on the kind cluster: the correct B1 fix grew 53 -> 63 MiB over 6 requests (noise from the
    allocator); the incident leaked ~7680 KiB/request. Half of that is the bar."""
    fixed = {"memory": [53 * MiB, 63 * MiB], "phase_requests": 6, "requests": 12, "errors": 0, "restarts": 0}
    assert fc.judge(fixed, 0.0, fc.leak_threshold_kib(7680.1))["healthy"] is True
    still_leaking = {"memory": [40 * MiB, 100 * MiB], "phase_requests": 6, "requests": 12, "errors": 0, "restarts": 0}
    assert fc.judge(still_leaking, 0.0, fc.leak_threshold_kib(7680.1))["healthy"] is False


def test_threshold_fallback_without_replay():
    assert fc.leak_threshold_kib(None) == fc.FALLBACK_LEAK_KIB and fc.leak_threshold_kib(1000) == 500


def test_oom_restart_fails_the_canary(cluster):
    cluster["restarts"] = 1
    cluster["memory"] = iter([120 * MiB, 30 * MiB])  # memory dropped because the pod was killed
    result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")
    assert result["verdict"] == "failed" and "restarted" in result["summary"]


def test_low_risk_promotes_only_when_enabled(cluster, monkeypatch):
    assert fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")["verdict"] == "passed"
    monkeypatch.setenv("FIX_CANARY_PROMOTE", "1")
    cluster["memory"] = iter([40 * MiB, 41 * MiB])
    result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")
    assert result["verdict"] == "promoted" and cluster["promoted"] == "ghostops-sample-app:fix-1"


def test_kill_switch_blocks_promotion(cluster, monkeypatch):
    monkeypatch.setenv("FIX_CANARY_PROMOTE", "1")
    with patch.object(governance, "is_paused", return_value=True):
        result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")
    assert result["verdict"] == "passed" and cluster["promoted"] is None


def test_build_failure_reported_and_cleaned_up(cluster):
    with patch.object(fc, "build_image", side_effect=RuntimeError("docker build failed")):
        result = fc.run("sample-app/app.py", "DIFF", LOGS, run_ref="r", risk_level="low")
    assert result["verdict"] == "error" and "docker build failed" in result["summary"]
    assert cluster["removed"] == 1


def test_no_traffic_not_applicable():
    assert fc.run("sample-app/math_utils.py", "DIFF", ["FAILED x"], run_ref="r", risk_level="low")["verdict"] == "not_applicable"


def test_canary_manifest_shares_app_label_with_track():
    m = fc._manifest("img", "fix-1")
    labels = m["spec"]["template"]["metadata"]["labels"]
    assert labels == {"app": "sample-app", "track": "canary"} and m["spec"]["selector"]["matchLabels"] == labels
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["imagePullPolicy"] == "Never" and c["resources"]["limits"]["memory"] == "128Mi"


# ---- in the pipeline -------------------------------------------------------------------------
SOURCE = {"sample-app/math_utils.py": "def multiply(a, b):\n    return a + b\n", "sample-app/test_math_utils.py": "..."}
CTX = {"alert_type": "ci_failure", "logs": ["FAILED x"], "metrics": {}, "source_files": SOURCE}


def _pipeline(canary_result):
    with patch.object(crew, "MOCK_LLM", True), patch.object(crew, "run_validation_checks", return_value={"exit_code": 0, "output": "3 passed"}), \
         patch("agents.knowledge_base.log_incident"):
        return crew.run_pipeline(CTX, fix_canary=lambda *a, **k: canary_result)


def test_failed_canary_fails_validation():
    result = _pipeline({"verdict": "failed", "summary": "Canary still leaks"})
    assert result["validation_result"]["passed"] is False and result["pr_result"]["reason"] == "validation_failed"
    assert result["risk"] is None


def test_passing_canary_lowers_risk():
    result = _pipeline({"verdict": "passed", "summary": "ok"})
    assert any(f["factor"] == "canary" for f in result["risk"]["factors"])
    assert result["patch"]["evidence"]["bundle"]["gates"]["fix_canary"]["verdict"] == "passed"


def test_load_image_uses_kind_when_present_else_k3s_else_fails():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": b"tar", "stderr": b""})()

    with patch.object(fc.shutil, "which", side_effect=lambda n: "/bin/" + n if n == "kind" else None), patch.object(fc, "_sh") as sh:
        fc.load_image("img:1")
    assert sh.call_args.args[:3] == ("kind", "load", "docker-image")

    with patch.object(fc.shutil, "which", side_effect=lambda n: "/bin/k3s" if n == "k3s" else None), patch.object(fc.subprocess, "run", side_effect=run):
        fc.load_image("img:1")
    assert calls[0][:2] == ["docker", "save"] and calls[1][:4] == ["sudo", "-n", "k3s", "ctr"]

    with patch.object(fc.shutil, "which", return_value=None):
        with pytest.raises(RuntimeError, match="neither kind nor k3s"):
            fc.load_image("img:1")
