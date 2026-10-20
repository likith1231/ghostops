"""Regression test generation: templates, and the before/after proof (real sandbox for the latter)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents import regression, validation_officer as vo


@pytest.mark.parametrize("root,file,name", [
    ("memory_leak", "sample-app/app.py", "sample-app/test_ghostops_regression_memory.py"),
    ("ci_test_failure", "sample-app/math_utils.py", "sample-app/test_ghostops_regression_math.py"),
    ("null_reference", "sample-app/user_service.py", "sample-app/test_ghostops_regression_null.py"),
])
def test_template_per_known_pattern(root, file, name):
    t = regression.generate({"root_cause": root}, {"file_path": file})
    assert t["path"] == name and "def test_" in t["content"] and t["source"] == "template"
    compile(t["content"], name, "exec")


def test_unknown_pattern_has_no_test():
    assert regression.generate({"root_cause": "unknown"}, {"file_path": "sample-app/app.py"}) is None
    assert regression.generate({"root_cause": "memory_leak"}, {"file_path": "other/thing.py"}) is None
    assert regression.generate({"root_cause": "memory_leak"}, {}) is None


def _sandbox(results):
    calls = []

    def run(file_path, original, diff, files, test_target=None):
        calls.append({"diff": diff, "target": test_target, "files": files})
        return results[len(calls) - 1]
    return run, calls


def test_verified_when_fails_before_and_passes_after():
    run, calls = _sandbox([{"exit_code": 1, "output": "1 failed"}, {"exit_code": 0, "output": "1 passed"}])
    test = {"path": "sample-app/test_r.py", "content": "def test_x(): pass\n"}
    with patch.object(vo, "_run_tests_in_sandbox", side_effect=run):
        proof = vo.verify_regression_test("sample-app/m.py", "src", "DIFF", {"sample-app/t.py": "t"}, test)
    assert proof["verified"] is True
    assert calls[0]["diff"] == "" and calls[1]["diff"] == "DIFF", "unpatched first, then patched"
    assert calls[0]["target"] == "sample-app/test_r.py" and "sample-app/test_r.py" in calls[0]["files"]


@pytest.mark.parametrize("before,after", [
    ({"exit_code": 0, "output": "passed"}, {"exit_code": 0, "output": "passed"}),  # doesn't detect the bug
    ({"exit_code": 1, "output": "failed"}, {"exit_code": 1, "output": "failed"}),  # fix doesn't satisfy it
    ({"exit_code": 2, "output": "usage error"}, {"exit_code": 0, "output": "passed"}),  # broken test, not a failing one
])
def test_not_verified_otherwise(before, after):
    run, _ = _sandbox([before, after])
    with patch.object(vo, "_run_tests_in_sandbox", side_effect=run):
        assert vo.verify_regression_test("f", "s", "d", {}, {"path": "t.py", "content": ""})["verified"] is False


def test_deployer_ships_the_regression_test(tmp_path):
    """create_pr writes the verified test next to the fix (checked through the git-backed tests' fixture shape)."""
    from agents.tests.test_deployer_git import CONTEXT, PIPELINE, git, github_ok, repo  # noqa: F401
    import subprocess

    from agents import deployer

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "config", "user.email", "t@e.com")
    git(work, "config", "user.name", "T")
    (work / "sample-app").mkdir()
    (work / "sample-app" / "math_utils.py").write_text(CONTEXT["source_files"]["sample-app/math_utils.py"])
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    pipeline = {**PIPELINE, "patch": {**PIPELINE["patch"], "regression_test": {"path": "sample-app/test_ghostops_regression_math.py", "content": "def test_r():\n    assert True\n"}}}
    with patch.object(deployer, "GITHUB_TOKEN", "t"), patch.object(deployer.requests, "post", return_value=github_ok()):
        result = deployer.create_pr(pipeline, CONTEXT, repo_path=work)
    assert "def test_r" in git(origin, "show", f"{result['branch']}:sample-app/test_ghostops_regression_math.py")
