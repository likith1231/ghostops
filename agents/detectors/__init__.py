"""Deterministic anomaly detectors (no LLM) for the GhostOps use-case catalogue."""

from __future__ import annotations

from agents.detectors.base import Detection
from agents.detectors.deploy import (
    detect_canary_regression,
    detect_config_drift,
    detect_crashloop,
    detect_hpa_flapping,
    detect_image_pull_failure,
)
from agents.detectors.logs import (
    detect_auth_burst,
    detect_dns_failure,
    detect_import_failure,
    detect_log_spike,
    detect_pool_exhaustion,
    detect_traceback,
)
from agents.detectors.metrics import (
    detect_cpu_throttling,
    detect_error_rate_slo,
    detect_latency_slo,
    detect_memory_leak,
)

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def run_log_detectors(logs: list[str]) -> list[Detection]:
    """Run every log-line detector, most severe first."""
    found = [
        d
        for d in (
            detect_traceback(logs),
            detect_import_failure(logs),
            detect_pool_exhaustion(logs),
            detect_dns_failure(logs),
        )
        if d
    ]
    return sorted(found, key=lambda d: (_SEVERITY_ORDER[d.severity], -d.confidence))


__all__ = [
    "Detection",
    "detect_auth_burst",
    "detect_canary_regression",
    "detect_config_drift",
    "detect_cpu_throttling",
    "detect_crashloop",
    "detect_dns_failure",
    "detect_error_rate_slo",
    "detect_hpa_flapping",
    "detect_image_pull_failure",
    "detect_import_failure",
    "detect_latency_slo",
    "detect_log_spike",
    "detect_memory_leak",
    "detect_pool_exhaustion",
    "detect_traceback",
    "run_log_detectors",
]
