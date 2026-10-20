"""Blast-radius gate: rejects patches that are too big or do risky things, before the sandbox."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents import blast_radius as br
from agents import mock_llm, validation_officer as vo
from agents.scenarios import ci_context, exception_context, leak_context

APP = "import os\nfrom flask import Flask, jsonify\n\napp = Flask(__name__)\n"


def diff(path, before, after):
    return mock_llm.unified_diff(path, before, after)


def rules(result):
    return {v["rule"] for v in result["violations"]}


@pytest.mark.parametrize("build", [leak_context, ci_context, exception_context])
def test_real_demo_fixes_pass(build):
    ctx = build()
    p = mock_llm.generate_patch(mock_llm.diagnose(ctx), ctx["source_files"])
    result = br.analyze(p["diff"], ctx["source_files"][p["file_path"]])
    assert result["passed"], result["violations"]


def test_too_large():
    before = "".join(f"x{i} = {i}\n" for i in range(50))
    after = "".join(f"x{i} = {i + 1}\n" for i in range(50))
    assert "too_large" in rules(br.analyze(diff("sample-app/app.py", before, after)))


def test_too_many_files():
    d = diff("sample-app/a.py", "a = 1\n", "a = 2\n") + diff("sample-app/b.py", "b = 1\n", "b = 2\n")
    assert "too_many_files" in rules(br.analyze(d))


@pytest.mark.parametrize("before,after", [
    ("def test_x():\n    assert f() == 1\n", "def test_x():\n    pass\n"),
    ("def test_x():\n    assert f() == 1\n", ""),
    ("def test_x():\n    assert f() == 1\n", "import pytest\n@pytest.mark.skip\ndef test_x():\n    assert f() == 1\n"),
    ("def test_x():\n    assert f() == 1\n", "def test_x():\n    assert True\n"),
])
def test_weakening_tests(before, after):
    assert "weakens_tests" in rules(br.analyze(diff("sample-app/test_app.py", before, after)))


def test_new_third_party_import():
    assert "new_dependency" in rules(br.analyze(diff("sample-app/app.py", APP, "import redis\n" + APP), APP))


def test_stdlib_or_existing_import_is_fine():
    assert br.analyze(diff("sample-app/app.py", APP, "from collections import deque\n" + APP), APP)["passed"]
    assert br.analyze(diff("sample-app/app.py", APP, APP + "from flask import request\n"), APP)["passed"]


def test_requirements_change():
    assert "new_dependency" in rules(br.analyze(diff("sample-app/requirements.txt", "flask\n", "flask\nleftpad\n")))


@pytest.mark.parametrize("path,line", [("sample-app/auth.py", "x = 1"), ("sample-app/app.py", "API_KEY = os.environ['API_KEY']")])
def test_sensitive_code(path, line):
    assert "sensitive_code" in rules(br.analyze(diff(path, "a = 0\n", f"a = 0\n{line}\n")))


@pytest.mark.parametrize("line", ["eval(user_input)", "os.system('rm x')", "subprocess.run(cmd)", "pickle.loads(blob)", "yaml.load(text)"])
def test_dangerous_calls(line):
    assert "dangerous_call" in rules(br.analyze(diff("sample-app/app.py", "a = 0\n", f"a = 0\n{line}\n")))


def test_safe_yaml_load_allowed():
    assert br.analyze(diff("sample-app/app.py", "a = 0\n", "a = 0\nyaml.load(t, Loader=yaml.SafeLoader)\n"))["passed"]


@pytest.mark.parametrize("line", ["requests.post(URL, data=d)", "urllib.request.urlopen(u)", "s = socket.socket()"])
def test_network_calls(line):
    assert "network_call" in rules(br.analyze(diff("sample-app/app.py", "a = 0\n", f"a = 0\n{line}\n")))


@pytest.mark.parametrize("line", ["privileged: true", "hostNetwork: true", "runAsUser: 0"])
def test_privilege_in_manifest(line):
    assert "privilege" in rules(br.analyze(diff("infra/k8s/sample-app.yaml", "kind: Deployment\n", f"kind: Deployment\n{line}\n")))


def test_metrics():
    m = br.analyze(diff("sample-app/app.py", "a = 1\nb = 2\n", "a = 1\nb = 3\nc = 4\n"))["metrics"]
    assert m == {"files": 1, "added": 2, "removed": 1}


def test_gate_runs_before_sandbox():
    bad = diff("sample-app/app.py", APP, APP + "eval(x)\n")
    with patch.object(vo, "_run_opa_checks", return_value=None), patch.object(vo, "_run_tests_in_sandbox") as sandbox:
        result = vo.run_validation_checks("sample-app/app.py", APP, bad)
    sandbox.assert_not_called()
    verdict = vo.interpret_sandbox_result(result)
    assert verdict["passed"] is False and "blast-radius gate (dangerous_call)" in verdict["summary"]
