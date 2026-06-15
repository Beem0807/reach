import json
import logging

from shared.auth import _bearer, _verify_agent_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_sync(body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()

    if not agent_id or not machine_fp:
        return _err("agent_id and machine_fingerprint required")

    agent = _verify_agent_token(raw_token, agent_id)
    if not agent:
        return _err("unauthorized", 401)

    agent_status = agent.get("status")
    if agent_status not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    now = _now()
    next_poll = 5 if int(agent.get("active_until") or 0) > now else 30

    agents_repo.update_heartbeat(
        agent_id,
        reactivate=(agent_status == "INACTIVE"),
        now_iso=_iso(),
    )

    jobs_payload = []
    for job in jobs_repo.get_pending_for_agent(agent_id):
        if jobs_repo.set_running(job["job_id"], _iso()):
            jobs_payload.append({
                "job_id": job["job_id"],
                "command": job["command"],
                "mode": job.get("mode", "wild"),
            })

    return _ok({"jobs": jobs_payload, "next_poll_seconds": next_poll})


def agent_sync_handler(event, context):
    logger.info("POST /agent/sync")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_sync(body, token)
