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
import os
from collections.abc import Callable
from typing import Any

from crewai import Crew, Process

from agents import blast_radius, correlation, evidence, feedback, governance, mock_llm, postmortem, regression, replay, risk, shield
from agents.config import MOCK_LLM
from agents.diagnostic_reasoner import build_reasoner_agent, build_reasoner_task
from agents.patch_generator import build_patch_agent, build_patch_task
from agents.validation_officer import (
    build_validation_agent,
    build_validation_task,
    interpret_sandbox_result,
    _run_tests_in_sandbox,
    run_validation_checks,
    verify_regression_test,
)
from opentelemetry import trace

logger = logging.getLogger("ghostops.crew")
tracer = trace.get_tracer(__name__)

# In mock mode a validated fix opens a real PR only when this is also set.
MOCK_CREATE_PR: bool = os.getenv("GHOSTOPS_MOCK_CREATE_PR", "").lower() in {"1", "true", "yes"}

StageCallback = Callable[[str, str, dict], None]


def _no_op(stage: str, status: str, data: dict) -> None:
    """Default stage callback."""


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
def run_pipeline(failure_context: dict, on_stage: StageCallback | None = None, fix_canary: Callable[..., dict] | None = None) -> dict[str, Any]:
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
    on_stage:
        Optional ``(stage, status, data)`` callback, called as each stage
        (``diagnosis``, ``patch``, ``validation``, ``deploy``) starts and ends.
        The backend uses it to stream live progress to the dashboard.
    fix_canary:
        Optional ``(file_path, diff, logs, run_ref=, risk_level=) -> result``: canary the
        validated fix on the cluster before delivering it (the backend passes one for
        real alerts).

    Returns
    -------
    dict
        ``{"diagnosis": ..., "patch": ..., "validation_result": ...}``
    """
    notify = on_stage or _no_op

    # ----- 0. SHIELD: untrusted telemetry never reaches the model raw ----
    failure_context, shield_report = shield.sanitize(failure_context)
    if shield_report["findings"]:
        logger.warning("Shield: %s risk, %d line(s) quarantined", shield_report["risk"], shield_report["quarantined"])
        governance.record(
            "shield", shield_report["risk"], f"{shield_report['quarantined']} line(s) quarantined",
            run_ref=failure_context.get("run_ref"), evidence={"findings": shield_report["findings"][:10]},
        )

    # ----- 1. DIAGNOSTIC REASONER ------------------------------------
    logger.info("Stage 1/3 — Running Diagnostic Reasoner …")
    notify("diagnosis", "running", {})

    with tracer.start_as_current_span("diagnostic_reasoner") as span:
        span.set_attribute("alert_type", failure_context.get("alert_type", "unknown"))
        if MOCK_LLM:
            diagnosis = mock_llm.diagnose(failure_context)
            diagnosis_text = json.dumps(diagnosis)
        else:
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
    run_ref = failure_context.get("run_ref")
    governance.record(
        "diagnose", str(diagnosis.get("root_cause", "unknown")), str(diagnosis.get("summary", ""))[:300],
        run_ref=run_ref, evidence={"confidence": diagnosis.get("confidence"), "mock": MOCK_LLM},
    )
    notify("diagnosis", "done", diagnosis)

    # ----- 2. PATCH GENERATOR ----------------------------------------
    logger.info("Stage 2/3 — Running Patch Generator …")
    notify("patch", "running", {})
    source_files: dict[str, str] = failure_context.get("source_files", {})
    if not source_files:
        logger.warning("No source_files in failure_context — patch stage may fail.")

    with tracer.start_as_current_span("patch_generator"):
        if MOCK_LLM:
            patch = mock_llm.generate_patch(diagnosis, source_files)
        else:
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
    governance.record(
        "patch", "generated" if patch.get("diff") else "none", str(patch.get("explanation", ""))[:300],
        run_ref=run_ref, evidence={"diff_chars": len(patch.get("diff", "")), "file": patch.get("file_path")},
    )
    notify("patch", "done", patch)

    # ----- 3. VALIDATION OFFICER -------------------------------------
    logger.info("Stage 3/3 — Running Validation Officer …")
    notify("validation", "running", {})
    # The mock names the file it patched; it is only trusted if it is one of the
    # known source files. Otherwise take the first source file.
    named = patch.get("file_path")
    file_path = named if named in source_files else next(iter(source_files), "app.py")
    original_content = source_files.get(file_path, "")
    diff_text = patch.get("diff", "")
    # The other files (e.g. the tests) go into the sandbox alongside the patched one.
    extra_files = {k: v for k, v in source_files.items() if k != file_path}

    with tracer.start_as_current_span("validation_officer") as span:
        if MOCK_LLM:
            validation_result = interpret_sandbox_result(
                run_validation_checks(file_path, original_content, diff_text, extra_files)
            )
        else:
            validation_agent = build_validation_agent()
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
        # A regression test that fails before the fix and passes after it (both proven in the sandbox).
        if validation_result.get("passed") is True:
            test = regression.generate(diagnosis, {**patch, "file_path": file_path})
            if test:
                proof = verify_regression_test(file_path, original_content, diff_text, extra_files, test)
                validation_result["regression_test"] = proof
                if proof["verified"]:
                    patch["regression_test"] = {"path": proof["path"], "content": proof["content"]}
                governance.record(
                    "regression_test", "verified" if proof["verified"] else "rejected",
                    f"{proof['path']}: failed before={proof['failed_before']}, passed after={proof['passed_after']}",
                    run_ref=failure_context.get("run_ref"),
                )
        # Counterfactual replay: the incident's real traffic, before and after the fix.
        if validation_result.get("passed") is True:
            replayed = run_replay(file_path, original_content, diff_text, extra_files, failure_context.get("replay_logs") or failure_context.get("logs", []))
            validation_result["replay"] = replayed
            governance.record("replay", replayed["verdict"], replayed["summary"], run_ref=failure_context.get("run_ref"))
            if replayed["verdict"] == "not_fixed":
                validation_result["passed"] = False
                validation_result["summary"] = f"Counterfactual replay: {replayed['summary']}"
        span.set_attribute("validation_passed", validation_result.get("passed", False))
        logger.info("Validation passed: %s", validation_result.get("passed"))
    governance.record(
        "validate", "passed" if validation_result.get("passed") is True else "failed", str(validation_result.get("summary", ""))[:300],
        run_ref=run_ref, evidence={"failing_tests": validation_result.get("failing_tests", [])},
    )
    notify("validation", "done", validation_result)

    # ----- 4. DEPLOYMENT ACTION (auto-PR on validated pass) -----------
    pr_result: dict[str, Any] = {"pr_created": False, "reason": "skipped"}

    # ----- RISK: how much autonomy this fix gets -------------------------------
    risk_report = None
    if validation_result.get("passed") is True:
        try:
            rate = feedback.acceptance(str(diagnosis.get("root_cause", "")))["acceptance_rate"]
        except Exception:  # noqa: BLE001
            rate = None
        blast_report = blast_radius.analyze(diff_text, original_content)

        def score(canary_verdict=None):
            return risk.score(
                diagnosis=diagnosis, patch=patch, validation=validation_result, blast=blast_report, shield=shield_report,
                acceptance_rate=rate, occurrences=int(failure_context.get("occurrences", 1) or 1),
                regression_test=bool(patch.get("regression_test")),
                replay_verdict=(validation_result.get("replay") or {}).get("verdict"), canary_verdict=canary_verdict,
            )

        risk_report = score()

        # ----- CANARY: the fix on a real pod before it goes anywhere else ------
        if fix_canary is not None:
            notify("validation", "running", {**validation_result, "step": "fix_canary"})
            canary_result = fix_canary(
                file_path, diff_text, failure_context.get("replay_logs") or failure_context.get("logs", []),
                run_ref=run_ref, risk_level=risk_report["level"],
                # The incident's own leak rate, measured by the counterfactual replay.
                incident_kib_per_request=((validation_result.get("replay") or {}).get("unpatched") or {}).get("kib_growth_per_request"),
            )
            validation_result["fix_canary"] = canary_result
            if canary_result.get("verdict") in ("failed", "error"):
                validation_result["passed"] = False
                validation_result["summary"] = canary_result.get("summary", "The fix canary did not hold up.")
                risk_report = None
            else:
                risk_report = score(canary_result.get("verdict"))
            notify("validation", "done", validation_result)

    if risk_report is not None:
        governance.record(
            "risk", risk_report["level"], f"score {risk_report['score']} → {risk_report['decision']}",
            run_ref=run_ref, evidence={"factors": risk_report["factors"]},
        )
        # Proof-carrying fix: signed evidence of every gate this patch passed.
        try:
            signed = evidence.sign(evidence.build(
                run_ref=run_ref, alert_type=str(failure_context.get("alert_type", "unknown")), diagnosis=diagnosis,
                patch={**patch, "file_path": file_path}, validation=validation_result,
                blast=blast_report, risk=risk_report, shield=shield_report,
            ))
            patch["evidence"] = signed
            governance.record("evidence", "signed", f"key {signed['key_fingerprint']}", run_ref=run_ref,
                              evidence={"diff_sha256": signed["bundle"]["patch"]["diff_sha256"]})
        except Exception as exc:  # noqa: BLE001 - evidence must never block a validated fix
            logger.error("Could not sign evidence: %s", exc)

    notify("deploy", "running", {})
    existing_pr = (
        correlation.open_pr_for(str(diagnosis.get("root_cause", "")), patch.get("file_path") or file_path)
        if validation_result.get("passed") is True else None
    )
    if existing_pr:
        logger.info("An open PR already addresses %s: %s", diagnosis.get("root_cause"), existing_pr)
        pr_result = {"pr_created": False, "reason": "duplicate_of_open_pr", "pr_url": existing_pr}
    elif validation_result.get("passed") is True and not governance.allow("open_pr", run_ref=run_ref):
        pr_result = {"pr_created": False, "reason": "autonomy_paused"}
    elif validation_result.get("passed") is True and MOCK_LLM and not MOCK_CREATE_PR:
        logger.info("Stage 4/4 — Validation passed (mock mode), skipping PR creation.")
        pr_result = {"pr_created": False, "reason": "mock_mode"}
    elif validation_result.get("passed") is True:
        logger.info("Stage 4/4 — Validation passed, creating PR …")
        try:
            from agents.deployer import create_pr

            pr_result = _deliver(diagnosis, patch, validation_result, risk_report, failure_context, run_ref, create_pr)
            logger.info("PR result: %s", pr_result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Deployer failed: %s", exc, exc_info=True)
            pr_result = {"pr_created": False, "reason": str(exc)}
    else:
        logger.info("Validation did NOT pass — skipping PR creation.")
        pr_result = {"pr_created": False, "reason": "validation_failed"}

    governance.record(
        "open_pr", "opened" if pr_result.get("pr_created") else "skipped",
        str(pr_result.get("pr_url") or pr_result.get("reason", "")), run_ref=run_ref, evidence=pr_result,
    )
    notify("deploy", "done", pr_result)

    # ----- POSTMORTEM (every run, pass or fail) -----------------------
    report = None
    try:
        report = postmortem.write(
            run_ref=run_ref, alert_type=str(failure_context.get("alert_type", "unknown")), diagnosis=diagnosis,
            patch={**patch, "file_path": file_path}, validation=validation_result, outcome=pr_result,
            started_at=failure_context.get("started_at"),
        )
        if pr_result.get("pr_created") and pr_result.get("pr_number"):
            from agents import deployer

            deployer.comment_pr(pr_result["pr_number"], report["markdown"])
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not write the postmortem: %s", exc)

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
        "shield": shield_report,
        "risk": risk_report,
        "postmortem": {"id": report["id"], "title": report["title"]} if report else None,
    }


def run_replay(file_path, original, diff, extra_files, logs) -> dict[str, Any]:
    """Counterfactual replay in the Docker sandbox (patched out in unit tests)."""
    return replay.replay(file_path, original, diff, extra_files, logs, _run_tests_in_sandbox)


def _deliver(diagnosis, patch, validation_result, risk_report, failure_context, run_ref, create_pr) -> dict[str, Any]:
    """Open the PR the way the risk level allows: auto-merge to staging, review, or review + page."""
    from agents import deployer

    decision = (risk_report or {}).get("decision", "open_pr")
    pipeline_result = {"diagnosis": diagnosis, "patch": patch, "validation_result": validation_result, "risk": risk_report}
    staging = os.getenv("STAGING_BRANCH", "staging")
    # Without a token there is no PR to place anywhere: don't ask GitHub about branches.
    to_staging = decision == "auto_merge_staging" and bool(deployer.GITHUB_TOKEN) and deployer.branch_exists(staging)
    result = create_pr(pipeline_result=pipeline_result, failure_context=failure_context, base=staging if to_staging else None)
    if not result.get("pr_created"):
        return result
    result["decision"] = decision
    number = result.get("pr_number")
    if to_staging and governance.allow("auto_merge", run_ref=run_ref):
        merged = deployer.merge_pr(number)
        result["auto_merged"] = merged.get("merged", False)
        governance.record("auto_merge", "merged" if merged.get("merged") else "failed", f"PR #{number} into {staging}", run_ref=run_ref, evidence=merged)
    elif decision == "open_pr_and_page":
        result["labelled"] = deployer.label_pr(number, ["needs-human", "ghostops-high-risk"])
        paged = deployer.page_human(f"high-risk fix for {diagnosis.get('root_cause')} (PR #{number})", {"pr": result.get("pr_url"), "risk": risk_report})
        result["paged"] = paged.get("paged", False)
        governance.record("page", "sent" if paged.get("paged") else "skipped", paged.get("reason", "") or "pager notified", run_ref=run_ref, evidence=paged)
    return result


# ---------------------------------------------------------------------------
# CLI entry point (for quick manual testing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from agents.scenarios import ci_context, exception_context, leak_context

    # Pick which scenario to run based on CLI arg.
    if len(sys.argv) > 1 and sys.argv[1] == "c4":
        print("Running pipeline with C4 (CI failure): sample-app/math_utils.py …")
        context = ci_context()
    elif len(sys.argv) > 1 and sys.argv[1] == "a1":
        print("Running pipeline with A1 (unhandled exception): sample-app/user_service.py …")
        context = exception_context()
    else:
        print("Running pipeline with B1 (memory leak): sample-app/app.py …")
        context = leak_context()

    result = run_pipeline(context)
    print("\n" + "=" * 72)
    print("PIPELINE RESULT")
    print("=" * 72)
    print(json.dumps(result, indent=2))
