"""
GhostOps Mock LLM — offline diagnosis and patches
=================================================

Stands in for the Diagnostic Reasoner and Patch Generator when
``GHOSTOPS_MOCK_LLM=1``, so the whole pipeline (OPA check, Docker sandbox,
knowledge base, dashboard) runs without an API key.

* ``diagnose()`` classifies the incident from the deterministic detectors.
* ``generate_patch()`` applies a known fix template and emits a real unified
  diff, which the Validation Officer then checks exactly like an LLM's diff.

Output shapes match the real agents' JSON so nothing downstream can tell
the difference — except ``"mock": true``, which the deployer uses to skip PRs.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from agents.detectors import detect_memory_leak, detect_traceback


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------
def _memory_samples(metrics: dict[str, Any]) -> tuple[list[float], float | None]:
    samples: list[float] = []
    limit: float | None = None
    for name, points in metrics.items():
        values = [float(p["value"]) for p in points if isinstance(p, dict) and "value" in p]
        if "limit" in name:
            limit = values[-1] if values else None
        elif "memory" in name and len(values) > len(samples):
            samples = values
    return samples, limit


def diagnose(failure_context: dict[str, Any]) -> dict[str, Any]:
    """Classify the incident the way the Diagnostic Reasoner would, from evidence only."""
    alert_type = failure_context.get("alert_type", "")
    logs: list[str] = [str(line) for line in failure_context.get("logs", [])]
    metrics: dict[str, Any] = failure_context.get("metrics", {}) or {}

    tb = detect_traceback(logs)
    if tb and ("NoneType" in tb.evidence[0] or alert_type == "app_exception"):
        return {
            "root_cause": "null_reference",
            "confidence": "high",
            "summary": f"{tb.evidence[0]} — a None value is used without a guard ({tb.evidence[1]}).",
            "evidence": [{"type": "log", "reference": e, "reasoning": "Traceback of the unhandled exception."} for e in tb.evidence],
            "mock": True,
        }

    if alert_type == "memory_high":
        samples, limit = _memory_samples(metrics)
        leak = detect_memory_leak(samples, limit, min_samples=3)
        evidence = [{"type": "metric", "reference": e, "reasoning": "Memory only ever grows."} for e in (leak.evidence if leak else [])]
        evidence += [{"type": "log", "reference": line, "reasoning": "Requests to the leaking endpoint."} for line in logs if "/leak" in line][:2]
        return {
            "root_cause": "memory_leak",
            "confidence": "high" if leak else "medium",
            "summary": "Memory grows with every request and is never released.",
            "evidence": evidence,
            "mock": True,
        }

    failed = [line for line in logs if "FAILED" in line or "AssertionError" in line or line.lstrip().startswith("E ")]
    if alert_type == "ci_failure" or failed:
        return {
            "root_cause": "ci_test_failure",
            "confidence": "high" if failed else "medium",
            "summary": "A unit test fails because of a logic regression in the application code.",
            "evidence": [{"type": "log", "reference": line.strip(), "reasoning": "Failing test output."} for line in failed[:3]],
            "mock": True,
        }

    return {
        "root_cause": "unknown",
        "confidence": "low",
        "summary": "Not enough evidence to classify this incident.",
        "evidence": [],
        "mock": True,
    }


# ---------------------------------------------------------------------------
# Patch templates
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FixTemplate:
    root_cause: str
    file_name: str  # basename the template applies to
    old: str
    new: str
    explanation: str


FIX_TEMPLATES: tuple[FixTemplate, ...] = (
    FixTemplate(
        root_cause="memory_leak",
        file_name="app.py",
        old="app = Flask(__name__)\nleak_store = []\n",
        new=(
            "app = Flask(__name__)\n"
            "# Bounded: the oldest chunks are evicted instead of being kept forever.\n"
            "leak_store = deque(maxlen=2)\n"
        ),
        explanation=(
            "leak_store was an unbounded list that kept a 10 MiB chunk for every /leak request. "
            "It is now a deque with maxlen=2, so old chunks are evicted and memory stays bounded."
        ),
    ),
    FixTemplate(
        root_cause="memory_leak",
        file_name="app.py",
        old="import os\n",
        new="import os\nfrom collections import deque\n",
        explanation="",
    ),
    FixTemplate(
        root_cause="ci_test_failure",
        file_name="math_utils.py",
        old="def multiply(a, b):\n    return a + b\n",
        new="def multiply(a, b):\n    return a * b\n",
        explanation="multiply() added its arguments instead of multiplying them; the operator is now *.",
    ),
    FixTemplate(
        root_cause="null_reference",
        file_name="user_service.py",
        old='    return profile["name"].strip()\n',
        new=(
            '    if not profile or not profile.get("name"):\n'
            '        return "anonymous"\n'
            '    return profile["name"].strip()\n'
        ),
        explanation=(
            "display_name() subscripted the upstream payload without checking it. It now returns "
            "the documented 'anonymous' fallback when the payload or its name is missing."
        ),
    ),
)


def unified_diff(path: str, before: str, after: str) -> str:
    """A ``--- a/path`` / ``+++ b/path`` unified diff, as the Patch Generator emits."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def generate_patch(diagnosis: dict[str, Any], source_files: dict[str, str]) -> dict[str, Any]:
    """Apply every matching fix template to the first file it fits; return diff + explanation."""
    root_cause = diagnosis.get("root_cause", "unknown")
    for path, content in source_files.items():
        name = PurePosixPath(path).name
        templates = [t for t in FIX_TEMPLATES if t.root_cause == root_cause and t.file_name == name and t.old in content]
        if not templates:
            continue
        patched = content
        for t in templates:
            patched = patched.replace(t.old, t.new, 1)
        return {
            "diff": unified_diff(path, content, patched),
            "explanation": " ".join(t.explanation for t in templates if t.explanation),
            "file_path": path,
            "mock": True,
        }
    return {
        "diff": "",
        "explanation": f"No offline fix template matches root cause '{root_cause}'.",
        "mock": True,
    }
