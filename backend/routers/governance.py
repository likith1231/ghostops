"""Kill switch and audit trail."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from agents import governance

router = APIRouter(prefix="/api/governance", tags=["governance"])


class Toggle(BaseModel):
    reason: str = ""
    by: str = "dashboard"


@router.get("")
def get_status() -> dict:
    return governance.status()


@router.post("/pause")
def pause(body: Toggle) -> dict:
    """Engage the kill switch: every automatic action (PRs, rollbacks, auto-merge) stops."""
    return governance.set_paused(True, by=body.by, reason=body.reason)


@router.post("/resume")
def resume(body: Toggle) -> dict:
    return governance.set_paused(False, by=body.by, reason=body.reason)


@router.get("/audit")
def audit(limit: int = 50) -> dict:
    """Recent decisions, newest first, and whether the hash chain is intact."""
    return {"entries": governance.entries(min(limit, 500)), "chain": governance.verify_chain()}
