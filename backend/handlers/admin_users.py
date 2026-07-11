"""Platform admin: list users within a tenant."""
import logging

from shared.response import _err, _ok
from shared.store import tenants_repo, users_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

from shared.admin_auth import verify_session_token as _verify_admin


def handle_list_users(tenant_id: str, raw_token: str, q=None, limit=None, offset=0) -> dict:
    """List a tenant's users (platform-admin view). Optional `q` filters by
    username / name (substring). Pagination is **opt-in** (pass `limit` for a page
    plus `total`)."""
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)

    users = users_repo.list_by_tenant(tenant_id)
    ql = (q or "").strip().lower() or None
    if ql:
        users = [u for u in users
                 if ql in (u.get("username") or "").lower() or ql in (u.get("name") or "").lower()]
    total = len(users)
    if limit is not None:
        offset = max(0, offset)
        limit = max(1, min(limit, 100))
        users = users[offset:offset + limit]
    result = {"users": [_safe(u) for u in users]}
    if limit is not None:
        result.update(total=total, limit=limit, offset=offset)
    return _ok(result)


def list_users_handler(event, context):
    logger.info("GET /admin/tenants/{tenant_id}/users")
    auth = (event.get("headers") or {}).get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return _err("missing Authorization header", 401)
    tenant_id = (event.get("pathParameters") or {}).get("tenant_id", "")
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
    return handle_list_users(tenant_id, token, q=qs.get("q"), limit=limit, offset=offset)


def _safe(u: dict) -> dict:
    return {
        "user_id":             u["user_id"],
        "tenant_id":           u.get("tenant_id"),
        "name":                u.get("name"),
        "username":            u.get("username"),
        "role":                u.get("role"),
        "status":              u.get("status"),
        "must_reset_password": bool(u.get("must_reset_password")),
        "last_login_at":       u.get("last_login_at"),
        "created_at":          u.get("created_at"),
    }
