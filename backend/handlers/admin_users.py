import logging
import os
import secrets
from typing import Optional

from shared.auth import USER_TOKEN_PREFIX, _hmac_token
from shared.response import _err, _iso, _ok
from shared.store import agents_repo, tenants_repo, users_repo

_WILDCARD = "*"

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]


def _verify_admin(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, ADMIN_TOKEN)


def handle_create_user(tenant_id: str, body: dict, raw_token: str, api_url: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not tenants_repo.get(tenant_id):
        return _err("tenant not found", 404)

    name = body.get("name", "").strip()
    raw_user_token = USER_TOKEN_PREFIX + secrets.token_urlsafe(32)
    user_id = "user_" + secrets.token_urlsafe(12)

    users_repo.create({
        "user_id": user_id,
        "tenant_id": tenant_id,
        "token_hash": _hmac_token(raw_user_token),
        "name": name or None,
        "created_at": _iso(),
    })

    logger.info("Created user=%s tenant=%s", user_id, tenant_id)

    return _ok({
        "user_id": user_id,
        "tenant_id": tenant_id,
        "name": name or None,
        "token": raw_user_token,
        "commands": {
            "cli_login": f"reach login --api-url \"{api_url}\" --token \"{raw_user_token}\"",
        },
    }, 201)


def handle_list_users(tenant_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not tenants_repo.get(tenant_id):
        return _err("tenant not found", 404)

    users = users_repo.list_by_tenant(tenant_id)
    return _ok({
        "users": [
            {
                "user_id": u["user_id"],
                "name": u.get("name"),
                "created_at": u.get("created_at"),
            }
            for u in users
        ],
    })


def _get_user_in_tenant(tenant_id: str, user_id: str) -> Optional[dict]:
    user = users_repo.get(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        return None
    return user


def handle_delete_user(tenant_id: str, user_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not _get_user_in_tenant(tenant_id, user_id):
        return _err("user not found", 404)

    users_repo.delete(user_id)
    logger.info("Deleted user=%s tenant=%s", user_id, tenant_id)

    return _ok({"user_id": user_id, "deleted": True})


def handle_rotate_user_token(tenant_id: str, user_id: str, raw_token: str, api_url: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not _get_user_in_tenant(tenant_id, user_id):
        return _err("user not found", 404)

    raw_user_token = USER_TOKEN_PREFIX + secrets.token_urlsafe(32)
    users_repo.update_token_hash(user_id, _hmac_token(raw_user_token))

    logger.info("Rotated token for user=%s tenant=%s", user_id, tenant_id)

    return _ok({
        "user_id": user_id,
        "tenant_id": tenant_id,
        "token": raw_user_token,
        "commands": {
            "cli_login": f"reach login --api-url \"{api_url}\" --token \"{raw_user_token}\"",
        },
    })


def handle_get_user_agents(tenant_id: str, user_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    user = _get_user_in_tenant(tenant_id, user_id)
    if not user:
        return _err("user not found", 404)

    _raw = user.get("allowed_agent_ids")
    allowed = [_WILDCARD] if _raw is None else _raw
    return _ok({"user_id": user_id, "allowed_agent_ids": allowed})


def handle_set_user_agents(tenant_id: str, user_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    user = _get_user_in_tenant(tenant_id, user_id)
    if not user:
        return _err("user not found", 404)

    agent_ids = body.get("agent_ids")
    if not isinstance(agent_ids, list):
        return _err("agent_ids must be a list; use [\"*\"] to restore full access")

    if _WILDCARD in agent_ids:
        users_repo.set_allowed_agents(user_id, [_WILDCARD])
        logger.info("Unrestricted user=%s tenant=%s", user_id, tenant_id)
        return _ok({"user_id": user_id, "allowed_agent_ids": [_WILDCARD]})

    if agent_ids:
        tenant_agent_ids = {a["agent_id"] for a in agents_repo.list_by_tenant(tenant_id)}
        unknown = [a for a in agent_ids if a not in tenant_agent_ids]
        if unknown:
            return _err(f"unknown agent_ids: {unknown}", 400)

    users_repo.set_allowed_agents(user_id, agent_ids)
    logger.info("Set allowed agents for user=%s tenant=%s agents=%s", user_id, tenant_id, agent_ids)
    return _ok({"user_id": user_id, "allowed_agent_ids": agent_ids})


def handle_grant_agent_access(tenant_id: str, user_id: str, agent_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    user = _get_user_in_tenant(tenant_id, user_id)
    if not user:
        return _err("user not found", 404)

    _raw = user.get("allowed_agent_ids")
    current = [_WILDCARD] if _raw is None else _raw
    if _WILDCARD in current:
        return _err(
            "user has unrestricted access (*); use PUT /agents to set an explicit list first",
            409,
        )

    agent = agents_repo.get(agent_id)
    if not agent or agent.get("tenant_id") != tenant_id:
        return _err("agent not found", 404)

    if agent_id not in current:
        users_repo.set_allowed_agents(user_id, current + [agent_id])
        logger.info("Granted agent=%s to user=%s tenant=%s", agent_id, user_id, tenant_id)

    return _ok({"user_id": user_id, "agent_id": agent_id, "granted": True})


def handle_revoke_agent_access(tenant_id: str, user_id: str, agent_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    user = _get_user_in_tenant(tenant_id, user_id)
    if not user:
        return _err("user not found", 404)

    _raw = user.get("allowed_agent_ids")
    current = [_WILDCARD] if _raw is None else _raw
    if _WILDCARD in current:
        return _err(
            "user has unrestricted access (*); use PUT /agents to set an explicit list first",
            409,
        )

    if agent_id not in current:
        return _err("agent not in user's allowed list", 404)

    users_repo.set_allowed_agents(user_id, [a for a in current if a != agent_id])
    logger.info("Revoked agent=%s from user=%s tenant=%s", agent_id, user_id, tenant_id)
    return _ok({"user_id": user_id, "agent_id": agent_id, "revoked": True})


def _token_and_api_url(event: dict) -> tuple:
    headers = event.get("headers") or {}
    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    host = headers.get("host", "")
    api_url = f"https://{host}" if host else os.environ.get("API_URL", "")
    return token, api_url


def create_user_handler(event, context):
    import json
    logger.info("POST /admin/tenants/{tenant_id}/users")
    token, api_url = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    tenant_id = (event.get("pathParameters") or {}).get("tenant_id", "")
    return handle_create_user(tenant_id, body, token, api_url)


def list_users_handler(event, context):
    logger.info("GET /admin/tenants/{tenant_id}/users")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    tenant_id = (event.get("pathParameters") or {}).get("tenant_id", "")
    return handle_list_users(tenant_id, token)


def delete_user_handler(event, context):
    logger.info("DELETE /admin/tenants/{tenant_id}/users/{user_id}")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    path_params = event.get("pathParameters") or {}
    tenant_id = path_params.get("tenant_id", "")
    user_id = path_params.get("user_id", "")
    return handle_delete_user(tenant_id, user_id, token)


def rotate_user_token_handler(event, context):
    logger.info("POST /admin/tenants/{tenant_id}/users/{user_id}/rotate-token")
    token, api_url = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    path_params = event.get("pathParameters") or {}
    tenant_id = path_params.get("tenant_id", "")
    user_id = path_params.get("user_id", "")
    return handle_rotate_user_token(tenant_id, user_id, token, api_url)


def get_user_agents_handler(event, context):
    logger.info("GET /admin/tenants/{tenant_id}/users/{user_id}/agents")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    path_params = event.get("pathParameters") or {}
    return handle_get_user_agents(path_params.get("tenant_id", ""), path_params.get("user_id", ""), token)


def set_user_agents_handler(event, context):
    import json
    logger.info("PUT /admin/tenants/{tenant_id}/users/{user_id}/agents")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    path_params = event.get("pathParameters") or {}
    return handle_set_user_agents(path_params.get("tenant_id", ""), path_params.get("user_id", ""), body, token)


def grant_agent_access_handler(event, context):
    logger.info("POST /admin/tenants/{tenant_id}/users/{user_id}/agents/{agent_id}")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    path_params = event.get("pathParameters") or {}
    return handle_grant_agent_access(
        path_params.get("tenant_id", ""), path_params.get("user_id", ""), path_params.get("agent_id", ""), token
    )


def revoke_agent_access_handler(event, context):
    logger.info("DELETE /admin/tenants/{tenant_id}/users/{user_id}/agents/{agent_id}")
    token, _ = _token_and_api_url(event)
    if not token:
        return _err("missing Authorization header", 401)
    path_params = event.get("pathParameters") or {}
    return handle_revoke_agent_access(
        path_params.get("tenant_id", ""), path_params.get("user_id", ""), path_params.get("agent_id", ""), token
    )
