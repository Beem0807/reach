import json
import logging
import secrets

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.policy import _is_approved, _is_blocked, _is_readonly_blocked
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_create_job(body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    command = body.get("command", "").strip()

    if not agent_id or not command:
        return _err("agent_id and command required")
    if len(command) > 4096:
        return _err("command too long (max 4096 characters)", 400)

    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    if _is_blocked(command):
        return _err("command is blocked by safety policy", 403)

    agent = agents_repo.get(agent_id)
    if not agent or not can_access_agent(tenant, agent):
        return _err("agent not found", 404)
    if agent.get("status") != "ACTIVE":
        return _err("agent is not active", 409)

    mode = agent.get("mode", "wild")
    if mode == "readonly" and _is_readonly_blocked(command):
        return _err("command not permitted in readonly mode", 403)
    if mode == "approved" and not _is_approved(command, agent.get("approved_commands", [])):
        return _err("command not in approved list for this agent", 403)

    job_id = "job_" + secrets.token_urlsafe(16)
    now = _now()

    jobs_repo.create({
        "job_id": job_id,
        "tenant_id": tenant["tenant_id"],
        "agent_id": agent_id,
        "created_by": tenant["user_id"],
        "command": command,
        "status": "PENDING",
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "duration_ms": None,
        "created_at": _iso(),
        "started_at": None,
        "completed_at": None,
        "expires_at": now + 604800,
        "mode": mode,
    })

    agents_repo.set_active_until(agent_id, now + 120)

    return _ok({"job_id": job_id, "status": "PENDING"}, 201)


def create_job_handler(event, context):
    logger.info("POST /jobs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_create_job(body, token)
