import logging
from datetime import datetime, timezone

from shared.response import _iso_offset, _now
from shared.store import agents_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_heartbeat_check() -> dict:
    now = _now()

    cutoff_iso = datetime.fromtimestamp(now - 300, tz=timezone.utc).isoformat()
    marked_inactive = 0
    for agent in agents_repo.scan_stale_active(cutoff_iso):
        if agents_repo.mark_inactive(agent["agent_id"]):
            logger.info(
                "Marked agent %s INACTIVE (last_heartbeat_at=%s)",
                agent["agent_id"],
                agent.get("last_heartbeat_at"),
            )
            marked_inactive += 1

    expired_jobs = jobs_repo.expire_stale(_iso_offset(-3600))
    if expired_jobs:
        logger.info("Expired %d stale PENDING job(s)", expired_jobs)

    return {"marked_inactive": marked_inactive, "expired_jobs": expired_jobs}


def heartbeat_handler(event, context):
    result = handle_heartbeat_check()
    logger.info(
        "Heartbeat check complete: %d agent(s) marked INACTIVE, %d job(s) expired",
        result["marked_inactive"],
        result["expired_jobs"],
    )
    return result
