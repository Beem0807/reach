"""Audit log endpoints for platform admin and tenant admin."""
import logging

from shared.admin_auth import verify_session_token as _verify_admin
from shared.response import _err, _ok
from shared.store import audit_repo
from shared.auth import _verify_tenant_payload

logger = logging.getLogger()


def handle_list_platform_audit_logs(raw_token: str, limit: int = 100, cursor: str = None,
                                     action: str = None, actor: str = None,
                                     resource: str = None, ip: str = None,
                                     since: str = None, until: str = None) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    logs = audit_repo.list_platform(limit=limit, cursor=cursor,
                                    action=action or None, actor=actor or None,
                                    resource=resource or None, ip=ip or None,
                                    since=since or None, until=until or None)
    result: dict = {"logs": logs}
    if len(logs) == limit and logs:
        result["next_cursor"] = logs[-1]["created_at"]
    return _ok(result)


def handle_list_tenant_audit_logs(raw_token: str, limit: int = 100, cursor: str = None,
                                   action: str = None, actor: str = None,
                                   resource: str = None, ip: str = None,
                                   since: str = None, until: str = None) -> dict:
    payload = _verify_tenant_payload(raw_token)
    if not payload or payload.get("role") != "admin":
        return _err("unauthorized", 401)
    tenant_id = payload["tenant_id"]
    logs = audit_repo.list_by_tenant(tenant_id, limit=limit, cursor=cursor,
                                     action=action or None, actor=actor or None,
                                     resource=resource or None, ip=ip or None,
                                     since=since or None, until=until or None)
    result: dict = {"logs": logs}
    if len(logs) == limit and logs:
        result["next_cursor"] = logs[-1]["created_at"]
    return _ok(result)


# ---------------------------------------------------------------------------
# Lambda entry points
# ---------------------------------------------------------------------------
def _bearer(event: dict):
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def platform_audit_logs_handler(event, context):
    logger.info("GET /admin/audit-logs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    limit = int(qs.get("limit", 100))
    cursor = qs.get("cursor")
    return handle_list_platform_audit_logs(token, limit=limit, cursor=cursor,
        action=qs.get("action"), actor=qs.get("actor"),
        resource=qs.get("resource"), ip=qs.get("ip"),
        since=qs.get("since"), until=qs.get("until"))


def tenant_audit_logs_handler(event, context):
    logger.info("GET /tenant/audit-logs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    limit = int(qs.get("limit", 100))
    cursor = qs.get("cursor")
    return handle_list_tenant_audit_logs(token, limit=limit, cursor=cursor,
        action=qs.get("action"), actor=qs.get("actor"),
        resource=qs.get("resource"), ip=qs.get("ip"),
        since=qs.get("since"), until=qs.get("until"))
