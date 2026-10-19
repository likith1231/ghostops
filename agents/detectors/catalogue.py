"""The 17 use cases from the GhostOps use-case document, with implementation status."""

from __future__ import annotations

# end_to_end: diagnosed, patched, sandbox-validated and turned into a PR by the pipeline.
# detector:   the name of the deterministic detector in agents.detectors, if any.
USE_CASES: tuple[dict, ...] = (
    {"id": "A1", "category": "logs", "title": "Unhandled exception", "signal": "Traceback in stderr", "remediation": "Null-safety guard", "detector": "detect_traceback", "end_to_end": True},
    {"id": "A2", "category": "logs", "title": "Import failure", "signal": "ModuleNotFoundError at startup", "remediation": "Pin or restore the dependency", "detector": "detect_import_failure", "end_to_end": False},
    {"id": "A3", "category": "logs", "title": "Connection pool exhaustion", "signal": "QueuePool limit reached", "remediation": "try/finally or a context manager", "detector": "detect_pool_exhaustion", "end_to_end": False},
    {"id": "A4", "category": "logs", "title": "Auth token cascade", "signal": "401 burst across services", "remediation": "Dual-key validation", "detector": "detect_auth_burst", "end_to_end": False},
    {"id": "A5", "category": "logs", "title": "Log volume spike", "signal": "10x+ log rate", "remediation": "Gate the hot-loop log statement", "detector": "detect_log_spike", "end_to_end": False},
    {"id": "B1", "category": "metrics", "title": "Memory leak / OOM", "signal": "Monotonic memory growth", "remediation": "Bound the growing structure", "detector": "detect_memory_leak", "end_to_end": True},
    {"id": "B2", "category": "metrics", "title": "CPU throttling", "signal": "CFS throttled periods rising", "remediation": "Raise the limit or optimise", "detector": "detect_cpu_throttling", "end_to_end": False},
    {"id": "B3", "category": "metrics", "title": "Disk I/O saturation", "signal": "Sustained 100% disk utilisation", "remediation": "Reschedule heavy jobs", "detector": "detect_disk_saturation", "end_to_end": False},
    {"id": "B4", "category": "metrics", "title": "P99 latency spike", "signal": "Histogram past the SLO", "remediation": "Restore index / fix N+1", "detector": "detect_latency_slo", "end_to_end": False},
    {"id": "B5", "category": "metrics", "title": "Error-rate SLO breach", "signal": "5xx ratio over threshold", "remediation": "Fix regression or roll back", "detector": "detect_error_rate_slo", "end_to_end": False},
    {"id": "B6", "category": "metrics", "title": "DNS / network failure", "signal": "getaddrinfo failures", "remediation": "Recreate Service / fix NetworkPolicy", "detector": "detect_dns_failure", "end_to_end": False},
    {"id": "C1", "category": "deploy", "title": "Canary regression", "signal": "Canary vs stable error divergence", "remediation": "Roll back the canary", "detector": "detect_canary_regression", "end_to_end": False},
    {"id": "C2", "category": "deploy", "title": "Configuration drift", "signal": "Live state differs from Git", "remediation": "Sync Git and cluster", "detector": "detect_config_drift", "end_to_end": False},
    {"id": "C3", "category": "deploy", "title": "CrashLoopBackOff", "signal": "Restarts after an upgrade", "remediation": "Fix tag / Secret / limits", "detector": "detect_crashloop", "end_to_end": False},
    {"id": "C4", "category": "deploy", "title": "CI test failure", "signal": "Failing pytest run", "remediation": "Patch the regression", "detector": None, "end_to_end": True},
    {"id": "C5", "category": "deploy", "title": "Image pull failure", "signal": "ErrImagePull / ImagePullBackOff", "remediation": "Correct tag or refresh credentials", "detector": "detect_image_pull_failure", "end_to_end": False},
    {"id": "C6", "category": "deploy", "title": "HPA thrashing", "signal": "Replica count oscillation", "remediation": "Dampen scaling", "detector": "detect_hpa_flapping", "end_to_end": False},
)
