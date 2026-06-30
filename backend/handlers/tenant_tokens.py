"""Tenant admin: create and revoke named API tokens for CLI/MCP usage."""
import secrets
import logging

from shared.auth import _hmac_token
from shared.response import _err, _iso, _ok
from shared.store import api_tokens_repo
from shared.auth import _verify_tenant_payload
import shared.audit as audit

logger = logging.getLogger()

_TOKEN_PREFIX = "tok_"


def handle_create_api_token(body: dict, token_payload: dict, ip: str = "") -> dict:
    tenant_id = token_payload["tenant_id"]
    actor_id = token_payload["sub"]
    name = (body.get("name") or "CLI token").strip()

    raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_id = "tkid_" + secrets.token_hex(10)
    now = _iso()

    api_tokens_repo.create({
        "token_id":   token_id,
        "user_id":    actor_id,
        "tenant_id":  tenant_id,
        "token_hash": _hmac_token(raw),
        "name":       name,
        "status":     "ACTIVE",
        "created_at": now,
    })

    audit.write(
        "api_token.created",
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="api_token",
        resource_id=token_id,
        metadata={"name": name},
        ip_address=ip,
    )

    return _ok({
        "token_id": token_id,
        "name":     name,
        "token":    raw,
        "created_at": now,
    }, 201)


def handle_list_api_tokens(token_payload: dict) -> dict:
    tokens = api_tokens_repo.list_by_user(token_payload["sub"])
    return _ok({"tokens": [_safe(t) for t in tokens]})


def handle_rename_api_token(token_id: str, body: dict, token_payload: dict, ip: str = "") -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        return _err("name is required")

    tenant_id = token_payload["tenant_id"]
    tokens = api_tokens_repo.list_by_user(token_payload["sub"])
    match = next((t for t in tokens if t["token_id"] == token_id), None)
    if not match or match["tenant_id"] != tenant_id:
        return _err("token not found", 404)

    api_tokens_repo.rename(token_id, name)

    audit.write(
        "api_token.renamed",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="api_token",
        resource_id=token_id,
        metadata={"name": name},
        ip_address=ip,
    )
    return _ok({"token_id": token_id, "name": name})


def handle_revoke_api_token(token_id: str, token_payload: dict, ip: str = "") -> dict:
    """Two-step deletion. An ACTIVE token is first **revoked** (soft: kept as a
    REVOKED record for audit, can no longer authenticate). Deleting again - i.e.
    DELETE on an already-REVOKED token - **hard-deletes** the row. This makes
    revocation a required step before removal, and revocation is terminal (there
    is no un-revoke; issue a new token instead)."""
    tenant_id = token_payload["tenant_id"]
    tokens = api_tokens_repo.list_by_user(token_payload["sub"])
    match = next((t for t in tokens if t["token_id"] == token_id), None)
    if not match or match["tenant_id"] != tenant_id:
        return _err("token not found", 404)

    def _audit(action: str):
        audit.write(
            action,
            tenant_id=tenant_id,
            actor_id=token_payload["sub"],
            actor_name=token_payload.get("username"),
            actor_role=token_payload.get("role"),
            resource_type="api_token",
            resource_id=token_id,
            ip_address=ip,
        )

    if match.get("status") == "ACTIVE":
        api_tokens_repo.revoke(token_id, _iso())
        _audit("api_token.revoked")
        return _ok({"token_id": token_id, "status": "REVOKED"})

    # Already revoked -> permanently remove the record.
    api_tokens_repo.delete(token_id)
    _audit("api_token.deleted")
    return _ok({"token_id": token_id, "status": "DELETED"})


def handle_revoke_all_user_tokens(user_id: str, token_payload: dict, ip: str = "") -> dict:
    """Admin-only: revoke all active API tokens for a given user in the same tenant."""
    if token_payload.get("role") not in ("admin",):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    tokens = api_tokens_repo.list_by_user(user_id)
    now = _iso()
    revoked = 0
    for t in tokens:
        if t.get("tenant_id") != tenant_id:
            continue
        if t.get("status") == "ACTIVE":
            api_tokens_repo.revoke(t["token_id"], now)
            revoked += 1
    return _ok({"revoked": revoked})


def _safe(t: dict) -> dict:
    return {
        "token_id":    t["token_id"],
        "name":        t.get("name"),
        "status":      t.get("status"),
        "created_at":  t.get("created_at"),
        "last_used_at": t.get("last_used_at"),
        "revoked_at":  t.get("revoked_at"),
    }


# ---------------------------------------------------------------------------
# Lambda entry points
# ---------------------------------------------------------------------------
def _bearer(event: dict):
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _ip(event: dict) -> str:
    ctx = event.get("requestContext") or {}
    return (ctx.get("http") or ctx.get("identity") or {}).get("sourceIp", "")


def _auth(event: dict):
    token = _bearer(event)
    if not token:
        return None, _err("missing Authorization header", 401)
    payload = _verify_tenant_payload(token)
    if not payload:
        return None, _err("unauthorized", 401)
    return payload, None


def list_tokens_handler(event, context):
    logger.info("GET /tenant/api-tokens")
    payload, err = _auth(event)
    if err:
        return err
    return handle_list_api_tokens(payload)


def create_token_handler(event, context):
    import json
    logger.info("POST /tenant/api-tokens")
    payload, err = _auth(event)
    if err:
        return err
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_create_api_token(body, payload, ip=_ip(event))


def revoke_token_handler(event, context):
    logger.info("DELETE /tenant/api-tokens/{token_id}")
    payload, err = _auth(event)
    if err:
        return err
    token_id = (event.get("pathParameters") or {}).get("token_id", "")
    return handle_revoke_api_token(token_id, payload, ip=_ip(event))


def rename_token_handler(event, context):
    import json
    logger.info("PATCH /tenant/api-tokens/{token_id}")
    payload, err = _auth(event)
    if err:
        return err
    token_id = (event.get("pathParameters") or {}).get("token_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_rename_api_token(token_id, body, payload, ip=_ip(event))


def revoke_user_tokens_handler(event, context):
    logger.info("POST /tenant/users/{user_id}/revoke-tokens")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    return handle_revoke_all_user_tokens(user_id, payload, ip=_ip(event))
