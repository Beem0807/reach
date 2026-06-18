import json
import logging
from datetime import datetime, timedelta, timezone

from shared.auth import _bearer, _verify_agent_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, approvals_repo, jobs_repo

TOKEN_MAX_AGE_DAYS = 30

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_sync(body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()
    agent_version = body.get("agent_version", "").strip() or None
    running_as_root = body.get("running_as_root")  # bool from agent, None if old agent

    if not agent_id or not machine_fp:
        return _err("agent_id and machine_fingerprint required")

    agent = _verify_agent_token(raw_token, agent_id)
    if not agent:
        return _err("unauthorized", 401)

    agent_status = agent.get("status")
    if agent_status not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    token_issued_at = agent.get("token_issued_at")
    if token_issued_at:
        issued = datetime.fromisoformat(token_issued_at)
        if datetime.now(tz=timezone.utc) - issued >= timedelta(days=TOKEN_MAX_AGE_DAYS):
            return _err("token_expired", 403)

    now = _now()
    next_poll = 2 if int(agent.get("active_until") or 0) > now else 15

    agents_repo.update_heartbeat(
        agent_id,
        reactivate=(agent_status == "INACTIVE"),
        now_iso=_iso(),
        agent_version=agent_version,
        running_as_root=running_as_root if isinstance(running_as_root, bool) else None,
    )

    pending_jobs = jobs_repo.get_pending_for_agent(agent_id)
    approved_commands: list = []
    if any(j.get("mode") == "approved" for j in pending_jobs):
        approved_commands = [a["command"] for a in approvals_repo.list_by_agent(agent_id, status="approved")]

    jobs_payload = []
    for job in pending_jobs:
        if jobs_repo.set_running(job["job_id"], _iso()):
            mode = job.get("mode", "wild")
            jobs_payload.append({
                "job_id": job["job_id"],
                "command": job["command"],
                "mode": mode,
                "is_write": job.get("is_write", False),
                "approved_commands": approved_commands if mode == "approved" else [],
            })

    if jobs_payload:
        next_poll = 2
    return _ok({"jobs": jobs_payload, "next_poll_seconds": next_poll})


def agent_sync_handler(event, context):
    logger.info("POST /agent/sync")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_sync(body, token)
