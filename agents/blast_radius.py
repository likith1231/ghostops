"""
Blast-radius gate
=================

A third safety gate, between the OPA policy check and the sandbox. OPA
decides *which files* a patch may touch; this decides whether *what the patch
does* is small and safe enough for an AI-written fix. A patch is rejected if it:

* is too large (``MAX_CHANGED_LINES``) or touches more than one file;
* weakens tests: removes assertions or test functions, or adds skip/xfail;
* adds a dependency: a third-party import the file didn't already have, or
  a requirements/manifest change;
* touches sensitive code: auth, secrets, credentials, tokens, keys;
* adds dangerous calls: eval/exec, os.system, subprocess, pickle, unsafe yaml;
* adds network calls: requests, urllib, sockets, http clients;
* raises privileges in Kubernetes manifests.

Pure analysis of the diff; no LLM, no network.
"""

from __future__ import annotations

import re
import sys
from typing import Any

MAX_CHANGED_LINES = 40
MAX_FILES = 1

_STDLIB = set(sys.stdlib_module_names)
_TEST_PATH = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")
_DEPENDENCY_FILES = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|package\.json|go\.mod)$")
_SENSITIVE_PATH = re.compile(r"auth|secret|credential|password|token|security|crypto|\biam\b|rbac|\.pem$|\.key$", re.I)
_SENSITIVE_CODE = re.compile(r"\b(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key|credentials?)\b|os\.environ|getenv\(", re.I)
_DANGEROUS = re.compile(r"\b(eval|exec|compile|__import__)\s*\(|\bos\.(system|popen)\s*\(|\bsubprocess\.|\bpickle\.loads?\s*\(|\byaml\.load\s*\((?![^)]*SafeLoader)|\bmarshal\.loads\s*\(")
_NETWORK = re.compile(r"\b(requests|httpx|aiohttp|urllib|http\.client|socket|smtplib|ftplib)\b\.?|\bfetch\s*\(")
_PRIVILEGE = re.compile(r"privileged:\s*true|hostNetwork:\s*true|hostPID:\s*true|runAsUser:\s*0\b|allowPrivilegeEscalation:\s*true|add:\s*\[?\s*[\"']?(SYS_ADMIN|NET_ADMIN|ALL)", re.I)
_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")
_TEST_WEAKEN_ADDED = re.compile(r"pytest\.(skip|xfail)|@pytest\.mark\.(skip|xfail|skipif)|@unittest\.skip|\bassert\s+True\b")


def _parse(diff: str) -> dict[str, dict[str, list[str]]]:
    """{file: {"added": [...], "removed": [...]}} from a unified diff."""
    files: dict[str, dict[str, list[str]]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t")[0].strip()
            path = path[2:] if path.startswith(("a/", "b/")) else path
            current = files.setdefault(path, {"added": [], "removed": []})
        elif line.startswith("--- ") or line.startswith("@@"):
            continue
        elif current is not None and line.startswith("+"):
            current["added"].append(line[1:])
        elif current is not None and line.startswith("-"):
            current["removed"].append(line[1:])
    return files


def _top_module(line: str) -> str | None:
    m = _IMPORT.match(line)
    return (m.group(1) or m.group(2)).split(".")[0] if m else None


def analyze(diff: str, original: str = "") -> dict[str, Any]:
    """Violations and size metrics for a patch. ``original`` is the file before the patch."""
    files = _parse(diff)
    violations: list[dict[str, str]] = []

    def flag(rule: str, detail: str) -> None:
        violations.append({"rule": rule, "detail": detail})

    added_total = sum(len(f["added"]) for f in files.values())
    removed_total = sum(len(f["removed"]) for f in files.values())
    if added_total + removed_total > MAX_CHANGED_LINES:
        flag("too_large", f"{added_total + removed_total} changed lines (max {MAX_CHANGED_LINES})")
    if len(files) > MAX_FILES:
        flag("too_many_files", f"{len(files)} files changed (max {MAX_FILES})")

    already_imported = {_top_module(l) for l in original.splitlines()} - {None}
    for path, change in files.items():
        added, removed = change["added"], change["removed"]
        if _TEST_PATH.search(path):
            if any(re.search(r"\bassert\b|def test_", l) for l in removed):
                flag("weakens_tests", f"{path}: removes assertions or tests")
            if any(_TEST_WEAKEN_ADDED.search(l) for l in added):
                flag("weakens_tests", f"{path}: skips or neutralises tests")
        if _DEPENDENCY_FILES.search(path):
            flag("new_dependency", f"{path}: dependency manifest changed")
        if _SENSITIVE_PATH.search(path):
            flag("sensitive_code", f"{path}: auth/secrets file")
        for line in added:
            module = _top_module(line)
            if module and module not in _STDLIB and module not in already_imported:
                flag("new_dependency", f"new third-party import: {module}")
            if _SENSITIVE_CODE.search(line):
                flag("sensitive_code", f"touches secrets/credentials: {line.strip()[:80]}")
            if _DANGEROUS.search(line):
                flag("dangerous_call", line.strip()[:80])
            if _NETWORK.search(line) and not line.strip().startswith("#"):
                flag("network_call", line.strip()[:80])
            if _PRIVILEGE.search(line):
                flag("privilege", line.strip()[:80])

    # One entry per (rule, detail).
    unique = list({(v["rule"], v["detail"]): v for v in violations}.values())
    return {
        "passed": not unique,
        "violations": unique,
        "metrics": {"files": len(files), "added": added_total, "removed": removed_total},
    }
