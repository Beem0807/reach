import base64
import logging
import os
from typing import Optional

from shared.response import _err, _ok
from shared.store import jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]


def _verify_admin(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, ADMIN_TOKEN)


def _decode_cursor(s: str) -> Optional[str]:
    try:
        return base64.urlsafe_b64decode(s.encode()).decode()
    except Exception:
        return None


def _encode_cursor(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def handle_list_jobs_admin(raw_token: str, agent_id: str, tenant_id: str, created_by: str, limit: int, cursor: Optional[str] = None) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)

    if not any([agent_id, tenant_id, created_by]):
        return _err("at least one filter required: agent_id, tenant_id, or created_by", 400)

    decoded_cursor = _decode_cursor(cursor) if cursor else None
    rows = jobs_repo.list_admin(agent_id or None, tenant_id or None, created_by or None, limit, cursor=decoded_cursor)

    jobs = [
        {
            "job_id": j["job_id"],
            "agent_id": j["agent_id"],
            "created_by": j.get("created_by"),
            "command": j["command"],
            "status": j["status"],
            "exit_code": j.get("exit_code"),
            "duration_ms": j.get("duration_ms"),
            "created_at": j.get("created_at"),
            "completed_at": j.get("completed_at"),
        }
        for j in rows
    ]

    result: dict = {"jobs": jobs}
    if len(rows) == limit and rows:
        result["next_cursor"] = _encode_cursor(rows[-1]["created_at"])
    return _ok(result)


def list_jobs_admin_handler(event, context):
    logger.info("GET /admin/jobs")
    headers = event.get("headers") or {}
    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    agent_id = qs.get("agent_id", "")
    tenant_id = qs.get("tenant_id", "")
    created_by = qs.get("created_by", "")
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return handle_list_jobs_admin(token, agent_id, tenant_id, created_by, limit, cursor)
