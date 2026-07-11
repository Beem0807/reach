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


def _fleet_member_grant_error(readwrite_agent_ids, readonly_agent_ids, tenant_id: str):
    """Fleet members have ephemeral agent_ids (they churn as an ASG scales), so they
    must be granted via their fleet, never by individual agent_id. Return an error
    dict if either per-agent list names a fleet member, else None. The "*" wildcard
    is a broad grant, not a specific id, so it's allowed."""
    ids = {i for lst in (readwrite_agent_ids, readonly_agent_ids) for i in (lst or []) if i != "*"}
    if not ids:
        return None
    members = {a["agent_id"] for a in agents_repo.list_by_tenant(tenant_id) if a.get("fleet_id")}
    bad = sorted(ids & members)
    if bad:
        return _err(f"{bad[0]} is a fleet member - grant access via its fleet, not by agent id")
    return None


def _grant_exceeds_actor_scope(token_payload: dict, requested, tenant_id: str) -> bool:
    """True if `requested` (readwrite_agent_ids being granted) exceeds the acting
    admin's own agent access. Admins can only delegate access they hold:

    - unrestricted actor (readwrite_agent_ids is None) may grant anything, incl. null
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


def _user_matches_query(u: dict, q: str) -> bool:
    return q in (u.get("username") or "").lower() or q in (u.get("name") or "").lower()


def handle_list_tenant_users(token_payload: dict, role=None, status=None, q=None,
                             limit=None, offset=0) -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    users = users_repo.list_by_tenant(tenant_id)

    # Optional filters: exact role, exact status, and a substring over username/name.
    if role:
        users = [u for u in users if u.get("role") == role]
    if status:
        users = [u for u in users if (u.get("status") or "").upper() == status.upper()]
    ql = (q or "").strip().lower()
    if ql:
        users = [u for u in users if _user_matches_query(u, ql)]

    # Opt-in pagination: with no `limit`, return the full set (back-compat for the CLI
    # and any caller that wants everything). With a `limit`, return a page + the total.
    if limit is None:
        return _ok({"users": [_safe(u) for u in users]})
    total = len(users)
    page = users[offset:offset + limit]
    return _ok({"users": [_safe(u) for u in page], "total": total, "limit": limit, "offset": offset})


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

    raw = {f: body.get(f) for f in _SCOPE_FIELDS}
    for field, v in raw.items():
        if v is not None and (not isinstance(v, list) or not all(isinstance(i, str) for i in v)):
            return _err(f"{field} must be null or a list of ID strings")
        if v and "*" in v:
            return _err("wildcard '*' is not allowed - list every id explicitly")

    if role == "admin":
        # Admins are the tenant trust root: always tenant-wide, never scoped. This
        # guarantees at least one role can reach every agent (none orphaned).
        if any(raw[f] for f in _SCOPE_FIELDS):
            return _err(_ADMIN_SCOPE_ERR)
        scope = {f: None for f in _SCOPE_FIELDS}
    else:
        # No access by default: a developer/operator starts with no access and is
        # granted explicitly (per agent/fleet, read-only or read-write). Omitted → [].
        scope = {f: (raw[f] if raw[f] is not None else []) for f in _SCOPE_FIELDS}
        if set(scope["readwrite_agent_ids"]) & set(scope["readonly_agent_ids"]):
            return _err("an agent cannot be both read-write and read-only; lists must be disjoint")
        if set(scope["readwrite_fleet_ids"]) & set(scope["readonly_fleet_ids"]):
            return _err("a fleet cannot be both read-write and read-only; lists must be disjoint")
        fleet_err = _fleet_member_grant_error(scope["readwrite_agent_ids"], scope["readonly_agent_ids"], tenant_id)
        if fleet_err:
            return fleet_err

    # An admin can only grant agent access they hold themselves (union of both lists).
    rw_a, ro_a = scope["readwrite_agent_ids"], scope["readonly_agent_ids"]
    granted = None if rw_a is None else (rw_a + (ro_a or []))
    if _grant_exceeds_actor_scope(token_payload, granted, tenant_id):
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
        **scope,
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
        metadata={"username": username, "role": role, **scope},
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
    # Promotion to admin implies tenant-wide access; drop any prior agent/fleet scope
    # (read-write and read-only) so the admin isn't left artificially restricted.
    if role == "admin" and is_agent_restricted(user):
        users_repo.set_agent_access(user_id, None, None, None, None)
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
        "readwrite_agent_ids":   u.get("readwrite_agent_ids"),
        "readonly_agent_ids":  u.get("readonly_agent_ids"),
        "readwrite_fleet_ids":   u.get("readwrite_fleet_ids"),
        "readonly_fleet_ids":  u.get("readonly_fleet_ids"),
    }


def handle_get_user_agents(user_id: str, token_payload: dict) -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        return _err("user not found", 404)
    return _ok({
        "user_id": user_id,
        "readwrite_agent_ids": user.get("readwrite_agent_ids"),
        "readonly_agent_ids": user.get("readonly_agent_ids"),
        "readwrite_fleet_ids": user.get("readwrite_fleet_ids"),
        "readonly_fleet_ids": user.get("readonly_fleet_ids"),
    })


_SCOPE_FIELDS = ("readwrite_agent_ids", "readonly_agent_ids", "readwrite_fleet_ids", "readonly_fleet_ids")


def handle_set_user_agents(user_id: str, body: dict, token_payload: dict) -> dict:
    if not _require_admin(token_payload):
        return _err("forbidden", 403)
    tenant_id = token_payload["tenant_id"]
    user = users_repo.get(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        return _err("user not found", 404)

    # Read-write and read-only lists partition access by capability, for agents and
    # fleets alike. A field omitted from the body is left unchanged (so you can set
    # agent grants without clobbering fleet grants); an explicit value replaces it.
    scope: dict = {}
    for field in _SCOPE_FIELDS:
        if field in body:
            v = body[field]
            if v is not None and (not isinstance(v, list) or not all(isinstance(i, str) for i in v)):
                return _err(f"{field} must be null or a list of ID strings")
            if v and "*" in v:
                return _err("wildcard '*' is not allowed - list every id explicitly")
            scope[field] = v
        else:
            scope[field] = user.get(field)

    # An id can't be both read-write and read-only (within agents, and within fleets).
    if scope["readwrite_agent_ids"] is not None and scope["readonly_agent_ids"] is not None \
            and set(scope["readwrite_agent_ids"]) & set(scope["readonly_agent_ids"]):
        return _err("an agent cannot be both read-write and read-only; lists must be disjoint")
    if scope["readwrite_fleet_ids"] is not None and scope["readonly_fleet_ids"] is not None \
            and set(scope["readwrite_fleet_ids"]) & set(scope["readonly_fleet_ids"]):
        return _err("a fleet cannot be both read-write and read-only; lists must be disjoint")

    # Fleet members can't be granted by individual agent id - use the fleet.
    fleet_err = _fleet_member_grant_error(scope["readwrite_agent_ids"], scope["readonly_agent_ids"], tenant_id)
    if fleet_err:
        return fleet_err

    # Admins are always tenant-wide - you can't scope one to specific agents/fleets.
    if user.get("role") == "admin" and any(scope[f] for f in _SCOPE_FIELDS):
        return _err(_ADMIN_SCOPE_ERR)

    # An admin can only grant agent access they hold themselves - the containment check
    # covers the union of the read-write and read-only agent lists.
    rw_a, ro_a = scope["readwrite_agent_ids"], scope["readonly_agent_ids"]
    granted = None if rw_a is None else (rw_a + (ro_a or []))
    if _grant_exceeds_actor_scope(token_payload, granted, tenant_id):
        return _err("you can only grant access to agents you have access to", 403)

    users_repo.set_agent_access(user_id, scope["readwrite_agent_ids"], scope["readonly_agent_ids"],
                                scope["readwrite_fleet_ids"], scope["readonly_fleet_ids"])
    # Diff the read-write agent list for a readable audit trail (previous/current/added/removed).
    prev, current = user.get("readwrite_agent_ids"), scope["readwrite_agent_ids"]
    both_lists = prev is not None and current is not None
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
            "current": current,
            "added": sorted(set(current) - set(prev)) if both_lists else None,
            "removed": sorted(set(prev) - set(current)) if both_lists else None,
            **scope,
        },
    )
    return _ok({"user_id": user_id, **scope})


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
    return handle_list_tenant_users(payload, role=qs.get("role"), status=qs.get("status"),
                                    q=qs.get("q"), limit=limit, offset=offset)


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
