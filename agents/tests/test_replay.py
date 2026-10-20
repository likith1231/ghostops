"""Counterfactual replay: the incident's real traffic, before and after the fix."""

from __future__ import annotations

import json

import pytest

from agents import replay

LOGS = [
    '10.244.0.1 - - [19/Oct/2026 14:00:40] "GET /leak HTTP/1.1" 200 -',
    '10.244.0.6 - - [19/Oct/2026 14:00:41] "GET /metrics HTTP/1.1" 200 -',
    '10.244.0.1 - - [19/Oct/2026 14:00:42] "POST /api/checkout?x=1 HTTP/1.1" 500 -',
    "Traceback (most recent call last):",
]


def test_extracts_requests_skipping_scrapes():
    assert replay.extract_requests(LOGS) == [
        {"method": "GET", "path": "/leak", "status": 200},
        {"method": "POST", "path": "/api/checkout?x=1", "status": 500},
    ]


def test_extract_caps_volume():
    many = ['"GET /x HTTP/1.1" 200'] * (replay.MAX_REQUESTS + 50)
    assert len(replay.extract_requests(many)) == replay.MAX_REQUESTS


def test_harness_is_valid_python_with_requests_embedded():
    src = replay.harness("app", [{"method": "GET", "path": "/leak", "status": 200}])
    compile(src, "ghostops_replay.py", "exec")
    assert "import app as target" in src and "/leak" in src


def run(**kw):
    return {"replayable": True, "server_errors": 0, "exceptions": 0, "kib_growth_per_request": 0.0, **kw}


@pytest.mark.parametrize("before,after,expected", [
    (run(kib_growth_per_request=7680), run(), "reproduced_and_fixed"),
    (run(server_errors=20), run(), "reproduced_and_fixed"),
    (run(exceptions=3), run(exceptions=3), "not_fixed"),
    (run(kib_growth_per_request=7680), run(kib_growth_per_request=900), "not_fixed"),
    (run(), run(), "not_reproduced"),
])
def test_verdicts(before, after, expected):
    assert replay.verdict(before, after)["verdict"] == expected


def fake_sandbox(before, after):
    calls = []

    def run_in_sandbox(file_path, original, diff, files, command=None):
        calls.append({"diff": diff, "files": files, "command": command})
        data = before if diff == "" else after
        return {"exit_code": 0, "output": "noise\n" + replay.MARKER + json.dumps(data)}
    return run_in_sandbox, calls


def test_replay_runs_unpatched_then_patched():
    sandbox, calls = fake_sandbox(run(kib_growth_per_request=5000), run())
    result = replay.replay("sample-app/app.py", "src", "DIFF", {"sample-app/test_app.py": "t"}, LOGS, sandbox)
    assert result["verdict"] == "reproduced_and_fixed" and result["recorded_requests"] == 2
    assert [c["diff"] for c in calls] == ["", "DIFF"]
    assert calls[0]["command"] == ["python", "ghostops_replay.py"] and "sample-app/ghostops_replay.py" in calls[0]["files"]


def test_no_traffic_is_not_applicable():
    sandbox, calls = fake_sandbox(run(), run())
    assert replay.replay("sample-app/math_utils.py", "s", "d", {}, ["FAILED test_x"], sandbox)["verdict"] == "not_applicable"
    assert calls == []


def test_non_web_module_is_not_applicable():
    sandbox, _ = fake_sandbox({"replayable": False}, {"replayable": False})
    assert replay.replay("sample-app/user_service.py", "s", "d", {}, LOGS, sandbox)["verdict"] == "not_applicable"
