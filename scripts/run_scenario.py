"""
Run one GhostOps demo scenario through the agent pipeline and print a
pass/fail summary.

Usage (from the project root)::

    python -m scripts.run_scenario leak   # B1 — memory leak / OOM kill
    python -m scripts.run_scenario ci     # C4 — CI test failure

Exit code is 0 when the patch passed validation, 1 otherwise.
"""

from __future__ import annotations

import logging
import sys
import time

from agents.crew import SAMPLE_B1, SAMPLE_C4, run_pipeline

SCENARIOS = {
    "leak": ("Scenario 1 — Memory leak (OOM kill)", SAMPLE_B1),
    "ci": ("Scenario 2 — CI test failure", SAMPLE_C4),
}

GREEN, RED, BOLD, RESET = "\033[1;32m", "\033[1;31m", "\033[1m", "\033[0m"


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SCENARIOS:
        print(f"Usage: python -m scripts.run_scenario {{{'|'.join(SCENARIOS)}}}")
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    title, context = SCENARIOS[name]

    started = time.monotonic()
    result = run_pipeline(context)
    elapsed = time.monotonic() - started

    diagnosis = result.get("diagnosis", {})
    validation = result.get("validation_result", {})
    pr = result.get("pr_result", {})
    passed = validation.get("passed") is True

    if pr.get("pr_created"):
        pr_line = pr.get("pr_url", "")
    else:
        pr_line = f"not created ({pr.get('reason', 'unknown')})"

    print()
    print("=" * 72)
    print(f"{BOLD}{title}{RESET}")
    print("=" * 72)
    print(f"  Root cause : {diagnosis.get('root_cause', diagnosis.get('raw', 'unknown'))}")
    print(f"  Confidence : {diagnosis.get('confidence', '-')}")
    print(f"  Validation : {validation.get('summary', validation.get('reason', '-'))}")
    print(f"  Pull request: {pr_line}")
    print(f"  Time taken : {elapsed:.0f} s")
    print()
    if passed:
        print(f"{GREEN}  ✅ PASSED — the AI fix was validated in the sandbox{RESET}")
    else:
        print(f"{RED}  ❌ FAILED — the patch did not pass validation{RESET}")
    print("=" * 72)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
