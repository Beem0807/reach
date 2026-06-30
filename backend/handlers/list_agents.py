import logging
from typing import Optional

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_list_agents(raw_token: str, tag: Optional[str] = None) -> dict:
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
            "last_heartbeat_at": a.get("last_heartbeat_at"),
            "token_issued_at": a.get("token_issued_at"),
            "install_token_expires_at": a.get("install_token_expires_at"),
            "active_until": a.get("active_until"),
            "fleet_id": a.get("fleet_id"),
            "type": a.get("type"),
            "mode": a.get("mode", "wild"),
            "access_level": a.get("access_level") or "open",
            "running_as_root": a.get("running_as_root"),
            "k8s_permissions_reported": bool(a.get("k8s_permissions_hash")),
            "k8s_permissions_drift": a.get("k8s_permissions_drift", False),
            "tags": a.get("tags") or [],
            "grant_service_mgmt": a.get("grant_service_mgmt", False),
            "grant_docker": a.get("grant_docker", False),
            "service_mgmt_detected": a.get("service_mgmt_detected"),
            "docker_detected": a.get("docker_detected"),
        }
        for a in rows
        if a.get("status") != "DELETED"
        and can_access_agent(user, a)
        and (tag is None or tag in (a.get("tags") or []))
    ]

    return _ok({"agents": agents})


def list_agents_handler(event, context):
    logger.info("GET /agents")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    tag = (event.get("queryStringParameters") or {}).get("tag")
    return handle_list_agents(token, tag)
