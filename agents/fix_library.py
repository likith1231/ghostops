"""
Fix library — skills learned from human-approved fixes
======================================================

When a human merges one of GhostOps' PRs, the fix is recorded (root cause +
file + the change, normalised). Once the same change has been merged
``PROMOTE_AFTER`` times it becomes a promoted skill: for a new incident with
that root cause on that file, GhostOps tries the learned fix first — if it
still applies cleanly to the current code it is used with no LLM call
(instant, free, offline). It still goes through every gate.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from agents import governance, knowledge_base

PROMOTE_AFTER = 2

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS fix_library (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    root_cause TEXT NOT NULL,
    file_name  TEXT NOT NULL,
    change_key TEXT NOT NULL,
    diff       TEXT NOT NULL,
    merges     INTEGER NOT NULL DEFAULT 1,
    uses       INTEGER NOT NULL DEFAULT 0,
    learned_at TEXT NOT NULL,
    last_used  TEXT,
    UNIQUE (root_cause, file_name, change_key)
);
"""


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else knowledge_base._DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _target(diff: str) -> str | None:
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t")[0].strip()
            return PurePosixPath(path[2:] if path.startswith(("a/", "b/")) else path).name
    return None


def change_key(diff: str) -> str:
    """The change itself, independent of paths, line numbers and whitespace."""
    return "\n".join(
        line[0] + " ".join(line[1:].split())
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---")) and line[1:].strip()
    )


def learn(root_cause: str, diff: str, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Record a human-merged fix; returns the library entry (promoted once merged often enough)."""
    file_name, key = _target(diff), change_key(diff)
    if not root_cause or not file_name or not key:
        return None
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO fix_library (root_cause, file_name, change_key, diff, learned_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(root_cause, file_name, change_key) DO UPDATE SET merges = merges + 1, diff = excluded.diff",
            (root_cause, file_name, key, diff, now),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM fix_library WHERE root_cause = ? AND file_name = ? AND change_key = ?", (root_cause, file_name, key)).fetchone())
    finally:
        conn.close()
    row["promoted"] = row["merges"] >= PROMOTE_AFTER
    governance.record("fix_library", "promoted" if row["promoted"] else "learned", f"{root_cause} on {file_name} (merged {row['merges']}x)", db_path=db_path)
    return row


def entries(*, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM fix_library ORDER BY merges DESC, id")]
    finally:
        conn.close()
    for r in rows:
        r["promoted"] = r["merges"] >= PROMOTE_AFTER
    return rows


def _applies(file_path: str, content: str, diff: str) -> bool:
    from agents.validation_officer import _apply_patch, _write_tree

    with tempfile.TemporaryDirectory() as td:
        _write_tree(Path(td), {file_path: content})
        return _apply_patch(Path(td), file_path, diff) is None


def suggest(root_cause: str, source_files: dict[str, str], *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """A promoted skill that applies cleanly to the current code, as a patch; otherwise None."""
    conn = _connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM fix_library WHERE root_cause = ? AND merges >= ? ORDER BY merges DESC, uses DESC", (root_cause, PROMOTE_AFTER)
        )]
    finally:
        conn.close()
    for skill in rows:
        for path, content in source_files.items():
            if PurePosixPath(path).name != skill["file_name"] or not _applies(path, content, skill["diff"]):
                continue
            conn = _connect(db_path)
            try:
                conn.execute("UPDATE fix_library SET uses = uses + 1, last_used = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), skill["id"]))
                conn.commit()
            finally:
                conn.close()
            return {
                "diff": skill["diff"],
                "file_path": path,
                "explanation": f"Learned fix from the fix library: humans merged this change {skill['merges']} times for {root_cause} in {skill['file_name']}.",
                "source": "fix_library",
                "skill_id": skill["id"],
            }
    return None
