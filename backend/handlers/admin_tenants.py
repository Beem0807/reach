import logging
import os
import re
import secrets

import shared.audit as audit
from shared.password import generate_temp_password, hash_password
from shared.exceptions import NameTakenError
from shared.response import _err, _iso, _ok
from shared.store import tenants_repo, users_repo

_USERNAME_RE = re.compile(r'^[a-z0-9]+$')
_VALID_ROLES = ("admin", "operator", "developer")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

from shared.admin_auth import verify_session_token as _verify_admin


def handle_create_tenant(body: dict, raw_token: str, ip: str = "") -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    name = (body.get("name") or "").strip()
    if not name:
        return _err("name is required")
    tenant_id = "tenant_" + secrets.token_hex(8)

    try:
        tenants_repo.create({
            "tenant_id": tenant_id,
            "name":      name,
            "status":    "ACTIVE",
            "created_at": _iso(),
        })
    except NameTakenError:
        return _err("a tenant with that name already exists", 409)

    logger.info("Created tenant=%s", tenant_id)
    audit.write("tenant.created", resource_type="tenant", resource_id=tenant_id,
                metadata={"name": name}, ip_address=ip)

    return _ok({"tenant_id": tenant_id, "name": name, "status": "ACTIVE"}, 201)


def handle_disable_tenant(tenant_id: str, raw_token: str, ip: str = "") -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)
    tenants_repo.set_status(tenant_id, "DISABLED")
    audit.write("tenant.disabled", resource_type="tenant", resource_id=tenant_id, ip_address=ip)
    return _ok({"tenant_id": tenant_id, "status": "DISABLED"})


def handle_enable_tenant(tenant_id: str, raw_token: str, ip: str = "") -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)
    tenants_repo.set_status(tenant_id, "ACTIVE")
    audit.write("tenant.enabled", resource_type="tenant", resource_id=tenant_id, ip_address=ip)
    return _ok({"tenant_id": tenant_id, "status": "ACTIVE"})


def handle_create_tenant_admin_user(tenant_id: str, body: dict, raw_token: str, ip: str = "") -> dict:
    """Platform admin creates the first (or any) tenant admin user with a temp password."""
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)
    if tenant.get("status") == "DISABLED":
        return _err("cannot add users to a disabled tenant", 403)

    username = (body.get("username") or "").strip().lower()
    name = (body.get("name") or "").strip()
    role = (body.get("role") or "developer").strip()
    if not username:
        return _err("username required")
    if len(username) < 2:
        return _err("username must be at least 2 characters")
    if len(username) > 32:
        return _err("username must be 32 characters or fewer")
    if not _USERNAME_RE.match(username):
        return _err("username must contain only lowercase letters and numbers")
    if role not in _VALID_ROLES:
        return _err(f"role must be one of {_VALID_ROLES}")

    temp_pw = generate_temp_password()
    user_id = "user_" + secrets.token_urlsafe(12)
    now = _iso()

    # Admins are tenant-wide (unrestricted); everyone else starts with no access by
    # default and is granted agents/fleets explicitly from the tenant console.
    empty = None if role == "admin" else []

    try:
        users_repo.create({
            "user_id":             user_id,
            "tenant_id":           tenant_id,
            "name":                name or username,
            "username":            username,
            "password_hash":       hash_password(temp_pw),
            "role":                role,
            "must_reset_password": True,
            "status":              "ACTIVE",
            "readwrite_agent_ids":   empty,
            "readwrite_fleet_ids":   empty,
            "readonly_agent_ids":  empty,
            "readonly_fleet_ids":  empty,
            "created_at":          now,
        })
    except NameTakenError:
        return _err("username already exists", 409)

    audit.write(
        "user.created",
        tenant_id=tenant_id,
        resource_type="user",
        resource_id=user_id,
        metadata={"username": username, "role": role, "created_by": "platform_admin"},
        ip_address=ip,
    )
    logger.info("Platform admin created user=%s tenant=%s role=%s", user_id, tenant_id, role)

    return _ok({
        "user_id":             user_id,
        "username":            username,
        "name":                name or username,
        "role":                role,
        "tenant_id":           tenant_id,
        "temp_password":       temp_pw,
        "must_reset_password": True,
    }, 201)


def handle_platform_reset_user_password(tenant_id: str, user_id: str, raw_token: str, ip: str = "") -> dict:
    """Platform admin generates a new temp password for a tenant user who lost access."""
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    temp_pw = generate_temp_password()
    users_repo.update_password(user_id, hash_password(temp_pw), must_reset=True)
    audit.write("user.password_reset", tenant_id=tenant_id, resource_type="user",
                resource_id=user_id, metadata={"reset_by": "platform_admin"}, ip_address=ip)
    return _ok({"user_id": user_id, "temp_password": temp_pw, "must_reset_password": True})


def handle_platform_disable_user(tenant_id: str, user_id: str, raw_token: str, ip: str = "") -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    users_repo.disable(user_id, _iso())
    audit.write("user.disabled", tenant_id=tenant_id, resource_type="user",
                resource_id=user_id, metadata={"disabled_by": "platform_admin"}, ip_address=ip)
    return _ok({"user_id": user_id, "status": "REVOKED"})


def handle_platform_set_user_role(tenant_id: str, user_id: str, body: dict, raw_token: str, ip: str = "") -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    role = (body.get("role") or "").strip()
    if role not in _VALID_ROLES:
        return _err(f"role must be one of {_VALID_ROLES}")
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    users_repo.set_role(user_id, role)
    audit.write("user.role_changed", tenant_id=tenant_id, resource_type="user",
                resource_id=user_id, metadata={"role": role, "changed_by": "platform_admin"}, ip_address=ip)
    return _ok({"user_id": user_id, "role": role})


def handle_platform_update_user_name(tenant_id: str, user_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    name = (body.get("name") or "").strip()
    if not name:
        return _err("name required")
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    users_repo.update_name(user_id, name)
    audit.write(
        "user.name_changed",
        tenant_id=tenant_id,
        resource_type="user",
        resource_id=user_id,
        metadata={"name": name},
    )
    return _ok({"user_id": user_id, "name": name})


def handle_list_tenants(raw_token: str, q=None, limit=None, offset=0) -> dict:
    """List every tenant. Optional `q` filters by tenant name / id (substring).
    Pagination is **opt-in**: pass `limit` for one page plus a `total`."""
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    tenants = tenants_repo.list_all()
    ql = (q or "").strip().lower() or None
    if ql:
        tenants = [t for t in tenants
                   if ql in (t.get("name") or "").lower() or ql in (t.get("tenant_id") or "").lower()]
    tenants.sort(key=lambda t: ((t.get("name") or "").lower(), t.get("tenant_id") or ""))
    total = len(tenants)
    if limit is not None:
        offset = max(0, offset)
        limit = max(1, min(limit, 100))
        tenants = tenants[offset:offset + limit]
    result = {"tenants": tenants}
    if limit is not None:
        result.update(total=total, limit=limit, offset=offset)
    return _ok(result)


def handle_delete_tenant(tenant_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)

    tenants_repo.delete_cascade(tenant_id)
    audit.write(
        "tenant.deleted",
        resource_type="tenant",
        resource_id=tenant_id,
        metadata={"name": tenant.get("name")},
    )
    logger.info("Deleted tenant=%s (cascade)", tenant_id)
    return _ok({}, 204)


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
    qs = event.get("queryStringParameters") or {}
    limit = None
    if qs.get("limit") is not None:
        try:
            limit = max(1, min(int(qs["limit"]), 100))
        except (ValueError, TypeError):
            limit = 20
    try:
        offset = max(0, int(qs.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0
    return handle_list_tenants(token, q=qs.get("q"), limit=limit, offset=offset)


def delete_tenant_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    logger.info("DELETE /admin/tenants/%s", tenant_id)
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_delete_tenant(tenant_id, token)


def disable_tenant_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_disable_tenant(tenant_id, token)


def enable_tenant_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_enable_tenant(tenant_id, token)


def create_admin_user_handler(event, context):
    import json
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_create_tenant_admin_user(tenant_id, body, token, ip)


def platform_reset_password_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    user_id = path.get("user_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_platform_reset_user_password(tenant_id, user_id, token, ip)


def platform_disable_user_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    user_id = path.get("user_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_platform_disable_user(tenant_id, user_id, token, ip)


def platform_set_role_handler(event, context):
    import json
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    user_id = path.get("user_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_platform_set_user_role(tenant_id, user_id, body, token, ip)


def platform_update_name_handler(event, context):
    import json
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    user_id = path.get("user_id", "")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_platform_update_user_name(tenant_id, user_id, body, token)
