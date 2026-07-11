import json
import logging
import secrets

import shared.audit as audit
from shared.auth import _bearer, _verify_agent_token
from shared.response import _err, _iso, _ok
from shared.store import agent_history_repo, agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_deregister(body: dict, raw_token: str) -> dict:
    """Called by a host agent on ASG scale-in (lifecycle hook / shutdown) to remove
    itself from its fleet immediately, instead of waiting for the reaper. Only host
    agents that are actually members of a fleet are deregistered; anything else is a
    no-op the caller shouldn't be making."""
    machine_fp = (body.get("machine_fingerprint") or "").strip()
    if not machine_fp:
        return _err("machine_fingerprint required", 400)

    # Credential-only: the agent token identifies the agent; no agent_id is sent.
    agent = _verify_agent_token(raw_token)
    if not agent:
        return _err("unauthorized", 401)
    agent_id = agent["agent_id"]

    if agent.get("status") not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    # Deregistration is a fleet (cattle) concept - only host fleet members qualify.
    if agent.get("type") != "host":
        return _err("only host agents can deregister", 409)
    fleet_id = agent.get("fleet_id")
    if not fleet_id:
        return _err("agent is not a fleet member", 409)

    agent_history_repo.create({
        "history_id": "agenthistory_" + secrets.token_urlsafe(8),
        "agent_id": agent_id,
        "tenant_id": agent.get("tenant_id", ""),
        "from_status": agent.get("status", "ACTIVE"),
        "to_status": "DELETED",
        "triggered_by": "agent-deregister",
        "note": "deregistered on scale-in",
        "created_at": _iso(),
    })
    audit.write(
        "agent.deregistered",
        tenant_id=agent.get("tenant_id", ""),
        actor_id=agent_id,
        actor_name=agent.get("hostname") or agent_id,
        actor_role="agent",
        resource_type="agent",
        resource_id=agent_id,
        metadata={"fleet_id": fleet_id, "hostname": agent.get("hostname")},
    )
    agents_repo.delete(agent_id)
    logger.info("Agent %s deregistered from fleet=%s on scale-in", agent_id, fleet_id)

    return _ok({"agent_id": agent_id, "deregistered": True})


def agent_deregister_handler(event, context):
    logger.info("POST /agent/deregister")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_deregister(body, token)
