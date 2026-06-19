import logging

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_get_agent(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    agent = agents_repo.get(agent_id)
    if not agent or agent.get("status") == "DELETED" or not can_access_agent(user, agent):
        return _err("not found", 404)

    return _ok({
        "agent_id": agent["agent_id"],
        "status": agent["status"],
        "hostname": agent.get("hostname"),
        "agent_version": agent.get("agent_version"),
        "machine_fingerprint": agent.get("machine_fingerprint"),
        "claimed_at": agent.get("claimed_at"),
        "token_issued_at": agent.get("token_issued_at"),
        "last_heartbeat_at": agent.get("last_heartbeat_at"),
        "active_until": agent.get("active_until"),
        "mode": agent.get("mode", "wild"),
        "access_level": agent.get("access_level") or "open",
        "tags": agent.get("tags") or [],
    })


def get_agent_handler(event, context):
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    logger.info("GET /agents/%s", agent_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_get_agent(agent_id, token)
