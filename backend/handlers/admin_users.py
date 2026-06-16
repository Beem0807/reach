import logging
import os
import secrets
from typing import Optional

from shared.auth import USER_TOKEN_PREFIX, _hmac_token
from shared.response import _err, _iso, _ok
from shared.store import tenants_repo, users_repo

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
