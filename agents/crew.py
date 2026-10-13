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
from opentelemetry import trace

logger = logging.getLogger("ghostops.crew")
tracer = trace.get_tracer(__name__)


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
    
    with tracer.start_as_current_span("diagnostic_reasoner") as span:
        span.set_attribute("alert_type", failure_context.get("alert_type", "unknown"))
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

    with tracer.start_as_current_span("patch_generator"):
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

    with tracer.start_as_current_span("validation_officer") as span:
        validation_agent = build_validation_agent()
        # The other files (e.g. the tests) go into the sandbox alongside the patched one.
        extra_files = {k: v for k, v in source_files.items() if k != file_path}
        validation_task = build_validation_task(
            validation_agent, file_path, original_content, diff_text, extra_files
        )

        validation_crew = Crew(
            agents=[validation_agent],
            tasks=[validation_task],
            process=Process.sequential,
            verbose=True,
        )
        validation_result_raw = validation_crew.kickoff()
        validation_result = _safe_json_loads(str(validation_result_raw))
        span.set_attribute("validation_passed", validation_result.get("passed", False))
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

    from agents.scenarios import ci_context, leak_context

    # Pick which scenario to run based on CLI arg.
    if len(sys.argv) > 1 and sys.argv[1] == "c4":
        print("Running pipeline with C4 (CI failure): sample-app/math_utils.py …")
        context = ci_context()
    else:
        print("Running pipeline with B1 (memory leak): sample-app/app.py …")
        context = leak_context()

    result = run_pipeline(context)
    print("\n" + "=" * 72)
    print("PIPELINE RESULT")
    print("=" * 72)
    print(json.dumps(result, indent=2))
