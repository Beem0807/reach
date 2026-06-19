import base64
import logging
from typing import Optional

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _decode_cursor(s: str) -> Optional[str]:
    try:
        return base64.urlsafe_b64decode(s.encode()).decode()
    except Exception:
        return None


def _encode_cursor(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def handle_list_jobs(raw_token: str, agent_id: Optional[str], limit: int, cursor: Optional[str] = None) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    if agent_id:
        agent = agents_repo.get(agent_id)
        if not agent or not can_access_agent(tenant, agent):
            return _err("agent not found", 404)

    decoded_cursor = _decode_cursor(cursor) if cursor else None
    rows = jobs_repo.list_by_tenant(tenant["tenant_id"], agent_id, limit, created_by=tenant["user_id"], cursor=decoded_cursor)

    if not agent_id:
        _cache: dict = {}
        def _accessible(aid: str) -> bool:
            if aid not in _cache:
                a = agents_repo.get(aid)
                _cache[aid] = a is not None and can_access_agent(tenant, a)
            return _cache[aid]
        rows = [j for j in rows if _accessible(j["agent_id"])]

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


def list_jobs_handler(event, context):
    logger.info("GET /jobs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    agent_filter = qs.get("agent_id")
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return handle_list_jobs(token, agent_filter, limit, cursor)
