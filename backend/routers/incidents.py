from fastapi import APIRouter, HTTPException
import sqlite3
import logging
from agents import correlation, feedback
from agents.knowledge_base import _DB_PATH, get_past_incidents

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
logger = logging.getLogger("ghostops.backend.routers.incidents")

@router.get("/summary")
async def incidents_summary() -> dict:
    """Aggregate incident statistics for Grafana stat/gauge panels.
    Returns total count, pass count, fail count, pass rate, and PR count.
    """
    if not _DB_PATH.exists():
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": 0.0,
            "prs_created": 0,
        }

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        row = conn.execute(
            """\
            SELECT
                COUNT(*)                                    AS total,
                SUM(CASE WHEN validation_passed = 1 THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN validation_passed = 0 THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN pr_created = 1 THEN 1 ELSE 0 END)        AS prs_created
            FROM incidents
            """
        ).fetchone()
        conn.close()

        total, passed, failed, prs_created = row
        total = total or 0
        passed = passed or 0
        failed = failed or 0
        prs_created = prs_created or 0
        pass_rate = round((passed / total * 100) if total > 0 else 0, 1)

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "prs_created": prs_created,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to query incident summary: %s", exc)
        return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0, "prs_created": 0}

@router.get("/recent")
async def incidents_recent(limit: int = 20) -> list[dict]:
    """Return the most recent incidents as a list of dicts for Grafana tables."""
    return get_past_incidents(limit=min(limit, 100))

@router.get("/timeseries")
async def incidents_timeseries() -> list[dict]:
    """Return daily pass/fail counts for Grafana time-series panels."""
    if not _DB_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """\
            SELECT
                DATE(timestamp) AS date,
                SUM(CASE WHEN validation_passed = 1 THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN validation_passed = 0 THEN 1 ELSE 0 END) AS failed
            FROM incidents
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
            LIMIT 30
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to query incident timeseries: %s", exc)
        return []


@router.get("/correlated")
async def incidents_correlated(limit: int = 20) -> list[dict]:
    """Incidents as GhostOps groups them: repeats and related alerts on one workload."""
    return correlation.incidents(min(limit, 100), db_path=_DB_PATH)


@router.get("/feedback")
async def incidents_feedback() -> dict:
    """What human reviewers did with GhostOps' PRs, as last synced from GitHub."""
    return feedback.acceptance(db_path=_DB_PATH)


@router.post("/feedback/sync")
def incidents_feedback_sync() -> dict:
    """Ask GitHub for the current state of every undecided PR, then report."""
    counts = feedback.sync_pr_outcomes(db_path=_DB_PATH)
    return {**counts, **feedback.acceptance(db_path=_DB_PATH)}


@router.get("/{incident_id}")
async def incident_detail(incident_id: int) -> dict:
    """One incident with its full diff and outcome, for the dashboard's detail view."""
    if not _DB_PATH.exists():
        raise HTTPException(status_code=404, detail="incident not found")
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to query incident %s: %s", incident_id, exc)
        raise HTTPException(status_code=500, detail="knowledge base unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return dict(row)
