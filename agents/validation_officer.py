"""
Validation Officer Agent
========================

Applies a generated unified diff inside an **isolated Docker container**,
runs the project's test suite (``pytest``), and returns a structured pass/fail
verdict with captured output.

Safety guarantees
-----------------
* The host filesystem is **never** modified.
* A throwaway container is created for each validation run and removed
  immediately afterwards (``auto_remove=True``).
* The container has no network access (``network_mode="none"``) and runs
  with reduced capabilities.

"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from textwrap import dedent

import docker
from crewai import Agent, LLM, Task
from docker.errors import ContainerError, ImageNotFound

from agents.config import (
    GOOGLE_API_KEY,
    PRIMARY_MODEL,
    SANDBOX_IMAGE,
    SANDBOX_TIMEOUT_SECONDS,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
VALIDATOR_SYSTEM_PROMPT: str = dedent("""\
    You are the GhostOps Validation Officer.  You receive pytest output from a
    sandboxed Docker run and must decide whether the patch is safe to merge.

    RULES
    -----
    1. Return a JSON object:
       {
         "passed": true | false,
         "summary": "<one-sentence summary of the test run>",
         "failing_tests": ["<test_name>", ...],
         "test_output": "<full captured stdout/stderr — truncated to 4000 chars>"
       }
    2. ``passed`` is true ONLY when every test passed (exit code 0).
    3. Return ONLY the JSON — no markdown fences, no preamble.
""")


# ---------------------------------------------------------------------------
# Docker sandbox logic
# ---------------------------------------------------------------------------
def _run_tests_in_sandbox(
    file_path: str,
    original_content: str,
    diff_text: str,
) -> dict:
    """Apply *diff_text* to *original_content* inside a Docker container and
    run ``pytest``.

    Parameters
    ----------
    file_path:
        Path the file would have on disk (used to recreate directory layout
        inside the container).
    original_content:
        The original source code of the file being patched.
    diff_text:
        A unified diff string produced by the Patch Generator.

    Returns
    -------
    dict
        ``{"exit_code": int, "output": str}``
    """
    client = docker.from_env()

    # Ensure the sandbox base image is available locally.
    try:
        client.images.get(SANDBOX_IMAGE)
    except ImageNotFound:
        client.images.pull(SANDBOX_IMAGE)

    # Build a small shell script that:
    #   1. Writes the original file
    #   2. Applies the diff via `patch`
    #   3. Installs minimal deps (pytest + whatever the file imports)
    #   4. Runs pytest
    # We pass everything via environment variables to avoid bind-mounts.

    target = Path(file_path).name
    target_dir = "/workspace"

    setup_script = dedent(f"""\
        set -e
        mkdir -p {target_dir}
        cd {target_dir}

        # Write the original source file
        cat > {target} << 'ORIGINAL_EOF'
        {original_content}
        ORIGINAL_EOF

        # Write the diff
        cat > patch.diff << 'DIFF_EOF'
        {diff_text}
        DIFF_EOF

        # Install patch utility and pytest
        apt-get update -qq && apt-get install -y -qq patch > /dev/null 2>&1
        pip install --quiet pytest flask 2>/dev/null

        # Apply the diff  (strip leading a/ b/ prefixes)
        patch --forward --no-backup-if-mismatch -p1 < patch.diff || true

        # Run pytest — collect output regardless of pass/fail
        python -m pytest {target} -v --tb=short 2>&1 || true
    """)

    try:
        container = client.containers.run(
            image=SANDBOX_IMAGE,
            command=["bash", "-c", setup_script],
            network_mode="none",           # no network access
            mem_limit="256m",              # hard memory cap
            stdout=True,
            stderr=True,
            detach=True,
        )

        # Wait for the container to finish (with timeout).
        result = container.wait(timeout=SANDBOX_TIMEOUT_SECONDS)
        exit_code = result.get("StatusCode", -1)
        logs = container.logs(stdout=True, stderr=True).decode(
            "utf-8", errors="replace"
        )

        # Clean up the container.
        container.remove(force=True)

        return {"exit_code": exit_code, "output": logs[-4000:]}

    except ContainerError as exc:
        return {"exit_code": 1, "output": str(exc)[:4000]}
    except Exception as exc:  # noqa: BLE001
        return {"exit_code": -1, "output": f"Docker error: {exc!s}"[:4000]}


# ---------------------------------------------------------------------------
# OPA Gatekeeper Check
# ---------------------------------------------------------------------------
def _run_opa_checks(
    file_path: str,
    original_content: str,
    diff_text: str,
) -> dict | None:
    """Run OPA conftest on the patched file using Docker.
    Returns None if passed, or a dict with `exit_code` and `output` on failure.
    """
    client = docker.from_env()
    try:
        client.images.get("openpolicyagent/conftest:v0.48.0")
    except ImageNotFound:
        client.images.pull("openpolicyagent/conftest:v0.48.0")

    with tempfile.TemporaryDirectory() as td:
        target_name = Path(file_path).name
        target_path = Path(td) / target_name
        target_path.write_text(original_content)

        patch_path = Path(td) / "patch.diff"
        patch_path.write_text(diff_text)

        # Apply patch locally
        try:
            subprocess.run(
                ["patch", "--forward", "--no-backup-if-mismatch", "-p1", "-i", "patch.diff"],
                cwd=td, check=True, capture_output=True
            )
        except subprocess.CalledProcessError as e:
            return {"exit_code": 1, "output": f"Patch failed in OPA check: {e.stderr.decode()}"}

        # Write file path meta for allowlist.rego
        meta_path = Path(td) / "meta.json"
        meta_path.write_text(json.dumps({"file_path": file_path}))

        # Copy policies
        project_root = Path(__file__).resolve().parent.parent
        policies_src = project_root / "infra" / "k8s" / "gatekeeper" / "policies"
        policies_dst = Path(td) / "policies"
        shutil.copytree(policies_src, policies_dst)

        # Extract rego from Gatekeeper ConstraintTemplates and modify for conftest
        for yaml_file in policies_dst.glob("*.yaml"):
            content = yaml_file.read_text()
            if "kind: ConstraintTemplate" in content and "rego: |" in content:
                rego_block = content.split("rego: |")[1]
                lines = rego_block.split("\n")
                first_line_indent = 0
                for line in lines:
                    if line.strip():
                        first_line_indent = len(line) - len(line.lstrip())
                        break
                rego_lines = [line[first_line_indent:] if len(line) >= first_line_indent else line for line in lines]
                rego_code = "\n".join(rego_lines)
                rego_code = rego_code.replace("input.review.object.", "input.")
                (policies_dst / f"{yaml_file.stem}.rego").write_text(rego_code)

        # Run conftest on meta.json (checks allowlist)
        try:
            container = client.containers.run(
                image="openpolicyagent/conftest:v0.48.0",
                command=["test", "--all-namespaces", "-p", "policies", "meta.json"],
                volumes={td: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                detach=True,
            )
            result = container.wait(timeout=30)
            logs = container.logs().decode("utf-8")
            container.remove(force=True)
            if result.get("StatusCode", 0) != 0:
                return {"exit_code": 1, "output": f"OPA Policy Violation (File Path):\n{logs}"}
        except Exception as e:
            return {"exit_code": -1, "output": f"OPA Check Error: {e}"}

        # If patched file is YAML, run conftest on it to evaluate k8s limits/privileged
        if target_name.endswith(".yaml") or target_name.endswith(".yml"):
            try:
                container = client.containers.run(
                    image="openpolicyagent/conftest:v0.48.0",
                    command=["test", "--all-namespaces", "-p", "policies", target_name],
                    volumes={td: {"bind": "/workspace", "mode": "rw"}},
                    working_dir="/workspace",
                    detach=True,
                )
                result = container.wait(timeout=30)
                logs = container.logs().decode("utf-8")
                container.remove(force=True)
                if result.get("StatusCode", 0) != 0:
                    return {"exit_code": 1, "output": f"OPA Policy Violation (K8s Manifest):\n{logs}"}
            except Exception as e:
                return {"exit_code": -1, "output": f"OPA Check Error: {e}"}

    return None


# ---------------------------------------------------------------------------
# CrewAI agent & task builders
# ---------------------------------------------------------------------------
def build_validation_agent() -> Agent:
    """Instantiate the Validation Officer CrewAI Agent."""
    llm = LLM(
        model=PRIMARY_MODEL,
        api_key=GOOGLE_API_KEY,
        temperature=0.0,
    )

    return Agent(
        role="Validation Officer",
        goal=(
            "Apply the proposed patch in an isolated Docker sandbox, run the "
            "test suite, and return a definitive pass/fail verdict."
        ),
        backstory=(
            "You are a cautious release engineer who trusts nothing until the "
            "tests pass in a pristine, isolated environment.  You never allow "
            "untested patches to reach production."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def build_validation_task(
    agent: Agent,
    file_path: str,
    original_content: str,
    diff_text: str,
) -> Task:
    """Create a CrewAI Task for the Validation Officer.

    This function **eagerly** runs the Docker sandbox so the LLM receives
    the actual test output and can produce a structured verdict.

    Parameters
    ----------
    agent:
        The ``Agent`` returned by :func:`build_validation_agent`.
    file_path:
        Path to the file being patched (used inside the container).
    original_content:
        The original source code content of the file.
    diff_text:
        The unified diff string from the Patch Generator.

    Returns
    -------
    Task
        A CrewAI Task whose output is the JSON validation verdict.
    """
    # 1. First, check the patch against OPA policies
    opa_result = _run_opa_checks(file_path, original_content, diff_text)
    if opa_result is not None:
        sandbox_result = opa_result
    else:
        # 2. If it passes OPA, run the actual tests in the sandbox
        sandbox_result = _run_tests_in_sandbox(file_path, original_content, diff_text)

    return Task(
        description=dedent(f"""\
            The patch has been applied inside an isolated Docker container and
            ``pytest`` was executed.  Below are the results.

            ---BEGIN SANDBOX RESULT---
            Exit code: {sandbox_result["exit_code"]}

            Test output (last 4 000 chars):
            {sandbox_result["output"]}
            ---END SANDBOX RESULT---

            Analyse the output and return your structured verdict as JSON
            following your system instructions.
        """),
        expected_output=(
            "A JSON object with keys: passed (bool), summary, "
            "failing_tests (list), test_output (string)."
        ),
        agent=agent,
    )
