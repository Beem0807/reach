import logging
import os
import secrets

from shared.response import _err, _iso, _ok
from shared.store import tenants_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]


def _verify_admin(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, ADMIN_TOKEN)


def handle_create_tenant(body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    name = body.get("name", "").strip()
    tenant_id = "tenant_" + secrets.token_hex(8)

    tenants_repo.create({
        "tenant_id": tenant_id,
        "name": name or None,
        "created_at": _iso(),
    })

    logger.info("Created tenant=%s", tenant_id)

    return _ok({"tenant_id": tenant_id, "name": name or None}, 201)


def handle_list_tenants(raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    return _ok({"tenants": tenants_repo.list_all()})


def _token_from_event(event: dict) -> str:
    headers = event.get("headers") or {}
    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def create_tenant_handler(event, context):
    import json
    logger.info("POST /admin/tenants")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_create_tenant(body, token)


def list_tenants_handler(event, context):
    logger.info("GET /admin/tenants")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_list_tenants(token)
