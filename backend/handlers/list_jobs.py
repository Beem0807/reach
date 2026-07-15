import base64
import logging
from typing import Optional

from shared.access import can_access_agent, can_access_fleet
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo, fleets_repo, jobs_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _decode_cursor(s: str) -> Optional[str]:
    try:
        return base64.urlsafe_b64decode(s.encode()).decode()
    except Exception:
        return None


def _encode_cursor(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def handle_list_jobs(raw_token: str, agent_id: Optional[str], limit: int, cursor: Optional[str] = None,
                     fleet_id: Optional[str] = None, run_id: Optional[str] = None,
                     q: Optional[str] = None) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)
    ql = (q or "").strip().lower() or None

    member_ids: Optional[set] = None
    if fleet_id:
        # Fleet jobs = jobs across every member. Resolve the member set and filter
        # tenant jobs to it (jobs carry an agent_id, not a fleet_id).
        fleet = fleets_repo.get(fleet_id)
        if not fleet or fleet.get("tenant_id") != tenant["tenant_id"] or not can_access_fleet(tenant, fleet):
            return _err("fleet not found", 404)
        member_ids = {a["agent_id"] for a in agents_repo.list_by_fleet(fleet_id) if a.get("fleet_id") == fleet_id}
        agent_id = None  # fleet_id and agent_id are mutually exclusive here
    target_fleet_id = None
    if agent_id and member_ids is None:
        agent = agents_repo.get(agent_id)
        if not agent or not can_access_agent(tenant, agent):
            return _err("agent not found", 404)
        target_fleet_id = agent.get("fleet_id")

    decoded_cursor = _decode_cursor(cursor) if cursor else None
    if run_id:
        # Run detail = the exact member jobs, via the indexed run_id (no tenant scan).
        rows = jobs_repo.list_by_run(tenant["tenant_id"], run_id)
        if agent_id:
            rows = [j for j in rows if j.get("agent_id") == agent_id]
    else:
        # A command search is filtered in Python, so pull a generous window.
        fetch_limit = 500 if ql else limit
        rows = jobs_repo.list_by_tenant(tenant["tenant_id"], agent_id, fetch_limit, cursor=decoded_cursor)
        if ql:
            rows = [j for j in rows if ql in (j.get("command") or "").lower()]

    # A fleet fan-out batch is gated by **fleet** access, not per-agent - its members
    # are ephemeral (reaped when an ASG scales in), so a per-agent accessibility check
    # would hide a run's jobs once their hosts are gone. The batch carries the fleet id
    # (run_fleet_id), so we authorize once against the fleet and keep every job.
    run_fleet_id = next((j.get("run_fleet_id") for j in rows if j.get("run_fleet_id")), None) if run_id else None
    if run_fleet_id:
        fleet = fleets_repo.get(run_fleet_id)
        if not fleet or fleet.get("tenant_id") != tenant["tenant_id"] or not can_access_fleet(tenant, fleet):
            return _err("run not found", 404)
        rows = [j for j in rows if j.get("run_fleet_id") == run_fleet_id]
    elif member_ids is not None:
        rows = [j for j in rows if j["agent_id"] in member_ids]
    elif not agent_id:
        _cache: dict = {}
        def _accessible(aid: str) -> bool:
            if aid not in _cache:
                a = agents_repo.get(aid)
                _cache[aid] = a is not None and can_access_agent(tenant, a)
            return _cache[aid]
        rows = [j for j in rows if _accessible(j["agent_id"])]

    # A command search returns the matches within the fetched window (like a batch),
    # capped to `limit`; it doesn't cursor-paginate.
    if ql:
        rows = rows[:limit]

    _agent_cache: dict = {}
    def _agent(aid: str) -> dict:
        if aid not in _agent_cache:
            _agent_cache[aid] = agents_repo.get(aid) or {}
        return _agent_cache[aid]

    jobs = [
        {
            "job_id": j["job_id"],
            "agent_id": j["agent_id"],
            "agent_hostname": _agent(j["agent_id"]).get("hostname"),
            "agent_mode": _agent(j["agent_id"]).get("mode"),
            # Fall back to the stamped fleet id when the agent record is gone (reaped
            # ASG member), so a fleet job stays attributable to its fleet.
            "agent_fleet_id": _agent(j["agent_id"]).get("fleet_id") or j.get("run_fleet_id"),
            "run_id": j.get("run_id"),
            "run_tag": j.get("run_tag"),
            "run_fleet_id": j.get("run_fleet_id"),
            "wave": j.get("wave") or 0,   # staged-rollout wave index (0 = first / non-staged)
            "created_by": j.get("created_by"),
            "command": j["command"],
            "status": j["status"],
            "exit_code": j.get("exit_code"),
            "stdout": j.get("stdout"),
            "stderr": j.get("stderr"),
            "stdout_truncated": bool(j.get("stdout_truncated")),
            "stderr_truncated": bool(j.get("stderr_truncated")),
            "duration_ms": j.get("duration_ms"),
            "created_at": j.get("created_at"),
            "completed_at": j.get("completed_at"),
        }
        for j in rows
    ]

    result: dict = {"jobs": jobs}
    # When a specific agent was requested, surface whether it's a fleet member so
    # clients can redirect to the fleet view (jobs are otherwise agent-scoped).
    if target_fleet_id:
        result["agent_fleet_id"] = target_fleet_id
    # No cursor for a batch view or a search - those are materialized in one window.
    if not run_id and not ql and len(rows) == limit and rows:
        result["next_cursor"] = _encode_cursor(rows[-1]["created_at"])
    return _ok(result)


def list_jobs_handler(event, context):
    logger.info("GET /jobs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    agent_filter = qs.get("agent_id")
    fleet_filter = qs.get("fleet_id")
    batch_filter = qs.get("run_id")
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return handle_list_jobs(token, agent_filter, limit, cursor, fleet_id=fleet_filter, run_id=batch_filter, q=qs.get("q"))
