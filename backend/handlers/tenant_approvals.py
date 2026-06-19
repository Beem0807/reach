import logging

from shared.access import can_access_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo, approvals_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_list_my_pending(query: dict, raw_token: str) -> dict:
    """Current user's pending approval requests, optionally filtered by agent."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    agent_id = (query.get("agent_id") or "").strip() or None

    if agent_id:
        agent = agents_repo.get(agent_id)
        if not agent or not can_access_agent(user, agent):
            return _err("agent not found", 404)

    items = approvals_repo.list_by_tenant(
        user["tenant_id"],
        agent_id=agent_id,
        status="pending",
        requested_by=user["user_id"],
    )

    if not agent_id:
        _cache: dict = {}
        def _can_access(aid: str) -> bool:
            if aid not in _cache:
                a = agents_repo.get(aid)
                _cache[aid] = a is not None and can_access_agent(user, a)
            return _cache[aid]
        items = [r for r in items if _can_access(r["agent_id"])]

    return _ok({"approvals": items})


def handle_list_agent_approved(agent_id: str, raw_token: str, status: str = "approved") -> dict:
    """Approval records for an agent.

    status="approved" (default): effective approved commands, agent-wide.
    status="pending"|"denied": current user's own records filtered by status.
    status="expired": current user's own records in terminal expired state.
    """
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    agent = agents_repo.get(agent_id)
    if not agent or not can_access_agent(user, agent):
        return _err("agent not found", 404)

    if status == "approved":
        items = approvals_repo.list_by_agent(agent_id, status="approved")
    elif status in ("pending", "denied"):
        items = approvals_repo.list_by_agent(agent_id, status=status, requested_by=user["user_id"])
    elif status == "expired":
        items = approvals_repo.list_by_agent(agent_id, status="expired", requested_by=user["user_id"])
    else:
        return _err(f"invalid status '{status}'; use approved, pending, denied, or expired", 400)

    approved_commands = [a["command"] for a in items] if status == "approved" else []
    return _ok({"approved_commands": approved_commands, "approvals": items})


def list_my_pending_handler(event, context):
    logger.info("GET /approvals/pending")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    query = event.get("queryStringParameters") or {}
    return handle_list_my_pending(query, token)


def list_agent_approved_handler(event, context):
    logger.info("GET /agents/{agent_id}/approved-commands")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    qs = event.get("queryStringParameters") or {}
    status = qs.get("status", "approved")
    return handle_list_agent_approved(agent_id, token, status=status)
