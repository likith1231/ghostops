"""
GhostOps Feedback Loop
======================

Reads what human reviewers did with GhostOps' pull requests (merged, closed
without merging, superseded, or still open) from the GitHub API and records it in the
knowledge base. Future diagnoses see it through ``past_incident_context()``,
and the dashboard reports the human acceptance rate.

Public repositories need no token; ``GITHUB_TOKEN`` is sent when set.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from agents import knowledge_base

logger = logging.getLogger("ghostops.feedback")

_PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")
# Merged and superseded are final; closed PRs are re-checked in case they get labelled later.
# PRs closed with one of these labels were duplicates, not rejected fixes.
SUPERSEDED_LABELS = {"duplicate", "superseded"}


def _token() -> str:
    try:
        from agents.config import GITHUB_TOKEN

        return GITHUB_TOKEN
    except Exception:  # noqa: BLE001 - config refuses to import without an LLM key
        import os

        return os.getenv("GITHUB_TOKEN", "")


def pr_state(pr_url: str, token: str = "") -> str | None:
    """'merged' | 'closed' | 'superseded' | 'open' for a GitHub PR URL, or None if it can't be read."""
    m = _PR_URL.match(pr_url or "")
    if not m:
        return None
    owner, repo, number = m.groups()
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}", headers=headers, timeout=10)
    except requests.RequestException as exc:
        logger.warning("GitHub unreachable for %s: %s", pr_url, exc)
        return None
    if resp.status_code != 200:
        logger.warning("GitHub returned %s for %s", resp.status_code, pr_url)
        return None
    data = resp.json()
    if data.get("merged_at"):
        return "merged"
    if data.get("state") != "closed":
        return "open"
    labels = {label.get("name", "").lower() for label in data.get("labels", [])}
    return "superseded" if labels & SUPERSEDED_LABELS else "closed"


def sync_pr_outcomes(*, db_path: str | Path | None = None) -> dict[str, int]:
    """Refresh pr_state for every incident with a PR that is not merged/closed yet."""
    path = Path(db_path) if db_path else knowledge_base._DB_PATH
    counts = {"checked": 0, "updated": 0, "failed": 0}
    if not path.exists():
        return counts
    knowledge_base.init_db(path)  # adds the feedback columns to older databases
    token = _token()
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT id, pr_url, pr_state FROM incidents WHERE pr_url LIKE 'https://github.com/%' "
            "AND (pr_state IS NULL OR pr_state NOT IN ('merged', 'superseded'))"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for incident_id, url, old in rows:
            counts["checked"] += 1
            state = pr_state(url, token)
            if state is None:
                counts["failed"] += 1
                continue
            if state != old:
                counts["updated"] += 1
            conn.execute("UPDATE incidents SET pr_state = ?, pr_checked_at = ? WHERE id = ?", (state, now, incident_id))
        conn.commit()
    finally:
        conn.close()
    return counts


def acceptance(*, db_path: str | Path | None = None) -> dict[str, Any]:
    """How human reviewers treated GhostOps' PRs."""
    path = Path(db_path) if db_path else knowledge_base._DB_PATH
    empty = {"prs": 0, "merged": 0, "closed": 0, "superseded": 0, "open": 0, "unknown": 0, "acceptance_rate": None, "last_checked": None}
    if not path.exists():
        return empty
    knowledge_base.init_db(path)
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT pr_state, pr_checked_at FROM incidents WHERE pr_created = 1").fetchall()
    finally:
        conn.close()
    out = dict(empty, prs=len(rows))
    for state, _ in rows:
        out[state if state in ("merged", "closed", "superseded", "open") else "unknown"] += 1
    # Superseded (duplicate) PRs say nothing about the fix's quality: left out of the rate.
    decided = out["merged"] + out["closed"]
    out["acceptance_rate"] = round(out["merged"] / decided * 100, 1) if decided else None
    out["last_checked"] = max((c for _, c in rows if c), default=None)
    return out
