import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import shared.audit as audit
from shared.response import _iso_offset, _now
from shared.store import agent_history_repo, agents_repo, approvals_repo, audit_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _human_ts(iso: str) -> str:
    """Readable UTC timestamp (to the second, no microseconds) for human-facing
    notes. Falls back to the raw value if it can't be parsed."""
    if not iso:
        return "unknown time"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return iso


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
            agent_history_repo.create({
                "history_id": "agenthistory_" + secrets.token_urlsafe(8),
                "agent_id": agent["agent_id"],
                "tenant_id": agent.get("tenant_id", ""),
                "from_status": "ACTIVE",
                "to_status": "INACTIVE",
                "triggered_by": "heartbeat",
                "note": f"no heartbeat since {_human_ts(agent.get('last_heartbeat_at'))}",
                "created_at": now_iso,
            })
            audit.write(
                "agent.unreachable",
                tenant_id=agent.get("tenant_id", ""),
                actor_id=agent["agent_id"],
                actor_name=agent.get("hostname") or agent["agent_id"],
                actor_role="agent",
                resource_type="agent",
                resource_id=agent["agent_id"],
                metadata={"last_heartbeat_at": agent.get("last_heartbeat_at"), "hostname": agent.get("hostname")},
            )
            marked_inactive += 1

    expired_jobs = jobs_repo.expire_stale(_iso_offset(-3600))
    if expired_jobs:
        logger.info("Expired %d stale PENDING job(s)", expired_jobs)

    expired_approvals = 0
    deleted_approvals = 0
    deleted_jobs = 0
    deleted_audit_logs = 0
    deleted_agent_history = 0

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

        audit_retention_days = int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
        before_audit = (now_dt - timedelta(days=audit_retention_days)).isoformat()
        deleted_audit_logs = audit_repo.delete_stale(before_audit)
        if deleted_audit_logs:
            logger.info("Deleted %d audit log(s) older than %d days", deleted_audit_logs, audit_retention_days)

        agent_history_retention_days = int(os.environ.get("AGENT_HISTORY_RETENTION_DAYS", "30"))
        before_history = (now_dt - timedelta(days=agent_history_retention_days)).isoformat()
        deleted_agent_history = agent_history_repo.delete_stale(before_history)
        if deleted_agent_history:
            logger.info("Deleted %d agent history record(s) older than %d days", deleted_agent_history, agent_history_retention_days)

    return {
        "marked_inactive": marked_inactive,
        "expired_jobs": expired_jobs,
        "expired_approvals": expired_approvals,
        "deleted_approvals": deleted_approvals,
        "deleted_jobs": deleted_jobs,
        "deleted_audit_logs": deleted_audit_logs,
        "deleted_agent_history": deleted_agent_history,
    }


def heartbeat_handler(event, context):
    result = handle_heartbeat_check()
    logger.info(
        "Heartbeat check complete: %d agent(s) marked INACTIVE, %d job(s) expired, "
        "%d approval(s) expired, %d stale approval(s) deleted, %d stale job(s) deleted, "
        "%d audit log(s) deleted, %d agent history record(s) deleted",
        result["marked_inactive"],
        result["expired_jobs"],
        result["expired_approvals"],
        result["deleted_approvals"],
        result["deleted_jobs"],
        result["deleted_audit_logs"],
        result["deleted_agent_history"],
    )
    return result
