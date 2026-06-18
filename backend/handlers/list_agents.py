import logging

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_list_agents(raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    rows = agents_repo.list_by_tenant(user["tenant_id"])

    agents = [
        {
            "agent_id": a["agent_id"],
            "status": a.get("status"),
            "hostname": a.get("hostname"),
            "agent_version": a.get("agent_version"),
            "claimed_at": a.get("claimed_at"),
            "mode": a.get("mode", "wild"),
        }
        for a in rows
        if can_access_agent(user, a)
    ]

    return _ok({"agents": agents})


def list_agents_handler(event, context):
    logger.info("GET /agents")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_list_agents(token)
