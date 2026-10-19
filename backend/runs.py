"""
In-memory registry of pipeline runs
===================================

Lets the dashboard start a pipeline run and follow it stage by stage.
Runs live only in memory (the knowledge base keeps the permanent record);
the oldest are dropped once ``max_runs`` is reached.
"""

from __future__ import annotations

import copy
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

# One pipeline at a time, shared by the Alertmanager webhook and /api/simulate:
# the deployer switches git branches to open the PR.
pipeline_lock = threading.Lock()

STAGES = ("diagnosis", "patch", "validation", "deploy")
TERMINAL = {"passed", "failed", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Thread-safe store of recent runs. Every change bumps the run's ``version``."""

    def __init__(self, max_runs: int = 50) -> None:
        self._lock = threading.Lock()
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # Every version of every run, so a live stream can replay each change in order
        # instead of only the latest state (fast stages would otherwise be skipped).
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._max_runs = max_runs

    def create(self, scenario: str, title: str) -> dict[str, Any]:
        run = {
            "id": uuid.uuid4().hex[:12],
            "scenario": scenario,
            "title": title,
            "status": "running",
            "created_at": _now(),
            "updated_at": _now(),
            "version": 0,
            "stages": {s: {"status": "pending", "data": {}} for s in STAGES},
            "result": None,
            "error": None,
            "context": None,
        }
        with self._lock:
            self._runs[run["id"]] = run
            self._history[run["id"]] = [copy.deepcopy(run)]
            while len(self._runs) > self._max_runs:
                old_id, _ = self._runs.popitem(last=False)
                self._history.pop(old_id, None)
            return copy.deepcopy(run)

    def _touch(self, run: dict[str, Any]) -> None:
        run["version"] += 1
        run["updated_at"] = _now()
        self._history.setdefault(run["id"], []).append(copy.deepcopy(run))

    def update_stage(self, run_id: str, stage: str, status: str, data: dict | None = None) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or stage not in run["stages"]:
                return
            run["stages"][stage] = {"status": status, "data": copy.deepcopy(data or {})}
            self._touch(run)

    def set_context(self, run_id: str, context: dict) -> None:
        """Attach what the pipeline was given (alert type, metrics, log tail) for the dashboard."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run["context"] = copy.deepcopy(context)
            self._touch(run)

    def finish(self, run_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        if status not in TERMINAL:
            raise ValueError(f"not a terminal status: {status}")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run["status"] = status
            run["result"] = copy.deepcopy(result)
            run["error"] = error
            for stage in run["stages"].values():
                if stage["status"] == "running":
                    stage["status"] = "error" if status == "error" else "done"
            self._touch(run)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return copy.deepcopy(run) if run else None

    def history(self, run_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Versions of a run from index *since* on (oldest first)."""
        with self._lock:
            return [copy.deepcopy(r) for r in self._history.get(run_id, [])[since:]]

    def list(self) -> list[dict[str, Any]]:
        """Most recent first."""
        with self._lock:
            return [copy.deepcopy(r) for r in reversed(self._runs.values())]


store = RunStore()
