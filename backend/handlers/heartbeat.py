import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import shared.audit as audit
from shared.response import _iso_offset, _now
from shared.settings import effective_settings
from shared.store import (agent_history_repo, agents_repo, approvals_repo, audit_repo,
                          fleets_repo, jobs_repo, runs_repo, tenants_repo)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# When a fleet doesn't set its own reap window, dead members are removed this long
# after their last heartbeat. Kept in sync with tenant_fleets.DEFAULT_REAP_AFTER_SECONDS.
DEFAULT_REAP_AFTER_SECONDS = int(os.environ.get("FLEET_REAP_AFTER_SECONDS", str(30 * 60)))


def _human_ts(iso: str) -> str:
    """Readable UTC timestamp (to the second, no microseconds) for human-facing
    notes. Falls back to the raw value if it can't be parsed."""
    if not iso:
        return "unknown time"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return iso


def _iso_to_epoch(iso: str):
    """Parse an ISO timestamp to epoch seconds, or None if it can't be parsed."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


def _reap_fleet_members(now: int, now_iso: str) -> int:
    """Remove fleet members that scaled in and stopped heartbeating. Each fleet's
    reap_after_seconds (or the default) is the grace window after the last heartbeat;
    past it the member's record is deleted (it's a terminated ASG instance). A history
    and audit entry are written first for traceability."""
    fleets = fleets_repo.scan_all()
    if not fleets:
        return 0

    window_by_fleet: dict = {}
    name_by_fleet: dict = {}
    for f in fleets:
        secs = f.get("reap_after_seconds")
        window_by_fleet[f["fleet_id"]] = int(secs) if secs else DEFAULT_REAP_AFTER_SECONDS
        name_by_fleet[f["fleet_id"]] = f.get("name") or f["fleet_id"]

    # Coarse filter by the smallest window so the scan only sees stale members;
    # then apply each member's own fleet window exactly.
    min_window = min(window_by_fleet.values())
    cutoff_iso = datetime.fromtimestamp(now - min_window, tz=timezone.utc).isoformat()

    reaped = 0
    for agent in agents_repo.scan_reapable_fleet_members(cutoff_iso):
        fleet_id = agent.get("fleet_id")
        window = window_by_fleet.get(fleet_id)
        if window is None:
            continue
        hb_epoch = _iso_to_epoch(agent.get("last_heartbeat_at"))
        if hb_epoch is None or hb_epoch >= now - window:
            continue

        fleet_name = name_by_fleet.get(fleet_id, fleet_id)
        agent_history_repo.create({
            "history_id": "agenthistory_" + secrets.token_urlsafe(8),
            "agent_id": agent["agent_id"],
            "tenant_id": agent.get("tenant_id", ""),
            "from_status": agent.get("status", "INACTIVE"),
            "to_status": "DELETED",
            "triggered_by": "reaper",
            "note": f"reaped from fleet '{fleet_name}' - no heartbeat since {_human_ts(agent.get('last_heartbeat_at'))}",
            "created_at": now_iso,
        })
        audit.write(
            "agent.reaped",
            tenant_id=agent.get("tenant_id", ""),
            actor_id=agent["agent_id"],
            actor_name=agent.get("hostname") or agent["agent_id"],
            actor_role="system",
            resource_type="agent",
            resource_id=agent["agent_id"],
            metadata={
                "fleet_id": fleet_id,
                "fleet_name": fleet_name,
                "last_heartbeat_at": agent.get("last_heartbeat_at"),
                "reap_after_seconds": window,
            },
        )
        agents_repo.delete(agent["agent_id"])
        reaped += 1
        logger.info(
            "Reaped fleet member %s (fleet=%s, last_heartbeat_at=%s, window=%ds)",
            agent["agent_id"], fleet_id, agent.get("last_heartbeat_at"), window,
        )
    return reaped


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

    reaped_members = _reap_fleet_members(now, now_iso)
    if reaped_members:
        logger.info("Reaped %d dead fleet member(s)", reaped_members)

    expired_jobs = jobs_repo.expire_stale(_iso_offset(-3600))
    if expired_jobs:
        logger.info("Expired %d stale PENDING job(s)", expired_jobs)

    expired_approvals = 0
    deleted_approvals = 0
    deleted_jobs = 0
    deleted_runs = 0
    deleted_audit_logs = 0
    deleted_agent_history = 0

    if now_dt.minute == 0:
        expired_approvals = approvals_repo.mark_expired(now_iso)
        if expired_approvals:
            logger.info("Marked %d approval(s) as expired", expired_approvals)

    if now_dt.hour == 0 and now_dt.minute == 0:
        # Retention is per-tenant now: each tenant's window comes from its own settings
        # (tenant admin / platform-admin override), so we sweep tenant by tenant.
        def _cutoff(days: int) -> str:
            return (now_dt - timedelta(days=days)).isoformat()

        for tenant in tenants_repo.list_all():
            tid = tenant["tenant_id"]
            s = effective_settings(tenant)
            deleted_approvals += approvals_repo.delete_stale(_cutoff(s["approval_retention_days"]), tenant_id=tid)
            deleted_jobs += jobs_repo.delete_stale(_cutoff(s["job_retention_days"]), tenant_id=tid)
            deleted_runs += runs_repo.delete_stale(_cutoff(s["run_retention_days"]), tenant_id=tid)
            deleted_audit_logs += audit_repo.delete_stale(_cutoff(s["audit_retention_days"]), tenant_id=tid)
            deleted_agent_history += agent_history_repo.delete_stale(_cutoff(s["agent_history_retention_days"]), tenant_id=tid)

        # Platform-level audit trail (tenant_id IS NULL) is a compliance concern the
        # tenant can't relax, so it keeps its own platform-wide env-driven window.
        platform_audit_days = int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
        deleted_platform_audit = audit_repo.delete_stale(_cutoff(platform_audit_days), platform_only=True)
        deleted_audit_logs += deleted_platform_audit

        if deleted_approvals or deleted_jobs or deleted_runs or deleted_audit_logs or deleted_agent_history:
            logger.info(
                "Retention sweep: deleted %d approval(s), %d job(s), %d run(s), %d audit log(s), %d agent history record(s)",
                deleted_approvals, deleted_jobs, deleted_runs, deleted_audit_logs, deleted_agent_history)

    return {
        "marked_inactive": marked_inactive,
        "reaped_members": reaped_members,
        "expired_jobs": expired_jobs,
        "expired_approvals": expired_approvals,
        "deleted_approvals": deleted_approvals,
        "deleted_jobs": deleted_jobs,
        "deleted_runs": deleted_runs,
        "deleted_audit_logs": deleted_audit_logs,
        "deleted_agent_history": deleted_agent_history,
    }


def heartbeat_handler(event, context):
    result = handle_heartbeat_check()
    logger.info(
        "Heartbeat check complete: %d agent(s) marked INACTIVE, %d fleet member(s) reaped, "
        "%d job(s) expired, %d approval(s) expired, %d stale approval(s) deleted, "
        "%d stale job(s) deleted, %d stale run(s) deleted, %d audit log(s) deleted, "
        "%d agent history record(s) deleted",
        result["marked_inactive"],
        result["reaped_members"],
        result["expired_jobs"],
        result["expired_approvals"],
        result["deleted_approvals"],
        result["deleted_jobs"],
        result["deleted_runs"],
        result["deleted_audit_logs"],
        result["deleted_agent_history"],
    )
    return result
