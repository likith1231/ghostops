"""
Counterfactual replay
=====================

Unit tests check what the developer thought of; this checks the fix against
what actually happened. The requests recorded in the pod's access log during
the incident are replayed, in the Docker sandbox, against the unpatched code
(the incident must reproduce: errors, exceptions or memory growth per
request) and against the patched code (it must not).

Applies to HTTP incidents whose patched module is a Flask app; otherwise the
result is "not_applicable" and nothing is blocked.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from textwrap import dedent
from typing import Any

# Werkzeug / common access-log request line: "GET /path?q=1 HTTP/1.1" 200
_REQUEST = re.compile(r'"(GET|POST|PUT|DELETE|PATCH|HEAD) (/[^ "]*) HTTP/[\d.]+" (\d{3})')
_SKIP = {"/metrics", "/favicon.ico"}
MAX_REQUESTS = 200
AMPLIFY = 10  # replay the recorded traffic this many times, so per-request growth stands out

GROWTH_PROBLEM_KIB = 256  # memory still growing per request (second half of the replay) that counts as a leak
MARKER = "GHOSTOPS_REPLAY "


def extract_requests(logs: list[str]) -> list[dict[str, Any]]:
    """Requests seen in the logs, oldest first (scrapes excluded)."""
    out = []
    for line in logs:
        m = _REQUEST.search(str(line))
        if m and m.group(2).split("?")[0] not in _SKIP:
            out.append({"method": m.group(1), "path": m.group(2), "status": int(m.group(3))})
    return out[-MAX_REQUESTS:]


def harness(module: str, requests: list[dict[str, Any]]) -> str:
    """A script that replays the requests against ``module.app`` and prints one JSON result line."""
    return dedent(f'''\
        import gc, json, tracemalloc
        tracemalloc.start()
        import {module} as target

        app = getattr(target, "app", None)
        result = {{"replayable": hasattr(app, "test_client")}}
        if result["replayable"]:
            client = app.test_client()
            requests = json.loads({json.dumps(json.dumps(requests))})
            statuses, exceptions, memory = {{}}, 0, []
            for _ in range({AMPLIFY}):
                for r in requests:
                    try:
                        code = client.open(r["path"], method=r["method"]).status_code
                    except Exception:
                        exceptions += 1
                        code = 500
                    statuses[str(code)] = statuses.get(str(code), 0) + 1
                gc.collect()
                memory.append(tracemalloc.get_traced_memory()[0])
            # A leak keeps growing with traffic; a bounded cache levels off. Measure the second half only.
            half = len(memory) // 2
            growth = (memory[-1] - memory[half - 1]) / max(1, len(requests) * (len(memory) - half))
            result.update(
                requests=len(requests) * {AMPLIFY},
                statuses=statuses,
                server_errors=sum(n for c, n in statuses.items() if c.startswith("5")),
                exceptions=exceptions,
                kib_growth_per_request=round(growth / 1024, 1),
                memory_mib=[round(m / 1048576, 1) for m in memory],
            )
        print("{MARKER}" + json.dumps(result))
        ''')


def _parse(output: str) -> dict[str, Any] | None:
    for line in output.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    return None


def _problem(run: dict[str, Any]) -> list[str]:
    found = []
    if run.get("server_errors"):
        found.append(f"{run['server_errors']} server errors")
    if run.get("exceptions"):
        found.append(f"{run['exceptions']} exceptions")
    if run.get("kib_growth_per_request", 0) >= GROWTH_PROBLEM_KIB:
        found.append(f"memory growing {run['kib_growth_per_request']} KiB per request")
    return found


def verdict(unpatched: dict[str, Any], patched: dict[str, Any]) -> dict[str, Any]:
    before, after = _problem(unpatched), _problem(patched)
    if not before:
        return {"verdict": "not_reproduced", "summary": "The recorded traffic does not reproduce the incident on the unpatched code."}
    if after:
        return {"verdict": "not_fixed", "summary": f"Replay still shows {', '.join(after)} with the fix (before: {', '.join(before)})."}
    return {"verdict": "reproduced_and_fixed", "summary": f"Replayed traffic reproduced the incident ({', '.join(before)}) and the fix removes it."}


def replay(file_path: str, original: str, diff: str, extra_files: dict[str, str], logs: list[str], run_in_sandbox) -> dict[str, Any]:
    """Replay the incident's requests before and after the patch (``run_in_sandbox`` = the sandbox runner)."""
    requests = extract_requests(logs)
    module = PurePosixPath(file_path).stem
    if not requests or not file_path.endswith(".py"):
        return {"verdict": "not_applicable", "summary": "No recorded HTTP traffic to replay.", "requests": len(requests)}
    script = {str(PurePosixPath(file_path).parent / "ghostops_replay.py"): harness(module, requests)}
    command = ["python", "ghostops_replay.py"]
    before = _parse(run_in_sandbox(file_path, original, "", {**extra_files, **script}, command=command)["output"])
    after = _parse(run_in_sandbox(file_path, original, diff, {**extra_files, **script}, command=command)["output"])
    if not before or not after or not before.get("replayable"):
        return {"verdict": "not_applicable", "summary": f"{module} is not a replayable web app.", "requests": len(requests)}
    return {**verdict(before, after), "recorded_requests": len(requests), "unpatched": before, "patched": after}
