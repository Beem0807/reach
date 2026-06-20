"""Platform admin: list users within a tenant."""
import logging

from shared.response import _err, _ok
from shared.store import tenants_repo, users_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

from shared.admin_auth import verify_session_token as _verify_admin


def handle_list_users(tenant_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)

    users = users_repo.list_by_tenant(tenant_id)
    return _ok({"users": [_safe(u) for u in users]})


def list_users_handler(event, context):
    logger.info("GET /admin/tenants/{tenant_id}/users")
    auth = (event.get("headers") or {}).get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return _err("missing Authorization header", 401)
    tenant_id = (event.get("pathParameters") or {}).get("tenant_id", "")
    return handle_list_users(tenant_id, token)


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
