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
    deleted_jobs = 0

    if now_dt.minute == 0:
        expired_approvals = approvals_repo.mark_expired(now_iso)
        if expired_approvals:
            logger.info("Marked %d approval(s) as expired", expired_approvals)

    if now_dt.hour == 0 and now_dt.minute == 0:
        approval_retention_days = int(os.environ.get("APPROVAL_RETENTION_DAYS", "7"))
        before_approvals = (now_dt - timedelta(days=approval_retention_days)).isoformat()
        deleted_approvals = approvals_repo.delete_stale(before_approvals)
        if deleted_approvals:
            logger.info("Deleted %d stale approval record(s) older than %d days", deleted_approvals, approval_retention_days)

        job_retention_days = int(os.environ.get("JOB_RETENTION_DAYS", "7"))
        before_jobs = (now_dt - timedelta(days=job_retention_days)).isoformat()
        deleted_jobs = jobs_repo.delete_stale(before_jobs)
        if deleted_jobs:
            logger.info("Deleted %d stale job record(s) older than %d days", deleted_jobs, job_retention_days)

    return {
        "marked_inactive": marked_inactive,
        "expired_jobs": expired_jobs,
        "expired_approvals": expired_approvals,
        "deleted_approvals": deleted_approvals,
        "deleted_jobs": deleted_jobs,
    }


def heartbeat_handler(event, context):
    result = handle_heartbeat_check()
    logger.info(
        "Heartbeat check complete: %d agent(s) marked INACTIVE, %d job(s) expired, "
        "%d approval(s) expired, %d stale approval(s) deleted, %d stale job(s) deleted",
        result["marked_inactive"],
        result["expired_jobs"],
        result["expired_approvals"],
        result["deleted_approvals"],
        result["deleted_jobs"],
    )
    return result
