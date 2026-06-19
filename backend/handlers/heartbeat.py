import logging
import os
from datetime import datetime, timedelta, timezone

from shared.response import _iso_offset, _now
from shared.store import agents_repo, approvals_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_heartbeat_check() -> dict:
    now = _now()
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    now_iso = now_dt.isoformat()

    cutoff_iso = datetime.fromtimestamp(now - 45, tz=timezone.utc).isoformat()
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

    expired_approvals = 0
    deleted_approvals = 0

    if now_dt.minute == 0:
        expired_approvals = approvals_repo.mark_expired(now_iso)
        if expired_approvals:
            logger.info("Marked %d approval(s) as expired", expired_approvals)

    if now_dt.hour == 0 and now_dt.minute == 0:
        retention_days = int(os.environ.get("APPROVAL_RETENTION_DAYS", "7"))
        before_iso = (now_dt - timedelta(days=retention_days)).isoformat()
        deleted_approvals = approvals_repo.delete_stale(before_iso)
        if deleted_approvals:
            logger.info("Deleted %d stale approval record(s) older than %d days", deleted_approvals, retention_days)

    return {
        "marked_inactive": marked_inactive,
        "expired_jobs": expired_jobs,
        "expired_approvals": expired_approvals,
        "deleted_approvals": deleted_approvals,
    }


def heartbeat_handler(event, context):
    result = handle_heartbeat_check()
    logger.info(
        "Heartbeat check complete: %d agent(s) marked INACTIVE, %d job(s) expired, "
        "%d approval(s) expired, %d stale approval(s) deleted",
        result["marked_inactive"],
        result["expired_jobs"],
        result["expired_approvals"],
        result["deleted_approvals"],
    )
    return result
