"""Log-based detectors for the Category A use cases (A1-A5) and B6."""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence

from agents.detectors.base import Detection

_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):")
_EXC_LINE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception)): (.*)$")
_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_IMPORT_ERR = re.compile(r"(?:ModuleNotFoundError: No module named|ImportError: cannot import name) '([^']+)'")
_POOL = re.compile(r"QueuePool limit of size (\d+) overflow (\d+) reached")
_AUTH = re.compile(r"\b401\b|Unauthorized|token expired", re.IGNORECASE)
_DNS = re.compile(r"getaddrinfo failed|Name or service not known|DNS resolution timeout", re.IGNORECASE)


def detect_traceback(logs: Sequence[str]) -> Detection | None:
    """A1: an unhandled Python exception with a traceback."""
    for i, line in enumerate(logs):
        if not _TRACEBACK_START.match(line.strip()):
            continue
        frames = []
        for follow in logs[i + 1 :]:
            m = _FRAME.search(follow)
            if m:
                frames.append(m.groups())
                continue
            exc = _EXC_LINE.match(follow.strip())
            if exc:
                file, lineno, func = frames[-1] if frames else ("?", "?", "?")
                return Detection(
                    use_case="A1",
                    title=f"Unhandled {exc.group(1)}",
                    severity="high",
                    confidence=0.95 if frames else 0.7,
                    evidence=[f"{exc.group(1)}: {exc.group(2)}", f"at {file}:{lineno} in {func}"],
                    suggested_action="Add a guard for the failing expression and re-run the tests.",
                )
    return None


def detect_import_failure(logs: Sequence[str]) -> Detection | None:
    """A2: a missing or broken dependency at startup."""
    for line in logs:
        m = _IMPORT_ERR.search(line)
        if m:
            return Detection(
                use_case="A2",
                title=f"Missing dependency '{m.group(1)}'",
                severity="critical",
                confidence=0.9,
                evidence=[line.strip()],
                suggested_action=f"Restore or pin '{m.group(1)}' in requirements.txt.",
            )
    return None


def detect_pool_exhaustion(logs: Sequence[str], min_hits: int = 3) -> Detection | None:
    """A3: repeated database connection-pool timeouts."""
    hits = [line for line in logs if _POOL.search(line)]
    if len(hits) < min_hits:
        return None
    size, overflow = _POOL.search(hits[0]).groups()
    return Detection(
        use_case="A3",
        title="Database connection pool exhausted",
        severity="high",
        confidence=min(0.6 + 0.05 * len(hits), 0.95),
        evidence=[f"{len(hits)} pool timeouts (size={size}, overflow={overflow})", hits[0].strip()],
        suggested_action="Release sessions in a try/finally block or a context manager.",
    )


def detect_auth_burst(
    events: Sequence[tuple[float, str, str]], window_s: float = 60.0, min_services: int = 3, min_events: int = 10
) -> Detection | None:
    """A4: a burst of 401s across several services in a narrow window.

    ``events`` are ``(epoch_seconds, service, log_line)``.
    """
    hits = sorted((t, svc) for t, svc, line in events if _AUTH.search(line))
    start = 0
    for end in range(len(hits)):
        while hits[end][0] - hits[start][0] > window_s:
            start += 1
        window = hits[start : end + 1]
        services = {svc for _, svc in window}
        if len(window) >= min_events and len(services) >= min_services:
            return Detection(
                use_case="A4",
                title="Authentication failure cascade",
                severity="critical",
                confidence=0.85,
                evidence=[f"{len(window)} auth failures across {len(services)} services in {window_s:.0f}s"],
                suggested_action="Check the token issuer's recent restart or key rotation; enable dual-key validation.",
            )
    return None


def detect_log_spike(
    rate_history: Sequence[float], current_rate: float, z_threshold: float = 4.0, min_ratio: float = 10.0
) -> Detection | None:
    """A5: log lines per minute far above the service's baseline."""
    if len(rate_history) < 5:
        return None
    mean = statistics.fmean(rate_history)
    stdev = statistics.pstdev(rate_history) or 1.0
    z = (current_rate - mean) / stdev
    ratio = current_rate / mean if mean else float("inf")
    if z < z_threshold or ratio < min_ratio:
        return None
    return Detection(
        use_case="A5",
        title="Anomalous log volume spike",
        severity="medium",
        confidence=0.8,
        evidence=[f"{current_rate:.0f} lines/min vs baseline {mean:.0f} ({ratio:.0f}x, z={z:.1f})"],
        suggested_action="Find the hot-loop debug statement or retry loop and gate or remove it.",
    )


def detect_dns_failure(logs: Sequence[str], min_hits: int = 3) -> Detection | None:
    """B6: repeated DNS / name-resolution failures."""
    hits = [line for line in logs if _DNS.search(line)]
    if len(hits) < min_hits:
        return None
    return Detection(
        use_case="B6",
        title="DNS / service resolution failure",
        severity="high",
        confidence=0.85,
        evidence=[f"{len(hits)} resolution failures", hits[0].strip()],
        suggested_action="Verify the target Service exists, then check CoreDNS and NetworkPolicy.",
    )
