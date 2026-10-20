"""
GhostOps Shield — prompt-injection defence for untrusted telemetry
==================================================================

Logs, alert annotations and error messages are attacker-controlled text (one
crafted request can put anything into a log line), and GhostOps hands that
text to an LLM. Before diagnosis, the shield:

* detects instruction attacks: overrides ("ignore previous instructions"),
  role hijacks ("you are now…"), fake chat turns, attempts to extract secrets,
  and shell/code payloads;
* detects hidden content: zero-width and bidi-override characters, ANSI
  escapes, large encoded blobs;
* quarantines matching log lines (the model sees a marker, never the payload)
  and strips hidden characters from every line.

Source files are scanned and reported but never modified: patches must apply
to them byte for byte.
"""

from __future__ import annotations

import re
from typing import Any

# (category, pattern) — case-insensitive, matched per line.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("instruction_override", re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier|system)\b.{0,40}\b(instructions?|prompts?|rules?|context)\b", re.I)),
    ("instruction_override", re.compile(r"\b(new|updated|real) (instructions?|system prompt)\b", re.I)),
    ("role_hijack", re.compile(r"\b(you are now|act as|pretend to be|from now on,? you)\b", re.I)),
    ("fake_turn", re.compile(r"^\s*(system|assistant|user|human)\s*:|<\|?(im_start|system|endoftext)\|?>|\[/?INST\]|###\s*(instruction|system)", re.I)),
    ("addressing_agent", re.compile(r"\b(dear|hey|attention)\s+(ai|llm|assistant|agent|ghostops|model)\b|\b(ghostops|ai|assistant)\s*[,:]\s*(please|you must|ignore|classify|mark|approve)", re.I)),
    ("verdict_tampering", re.compile(r"\b(classify|mark|report|diagnose)\b.{0,30}\b(as|this as)\b.{0,20}\b(unknown|healthy|safe|passed|resolved|no issue)\b", re.I)),
    ("secret_exfiltration", re.compile(r"\b(print|reveal|show|send|leak|output|include|post|exfiltrate)\b.{0,40}\b(api[_ -]?keys?|tokens?|secrets?|passwords?|credentials?|env(ironment)?( variables)?|github_token|anthropic_api_key)\b", re.I)),
    ("code_execution", re.compile(r"(curl|wget)\b[^|\n]{0,200}\|\s*(ba)?sh\b|\brm\s+-rf\s+/|\b(os\.system|subprocess\.(run|popen|call)|eval|exec)\s*\(|\bbase64\s+(-d|--decode)\b|/dev/tcp/", re.I)),
]

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_BIDI = re.compile("[‪-‮⁦-⁩]")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_BLOB = re.compile(r"[A-Za-z0-9+/=_-]{160,}")

HIDDEN_CATEGORIES = {"zero_width", "bidi_override", "ansi_escape", "encoded_blob"}


def scan_line(line: str) -> list[str]:
    """Categories of suspicious content in one line (empty when clean)."""
    found = [cat for cat, rule in _RULES if rule.search(line)]
    if _ZERO_WIDTH.search(line):
        found.append("zero_width")
    if _BIDI.search(line):
        found.append("bidi_override")
    if _ANSI.search(line):
        found.append("ansi_escape")
    if _BLOB.search(line):
        found.append("encoded_blob")
    return list(dict.fromkeys(found))


def clean_hidden(line: str) -> str:
    """Strip characters a human reviewer can't see but a model reads."""
    return _ANSI.sub("", _BIDI.sub("", _ZERO_WIDTH.sub("", line)))


def _quarantine(line: str, categories: list[str]) -> str:
    return f"[QUARANTINED by GhostOps shield: {', '.join(categories)}]"


def sanitize(failure_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A copy of the context that is safe to show the model, and a report of what was found."""
    findings: list[dict[str, Any]] = []
    logs_out: list[str] = []
    for i, raw in enumerate(failure_context.get("logs", []) or []):
        line = str(raw)
        categories = scan_line(line)
        attack = [c for c in categories if c not in HIDDEN_CATEGORIES]
        if attack:
            findings.append({"where": f"logs[{i}]", "categories": categories, "excerpt": clean_hidden(line)[:160], "action": "quarantined"})
            logs_out.append(_quarantine(line, categories))
        elif categories:
            findings.append({"where": f"logs[{i}]", "categories": categories, "excerpt": clean_hidden(line)[:160], "action": "cleaned"})
            logs_out.append(clean_hidden(line))
        else:
            logs_out.append(line)

    for path, content in (failure_context.get("source_files", {}) or {}).items():
        for n, line in enumerate(str(content).splitlines(), start=1):
            categories = [c for c in scan_line(line) if c not in ("code_execution",)]  # code legitimately calls eval/subprocess
            if categories:
                findings.append({"where": f"{path}:{n}", "categories": categories, "excerpt": clean_hidden(line)[:160], "action": "reported"})

    quarantined = sum(1 for f in findings if f["action"] == "quarantined")
    risk = "high" if quarantined >= 3 or any(f["action"] == "reported" for f in findings) else "elevated" if quarantined else "low" if findings else "none"
    clean = {**failure_context, "logs": logs_out}
    report = {"risk": risk, "quarantined": quarantined, "findings": findings}
    return clean, report
