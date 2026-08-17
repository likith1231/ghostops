"""
GhostOps Deployer — Auto-Create GitHub PR on Validated Pass
============================================================

Pure automation module (no LLM, not a CrewAI agent).  Takes a completed
pipeline result, and — **only** when validation passed — creates a git
branch, applies the patch, commits, pushes, and opens a GitHub Pull Request
via the REST API.

If anything fails (missing token, git error, GitHub API error) the function
returns a descriptive error dict but **never raises** — same graceful-
degradation pattern used throughout the GhostOps backend.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from agents.config import GITHUB_REPO, GITHUB_TOKEN

logger = logging.getLogger("ghostops.deployer")

# Project root — two levels up from this file (agents/deployer.py → ghostops/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_evidence(evidence: Any, style: str = "markdown") -> str:
    """Format the diagnosis ``evidence`` field defensively.

    The LLM returns evidence in one of three shapes:
    - ``list[dict]`` — each dict has ``type``, ``reference``, ``reasoning``
    - ``list[str]`` — each item is a plain string
    - ``str`` — a single multi-line string

    Parameters
    ----------
    style:
        ``"markdown"`` for PR body (bold, backticks),
        ``"plain"`` for commit messages (brackets).
    """
    if not evidence:
        return ""

    # --- Single string ---------------------------------------------------
    if isinstance(evidence, str):
        # Indent each line as a bullet if multi-line, else just return it.
        lines = evidence.strip().splitlines()
        if style == "markdown":
            return "\n".join(f"  - {line.strip()}" for line in lines if line.strip()) + "\n"
        return "\n".join(f"  - {line.strip()}" for line in lines if line.strip())

    # --- List ------------------------------------------------------------
    if isinstance(evidence, list):
        parts: list[str] = []
        for ev in evidence:
            if isinstance(ev, dict):
                # Try exact keys first, fall back to capitalized or alternate keys
                ev_type = ev.get("type", ev.get("Type", ev.get("source", "?")))
                ref = ev.get("reference", ev.get("Reference", ev.get("detail", "")))
                reasoning = ev.get("reasoning", ev.get("Reasoning", ""))
                # If nested in "evidence"
                if ev_type == "?" and not ref and "evidence" in ev and isinstance(ev["evidence"], dict):
                    nested = ev["evidence"]
                    ev_type = nested.get("type", "?")
                    ref = nested.get("reference", "")
                    reasoning = nested.get("reasoning", "")
                if style == "markdown":
                    parts.append(f"  - **{ev_type}**: `{ref}` — {reasoning}")
                else:
                    parts.append(f"  - [{ev_type}] {ref} — {reasoning}")
            elif isinstance(ev, str):
                parts.append(f"  - {ev.strip()}")
            else:
                parts.append(f"  - {ev!s}")
        return "\n".join(parts) + ("\n" if style == "markdown" else "")

    # --- Unexpected type — convert to string ------------------------------
    return f"  - {evidence!s}\n" if style == "markdown" else f"  - {evidence!s}"

def _run_git(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """Run a git command and return the CompletedProcess.

    Raises ``subprocess.CalledProcessError`` on non-zero exit so callers
    can catch it in one place.
    """
    cmd = ["git"] + list(args)
    logger.debug("git: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=cwd or _PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


def _current_branch(cwd: str | Path | None = None) -> str:
    """Return the name of the currently checked-out branch."""
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return result.stdout.strip()


def _sanitise_for_branch(text: str) -> str:
    """Turn arbitrary text into a git-branch-safe string."""
    sanitised = re.sub(r"[^a-zA-Z0-9_-]", "-", text)
    sanitised = re.sub(r"-{2,}", "-", sanitised).strip("-").lower()
    return sanitised[:40]  # keep branch names short


def _build_pr_body(
    failure_context: dict[str, Any],
    diagnosis: dict[str, Any],
    validation_result: dict[str, Any],
    patch: dict[str, Any],
) -> str:
    """Compose a rich Markdown PR description."""
    alert_type = failure_context.get("alert_type", "unknown")
    root_cause = diagnosis.get("root_cause", "unknown")
    confidence = str(diagnosis.get("confidence", "unknown"))
    summary = diagnosis.get("summary", "(no summary)")
    explanation = patch.get("explanation", "(no explanation)")

    # Evidence bullets (handles str, list[dict], list[str] shapes)
    evidence_lines = _format_evidence(diagnosis.get("evidence", []), style="markdown")

    validation_summary = validation_result.get("summary", "(no summary)")
    validation_passed = validation_result.get("passed", False)

    # Build concise log excerpt (first 5 lines)
    logs = failure_context.get("logs", [])
    log_excerpt = "\n".join(f"  {line}" for line in logs[:5])
    if len(logs) > 5:
        log_excerpt += f"\n  ... ({len(logs) - 5} more lines)"

    body = f"""\
## 🤖 GhostOps Auto-Generated Fix

This PR was automatically created by the GhostOps AI agent pipeline.

---

### 🚨 Original Alert
- **Alert type**: `{alert_type}`
- **Triggered at**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

<details>
<summary>Recent logs (excerpt)</summary>

```
{log_excerpt}
```
</details>

---

### 🔍 Diagnosed Root Cause
- **Classification**: `{root_cause}`
- **Confidence**: `{confidence}`
- **Summary**: {summary}

**Evidence**:
{evidence_lines}
---

### 🔧 Patch Explanation
{explanation}

---

### ✅ Sandbox Validation
- **Passed**: {'✅ Yes' if validation_passed else '❌ No'}
- **Summary**: {validation_summary}

---

> **Note**: This fix was generated, tested, and submitted entirely by GhostOps
> agents.  A human review is still required before merging.
"""
    return body


def _build_commit_message(diagnosis: dict[str, Any]) -> str:
    """Build a conventional-commit-style message from the diagnosis."""
    root_cause = diagnosis.get("root_cause", "unknown")
    summary = diagnosis.get("summary", "auto-generated fix")

    # First line: conventional commit
    title = f"fix({root_cause}): {summary}"
    # Truncate to 72 chars for git best-practice
    if len(title) > 72:
        title = title[:69] + "..."

    # Body
    body_parts = [
        "",
        f"Root cause: {root_cause}",
        f"Confidence: {diagnosis.get('confidence', 'unknown')}",
        "",
        "Evidence:",
    ]
    evidence_text = _format_evidence(diagnosis.get("evidence", []), style="plain")
    if evidence_text:
        body_parts.append(evidence_text)

    body_parts.append("")
    body_parts.append("Generated by GhostOps AI agent pipeline.")

    return title + "\n" + "\n".join(body_parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_pr(
    pipeline_result: dict[str, Any],
    failure_context: dict[str, Any],
    repo_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a GitHub PR for a validated patch.

    Parameters
    ----------
    pipeline_result:
        The dict returned by ``run_pipeline()`` — must contain
        ``diagnosis``, ``patch``, and ``validation_result`` keys.
    failure_context:
        The original failure-context dict that triggered the pipeline.
    repo_path:
        Path to the local git repo.  Defaults to the project root.

    Returns
    -------
    dict
        Always contains ``pr_created`` (bool).  On success also contains
        ``pr_url``, ``branch``, and ``pr_number``.  On failure contains
        ``reason`` (str).
    """
    repo = Path(repo_path) if repo_path else _PROJECT_ROOT

    # ------------------------------------------------------------------
    # 1. Safety gate: only act on validated passes
    # ------------------------------------------------------------------
    validation_result = pipeline_result.get("validation_result", {})
    if validation_result.get("passed") is not True:
        reason = "Validation did not pass — skipping PR creation."
        logger.info(reason)
        return {"pr_created": False, "reason": "validation_failed"}

    # ------------------------------------------------------------------
    # 2. Auth gate: require GITHUB_TOKEN
    # ------------------------------------------------------------------
    if not GITHUB_TOKEN:
        reason = "GITHUB_TOKEN not set — cannot create PR."
        logger.warning(reason)
        return {"pr_created": False, "reason": "GITHUB_TOKEN not set"}

    diagnosis = pipeline_result.get("diagnosis", {})
    patch = pipeline_result.get("patch", {})
    diff_text = patch.get("diff", "")

    if not diff_text:
        logger.warning("Patch contains no diff — nothing to commit.")
        return {"pr_created": False, "reason": "empty_diff"}

    # ------------------------------------------------------------------
    # 3. Build branch name
    # ------------------------------------------------------------------
    root_cause = _sanitise_for_branch(
        diagnosis.get("root_cause", "unknown")
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch_name = f"fix/{root_cause}-{timestamp}"

    original_branch = _current_branch(cwd=repo)
    logger.info(
        "Creating PR: branch=%s from=%s", branch_name, original_branch
    )

    try:
        # --------------------------------------------------------------
        # 4. Create branch
        # --------------------------------------------------------------
        _run_git("checkout", "-b", branch_name, cwd=repo)

        # --------------------------------------------------------------
        # 5. Apply the diff
        # --------------------------------------------------------------
        _apply_diff(diff_text, failure_context, repo)

        # --------------------------------------------------------------
        # 6. Commit
        # --------------------------------------------------------------
        _run_git("add", "-A", cwd=repo)

        commit_msg = _build_commit_message(diagnosis)

        # Write commit message to a temp file to avoid shell escaping issues
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write(commit_msg)
            msg_file = f.name

        try:
            _run_git("commit", "-F", msg_file, cwd=repo)
        finally:
            os.unlink(msg_file)

        # --------------------------------------------------------------
        # 7. Push
        # --------------------------------------------------------------
        _run_git("push", "origin", branch_name, cwd=repo)

        # --------------------------------------------------------------
        # 8. Open PR via GitHub REST API
        # --------------------------------------------------------------
        pr_body = _build_pr_body(
            failure_context, diagnosis, validation_result, patch,
        )

        pr_title = _build_commit_message(diagnosis).split("\n")[0]

        pr_data = _create_github_pr(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=original_branch,
        )

        logger.info(
            "✅ PR created: %s (#%s)",
            pr_data.get("html_url", "?"),
            pr_data.get("number", "?"),
        )

        return {
            "pr_created": True,
            "pr_url": pr_data.get("html_url", ""),
            "pr_number": pr_data.get("number"),
            "branch": branch_name,
        }

    except subprocess.CalledProcessError as exc:
        logger.error(
            "Git command failed: %s\nstdout: %s\nstderr: %s",
            exc.cmd,
            exc.stdout,
            exc.stderr,
        )
        return {
            "pr_created": False,
            "reason": f"git error: {exc.stderr.strip() or exc.stdout.strip()}",
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Deployer failed: %s", exc, exc_info=True)
        return {"pr_created": False, "reason": str(exc)}
    finally:
        # Always return to the original branch, even on failure.
        try:
            _run_git("checkout", original_branch, cwd=repo)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not switch back to %s — manual cleanup may be needed.",
                original_branch,
            )


# ---------------------------------------------------------------------------
# Diff application
# ---------------------------------------------------------------------------

def _apply_diff(
    diff_text: str,
    failure_context: dict[str, Any],
    repo: Path,
) -> None:
    """Apply the LLM-generated unified diff to the repo.

    Strategy:
    1. Try ``git apply`` first (clean and correct).
    2. If that fails (LLM diffs often have slightly wrong paths), fall back
       to writing the patched content directly.  We reconstruct the patched
       file by applying the diff to the original source content from the
       ``failure_context``.
    """
    # --- Attempt 1: git apply -------------------------------------------
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, dir=repo
    ) as f:
        f.write(diff_text)
        patch_file = f.name

    try:
        _run_git("apply", "--check", patch_file, cwd=repo)
        _run_git("apply", patch_file, cwd=repo)
        logger.info("Diff applied cleanly via `git apply`.")
        return
    except subprocess.CalledProcessError:
        logger.info(
            "`git apply` failed — falling back to direct file write."
        )
    finally:
        os.unlink(patch_file)

    # --- Attempt 2: direct file write -----------------------------------
    # The source_files dict maps basenames (e.g. "app.py") to their content.
    # We need to figure out where in the repo each file lives.
    source_files = failure_context.get("source_files", {})
    if not source_files:
        raise RuntimeError(
            "git apply failed and no source_files in failure_context to fall back on."
        )

    for filename, original_content in source_files.items():
        # Find the actual path in the repo.  Try common locations.
        candidates = [
            repo / "sample-app" / filename,
            repo / filename,
        ]
        target_path = None
        for c in candidates:
            if c.exists():
                target_path = c
                break

        if target_path is None:
            # If file doesn't exist yet, put it in sample-app/
            target_path = repo / "sample-app" / filename
            target_path.parent.mkdir(parents=True, exist_ok=True)

        # Apply the unified diff manually using Python's difflib-style
        # approach: parse the diff to extract the patched content.
        patched_content = _apply_unified_diff_to_content(
            original_content, diff_text, filename
        )

        if patched_content is not None:
            target_path.write_text(patched_content, encoding="utf-8")
            logger.info("Wrote patched file: %s", target_path)
        else:
            logger.warning(
                "Could not extract patched content for %s from diff — "
                "writing original content as-is.",
                filename,
            )


def _apply_unified_diff_to_content(
    original: str,
    diff_text: str,
    filename: str,
) -> str | None:
    """Apply a unified diff to *original* content and return the patched text.

    This is a simple line-level patcher that handles the most common case:
    a single-file unified diff with one or more hunks.  Returns ``None``
    if the diff can't be parsed.
    """
    original_lines = original.splitlines(keepends=True)
    patched_lines: list[str] = []
    current_line = 0  # index into original_lines

    # Find hunks in the diff
    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")

    in_relevant_diff = False
    hunks: list[tuple[int, list[str]]] = []  # (start_line_0indexed, hunk_lines)
    current_hunk_lines: list[str] = []
    current_hunk_start: int = 0

    for line in diff_text.splitlines(keepends=True):
        # Check if this is a file header relevant to our file
        if line.startswith("--- ") or line.startswith("+++ "):
            if filename in line:
                in_relevant_diff = True
            continue

        if not in_relevant_diff:
            continue

        m = hunk_header_re.match(line)
        if m:
            # Save previous hunk
            if current_hunk_lines:
                hunks.append((current_hunk_start, current_hunk_lines))
            current_hunk_start = int(m.group(1)) - 1  # 0-indexed
            current_hunk_lines = []
            continue

        if line.startswith("+") or line.startswith("-") or line.startswith(" "):
            current_hunk_lines.append(line)

    # Save last hunk
    if current_hunk_lines:
        hunks.append((current_hunk_start, current_hunk_lines))

    if not hunks:
        return None

    # Apply hunks
    for hunk_start, hunk_lines in hunks:
        # Copy lines before this hunk
        while current_line < hunk_start and current_line < len(original_lines):
            patched_lines.append(original_lines[current_line])
            current_line += 1

        # Process hunk
        for hline in hunk_lines:
            if hline.startswith("+"):
                # Added line
                content = hline[1:]
                if not content.endswith("\n"):
                    content += "\n"
                patched_lines.append(content)
            elif hline.startswith("-"):
                # Removed line — skip in original
                current_line += 1
            elif hline.startswith(" "):
                # Context line — copy from original
                if current_line < len(original_lines):
                    patched_lines.append(original_lines[current_line])
                    current_line += 1

    # Copy remaining lines after last hunk
    while current_line < len(original_lines):
        patched_lines.append(original_lines[current_line])
        current_line += 1

    return "".join(patched_lines)


# ---------------------------------------------------------------------------
# GitHub REST API
# ---------------------------------------------------------------------------

def _create_github_pr(
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict[str, Any]:
    """Create a pull request via the GitHub REST API.

    Returns the JSON response from GitHub (contains ``html_url``,
    ``number``, etc.).  Raises on HTTP errors so the caller can handle
    them.
    """
    owner, repo_name = GITHUB_REPO.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    payload = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    }

    logger.info("Creating GitHub PR: POST %s", url)
    resp = requests.post(url, headers=headers, json=payload, timeout=30)

    if resp.status_code == 201:
        return resp.json()

    # Log detailed error info
    logger.error(
        "GitHub API error %d: %s",
        resp.status_code,
        resp.text[:500],
    )
    raise RuntimeError(
        f"GitHub API returned {resp.status_code}: {resp.text[:200]}"
    )
