"""The deployer's PR flow against a real (temporary) git repo with a bare 'origin'.

Only the GitHub REST call is faked; branching, applying, committing and pushing are real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents import deployer

ORIGINAL = "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a + b\n"
DIFF = (
    "--- a/sample-app/math_utils.py\n+++ b/sample-app/math_utils.py\n"
    "@@ -4,3 +4,3 @@\n \n def multiply(a, b):\n-    return a + b\n+    return a * b\n"
)
PIPELINE = {
    "diagnosis": {"root_cause": "ci_test_failure", "confidence": "high", "summary": "multiply adds", "evidence": ["[LOG] assert 6 == 8"]},
    "patch": {"diff": DIFF, "explanation": "use *"},
    "validation_result": {"passed": True, "summary": "2 passed"},
}
CONTEXT = {"alert_type": "ci_failure", "logs": [f"line {i}" for i in range(8)], "source_files": {"sample-app/math_utils.py": ORIGINAL}}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "user.name", "Test")
    (work / "sample-app").mkdir()
    (work / "sample-app" / "math_utils.py").write_text(ORIGINAL)
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    return work, origin


def github_ok(number=42):
    resp = MagicMock(status_code=201)
    resp.json.return_value = {"html_url": f"https://github.com/x/y/pull/{number}", "number": number}
    return resp


def run(repo_path, response=None):
    with patch.object(deployer, "GITHUB_TOKEN", "t0ken"), \
         patch.object(deployer.requests, "post", return_value=response or github_ok()) as post:
        result = deployer.create_pr(PIPELINE, CONTEXT, repo_path=repo_path)
    return result, post


def test_pr_flow_pushes_fix_branch_and_opens_pr(repo):
    work, origin = repo
    result, post = run(work)
    assert result["pr_created"] is True and result["pr_number"] == 42
    branch = result["branch"]
    assert branch.startswith("fix/ci_test_failure-")
    # The fix is on the remote branch, the original branch is untouched.
    assert "return a * b" in git(origin, "show", f"{branch}:sample-app/math_utils.py")
    assert "return a + b" in (work / "sample-app" / "math_utils.py").read_text().split("multiply")[1]
    payload = post.call_args.kwargs["json"]
    assert payload["head"] == branch and payload["base"] == "main"
    assert payload["title"].startswith("fix(ci_test_failure):")
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer t0ken"


def test_pr_flow_returns_to_original_branch_and_cleans_up(repo):
    work, _ = repo
    result, _ = run(work)
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert result["branch"] not in git(work, "branch", "--list"), "local fix branch must not pile up"
    assert git(work, "status", "--porcelain") == ""


def test_pr_flow_commit_message_is_conventional(repo):
    work, origin = repo
    result, _ = run(work)
    message = git(origin, "log", "-1", "--format=%B", result["branch"])
    assert message.splitlines()[0].startswith("fix(ci_test_failure): multiply adds")
    assert "Root cause: ci_test_failure" in message


def test_github_rejection_is_reported_and_tree_restored(repo):
    work, _ = repo
    bad = MagicMock(status_code=422, text='{"message":"Validation Failed"}')
    result, _ = run(work, response=bad)
    assert result["pr_created"] is False and "422" in result["reason"]
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(work, "status", "--porcelain") == ""


def test_push_failure_reports_git_error(repo):
    work, _ = repo
    git(work, "remote", "set-url", "origin", "/nonexistent/repo.git")
    result, post = run(work)
    assert result["pr_created"] is False and result["reason"].startswith("git error")
    post.assert_not_called()
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_basename_diff_falls_back_to_direct_write(repo):
    work, origin = repo
    basename_diff = DIFF.replace("sample-app/", "")  # LLMs often drop the directory
    pipeline = {**PIPELINE, "patch": {"diff": basename_diff, "explanation": "use *"}}
    with patch.object(deployer, "GITHUB_TOKEN", "t"), patch.object(deployer.requests, "post", return_value=github_ok()):
        result = deployer.create_pr(pipeline, {**CONTEXT, "source_files": {"math_utils.py": ORIGINAL}}, repo_path=work)
    assert result["pr_created"] is True
    assert "return a * b" in git(origin, "show", f"{result['branch']}:sample-app/math_utils.py")


# ---- pure helpers -------------------------------------------------------------------------
def test_apply_unified_diff_single_hunk():
    out = deployer._apply_unified_diff_to_content(ORIGINAL, DIFF, "math_utils.py")
    assert out == "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n"


def test_apply_unified_diff_multiple_hunks():
    original = "".join(f"line{i}\n" for i in range(1, 21))
    diff = (
        "--- a/f.py\n+++ b/f.py\n"
        "@@ -2,1 +2,1 @@\n-line2\n+LINE2\n"
        "@@ -15,1 +15,2 @@\n line15\n+inserted\n"
    )
    out = deployer._apply_unified_diff_to_content(original, diff, "f.py").splitlines()
    assert out[1] == "LINE2" and out[15] == "inserted" and out[-1] == "line20" and len(out) == 21


def test_apply_unified_diff_for_other_file_is_none():
    assert deployer._apply_unified_diff_to_content(ORIGINAL, DIFF, "app.py") is None


@pytest.mark.parametrize("text,expected", [("memory_leak", "memory_leak"), ("CI Test/Failure!!", "ci-test-failure"), ("x" * 80, "x" * 40)])
def test_sanitise_for_branch(text, expected):
    assert deployer._sanitise_for_branch(text) == expected


def test_commit_title_truncated_to_72_chars():
    title = deployer._build_commit_message({"root_cause": "memory_leak", "summary": "s" * 200}).splitlines()[0]
    assert len(title) == 72 and title.endswith("...")


def test_pr_body_contents():
    body = deployer._build_pr_body(CONTEXT, PIPELINE["diagnosis"], PIPELINE["validation_result"], PIPELINE["patch"])
    assert "`ci_failure`" in body and "`ci_test_failure`" in body and "✅ Yes" in body
    assert "... (3 more lines)" in body, "log excerpt is capped at 5 lines"
    assert "A human review is still required" in body
