"""Platform admin: read-only agent listing (used for tenant card counts)."""
import logging
import os

from shared.policy import compute_access_level
from shared.response import _err, _ok
from shared.store import agents_repo, tenants_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

from shared.admin_auth import verify_session_token as _verify_admin


def handle_list_agents_admin(tenant_id: str, raw_token: str, tag: str = None) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not tenant_id:
        return _err("tenant_id query parameter required", 400)

    if not tenants_repo.get(tenant_id):
        return _err("tenant not found", 404)

    rows = agents_repo.list_by_tenant(tenant_id)

    def _enrich(a: dict) -> dict:
        mode = a.get("mode", "wild")
        root_str = a.get("running_as_root")
        root_bool = root_str == "true" if root_str in ("true", "false") else None
        access_level = compute_access_level(mode, bool(root_bool)) if root_bool is not None else None
        return {
            "agent_id":                  a["agent_id"],
            "tenant_id":                 a.get("tenant_id"),
            "status":                    a.get("status"),
            "hostname":                  a.get("hostname"),
            "agent_version":             a.get("agent_version"),
            "mode":                      mode,
            "running_as_root":           root_str,
            "access_level":              access_level,
            "claimed_at":                a.get("claimed_at"),
            "last_heartbeat_at":         a.get("last_heartbeat_at"),
            "active_until":              a.get("active_until"),
            "token_issued_at":           a.get("token_issued_at"),
            "install_token_expires_at":  a.get("install_token_expires_at"),
            "rotation_requested":        a.get("rotation_requested", False),
            "fleet_id":                  a.get("fleet_id"),
            "type":                      a.get("type"),
            "tags":                      a.get("tags") or [],
        }

    return _ok({
        "agents": [
            _enrich(a)
            for a in rows
            if tag is None or tag in (a.get("tags") or [])
        ]
    })


def list_agents_admin_handler(event, context):
    logger.info("GET /admin/agents")
    auth = (event.get("headers") or {}).get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    tenant_id = qs.get("tenant_id", "")
    tag = qs.get("tag") or None
    return handle_list_agents_admin(tenant_id, token, tag)
