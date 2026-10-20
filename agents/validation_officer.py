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

import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from textwrap import dedent

import docker
from crewai import Agent, LLM, Task
from docker.errors import ContainerError, ImageNotFound

from agents import blast_radius

from agents.config import (
    LLM_API_KEY,
    LLM_OPTIONS,
    PRIMARY_MODEL,
    SANDBOX_BASE_IMAGE,
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
def _write_tree(root: Path, files: dict[str, str]) -> None:
    """Write ``{relative_path: content}`` under *root*, creating directories."""
    for rel, content in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _apply_patch(root: Path, file_path: str, diff_text: str) -> str | None:
    """Apply *diff_text* to the tree under *root* with GNU ``patch``.

    LLM diffs name the file either relative to the repo (``a/sample-app/app.py``)
    or by its basename (``a/app.py``), so try the repo root and the file's own
    directory, each with ``-p1`` and ``-p0``.  Returns None on success, or the
    error output of the last attempt.
    """
    file_dir = root / Path(file_path).parent
    error = "patch was not attempted"
    for cwd in dict.fromkeys([root, file_dir]):
        for strip in ("-p1", "-p0"):
            base = ["patch", "--forward", "--batch", "--no-backup-if-mismatch", strip]
            check = subprocess.run(
                [*base, "--dry-run"], input=diff_text, text=True, cwd=cwd, capture_output=True,
            )
            if check.returncode == 0:
                subprocess.run(base, input=diff_text, text=True, cwd=cwd, capture_output=True, check=True)
                return None
            error = (check.stdout + check.stderr).strip()
    return error


def _ensure_sandbox_image(client: docker.DockerClient) -> None:
    """Build the sandbox image (base + pytest + the sample app's deps) once.

    The sandbox runs with networking disabled, so dependencies must be baked in.
    """
    try:
        client.images.get(SANDBOX_IMAGE)
    except ImageNotFound:
        dockerfile = (
            f"FROM {SANDBOX_BASE_IMAGE}\n"
            "RUN pip install --no-cache-dir pytest flask prometheus_client\n"
        )
        client.images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=SANDBOX_IMAGE, rm=True)


def _run_tests_in_sandbox(
    file_path: str,
    original_content: str,
    diff_text: str,
    extra_files: dict[str, str] | None = None,
    test_target: str | None = None,
    command: list[str] | None = None,
) -> dict:
    """Apply *diff_text* to *original_content* and run ``pytest`` on it inside a
    throwaway Docker container.

    Parameters
    ----------
    file_path:
        Repo-relative path of the file being patched (e.g. ``sample-app/app.py``).
    original_content:
        The original source code of the file being patched.
    diff_text:
        A unified diff string produced by the Patch Generator.
    extra_files:
        Other files the tests need (e.g. the test module), ``{path: content}``.

    Returns
    -------
    dict
        ``{"exit_code": int, "output": str}``
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, {**(extra_files or {}), file_path: original_content})

        # An empty diff runs the tests on the unpatched code (used to prove a test fails before the fix).
        error = _apply_patch(root, file_path, diff_text) if diff_text.strip() else None
        if error is not None:
            return {"exit_code": 1, "output": f"Patch could not be applied:\n{error}"[:4000]}

        # Package the patched tree; the container gets a copy, never a host mount.
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.add(td, arcname="workspace")
        archive.seek(0)

    workdir = str(Path("/workspace") / Path(file_path).parent)
    try:
        client = docker.from_env()
        _ensure_sandbox_image(client)
        container = client.containers.create(
            image=SANDBOX_IMAGE,
            command=command or ["python", "-m", "pytest", "-v", "--tb=short", "-p", "no:cacheprovider", *([Path(test_target).name] if test_target else [])],
            working_dir=workdir,
            environment={"PYTHONPATH": workdir, "PYTHONDONTWRITEBYTECODE": "1"},
            network_mode="none",           # no network access
            mem_limit="256m",              # hard memory cap
        )
        try:
            container.put_archive("/", archive.getvalue())
            container.start()
            result = container.wait(timeout=SANDBOX_TIMEOUT_SECONDS)
            exit_code = result.get("StatusCode", -1)
            logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        finally:
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
        target_name = file_path
        _write_tree(Path(td), {file_path: original_content})

        if diff_text.strip():
            error = _apply_patch(Path(td), file_path, diff_text)
            if error is not None:
                return {"exit_code": 1, "output": f"Patch failed in OPA check: {error}"}

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


def run_validation_checks(
    file_path: str,
    original_content: str,
    diff_text: str,
    extra_files: dict[str, str] | None = None,
) -> dict:
    """Run the OPA policy check, then (only if it passes) the sandboxed tests.

    Fails closed: an empty diff never passes, and a policy violation never
    reaches the sandbox.  Returns ``{"exit_code": int, "output": str}``.
    """
    if not diff_text.strip():
        return {"exit_code": 1, "output": "No patch to validate (empty diff)."}
    opa_result = _run_opa_checks(file_path, original_content, diff_text)
    if opa_result is not None:
        return opa_result
    # Gate 2: what the patch does (size, tests, dependencies, secrets, dangerous/network calls).
    blast = blast_radius.analyze(diff_text, original_content)
    if not blast["passed"]:
        details = "\n".join(f"- {v['rule']}: {v['detail']}" for v in blast["violations"])
        return {"exit_code": 1, "output": f"Blast-radius gate rejected the patch:\n{details}"}
    return _run_tests_in_sandbox(file_path, original_content, diff_text, extra_files)


_FAILED_TEST = re.compile(r"^(?:FAILED (\S+)|(\S+::\S+) FAILED)", re.MULTILINE)
_PASSED_COUNT = re.compile(r"(\d+) passed")


def interpret_sandbox_result(sandbox_result: dict) -> dict:
    """Turn a raw sandbox result into the Validation Officer's verdict, without an LLM."""
    exit_code = sandbox_result.get("exit_code", -1)
    output = str(sandbox_result.get("output", ""))
    failing = list(dict.fromkeys(a or b for a, b in _FAILED_TEST.findall(output)))
    passed = exit_code == 0
    if passed:
        m = _PASSED_COUNT.search(output)
        summary = f"All {m.group(1)} tests passed in the sandbox." if m else "All tests passed in the sandbox."
    elif "OPA Policy Violation" in output:
        summary = "Rejected by OPA policy before reaching the sandbox."
    elif output.startswith("Blast-radius gate rejected"):
        rules = sorted({line[2:].split(":")[0] for line in output.splitlines()[1:] if line.startswith("- ")})
        summary = f"Rejected by the blast-radius gate ({', '.join(rules)}) before reaching the sandbox."
    elif output.startswith("Patch could not be applied") or "Patch failed" in output:
        summary = "The patch did not apply cleanly."
    elif failing:
        summary = f"{len(failing)} test(s) still failing after the patch."
    else:
        summary = f"Validation failed (exit code {exit_code})."
    return {
        "passed": passed,
        "summary": summary,
        "failing_tests": failing,
        "test_output": output[-4000:],
    }


def verify_regression_test(
    file_path: str,
    original_content: str,
    diff_text: str,
    extra_files: dict[str, str],
    test: dict[str, str],
) -> dict:
    """A regression test must fail on the unpatched code and pass on the patched code."""
    files = {**extra_files, test["path"]: test["content"]}
    before = _run_tests_in_sandbox(file_path, original_content, "", files, test_target=test["path"])
    after = _run_tests_in_sandbox(file_path, original_content, diff_text, files, test_target=test["path"])
    failed_before = before["exit_code"] == 1  # pytest: 1 = tests failed (not a crash or usage error)
    passed_after = after["exit_code"] == 0
    return {
        "path": test["path"],
        "content": test["content"],
        "source": test.get("source", "template"),
        "failed_before": failed_before,
        "passed_after": passed_after,
        "verified": failed_before and passed_after,
        "before_output": before["output"][-1500:],
        "after_output": after["output"][-1500:],
    }


# ---------------------------------------------------------------------------
# CrewAI agent & task builders
# ---------------------------------------------------------------------------
def build_validation_agent() -> Agent:
    """Instantiate the Validation Officer CrewAI Agent."""
    llm = LLM(
        model=PRIMARY_MODEL,
        api_key=LLM_API_KEY,
        **LLM_OPTIONS,
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
    extra_files: dict[str, str] | None = None,
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
    extra_files:
        Other source files the tests need, ``{path: content}``.

    Returns
    -------
    Task
        A CrewAI Task whose output is the JSON validation verdict.
    """
    sandbox_result = run_validation_checks(file_path, original_content, diff_text, extra_files)

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
