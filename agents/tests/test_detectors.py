"""Tests for the deterministic anomaly detectors (no LLM, Docker or cluster needed)."""

import pytest

from agents.detectors import (
    detect_auth_burst,
    detect_canary_regression,
    detect_config_drift,
    detect_cpu_throttling,
    detect_crashloop,
    detect_dns_failure,
    detect_error_rate_slo,
    detect_hpa_flapping,
    detect_image_pull_failure,
    detect_import_failure,
    detect_latency_slo,
    detect_log_spike,
    detect_memory_leak,
    detect_pool_exhaustion,
    detect_traceback,
    run_log_detectors,
)

TRACEBACK = [
    "Traceback (most recent call last):",
    '  File "/app/user_service.py", line 47, in get_profile',
    "    return resp['data']['name']",
    "TypeError: 'NoneType' object is not subscriptable",
]


# ---- A1 ------------------------------------------------------------------
def test_traceback_detected_with_location():
    d = detect_traceback(TRACEBACK)
    assert d.use_case == "A1"
    assert "TypeError" in d.title
    assert "user_service.py:47" in d.evidence[1]


def test_traceback_uses_innermost_frame():
    logs = [
        "Traceback (most recent call last):",
        '  File "/app/a.py", line 1, in outer',
        '  File "/app/b.py", line 9, in inner',
        "ValueError: boom",
    ]
    assert "b.py:9" in detect_traceback(logs).evidence[1]


def test_traceback_absent_in_normal_logs():
    assert detect_traceback(['GET /health 200', 'INFO started']) is None


def test_traceback_empty_logs():
    assert detect_traceback([]) is None


def test_traceback_without_exception_line_is_ignored():
    assert detect_traceback(["Traceback (most recent call last):", '  File "x.py", line 1, in f']) is None


# ---- A2 ------------------------------------------------------------------
def test_import_failure_names_module():
    d = detect_import_failure(["ModuleNotFoundError: No module named 'requests'"])
    assert d.use_case == "A2" and "requests" in d.title and d.severity == "critical"


def test_import_error_cannot_import_name():
    d = detect_import_failure(["ImportError: cannot import name 'Foo'"])
    assert d is not None and "Foo" in d.title


def test_import_failure_absent():
    assert detect_import_failure(["all good"]) is None


# ---- A3 ------------------------------------------------------------------
POOL = "sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached"


def test_pool_exhaustion_needs_repetition():
    assert detect_pool_exhaustion([POOL, POOL]) is None


def test_pool_exhaustion_detected():
    d = detect_pool_exhaustion([POOL] * 4)
    assert d.use_case == "A3" and "size=5" in d.evidence[0]


def test_pool_confidence_grows_and_is_capped():
    low = detect_pool_exhaustion([POOL] * 3).confidence
    high = detect_pool_exhaustion([POOL] * 100).confidence
    assert low < high <= 0.95


# ---- A4 ------------------------------------------------------------------
def _burst(n, services, start=1000.0, spacing=1.0):
    return [(start + i * spacing, services[i % len(services)], "401 Unauthorized") for i in range(n)]


def test_auth_burst_across_services():
    d = detect_auth_burst(_burst(12, ["a", "b", "c"]))
    assert d.use_case == "A4" and "3 services" in d.evidence[0]


def test_auth_burst_single_service_ignored():
    assert detect_auth_burst(_burst(30, ["a"])) is None


def test_auth_burst_too_spread_out():
    assert detect_auth_burst(_burst(12, ["a", "b", "c"], spacing=30.0)) is None


def test_auth_burst_too_few_events():
    assert detect_auth_burst(_burst(5, ["a", "b", "c"])) is None


def test_auth_burst_ignores_non_auth_lines():
    events = [(1000.0 + i, "svc%d" % (i % 3), "200 OK") for i in range(20)]
    assert detect_auth_burst(events) is None


def test_auth_burst_matches_jwt_expiry_text():
    events = [(1000.0 + i, "svc%d" % (i % 3), "JWT token expired") for i in range(12)]
    assert detect_auth_burst(events) is not None


# ---- A5 ------------------------------------------------------------------
def test_log_spike_detected():
    d = detect_log_spike([500, 510, 490, 505, 495, 500], 50_000)
    assert d.use_case == "A5" and "x" in d.evidence[0]


def test_log_spike_normal_variation_ignored():
    assert detect_log_spike([500, 510, 490, 505, 495, 500], 560) is None


def test_log_spike_needs_history():
    assert detect_log_spike([500, 510], 50_000) is None


def test_log_spike_flat_baseline_does_not_divide_by_zero():
    assert detect_log_spike([100] * 6, 5000) is not None


def test_log_spike_requires_big_ratio_even_if_z_is_high():
    assert detect_log_spike([100] * 6, 300) is None


# ---- B6 ------------------------------------------------------------------
def test_dns_failure_detected():
    logs = ["socket.gaierror: [Errno -2] Name or service not known"] * 3
    assert detect_dns_failure(logs).use_case == "B6"


def test_dns_single_blip_ignored():
    assert detect_dns_failure(["getaddrinfo failed"]) is None


# ---- run_log_detectors ---------------------------------------------------
def test_run_log_detectors_orders_by_severity():
    logs = TRACEBACK + ["ModuleNotFoundError: No module named 'x'"]
    found = run_log_detectors(logs)
    assert [d.use_case for d in found] == ["A2", "A1"]


def test_run_log_detectors_clean():
    assert run_log_detectors(["GET /health 200"]) == []


# ---- B1 ------------------------------------------------------------------
def test_memory_leak_monotonic_growth():
    d = detect_memory_leak([27e6, 80e6, 118e6, 130e6])
    assert d.use_case == "B1" and d.severity == "high"


def test_memory_leak_critical_near_limit():
    d = detect_memory_leak([27e6, 80e6, 118e6, 130e6], limit=134_217_728)
    assert d.severity == "critical"


def test_memory_sawtooth_not_a_leak():
    assert detect_memory_leak([100, 200, 300, 50, 150]) is None


def test_memory_flat_not_a_leak():
    assert detect_memory_leak([100, 100, 100, 100]) is None


def test_memory_growth_below_threshold():
    assert detect_memory_leak([100, 105, 110, 115]) is None


def test_memory_leak_needs_enough_samples():
    assert detect_memory_leak([1, 2, 3]) is None


# ---- B2 ------------------------------------------------------------------
def test_cpu_throttling_detected():
    assert detect_cpu_throttling(0.6, 0.98).use_case == "B2"


@pytest.mark.parametrize("throttled,usage", [(0.1, 0.99), (0.6, 0.5)])
def test_cpu_throttling_not_detected(throttled, usage):
    assert detect_cpu_throttling(throttled, usage) is None


# ---- B4 ------------------------------------------------------------------
def test_latency_slo_breach():
    assert detect_latency_slo(0.8).severity == "high"


def test_latency_slo_severe():
    assert detect_latency_slo(3.0).severity == "critical"


def test_latency_within_slo():
    assert detect_latency_slo(0.5) is None


# ---- B5 ------------------------------------------------------------------
def test_error_rate_breach():
    d = detect_error_rate_slo(45, 1000)
    assert d.use_case == "B5" and d.severity == "critical"


def test_error_rate_moderate_breach_is_high():
    assert detect_error_rate_slo(20, 1000).severity == "high"


def test_error_rate_within_slo():
    assert detect_error_rate_slo(5, 1000) is None


def test_error_rate_ignores_tiny_sample():
    assert detect_error_rate_slo(5, 5) is None


# ---- C1 ------------------------------------------------------------------
def test_canary_regression_detected():
    assert detect_canary_regression(0.15, 0.001).use_case == "C1"


def test_canary_similar_to_stable_ignored():
    assert detect_canary_regression(0.03, 0.025) is None


def test_canary_below_absolute_floor_ignored():
    assert detect_canary_regression(0.005, 0.0) is None


# ---- C2 ------------------------------------------------------------------
def test_config_drift_lists_differences():
    d = detect_config_drift({"timeout": "30s", "a": "1"}, {"timeout": "120s", "a": "1"})
    assert d.use_case == "C2" and d.evidence == ["timeout: declared='30s' live='120s'"]


def test_config_drift_added_and_removed_keys():
    d = detect_config_drift({"old": "1"}, {"new": "2"})
    assert len(d.evidence) == 2


def test_config_in_sync():
    assert detect_config_drift({"a": "1"}, {"a": "1"}) is None


# ---- C3 / C5 -------------------------------------------------------------
def test_crashloop_reports_termination_reason():
    pods = [
        {"waiting_reason": "CrashLoopBackOff", "last_terminated_reason": "OOMKilled"},
        {"waiting_reason": "Running"},
    ]
    d = detect_crashloop(pods)
    assert d.use_case == "C3" and "OOMKilled" in d.evidence[1] and "1/2" in d.evidence[0]


def test_crashloop_none():
    assert detect_crashloop([{"waiting_reason": "Running"}]) is None


@pytest.mark.parametrize("reason", ["ErrImagePull", "ImagePullBackOff"])
def test_image_pull_failure(reason):
    d = detect_image_pull_failure([{"waiting_reason": reason, "image": "reg/api:v2.3.1"}])
    assert d.use_case == "C5" and "reg/api:v2.3.1" in d.evidence[1]


def test_image_pull_none():
    assert detect_image_pull_failure([]) is None


# ---- C6 ------------------------------------------------------------------
def test_hpa_flapping_detected():
    assert detect_hpa_flapping([3, 10, 3, 10, 3, 10, 3]).use_case == "C6"


def test_hpa_steady_scale_up_is_not_flapping():
    assert detect_hpa_flapping([3, 4, 5, 6, 7, 8, 9]) is None


def test_hpa_small_wobble_ignored():
    assert detect_hpa_flapping([3, 4, 3, 4, 3, 4, 3]) is None


def test_hpa_needs_history():
    assert detect_hpa_flapping([3, 10, 3]) is None


# ---- Detection ------------------------------------------------------------
def test_detection_to_dict_roundtrip():
    d = detect_latency_slo(1.0).to_dict()
    assert d["use_case"] == "B4" and isinstance(d["evidence"], list)
