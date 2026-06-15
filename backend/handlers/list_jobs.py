import logging
from typing import Optional

from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_list_jobs(raw_token: str, agent_id: Optional[str], limit: int) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    rows = jobs_repo.list_by_tenant(tenant["tenant_id"], agent_id, limit)

    jobs = [
        {
            "job_id": j["job_id"],
            "agent_id": j["agent_id"],
            "command": j["command"],
            "status": j["status"],
            "exit_code": j.get("exit_code"),
            "duration_ms": j.get("duration_ms"),
            "created_at": j.get("created_at"),
            "completed_at": j.get("completed_at"),
        }
        for j in rows
    ]

    return _ok({"jobs": jobs})


def list_jobs_handler(event, context):
    logger.info("GET /jobs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    agent_filter = qs.get("agent_id")
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return handle_list_jobs(token, agent_filter, limit)
