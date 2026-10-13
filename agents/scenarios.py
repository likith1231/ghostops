"""
GhostOps demo scenarios
=======================

Failure contexts for the two demo incidents, built from the real files in
sample-app/ so a validated fix becomes a PR against them:

* B1 ``leak_context()`` — /leak in sample-app/app.py keeps 10 MiB per request.
* C4 ``ci_context()``   — multiply() in sample-app/math_utils.py fails its test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(*paths: str) -> dict[str, str]:
    return {p: (ROOT / p).read_text(encoding="utf-8") for p in paths}


def leak_context() -> dict:
    """The live alert's context, with representative logs and metrics."""
    return {
        "alert_type": "memory_high",
        "logs": [
            '10.244.0.1 - - "GET /leak HTTP/1.1" 200 -',
            '10.244.0.1 - - "GET /leak HTTP/1.1" 200 -',
            '10.244.0.1 - - "GET /leak HTTP/1.1" 200 -',
            '10.244.0.1 - - "GET /health HTTP/1.1" 200 -',
        ],
        "metrics": {
            "container_memory_working_set_bytes": [
                {"ts": "t-60s", "value": 27_000_000},
                {"ts": "t-30s", "value": 80_000_000},
                {"ts": "t", "value": 118_000_000},
            ],
            "container_spec_memory_limit_bytes": [{"ts": "t", "value": 134_217_728}],
        },
        "source_files": _read("sample-app/app.py", "sample-app/test_app.py"),
        "past_incidents": [],
    }


def ci_context() -> dict:
    """Run the failing test for real and hand its output to the agents."""
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "test_math_utils.py", "-v", "--tb=short", "-p", "no:cacheprovider"],
        cwd=ROOT / "sample-app", capture_output=True, text=True,
    )
    print(run.stdout)
    return {
        "alert_type": "ci_failure",
        "logs": ["$ pytest sample-app/test_math_utils.py -v", *run.stdout.splitlines()[-25:]],
        "metrics": {},
        "source_files": _read("sample-app/math_utils.py", "sample-app/test_math_utils.py"),
        "past_incidents": [],
    }
