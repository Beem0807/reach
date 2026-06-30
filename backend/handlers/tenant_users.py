"""Tenant admin: manage users within the tenant."""
import re
import secrets
import logging

from shared.access import accessible_agent_ids, is_agent_restricted
from shared.password import hash_password, generate_temp_password
from shared.response import _err, _iso, _ok
from shared.store import agents_repo, api_tokens_repo, users_repo
from shared.auth import _verify_tenant_payload
import shared.audit as audit

logger = logging.getLogger()

VALID_ROLES = ("admin", "operator", "developer")
_USERNAME_RE = re.compile(r'^[a-z0-9]+$')
_ADMIN_SCOPE_ERR = "admins have tenant-wide access and cannot be restricted to specific agents"


def _require_admin(tp: dict) -> bool:
    return tp.get("role") == "admin"


def _grant_exceeds_actor_scope(token_payload: dict, requested, tenant_id: str) -> bool:
    """True if `requested` (allowed_agent_ids being granted) exceeds the acting
    admin's own agent access. Admins can only delegate access they hold:

    - unrestricted actor (allowed_agent_ids is None) may grant anything, incl. null
    - restricted actor may not grant tenant-wide (null), nor any agent outside their set

    The actor's scope is read fresh from their user record, not the token.
    """
    actor = users_repo.get(token_payload.get("sub") or token_payload.get("user_id", "")) or {}
    if not is_agent_restricted(actor):
        return False
    if requested is None:
        return True  # granting "all agents" while restricted is an escalation
    actor_agents = set(accessible_agent_ids(actor, agents_repo.list_by_tenant(tenant_id)))
    return any(a not in actor_agents for a in requested)


def handle_list_tenant_users(token_payload: dict) -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    users = users_repo.list_by_tenant(tenant_id)
    return _ok({"users": [_safe(u) for u in users]})


def handle_create_tenant_user(body: dict, token_payload: dict, ip: str = "") -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)

    tenant_id = token_payload["tenant_id"]
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
    if role not in VALID_ROLES:
        return _err(f"role must be one of {VALID_ROLES}")

    allowed_agent_ids_raw = body.get("allowed_agent_ids")  # None = all agents (*)
    if allowed_agent_ids_raw is not None:
        if not isinstance(allowed_agent_ids_raw, list) or not all(isinstance(i, str) for i in allowed_agent_ids_raw):
            return _err("allowed_agent_ids must be null or a list of agent ID strings")
    allowed_agent_ids = allowed_agent_ids_raw

    # Admins are the tenant trust root: always tenant-wide, never agent-scoped. This
    # guarantees at least one role can reach every agent (no agent can be orphaned).
    if role == "admin" and allowed_agent_ids is not None:
        return _err(_ADMIN_SCOPE_ERR)

    # An admin can only grant agent access they hold themselves.
    if _grant_exceeds_actor_scope(token_payload, allowed_agent_ids, tenant_id):
        return _err("you can only grant access to agents you have access to", 403)

    # Check uniqueness
    existing = users_repo.get_by_username(tenant_id, username)
    if existing:
        return _err("username already exists in this tenant", 409)

    temp_pw = generate_temp_password()
    user_id = "user_" + secrets.token_urlsafe(12)

    users_repo.create({
        "user_id":            user_id,
        "tenant_id":          tenant_id,
        "name":               name or username,
        "username":           username,
        "password_hash":      hash_password(temp_pw),
        "role":               role,
        "must_reset_password": True,
        "status":             "ACTIVE",
        "allowed_agent_ids":  allowed_agent_ids,
        "allowed_fleet_ids":  None,
        "created_at":         _iso(),
    })

    audit.write(
        "user.created",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        metadata={"username": username, "role": role, "allowed_agent_ids": allowed_agent_ids},
        ip_address=ip,
    )

    return _ok({
        "user_id":      user_id,
        "username":     username,
        "name":         name or username,
        "role":         role,
        "temp_password": temp_pw,
        "must_reset_password": True,
    }, 201)


def handle_disable_tenant_user(user_id: str, token_payload: dict, ip: str = "") -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    if user["user_id"] == token_payload["sub"]:
        return _err("cannot disable yourself", 409)

    now = _iso()
    users_repo.disable(user_id, now)

    audit.write(
        "user.disabled",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        ip_address=ip,
    )
    return _ok({"user_id": user_id, "status": "REVOKED"})


def handle_enable_tenant_user(user_id: str, token_payload: dict, ip: str = "") -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    if user.get("status") != "REVOKED":
        return _err("user is not disabled", 409)

    users_repo.enable(user_id)

    audit.write(
        "user.enabled",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        ip_address=ip,
    )
    return _ok({"user_id": user_id, "status": "ACTIVE"})


def handle_delete_tenant_user(user_id: str, token_payload: dict, ip: str = "") -> dict:
    """Hard-delete a user. Two-step like API tokens: the user must be disabled
    (REVOKED) first, so an active account can't be removed by accident. Also purges
    the user's API tokens so no orphaned credentials remain."""
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)
    if user["user_id"] == token_payload["sub"]:
        return _err("cannot delete yourself", 409)
    if user.get("status") != "REVOKED":
        return _err("disable the user before deleting", 409)

    # Purge the user's API tokens, then the user record itself.
    tokens = api_tokens_repo.list_by_user(user_id) or []
    for tok in tokens:
        api_tokens_repo.delete(tok["token_id"])
    users_repo.delete(user_id)

    audit.write(
        "user.deleted",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        metadata={"username": user.get("username"), "role": user.get("role"),
                  "tokens_deleted": len(tokens)},
        ip_address=ip,
    )
    return _ok({"user_id": user_id, "deleted": True})


def handle_set_user_role(user_id: str, body: dict, token_payload: dict, ip: str = "") -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    role = (body.get("role") or "").strip()
    if role not in VALID_ROLES:
        return _err(f"role must be one of {VALID_ROLES}")

    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)

    users_repo.set_role(user_id, role)
    # Promotion to admin implies tenant-wide access; drop any prior agent scope so
    # the admin isn't left artificially restricted.
    if role == "admin" and user.get("allowed_agent_ids") is not None:
        users_repo.set_allowed_agents(user_id, None)
    audit.write(
        "user.role_changed",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        metadata={"role": role},
        ip_address=ip,
    )
    return _ok({"user_id": user_id, "role": role})


def handle_reset_user_password(user_id: str, token_payload: dict, ip: str = "") -> dict:
    """Admin resets another user's password to a new temp password."""
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user["tenant_id"] != tenant_id:
        return _err("user not found", 404)

    temp_pw = generate_temp_password()
    users_repo.update_password(user_id, hash_password(temp_pw), must_reset=True)

    audit.write(
        "user.password_reset",
        tenant_id=tenant_id,
        actor_id=token_payload["sub"],
        actor_name=token_payload.get("username"),
        actor_role=token_payload.get("role"),
        resource_type="user",
        resource_id=user_id,
        ip_address=ip,
    )
    return _ok({"user_id": user_id, "temp_password": temp_pw, "must_reset_password": True})


def _safe(u: dict) -> dict:
    return {
        "user_id":             u["user_id"],
        "username":            u.get("username"),
        "name":                u.get("name"),
        "role":                u.get("role"),
        "status":              u.get("status"),
        "must_reset_password": bool(u.get("must_reset_password")),
        "last_login_at":       u.get("last_login_at"),
        "disabled_at":         u.get("disabled_at"),
        "created_at":          u.get("created_at"),
        "allowed_agent_ids":   u.get("allowed_agent_ids"),
    }


def handle_get_user_agents(user_id: str, token_payload: dict) -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        return _err("user not found", 404)
    return _ok({"user_id": user_id, "allowed_agent_ids": user.get("allowed_agent_ids")})


def handle_set_user_agents(user_id: str, body: dict, token_payload: dict) -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        return _err("user not found", 404)
    prev = user.get("allowed_agent_ids")  # None = all agents
    agent_ids = body.get("allowed_agent_ids")  # None = allow all agents
    if agent_ids is not None:
        if not isinstance(agent_ids, list) or not all(isinstance(i, str) for i in agent_ids):
            return _err("allowed_agent_ids must be null or a list of agent ID strings")

    # Admins are always tenant-wide - you can't scope one to specific agents.
    if user.get("role") == "admin" and agent_ids is not None:
        return _err(_ADMIN_SCOPE_ERR)

    # An admin can only grant agent access they hold themselves (this also stops a
    # restricted admin from widening their own scope).
    if _grant_exceeds_actor_scope(token_payload, agent_ids, tenant_id):
        return _err("you can only grant access to agents you have access to", 403)

    users_repo.set_allowed_agents(user_id, agent_ids)
    added = sorted(set(agent_ids or []) - set(prev or [])) if prev is not None and agent_ids is not None else None
    removed = sorted(set(prev or []) - set(agent_ids or [])) if prev is not None and agent_ids is not None else None
    audit.write(
        "user.agents_changed",
        tenant_id=tenant_id,
        actor_id=token_payload.get("sub") or token_payload.get("user_id", ""),
        actor_name=token_payload.get("username", ""),
        actor_role=token_payload.get("role", ""),
        resource_type="user",
        resource_id=user_id,
        metadata={
            "target_username": user.get("username"),
            "previous": prev,
            "current": agent_ids,
            "added": added,
            "removed": removed,
        },
    )
    return _ok({"user_id": user_id, "allowed_agent_ids": agent_ids})


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


def list_users_handler(event, context):
    logger.info("GET /tenant/users")
    payload, err = _auth(event)
    if err:
        return err
    return handle_list_tenant_users(payload)


def create_user_handler(event, context):
    import json
    logger.info("POST /tenant/users")
    payload, err = _auth(event)
    if err:
        return err
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_create_tenant_user(body, payload, ip=_ip(event))


def disable_user_handler(event, context):
    logger.info("POST /tenant/users/{user_id}/disable")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    return handle_disable_tenant_user(user_id, payload, ip=_ip(event))


def enable_user_handler(event, context):
    logger.info("POST /tenant/users/{user_id}/enable")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    return handle_enable_tenant_user(user_id, payload, ip=_ip(event))


def delete_user_handler(event, context):
    logger.info("DELETE /tenant/users/{user_id}")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    return handle_delete_tenant_user(user_id, payload, ip=_ip(event))


def set_role_handler(event, context):
    import json
    logger.info("PATCH /tenant/users/{user_id}/role")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_set_user_role(user_id, body, payload, ip=_ip(event))


def reset_password_handler(event, context):
    logger.info("POST /tenant/users/{user_id}/reset-password")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    return handle_reset_user_password(user_id, payload, ip=_ip(event))


def get_user_agents_handler(event, context):
    logger.info("GET /tenant/users/{user_id}/agents")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    return handle_get_user_agents(user_id, payload)


def set_user_agents_handler(event, context):
    import json
    logger.info("PUT /tenant/users/{user_id}/agents")
    payload, err = _auth(event)
    if err:
        return err
    user_id = (event.get("pathParameters") or {}).get("user_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_set_user_agents(user_id, body, payload)
