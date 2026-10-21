"""Repair the one mistake models make most often in a unified diff: the line counts in a hunk header."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")


def recount_hunks(diff: str) -> str:
    """Rewrite each ``@@ -a,b +c,d @@`` so b and d match the hunk body.

    GNU patch and git apply reject a hunk whose counts are off by one, which a model does now and then
    even when the change itself is right. Starts and the change lines are never altered.
    """
    if not diff or "@@" not in diff:
        return diff
    lines = diff.split("\n")
    trailing = 0
    while lines and lines[-1] == "":
        lines.pop()
        trailing += 1
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _HUNK.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        j, old, new = i + 1, 0, 0
        while j < len(lines) and not _HUNK.match(lines[j]):
            if lines[j].startswith("--- ") and j + 1 < len(lines) and lines[j + 1].startswith("+++ "):
                break  # the next file's header
            if lines[j].startswith("\\"):
                pass
            elif lines[j].startswith("-"):
                old += 1
            elif lines[j].startswith("+"):
                new += 1
            else:  # context; a blank line stands for an empty context line
                old += 1
                new += 1
            j += 1
        out.append(f"@@ -{m.group(1)},{old} +{m.group(2)},{new} @@{m.group(3)}")
        out.extend(lines[i + 1:j])
        i = j
    return "\n".join(out) + "\n" * trailing
