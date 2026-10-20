"""
Risk scoring and graduated autonomy
===================================

Every validated fix gets a 0–100 risk score with an explained breakdown, and
the score decides how much autonomy GhostOps takes:

* low    → open the PR against the staging branch and auto-merge it;
* medium → open a PR for human review;
* high   → open a PR, label it ``needs-human`` and page someone.

The kill switch overrides all of it.
"""

from __future__ import annotations

import re
from typing import Any

LOW_BELOW = 30
HIGH_FROM = 60

DECISIONS = {"low": "auto_merge_staging", "medium": "open_pr", "high": "open_pr_and_page"}


def _tests_ran(validation: dict[str, Any]) -> int:
    m = re.search(r"(\d+) passed", str(validation.get("test_output", "")) + " " + str(validation.get("summary", "")))
    return int(m.group(1)) if m else 0


def score(
    *,
    diagnosis: dict[str, Any],
    patch: dict[str, Any],
    validation: dict[str, Any],
    blast: dict[str, Any] | None = None,
    shield: dict[str, Any] | None = None,
    acceptance_rate: float | None = None,
    occurrences: int = 1,
    regression_test: bool = False,
    replay_verdict: str | None = None,
    canary_verdict: str | None = None,
) -> dict[str, Any]:
    """Risk of letting this fix through, with the factors that produced it."""
    factors: list[dict[str, Any]] = []

    def add(name: str, points: int, why: str) -> None:
        factors.append({"factor": name, "points": points, "why": why})

    metrics = (blast or {}).get("metrics") or {}
    changed = int(metrics.get("added", 0)) + int(metrics.get("removed", 0))
    if not metrics:
        changed = sum(1 for l in str(patch.get("diff", "")).splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
    add("diff size", min(25, round(changed * 25 / 40)), f"{changed} changed line(s)")
    files = int(metrics.get("files", 1) or 1)
    if files > 1:
        add("files touched", 10 * (files - 1), f"{files} files")

    confidence = str(diagnosis.get("confidence", "")).lower()
    try:
        numeric = float(confidence)
        confidence = "high" if numeric >= 0.8 else "medium" if numeric >= 0.5 else "low"
    except ValueError:
        pass
    add("AI confidence", {"high": 0, "medium": 15}.get(confidence, 30), f"diagnosis confidence: {confidence or 'unknown'}")

    if acceptance_rate is None:
        add("track record", 5, "no human decisions on this kind of fix yet")
    elif acceptance_rate >= 80:
        add("track record", -10, f"{acceptance_rate:.0f}% of these fixes were merged")
    elif acceptance_rate < 50:
        add("track record", 15, f"only {acceptance_rate:.0f}% of these fixes were merged")
    else:
        add("track record", 0, f"{acceptance_rate:.0f}% of these fixes were merged")

    tests = _tests_ran(validation)
    add("test evidence", 0 if tests >= 5 else 10 if tests >= 1 else 25, f"{tests} test(s) ran in the sandbox")
    if regression_test:
        add("regression test", -10, "a test that fails before and passes after the fix")
    if canary_verdict in ("passed", "promoted"):
        add("canary", -10, "the fix held up on a real canary pod under the incident's traffic")
    if replay_verdict == "reproduced_and_fixed":
        add("counterfactual replay", -10, "the incident's real traffic reproduced the bug and the fix removed it")

    shield_risk = (shield or {}).get("risk", "none")
    if shield_risk in ("elevated", "high"):
        add("prompt injection", 15 if shield_risk == "elevated" else 30, f"shield risk {shield_risk}: telemetry contained injection attempts")

    if occurrences > 1:
        add("recurrence", min(15, 5 * (occurrences - 1)), f"seen {occurrences} times")

    total = max(0, min(100, sum(f["points"] for f in factors)))
    level = "low" if total < LOW_BELOW else "medium" if total < HIGH_FROM else "high"
    return {"score": total, "level": level, "decision": DECISIONS[level], "factors": factors}
