"""
GhostOps Crew — Multi-Agent Diagnosis-and-Patch Pipeline
========================================================

Wires the three agents (Diagnostic Reasoner → Patch Generator → Validation
Officer) into a CrewAI ``Crew`` with a sequential task pipeline.

Public API
----------
``run_pipeline(failure_context: dict) -> dict``
    Executes the full diagnosis → patch → validation sequence and returns::

        {
            "diagnosis": { ... },      # Reasoner output
            "patch": { ... },           # Patch Generator output (diff + explanation)
            "validation_result": { ... } # Validation Officer verdict
        }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from crewai import Crew, Process

from agents.diagnostic_reasoner import build_reasoner_agent, build_reasoner_task
from agents.patch_generator import build_patch_agent, build_patch_task
from agents.validation_officer import build_validation_agent, build_validation_task

logger = logging.getLogger("ghostops.crew")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_json_loads(text: str) -> dict:
    """Try to parse *text* as JSON.  Strip markdown code fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove leading ```json and trailing ```
        lines = cleaned.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse agent output as JSON — returning raw text.")
        return {"raw": cleaned}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(failure_context: dict) -> dict[str, Any]:
    """Execute the GhostOps diagnosis → patch → validation pipeline.

    Parameters
    ----------
    failure_context:
        A dict with at least these keys:

        * ``alert_type`` — ``"memory_high"`` or ``"ci_failure"``
        * ``logs`` — list of recent log lines (strings)
        * ``metrics`` — dict of metric name → recent values
        * ``source_files`` — dict of ``{path: content}`` for relevant source
        * ``past_incidents`` — (optional) list of similar past incidents

    Returns
    -------
    dict
        ``{"diagnosis": ..., "patch": ..., "validation_result": ...}``
    """
    # ----- 1. DIAGNOSTIC REASONER ------------------------------------
    logger.info("Stage 1/3 — Running Diagnostic Reasoner …")
    reasoner_agent = build_reasoner_agent()
    reasoner_task = build_reasoner_task(reasoner_agent, failure_context)

    reasoner_crew = Crew(
        agents=[reasoner_agent],
        tasks=[reasoner_task],
        process=Process.sequential,
        verbose=True,
    )
    reasoner_result = reasoner_crew.kickoff()
    diagnosis_text = str(reasoner_result)
    diagnosis = _safe_json_loads(diagnosis_text)
    logger.info("Diagnosis: %s", diagnosis.get("root_cause", "unknown"))

    # ----- 2. PATCH GENERATOR ----------------------------------------
    logger.info("Stage 2/3 — Running Patch Generator …")
    source_files: dict[str, str] = failure_context.get("source_files", {})
    if not source_files:
        logger.warning("No source_files in failure_context — patch stage may fail.")

    patch_agent = build_patch_agent()
    patch_task = build_patch_task(patch_agent, diagnosis_text, source_files)

    patch_crew = Crew(
        agents=[patch_agent],
        tasks=[patch_task],
        process=Process.sequential,
        verbose=True,
    )
    patch_result = patch_crew.kickoff()
    patch_text = str(patch_result)
    patch = _safe_json_loads(patch_text)
    logger.info("Patch generated — diff length: %d chars", len(patch.get("diff", "")))

    # ----- 3. VALIDATION OFFICER -------------------------------------
    logger.info("Stage 3/3 — Running Validation Officer …")
    # Pick the first source file as the target for validation.
    file_path = next(iter(source_files), "app.py")
    original_content = source_files.get(file_path, "")
    diff_text = patch.get("diff", "")

    validation_agent = build_validation_agent()
    validation_task = build_validation_task(
        validation_agent, file_path, original_content, diff_text
    )

    validation_crew = Crew(
        agents=[validation_agent],
        tasks=[validation_task],
        process=Process.sequential,
        verbose=True,
    )
    validation_result_raw = validation_crew.kickoff()
    validation_result = _safe_json_loads(str(validation_result_raw))
    logger.info("Validation passed: %s", validation_result.get("passed"))

    # ----- 4. DEPLOYMENT ACTION (auto-PR on validated pass) -----------
    pr_result: dict[str, Any] = {"pr_created": False, "reason": "skipped"}

    if validation_result.get("passed") is True:
        logger.info("Stage 4/4 — Validation passed, creating PR …")
        try:
            from agents.deployer import create_pr

            pr_result = create_pr(
                pipeline_result={
                    "diagnosis": diagnosis,
                    "patch": patch,
                    "validation_result": validation_result,
                },
                failure_context=failure_context,
            )
            logger.info("PR result: %s", pr_result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Deployer failed: %s", exc, exc_info=True)
            pr_result = {"pr_created": False, "reason": str(exc)}
    else:
        logger.info("Validation did NOT pass — skipping PR creation.")
        pr_result = {"pr_created": False, "reason": "validation_failed"}

    # ----- 5. LOG TO KNOWLEDGE BASE (every run, pass or fail) --------
    try:
        from agents.knowledge_base import log_incident

        log_incident(
            failure_context=failure_context,
            diagnosis=diagnosis,
            patch=patch,
            validation_result=validation_result,
            pr_result=pr_result,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to log incident: %s", exc)

    # ----- ASSEMBLE FINAL RESULT -------------------------------------
    return {
        "diagnosis": diagnosis,
        "patch": patch,
        "validation_result": validation_result,
        "pr_result": pr_result,
    }


# ---------------------------------------------------------------------------
# CLI entry point (for quick manual testing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # ---------- Sample B1 failure context (memory leak) ----------
    sample_b1: dict[str, Any] = {
        "alert_type": "memory_high",
        "logs": [
            "2026-08-07T08:00:12Z app WARNING: /api/data handler allocated 2.3 GB",
            "2026-08-07T08:01:45Z kernel: Out of memory: Killed process 4321 (gunicorn)",
            "2026-08-07T08:01:45Z app ERROR: Worker process terminated (OOM)",
            "2026-08-07T07:55:00Z app INFO: request_cache size: 148201 entries",
            "2026-08-07T07:50:00Z app INFO: request_cache size: 102300 entries",
        ],
        "metrics": {
            "container_memory_usage_bytes": [
                {"ts": "08:00", "value": 1_900_000_000},
                {"ts": "07:55", "value": 1_600_000_000},
                {"ts": "07:50", "value": 1_200_000_000},
                {"ts": "07:45", "value": 800_000_000},
            ],
            "container_memory_limit_bytes": 2_000_000_000,
        },
        "source_files": {
            "app.py": (
                "from flask import Flask, jsonify\n"
                "\n"
                "app = Flask(__name__)\n"
                "\n"
                "# In-memory cache with no eviction policy\n"
                "request_cache = []\n"
                "\n"
                "\n"
                "@app.route('/api/data')\n"
                "def get_data():\n"
                "    result = expensive_computation()\n"
                "    request_cache.append(result)  # never cleared\n"
                "    return jsonify(result)\n"
                "\n"
                "\n"
                "def expensive_computation():\n"
                "    return {'value': list(range(10000))}\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    app.run(debug=True)\n"
            ),
        },
        "past_incidents": [
            {
                "id": "INC-0042",
                "summary": "OOM kill on /api/data endpoint due to unbounded cache",
                "resolution": "Added max-size limit to request_cache",
            }
        ],
    }

    # ---------- Sample C4 failure context (CI test failure) ----------
    sample_c4: dict[str, Any] = {
        "alert_type": "ci_failure",
        "logs": [
            "Run pytest tests/ -v",
            "tests/test_math_utils.py::test_add PASSED",
            "tests/test_math_utils.py::test_multiply FAILED",
            "FAILED tests/test_math_utils.py::test_multiply - AssertionError: assert 6 == 8",
            "=== 1 failed, 1 passed in 0.04s ===",
        ],
        "metrics": {},
        "source_files": {
            "math_utils.py": (
                "def add(a, b):\n"
                "    return a + b\n"
                "\n"
                "\n"
                "def multiply(a, b):\n"
                "    return a + b  # BUG: should be a * b\n"
            ),
            "tests/test_math_utils.py": (
                "from math_utils import add, multiply\n"
                "\n"
                "\n"
                "def test_add():\n"
                "    assert add(2, 3) == 5\n"
                "\n"
                "\n"
                "def test_multiply():\n"
                "    assert multiply(2, 4) == 8\n"
            ),
        },
        "past_incidents": [],
    }

    # Pick which sample to run based on CLI arg.
    if len(sys.argv) > 1 and sys.argv[1] == "c4":
        context = sample_c4
        print("Running pipeline with C4 (CI failure) sample context …")
    else:
        context = sample_b1
        print("Running pipeline with B1 (memory leak) sample context …")

    result = run_pipeline(context)
    print("\n" + "=" * 72)
    print("PIPELINE RESULT")
    print("=" * 72)
    print(json.dumps(result, indent=2))
