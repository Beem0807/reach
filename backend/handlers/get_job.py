import logging

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _now, _ok
from shared.store import agents_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_get_job(job_id: str, raw_token: str) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    job = jobs_repo.get(job_id)
    if not job:
        return _err("job not found", 404)
    if job.get("tenant_id") != tenant.get("tenant_id"):
        return _err("not found", 404)

    agent = agents_repo.get(job["agent_id"])
    if not agent or not can_access_agent(tenant, agent):
        return _err("not found", 404)

    if job.get("status") == "PENDING" and _now() > int(job.get("expires_at") or 0):
        jobs_repo.mark_expired(job_id)
        job["status"] = "EXPIRED"

    return _ok({
        "job_id": job["job_id"],
        "agent_id": job["agent_id"],
        "created_by": job.get("created_by"),
        "command": job["command"],
        "status": job["status"],
        "exit_code": job.get("exit_code"),
        "stdout": job.get("stdout"),
        "stderr": job.get("stderr"),
        "stdout_truncated": bool(job.get("stdout_truncated")),
        "stderr_truncated": bool(job.get("stderr_truncated")),
        "duration_ms": job.get("duration_ms"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
    })


def get_job_handler(event, context):
    job_id = (event.get("pathParameters") or {}).get("job_id", "")
    logger.info("GET /jobs/%s", job_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_get_job(job_id, token)
