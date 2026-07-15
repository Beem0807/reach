import json
import logging
import secrets

from shared.auth import _bearer, _verify_agent_token
from shared.redact import redact
from shared.response import _err, _iso, _ok
from shared.store import approvals_repo, jobs_repo, users_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_job_result(job_id: str, body: dict, raw_token: str) -> dict:
    machine_fp = body.get("machine_fingerprint", "").strip()
    status = body.get("status", "").strip()
    exit_code = body.get("exit_code")
    stdout = body.get("stdout", "")
    stderr = body.get("stderr", "")
    duration_ms = body.get("duration_ms", 0)
    blocked = body.get("blocked", False)
    is_write = body.get("is_write", blocked)  # agent corrects to True when blocked

    if status not in ("SUCCEEDED", "FAILED", "REJECTED"):
        return _err("status must be SUCCEEDED, FAILED, or REJECTED")
    if not machine_fp:
        return _err("machine_fingerprint required")

    # Credential-only: the agent token identifies the agent; no agent_id is sent.
    agent = _verify_agent_token(raw_token)
    if not agent:
        return _err("unauthorized", 401)
    agent_id = agent["agent_id"]
    # A revoked/deleted agent is cut off - it can't report results either (consistent
    # with /agent/sync and /agent/rotate-token).
    if agent.get("status") not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    job = jobs_repo.get(job_id)
    if not job:
        return _err("job not found", 404)
    if job.get("agent_id") != agent_id:
        return _err("job does not belong to this agent", 403)
    if job.get("status") not in ("RUNNING", "PENDING"):
        return _err(f"job already in terminal state: {job.get('status')}", 409)

    # The agent already caps output to its own limit and reports whether it dropped
    # bytes. The server re-caps as defence-in-depth (and to stay under the DynamoDB
    # item-size ceiling), and ORs the flag True whenever it has to cut further - so
    # `stdout_truncated`/`stderr_truncated` is authoritative regardless of which side
    # trimmed. See docs (SELF_HOSTING → Output limits).
    stdout_truncated = bool(body.get("stdout_truncated", False))
    stderr_truncated = bool(body.get("stderr_truncated", False))
    max_bytes = 50_000
    if len(stdout.encode()) > max_bytes:
        stdout = stdout.encode()[:max_bytes].decode(errors="replace") + "\n[TRUNCATED]"
        stdout_truncated = True
    if len(stderr.encode()) > max_bytes:
        stderr = stderr.encode()[:max_bytes].decode(errors="replace") + "\n[TRUNCATED]"
        stderr_truncated = True

    stdout = redact(stdout)
    stderr = redact(stderr)

    jobs_repo.set_result(job_id, {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "completed_at": _iso(),
        "is_write": is_write,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    })

    # Keep the run's cached counts/state current as each member reports (so the run
    # summary is authoritative even after member jobs are purged on retention).
    if job.get("run_id"):
        from handlers.runs import refresh_run
        refresh_run(job.get("tenant_id"), job.get("run_id"))

    if blocked:
        # A fleet member's approvals live at the fleet level (not per-agent), so a
        # blocked write raises a fleet-scoped pending request.
        command = job.get("command")
        fleet_id = agent.get("fleet_id")
        already = (approvals_repo.exists_pending_fleet(fleet_id, command) if fleet_id
                   else approvals_repo.exists_pending(agent_id, command))
        if not already:
            user = users_repo.get(job.get("created_by"))
            approvals_repo.create({
                "approval_id": "appr_" + secrets.token_urlsafe(12),
                "tenant_id": job.get("tenant_id"),
                "agent_id": None if fleet_id else agent_id,
                "fleet_id": fleet_id,
                "command": command,
                "requested_by": job.get("created_by"),
                "requester_name": user.get("name") if user else None,
                "job_id": job_id,
                "status": "pending",
                "created_at": _iso(),
                "reviewed_at": None,
                "reviewed_by": None,
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
