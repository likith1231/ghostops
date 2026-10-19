"""Metric-based detectors for the Category B use cases (B1, B2, B4, B5)."""

from __future__ import annotations

from collections.abc import Sequence

from agents.detectors.base import Detection


def detect_memory_leak(samples: Sequence[float], limit: float | None = None, min_samples: int = 4) -> Detection | None:
    """B1: memory that only ever grows (allowing a small dip tolerance)."""
    if len(samples) < min_samples:
        return None
    rises = sum(1 for a, b in zip(samples, samples[1:]) if b > a)
    if rises < len(samples) - 1 or samples[-1] <= samples[0] * 1.2:
        return None
    evidence = [f"memory grew {samples[0]:,.0f} -> {samples[-1]:,.0f} bytes over {len(samples)} samples"]
    severity = "high"
    if limit and samples[-1] >= 0.8 * limit:
        evidence.append(f"at {samples[-1] / limit:.0%} of the {limit:,.0f}-byte limit")
        severity = "critical"
    return Detection(
        use_case="B1",
        title="Memory leak (monotonic growth)",
        severity=severity,
        confidence=0.9,
        evidence=evidence,
        suggested_action="Bound the growing data structure (LRU / maxlen) or clear it after use.",
    )


def detect_cpu_throttling(throttled_ratio: float, usage_ratio: float, threshold: float = 0.25) -> Detection | None:
    """B2: a large share of CFS periods throttled while usage is pinned at the limit."""
    if throttled_ratio < threshold or usage_ratio < 0.9:
        return None
    return Detection(
        use_case="B2",
        title="CPU throttling",
        severity="medium",
        confidence=0.85,
        evidence=[f"{throttled_ratio:.0%} of CFS periods throttled at {usage_ratio:.0%} of the CPU limit"],
        suggested_action="Raise the CPU limit, or optimise the hot code path.",
    )


def detect_latency_slo(p99_seconds: float, slo_seconds: float = 0.5) -> Detection | None:
    """B4: P99 latency above the SLO target."""
    if p99_seconds <= slo_seconds:
        return None
    ratio = p99_seconds / slo_seconds
    return Detection(
        use_case="B4",
        title="P99 latency SLO breach",
        severity="critical" if ratio >= 4 else "high",
        confidence=0.8,
        evidence=[f"P99 {p99_seconds * 1000:.0f}ms vs SLO {slo_seconds * 1000:.0f}ms ({ratio:.1f}x)"],
        suggested_action="Check recent migrations for a dropped index and look for N+1 queries.",
    )


def detect_error_rate_slo(errors: float, total: float, slo: float = 0.01, min_requests: int = 20) -> Detection | None:
    """B5: 5xx ratio above the SLO threshold."""
    if total < min_requests:
        return None
    rate = errors / total
    if rate <= slo:
        return None
    return Detection(
        use_case="B5",
        title="Error-rate SLO breach",
        severity="critical" if rate >= 4 * slo else "high",
        confidence=0.85,
        evidence=[f"5xx rate {rate:.2%} vs SLO {slo:.2%} ({errors:.0f}/{total:.0f} requests)"],
        suggested_action="Correlate with the latest deployment; fix the regression or roll back.",
    )


def detect_disk_saturation(io_utilisation: Sequence[float], threshold: float = 0.95, min_consecutive: int = 3) -> Detection | None:
    """B3: disk busy (I/O time fraction) above *threshold* for several consecutive samples."""
    run = longest = 0
    for value in io_utilisation:
        run = run + 1 if value >= threshold else 0
        longest = max(longest, run)
    if longest < min_consecutive:
        return None
    return Detection(
        use_case="B3",
        title="Disk I/O saturation",
        severity="high",
        confidence=0.8,
        evidence=[f"disk at >= {threshold:.0%} utilisation for {longest} consecutive samples"],
        suggested_action="Find the scheduled job writing at that time and move it off-peak, or make its writes async.",
    )
