import logging
import os
import secrets

from shared.auth import INSTALL_TOKEN_PREFIX, _hmac_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, tenants_repo, users_repo
from shared.tags import validate_tags

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]
S3_BASE = os.environ.get("RELEASES_S3_BASE", "https://reach-releases.s3.amazonaws.com")
_AGENT_VERSION = os.environ.get("AGENT_VERSION", "latest")
_S3_VERSIONED = f"{S3_BASE}/agent/{_AGENT_VERSION}"

INSTALL_TOKEN_TTL = 86400  # 24 hours


def _verify_admin(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, ADMIN_TOKEN)


def _build_install_commands(api_url: str, agent_id: str, raw_install_token: str) -> dict:
    agent_flags = (
        f"--api-url \"{api_url}\" "
        f"--agent-id \"{agent_id}\" "
        f"--install-token \"{raw_install_token}\""
    )
    agent_config = (
        f'{{\"api_url\":\"{api_url}\",\"agent_id\":\"{agent_id}\",'
        f'\"install_token\":\"{raw_install_token}\"}}'
    )
    return {
        "agent_linux": (
            f"curl -fsSL {_S3_VERSIONED}/install.sh | sudo bash -s -- {agent_flags}"
        ),
        "agent_mac_arm": (
            f"mkdir -p /tmp/reach-agent\n"
            f"curl -fsSL {_S3_VERSIONED}/reach-agent-darwin-arm64 -o /tmp/reach-agent/reach-agent\n"
            f"chmod +x /tmp/reach-agent/reach-agent\n"
            f"echo '{agent_config}' > /tmp/reach-agent/config.json\n"
            f"REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent"
        ),
        "agent_mac_intel": (
            f"mkdir -p /tmp/reach-agent\n"
            f"curl -fsSL {_S3_VERSIONED}/reach-agent-darwin-amd64 -o /tmp/reach-agent/reach-agent\n"
            f"chmod +x /tmp/reach-agent/reach-agent\n"
            f"echo '{agent_config}' > /tmp/reach-agent/config.json\n"
            f"REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent"
        ),
        "cli_use": f"reach agents use {agent_id}",
    }


def handle_create_agent(body: dict, raw_token: str, api_url: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    tenant_id = body.get("tenant_id", "").strip()
    mode = body.get("mode", "wild").strip()

    if not tenant_id:
        return _err("tenant_id required")
    if mode not in ("wild", "readonly", "approved"):
        return _err("mode must be wild, readonly, or approved")
    if not tenants_repo.get(tenant_id):
        return _err("tenant not found", 404)

    agent_id = "agent_" + secrets.token_urlsafe(12)
    raw_install_token = INSTALL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = _now() + INSTALL_TOKEN_TTL

    agents_repo.create({
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "status": "CREATED",
        "type": "manual",
        "fleet_id": None,
        "mode": mode,
        "approved_commands": [],
        "install_token_hash": _hmac_token(raw_install_token),
        "install_token_expires_at": expires_at,
        "created_at": _iso(),
    })

    logger.info("Created agent=%s tenant=%s", agent_id, tenant_id)

    return _ok({
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "install_token": raw_install_token,
        "install_token_expires_at": _iso(),
        "mode": mode,
        "commands": _build_install_commands(api_url, agent_id, raw_install_token),
    }, 201)


def handle_reissue_install_token(agent_id: str, body: dict, raw_token: str, api_url: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)

    force = bool(body.get("force", False))
    if agent.get("status") == "ACTIVE" and not force:
        return _err(
            "agent is currently ACTIVE - reissuing will disconnect it immediately "
            "with no in-band recovery. Pass {\"force\": true} to proceed anyway.",
            409,
        )

    raw_install_token = INSTALL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = _now() + INSTALL_TOKEN_TTL

    agents_repo.reissue_install_token(agent_id, _hmac_token(raw_install_token), expires_at)

    logger.info("Reissued install token for agent=%s (was status=%s)", agent_id, agent.get("status"))

    return _ok({
        "agent_id": agent_id,
        "install_token": raw_install_token,
        "install_token_expires_at": _iso(),
        "commands": _build_install_commands(api_url, agent_id, raw_install_token),
    })


def handle_list_agents_admin(tenant_id: str, raw_token: str, tag: str = None) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not tenant_id:
        return _err("tenant_id query parameter required", 400)

    if not tenants_repo.get(tenant_id):
        return _err("tenant not found", 404)

    rows = agents_repo.list_by_tenant(tenant_id)

    return _ok({
        "agents": [
            {
                "agent_id": a["agent_id"],
                "status": a.get("status"),
                "hostname": a.get("hostname"),
                "agent_version": a.get("agent_version"),
                "claimed_at": a.get("claimed_at"),
                "token_issued_at": a.get("token_issued_at"),
                "mode": a.get("mode", "wild"),
                "tags": a.get("tags") or [],
            }
            for a in rows
            if tag is None or tag in (a.get("tags") or [])
        ]
    })


def handle_delete_agent(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)

    force = bool(body.get("force", False))
    if agent.get("status") == "ACTIVE" and not force:
        return _err(
            "agent is currently ACTIVE - deleting will disconnect it immediately "
            "with no in-band recovery. Pass {\"force\": true} to proceed anyway.",
            409,
        )

    tenant_id = agent["tenant_id"]
    agents_repo.delete(agent_id)
    users_repo.remove_agent_from_all_users(agent_id, tenant_id)
    logger.info("Deleted agent=%s (was status=%s)", agent_id, agent.get("status"))

    return _ok({"agent_id": agent_id, "deleted": True})


def handle_get_agent_tags(agent_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    return _ok({"agent_id": agent_id, "tags": agent.get("tags") or []})


def handle_set_agent_tags(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    tags = body.get("tags", [])
    err = validate_tags(tags)
    if err:
        return _err(err, 400)
    agents_repo.set_tags(agent_id, tags)
    logger.info("Set tags for agent=%s tags=%s", agent_id, tags)
    return _ok({"agent_id": agent_id, "tags": tags})


def handle_add_agent_tags(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    new_tags = body.get("tags", [])
    err = validate_tags(new_tags)
    if err:
        return _err(err, 400)
    current = set(agent.get("tags") or [])
    merged = list(current | set(new_tags))
    agents_repo.set_tags(agent_id, merged)
    logger.info("Added tags for agent=%s tags=%s", agent_id, new_tags)
    return _ok({"agent_id": agent_id, "tags": merged})


def handle_remove_agent_tags(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    remove = set(body.get("tags", []))
    current = agent.get("tags") or []
    updated = [t for t in current if t not in remove]
    agents_repo.set_tags(agent_id, updated)
    logger.info("Removed tags for agent=%s removed=%s", agent_id, remove)
    return _ok({"agent_id": agent_id, "tags": updated})


def _token_and_api_url(event: dict) -> tuple:
    headers = event.get("headers") or {}
    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    host = headers.get("host", "")
    api_url = f"https://{host}" if host else os.environ.get("API_URL", "")
    return token, api_url


def list_agents_admin_handler(event, context):
    logger.info("GET /admin/agents")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    tenant_id = qs.get("tenant_id", "")
    tag = qs.get("tag") or None
    return handle_list_agents_admin(tenant_id, token, tag)


def create_agent_handler(event, context):
    import json
    logger.info("POST /admin/agents")
    token, api_url = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_create_agent(body, token, api_url)


def reissue_install_token_handler(event, context):
    import json
    logger.info("POST /admin/agents/{agent_id}/reissue-install-token")
    token, api_url = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_reissue_install_token(agent_id, body, token, api_url)


def delete_agent_handler(event, context):
    import json
    logger.info("DELETE /admin/agents/{agent_id}")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_delete_agent(agent_id, body, token)


def get_agent_tags_handler(event, context):
    logger.info("GET /admin/agents/{agent_id}/tags")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_get_agent_tags(agent_id, token)


def set_agent_tags_handler(event, context):
    import json
    logger.info("PUT /admin/agents/{agent_id}/tags")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_set_agent_tags(agent_id, body, token)


def add_agent_tags_handler(event, context):
    import json
    logger.info("POST /admin/agents/{agent_id}/tags")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_add_agent_tags(agent_id, body, token)


def remove_agent_tags_handler(event, context):
    import json
    logger.info("DELETE /admin/agents/{agent_id}/tags")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_remove_agent_tags(agent_id, body, token)
