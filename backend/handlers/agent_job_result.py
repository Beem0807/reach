import json
import logging

from shared.auth import _bearer, _verify_agent_token
from shared.response import _err, _iso, _ok
from shared.store import jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_job_result(job_id: str, body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()
    status = body.get("status", "").strip()
    exit_code = body.get("exit_code")
    stdout = body.get("stdout", "")
    stderr = body.get("stderr", "")
    duration_ms = body.get("duration_ms", 0)

    if status not in ("SUCCEEDED", "FAILED", "REJECTED"):
        return _err("status must be SUCCEEDED, FAILED, or REJECTED")
    if not agent_id or not machine_fp:
        return _err("agent_id and machine_fingerprint required")

    agent = _verify_agent_token(raw_token, agent_id)
    if not agent:
        return _err("unauthorized", 401)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    job = jobs_repo.get(job_id)
    if not job:
        return _err("job not found", 404)
    if job.get("agent_id") != agent_id:
        return _err("job does not belong to this agent", 403)
    if job.get("status") not in ("RUNNING", "PENDING"):
        return _err(f"job already in terminal state: {job.get('status')}", 409)

    max_bytes = 50_000
    if len(stdout.encode()) > max_bytes:
        stdout = stdout.encode()[:max_bytes].decode(errors="replace") + "\n[TRUNCATED]"
    if len(stderr.encode()) > max_bytes:
        stderr = stderr.encode()[:max_bytes].decode(errors="replace") + "\n[TRUNCATED]"

    jobs_repo.set_result(job_id, {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "completed_at": _iso(),
    })

    return _ok({"ok": True})


def agent_job_result_handler(event, context):
    job_id = (event.get("pathParameters") or {}).get("job_id", "")
    logger.info("POST /agent/jobs/%s/result", job_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_job_result(job_id, body, token)
