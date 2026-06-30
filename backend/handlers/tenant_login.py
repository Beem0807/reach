"""Tenant admin login, forced password reset, and /tenant/me."""
import logging

from shared.password import hash_password, verify_password
from shared.response import _err, _iso, _ok
from shared.store import tenants_repo, users_repo
from shared.auth import _verify_tenant_payload, _verify_tenant_token
from shared.tenant_auth import create_tenant_token
import shared.audit as audit

logger = logging.getLogger()


def _audit_login_failed(reason: str, *, tenant_id=None, username="", user_id=None, ip="") -> None:
    """Record a failed tenant login. The actor is an unauthenticated party, so
    identity is captured as the attempted username in metadata rather than a real
    user_id. Scoped to the tenant when one was resolved, so tenant admins see
    failed attempts against their own accounts."""
    audit.write(
        "user.login_failed",
        tenant_id=tenant_id,
        actor_id="unknown",
        actor_name=username or "unknown",
        actor_role="TENANT_USER",
        resource_type="user",
        resource_id=user_id,
        metadata={"reason": reason, "attempted_username": username},
        ip_address=ip or None,
    )


def handle_tenant_login(body: dict, ip: str = "") -> dict:
    tenant_name = (body.get("tenant_name") or body.get("tenant_id") or "").strip()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()

    if not tenant_name or not username or not password:
        return _err("tenant_name, username and password are required")

    # Resolve tenant by name, falling back to treating the value as a tenant_id
    tenant = tenants_repo.get_by_name(tenant_name) or tenants_repo.get(tenant_name)
    if not tenant:
        _audit_login_failed("tenant_not_found", username=username, ip=ip)
        return _err("invalid credentials", 401)
    if tenant.get("status") == "DISABLED":
        _audit_login_failed("tenant_disabled", tenant_id=tenant["tenant_id"], username=username, ip=ip)
        return _err("tenant is disabled", 403)

    user = users_repo.get_by_username(tenant["tenant_id"], username)
    if not user or not user.get("password_hash"):
        _audit_login_failed("user_not_found", tenant_id=tenant["tenant_id"], username=username, ip=ip)
        return _err("invalid credentials", 401)
    if user.get("status") == "REVOKED" or user.get("disabled_at"):
        _audit_login_failed("account_disabled", tenant_id=tenant["tenant_id"], username=username, user_id=user["user_id"], ip=ip)
        return _err("account is disabled", 403)
    if not verify_password(password, user["password_hash"]):
        _audit_login_failed("bad_password", tenant_id=tenant["tenant_id"], username=username, user_id=user["user_id"], ip=ip)
        return _err("invalid credentials", 401)

    now = _iso()
    users_repo.set_last_login(user["user_id"], now)

    token = create_tenant_token(
        user_id=user["user_id"],
        tenant_id=tenant["tenant_id"],
        role=user.get("role", "TENANT_USER"),
        username=user["username"],
    )

    audit.write(
        "user.login",
        tenant_id=tenant["tenant_id"],
        actor_id=user["user_id"],
        actor_name=user.get("name") or username,
        actor_role=user.get("role", "TENANT_USER"),
        resource_type="user",
        resource_id=user["user_id"],
        ip_address=ip,
    )

    return _ok({
        "token": token,
        "must_reset_password": bool(user.get("must_reset_password")),
        "user": {
            "user_id": user["user_id"],
            "username": user["username"],
            "name": user.get("name"),
            "role": user.get("role"),
            "tenant_id": tenant["tenant_id"],
            "tenant_name": tenant["name"],
        },
    })


def handle_change_password(body: dict, token_payload: dict, ip: str = "") -> dict:
    current_pw = (body.get("current_password") or "").strip()
    new_pw = (body.get("new_password") or "").strip()

    if not current_pw or not new_pw:
        return _err("current_password and new_password are required")
    if len(new_pw) < 8:
        return _err("new_password must be at least 8 characters")

    user_id = token_payload["sub"]
    user = users_repo.get(user_id)
    if not user or not user.get("password_hash"):
        return _err("user not found", 404)

    if not verify_password(current_pw, user["password_hash"]):
        return _err("current password is incorrect", 401)

    users_repo.update_password(user_id, hash_password(new_pw), must_reset=False)

    audit.write(
        "user.password_changed",
        tenant_id=token_payload.get("tenant_id"),
        actor_id=user_id,
        actor_name=user.get("name"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        ip_address=ip,
    )

    return _ok({"changed": True})


def handle_tenant_me(token_payload: dict) -> dict:
    # Accepts a JWT payload ("sub") or a resolved user dict ("user_id"), so it works
    # for both console sessions and API tokens.
    user = users_repo.get(token_payload.get("user_id") or token_payload.get("sub"))
    if not user:
        return _err("user not found", 404)
    tenant = tenants_repo.get(token_payload["tenant_id"])
    return _ok({
        "user_id": user["user_id"],
        "username": user.get("username"),
        "name": user.get("name"),
        "role": user.get("role"),
        "tenant_id": user["tenant_id"],
        "tenant_name": tenant["name"] if tenant else None,
        "must_reset_password": bool(user.get("must_reset_password")),
    })


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


def tenant_login_handler(event, context):
    import json
    logger.info("POST /tenant/login")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_tenant_login(body, ip=_ip(event))


def change_password_handler(event, context):
    import json
    logger.info("POST /tenant/me/password")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    payload = _verify_tenant_payload(token)
    if not payload:
        return _err("unauthorized", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_change_password(body, payload, ip=_ip(event))


def tenant_me_handler(event, context):
    logger.info("GET /tenant/me")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    # API-key-aware so both console sessions and CLI/MCP API tokens can introspect.
    user = _verify_tenant_token(token)
    if not user:
        return _err("unauthorized", 401)
    return handle_tenant_me(user)
