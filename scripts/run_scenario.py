"""
Run one GhostOps demo scenario through the agent pipeline and print a
pass/fail summary.

Usage (from the project root)::

    python -m scripts.run_scenario leak   # memory leak in sample-app/app.py
    python -m scripts.run_scenario ci     # failing test for sample-app/math_utils.py
    python -m scripts.run_scenario latest # summary of the newest incident (used by demo.sh)

Both scenarios use the real files in sample-app/, so a validated fix becomes a
PR against them. Exit code is 0 when the patch passed validation, 1 otherwise.
"""

from __future__ import annotations

import logging
import sys
import time

from agents.scenarios import ci_context, leak_context

GREEN, RED, BOLD, RESET = "\033[1;32m", "\033[1;31m", "\033[1m", "\033[0m"


def print_summary(title: str, root_cause, confidence, validation, pr_line, passed: bool, elapsed=None) -> None:
    print()
    print("=" * 72)
    print(f"{BOLD}{title}{RESET}")
    print("=" * 72)
    print(f"  Root cause  : {root_cause}")
    print(f"  Confidence  : {confidence}")
    if validation:
        print(f"  Validation  : {validation}")
    print(f"  Pull request: {pr_line}")
    if elapsed is not None:
        print(f"  Time taken  : {elapsed:.0f} s")
    print()
    if passed:
        print(f"{GREEN}  ✅ PASSED: the AI fix passed the app's tests in the sandbox{RESET}")
    else:
        print(f"{RED}  ❌ FAILED: the patch did not pass validation{RESET}")
    print("=" * 72)


def show_latest(title: str, elapsed: float | None) -> int:
    from agents.knowledge_base import get_past_incidents

    incidents = get_past_incidents(limit=1)
    if not incidents:
        print("No incidents recorded yet.")
        return 1
    inc = incidents[0]
    passed = bool(inc.get("validation_passed"))
    print_summary(title, inc.get("root_cause") or "-", inc.get("confidence") or "-", None,
                  inc.get("pr_url") or "not created", passed, elapsed)
    return 0 if passed else 1


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name == "latest":
        title = sys.argv[2] if len(sys.argv) > 2 else "Latest incident"
        elapsed = float(sys.argv[3]) if len(sys.argv) > 3 else None
        return show_latest(title, elapsed)

    scenarios = {
        "leak": ("Scenario 1: Memory leak (OOM risk)", leak_context),
        "ci": ("Scenario 2: CI test failure", ci_context),
    }
    if name not in scenarios:
        print("Usage: python -m scripts.run_scenario {leak|ci|latest}")
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from agents.crew import run_pipeline

    title, build_context = scenarios[name]
    started = time.monotonic()
    result = run_pipeline(build_context())
    elapsed = time.monotonic() - started

    diagnosis = result.get("diagnosis", {})
    validation = result.get("validation_result", {})
    pr = result.get("pr_result", {})
    pr_line = pr.get("pr_url", "") if pr.get("pr_created") else f"not created ({pr.get('reason', 'unknown')})"

    print_summary(
        title,
        diagnosis.get("root_cause", diagnosis.get("raw", "unknown")),
        diagnosis.get("confidence", "-"),
        validation.get("summary", validation.get("reason", "-")),
        pr_line,
        validation.get("passed") is True,
        elapsed,
    )
    return 0 if validation.get("passed") is True else 1


if __name__ == "__main__":
    sys.exit(main())
