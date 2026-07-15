import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import shared.audit as audit
from shared.access import (accessible_agent_ids, can_access_agent, can_access_fleet,
                           can_write_agent, can_write_fleet, is_agent_restricted)
from shared.auth import _bearer, _verify_tenant_token
from shared.policy import (
    has_shell_operators,
    host_rule_to_command,
    normalize_host_rule,
    normalize_k8s_rule,
    rule_to_command,
)
from shared.response import _err, _iso, _ok
from shared.store import agents_repo, approvals_repo, fleets_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_DURATION_SECONDS = {"1h": 3600, "8h": 28800, "24h": 86400, "7d": 604800}
_ROLE_RANK = {"admin": 3, "operator": 2, "developer": 1}


def _require_role(user: dict, min_role: str) -> bool:
    return _ROLE_RANK.get(user.get("role", "developer"), 0) >= _ROLE_RANK.get(min_role, 0)


def _parse_expires_at(duration: str) -> Tuple[bool, Optional[str]]:
    if not duration or duration == "permanent":
        return True, None
    if duration == "now":
        return True, datetime.now(tz=timezone.utc).isoformat()
    if duration in _DURATION_SECONDS:
        secs = _DURATION_SECONDS[duration]
    else:
        m = re.fullmatch(r"(\d+)(h|d)", duration)
        if not m:
            return False, None
        n, unit = int(m.group(1)), m.group(2)
        secs = n * 3600 if unit == "h" else n * 86400
    expires = datetime.now(tz=timezone.utc) + timedelta(seconds=secs)
    return True, expires.isoformat()


def handle_list_my_pending(query: dict, raw_token: str) -> dict:
    """A developer's own approval requests, with server-side kind filter, text
    search (LIKE), agent filter, and pagination.

    - status=pending (default): the caller's own pending requests (scoped by
      requested_by) - always visible across any agent.
    - status=approved: effective approved commands (shared, not per-requester).
      With a specific agent, that agent's commands; with "all agents", the
      approved commands across every agent this developer can access."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    agent_id = (query.get("agent_id") or "").strip() or None
    kind = (query.get("type") or "").strip().lower() or None
    if kind not in (None, "host", "k8s"):
        return _err("type must be host or k8s", 400)
    q = (query.get("q") or "").strip() or None
    status = (query.get("status") or "pending").strip().lower()
    if status not in ("pending", "approved"):
        return _err("status must be pending or approved", 400)

    agent_ids = fleet_ids = fleet_id = None
    if agent_id:
        agent = agents_repo.get(agent_id)
        if not agent or not can_access_agent(user, agent):
            return _err("agent not found", 404)
        # A fleet member's approvals live at the fleet, so query the fleet instead.
        if agent.get("fleet_id"):
            fleet_id, agent_id = agent["fleet_id"], None
    elif status == "approved" and is_agent_restricted(user):
        # Approved commands are agent-wide; with no specific agent chosen, show the
        # approved commands across every agent and fleet this developer can access.
        # Unrestricted developers fall through with None (all tenant agents/fleets).
        agent_ids = accessible_agent_ids(user, agents_repo.list_by_tenant(user["tenant_id"]))
        fleet_ids = [f["fleet_id"] for f in fleets_repo.list_by_tenant(user["tenant_id"]) if can_access_fleet(user, f)]

    try:
        limit = int(query.get("limit") or 20)
        offset = int(query.get("offset") or 0)
    except (TypeError, ValueError):
        return _err("limit and offset must be integers", 400)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    items, total = approvals_repo.search_by_tenant(
        user["tenant_id"],
        status=status,
        agent_id=agent_id,
        agent_ids=agent_ids,
        fleet_id=fleet_id,
        fleet_ids=fleet_ids,
        # Pending is the caller's own; approved is the agent's shared allowlist.
        requested_by=user["user_id"] if status == "pending" else None,
        kind=kind,
        q=q,
        limit=limit,
        offset=offset,
    )
    return _ok({"approvals": items, "total": total, "limit": limit, "offset": offset})


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

    # A fleet member has no per-agent approvals - its effective (and own) approval
    # records live at the fleet, so query there instead.
    fleet_id = agent.get("fleet_id")

    def _list(status_val, own):
        requested_by = user["user_id"] if own else None
        if fleet_id:
            return approvals_repo.list_by_fleet(fleet_id, status=status_val, requested_by=requested_by)
        return approvals_repo.list_by_agent(agent_id, status=status_val, requested_by=requested_by)

    if status == "approved":
        items = _list("approved", own=False)
    elif status in ("pending", "denied", "expired"):
        items = _list(status, own=True)
    else:
        return _err(f"invalid status '{status}'; use approved, pending, denied, or expired", 400)

    approved_commands = [a["command"] for a in items] if status == "approved" else []
    # Surface fleet membership so the CLI can redirect to the fleet approvals view.
    return _ok({"approved_commands": approved_commands, "approvals": items, "agent_fleet_id": fleet_id})


def handle_tenant_list_all_approvals(query: dict, raw_token: str) -> dict:
    """List all approvals in the tenant (operator+). Unlike /approvals/pending this is not filtered to own items."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    agent_id = (query.get("agent_id") or "").strip() or None
    fleet_id = (query.get("fleet_id") or "").strip() or None
    status = (query.get("status") or "").strip() or None
    kind = (query.get("type") or "").strip().lower() or None
    if kind not in (None, "host", "k8s"):
        return _err("type must be host or k8s", 400)
    # scope narrows to standalone-agent ('agent') or fleet ('fleet') approvals.
    scope = (query.get("scope") or "").strip().lower() or None
    if scope not in (None, "agent", "fleet"):
        return _err("scope must be agent or fleet", 400)
    q = (query.get("q") or "").strip() or None

    try:
        limit = int(query.get("limit") or 20)
        offset = int(query.get("offset") or 0)
    except (TypeError, ValueError):
        return _err("limit and offset must be integers", 400)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    # Scoping applies to every role the same way: a scoped operator/admin only sees
    # approvals for the agents and fleets they're assigned to (asking for one they
    # can't access shows nothing). Approvals target an agent OR a fleet.
    agent_ids = fleet_ids = None
    if is_agent_restricted(user):
        allowed_agents = accessible_agent_ids(user, agents_repo.list_by_tenant(user["tenant_id"]))
        allowed_fleets = [f["fleet_id"] for f in fleets_repo.list_by_tenant(user["tenant_id"]) if can_access_fleet(user, f)]
        if agent_id is not None:
            agent_ids = [agent_id] if agent_id in allowed_agents else []
            agent_id = None
        elif fleet_id is not None:
            fleet_ids = [fleet_id] if fleet_id in allowed_fleets else []
            fleet_id = None
        elif scope == "agent":
            agent_ids = allowed_agents
        elif scope == "fleet":
            fleet_ids = allowed_fleets
        else:
            agent_ids, fleet_ids = allowed_agents, allowed_fleets

    approvals, total = approvals_repo.search_by_tenant(
        user["tenant_id"], status=status, agent_id=agent_id, agent_ids=agent_ids,
        fleet_id=fleet_id, fleet_ids=fleet_ids, scope=scope, kind=kind, q=q, limit=limit, offset=offset,
    )
    _ac: dict = {}
    _fc: dict = {}
    def _labels(a: dict) -> dict:
        if a.get("fleet_id"):
            fid = a["fleet_id"]
            if fid not in _fc:
                _fc[fid] = (fleets_repo.get(fid) or {}).get("name")
            return {"scope": "fleet", "fleet_name": _fc[fid], "agent_hostname": None}
        aid = a.get("agent_id")
        if aid not in _ac:
            _ac[aid] = (agents_repo.get(aid) or {}).get("hostname") if aid else None
        return {"scope": "agent", "agent_hostname": _ac[aid], "fleet_name": None}
    enriched = [{**a, **_labels(a)} for a in approvals]
    return _ok({"approvals": enriched, "total": total, "limit": limit, "offset": offset})


def handle_tenant_review_approval(approval_id: str, action: str, raw_token: str, body: Optional[dict] = None) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    approval = approvals_repo.get(approval_id)
    if not approval or approval.get("tenant_id") != user["tenant_id"]:
        return _err("approval not found", 404)

    # Approvals gate write commands, so reviewing one requires read-write access to
    # the target (agent or fleet). Read-only / out-of-scope reviewers are blocked.
    if approval.get("fleet_id"):
        fleet = fleets_repo.get(approval["fleet_id"])
        if not fleet or not can_access_fleet(user, fleet):
            return _err("approval not found", 404)
        if not can_write_fleet(user, fleet):
            return _err("you have read-only access to this fleet", 403)
    else:
        agent = agents_repo.get(approval.get("agent_id"))
        if not agent or not can_access_agent(user, agent):
            return _err("approval not found", 404)
        if not can_write_agent(user, agent):
            return _err("you have read-only access to this agent", 403)

    current_status = approval.get("status")
    if current_status in ("denied", "expired"):
        return _err(f"{current_status} approvals cannot be updated", 409)
    if current_status == "approved" and action == "deny":
        return _err("approved approvals cannot be denied - use duration=now to instantly expire instead", 409)

    new_status = "approved" if action == "approve" else "denied"
    reviewed_at = _iso()
    expires_at: Optional[str] = None

    if action == "approve":
        duration = (body or {}).get("duration", "permanent")
        if duration == "now" and current_status == "pending":
            return _err("duration=now is not valid for initial approval", 400)
        ok, expires_at = _parse_expires_at(duration)
        if not ok:
            return _err(f"invalid duration '{duration}'; use 1h, 8h, 24h, 7d, permanent, now, or Nh/Nd", 400)
        if duration == "now":
            new_status = "expired"

    reviewer = user.get("username") or user.get("user_id", "")
    approvals_repo.update_status(approval_id, new_status, reviewed_at, reviewer, expires_at=expires_at)
    updated = {**approval, "status": new_status, "reviewed_at": reviewed_at, "reviewed_by": reviewer, "expires_at": expires_at}
    # Audit action is one of: "approval.approved", "approval.denied", "approval.expired"
    audit.write(
        f"approval.{new_status}",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=reviewer,
        actor_role=user.get("role", ""),
        resource_type="approval",
        resource_id=approval_id,
        metadata={"command": approval.get("command"), "agent_id": approval.get("agent_id"), "expires_at": expires_at},
    )
    logger.info("Approval %s %s by user=%s", approval_id, new_status, user.get("user_id"))
    return _ok(updated)


def handle_tenant_create_approval(body: dict, raw_token: str) -> dict:
    """Create an approval for a standalone agent or a whole fleet. Operators/admins
    create directly approved; developers create pending. Fleet members carry no
    agent-scoped approvals - they inherit their fleet's."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    agent_id = (body or {}).get("agent_id", "").strip()
    fleet_id = (body or {}).get("fleet_id", "").strip()
    if agent_id and fleet_id:
        return _err("specify either agent_id or fleet_id, not both", 400)
    if not agent_id and not fleet_id:
        return _err("agent_id or fleet_id is required", 400)

    if fleet_id:
        # Fleet approval: applies to every current and future member. Fleets are host-only,
        # so these are host approvals - a structured host_rule (preferred) or a legacy
        # command string, never a k8s rule.
        fleet = fleets_repo.get(fleet_id)
        if not fleet or fleet.get("tenant_id") != user["tenant_id"] or not can_access_fleet(user, fleet):
            return _err("fleet not found", 404)
        if not can_write_fleet(user, fleet):
            return _err("you have read-only access to this fleet", 403)
        is_k8s = False
        scope_agent_id, scope_fleet_id = None, fleet_id
        scope_meta = {"fleet_id": fleet_id}

        def _active(status, requested_by=None):
            return approvals_repo.list_by_fleet(fleet_id, status=status, requested_by=requested_by)
    else:
        agent = agents_repo.get(agent_id)
        if not agent or agent.get("tenant_id") != user["tenant_id"] or not can_access_agent(user, agent):
            return _err("agent not found", 404)
        # Fleet members are approved at the fleet level, never by individual agent id.
        if agent.get("fleet_id"):
            return _err("this agent is a fleet member - approve commands on its fleet, not the agent", 409)
        # Approvals only ever cover write commands, so a read-only user has no business
        # creating one (a pending request or a pre-approval) for this agent.
        if not can_write_agent(user, agent):
            return _err("you have read-only access to this agent", 403)
        is_k8s = (agent.get("type") or "host") == "k8s"
        scope_agent_id, scope_fleet_id = agent_id, None
        scope_meta = {"agent_id": agent_id}

        def _active(status, requested_by=None):
            return approvals_repo.list_by_agent(agent_id, status=status, requested_by=requested_by)

    if not _require_role(user, "operator"):
        # Developer path: single command/rule → pending
        if is_k8s:
            host_rule = None
            k8s_rule = normalize_k8s_rule((body or {}).get("k8s_rule") or {})
            if not k8s_rule:
                return _err("k8s_rule with a valid write verb is required for k8s agents", 400)
            command = rule_to_command(k8s_rule)
        else:
            k8s_rule = None
            raw_host_rule = (body or {}).get("host_rule")
            if raw_host_rule is not None:
                host_rule = normalize_host_rule(raw_host_rule)
                if not host_rule:
                    return _err("host_rule needs a 'bin' and 'args' list with no shell operators (use * for a wildcard arg)", 400)
                command = host_rule_to_command(host_rule)
            else:
                host_rule = None
                command = (body or {}).get("command", "").strip()
                if not command:
                    return _err("command or host_rule is required", 400)
                if has_shell_operators(command):
                    return _err("approved commands can't contain shell operators (| ; && $() ` > <) "
                                "- request approval for a single command", 400)

        def _same(a):
            if is_k8s:
                return a.get("k8s_rule") == k8s_rule
            if host_rule is not None:
                return a.get("host_rule") == host_rule
            return a.get("host_rule") is None and a.get("command") == command

        active = _active("approved")
        if any(_same(a) for a in active):
            return _err("an equivalent rule already has an active approval here"
                        if is_k8s else "command already has an active approval here", 409)

        pending = _active("pending", user["user_id"])
        if any(_same(a) for a in pending):
            return _err("you already have a pending request for this rule"
                        if is_k8s else "you already have a pending request for this command", 409)

        now = _iso()
        requester = user.get("username") or user.get("user_id", "")
        approval = {
            "approval_id": "appr_" + secrets.token_urlsafe(12),
            "tenant_id": user["tenant_id"],
            "agent_id": scope_agent_id,
            "fleet_id": scope_fleet_id,
            "command": command,
            "k8s_rule": k8s_rule,
            "host_rule": host_rule,
            "requested_by": user.get("user_id", ""),
            "requester_name": requester,
            "job_id": None,
            "status": "pending",
            "created_at": now,
            "reviewed_at": None,
            "reviewed_by": None,
            "expires_at": None,
        }
        approvals_repo.create(approval)
        audit.write(
            "approval.requested",
            tenant_id=user["tenant_id"],
            actor_id=user.get("user_id", ""),
            actor_name=requester,
            actor_role=user.get("role", ""),
            resource_type="approval",
            resource_id=approval["approval_id"],
            metadata={"command": command, **scope_meta},
        )
        logger.info("Approval request (pending) created for %s command=%s by user=%s", scope_meta, command, user.get("user_id"))
        return _ok(approval, 201)

    # Operator/admin path: create directly as approved, supports bulk + duration.
    # k8s agents pre-approve structured rules ({verb, resource, namespace, name});
    # host agents pre-approve command strings. Each item is {command, k8s_rule}.
    if is_k8s:
        rule_list = (body or {}).get("k8s_rules")
        single_rule = (body or {}).get("k8s_rule")
        if rule_list is not None:
            if not isinstance(rule_list, list) or not rule_list:
                return _err("k8s_rules must be a non-empty list", 400)
            raw_rules = rule_list
            bulk = True
        elif single_rule is not None:
            raw_rules = [single_rule]
            bulk = False
        else:
            return _err("k8s_rule or k8s_rules is required for k8s agents", 400)
        items = []
        for raw in raw_rules:
            rule = normalize_k8s_rule(raw)
            if not rule:
                return _err("each k8s_rule needs a valid write verb (verb, resource, namespace, name)", 400)
            items.append({"command": rule_to_command(rule), "k8s_rule": rule, "host_rule": None})
    elif (body or {}).get("host_rule") is not None or (body or {}).get("host_rules") is not None:
        # Structured host approval(s): {bin, args[]} with positional "*" wildcards.
        rule_list = (body or {}).get("host_rules")
        if rule_list is not None:
            if not isinstance(rule_list, list) or not rule_list:
                return _err("host_rules must be a non-empty list", 400)
            raw_rules = rule_list
            bulk = True
        else:
            raw_rules = [(body or {}).get("host_rule")]
            bulk = False
        items = []
        for raw in raw_rules:
            hr = normalize_host_rule(raw)
            if not hr:
                return _err("each host_rule needs a 'bin' and 'args' list with no shell operators (use * for a wildcard arg)", 400)
            items.append({"command": host_rule_to_command(hr), "k8s_rule": None, "host_rule": hr})
    else:
        single_command = (body or {}).get("command", "").strip()
        command_list = (body or {}).get("commands")
        if command_list is not None:
            if not isinstance(command_list, list) or not command_list:
                return _err("commands must be a non-empty list", 400)
            commands = [c.strip() for c in command_list if isinstance(c, str) and c.strip()]
            if not commands:
                return _err("commands must contain at least one non-empty string", 400)
            bulk = True
        elif single_command:
            commands = [single_command]
            bulk = False
        else:
            return _err("command, commands, host_rule, or host_rules is required", 400)
        bad = next((c for c in commands if has_shell_operators(c)), None)
        if bad is not None:
            return _err("approved commands can't contain shell operators (| ; && $() ` > <); "
                        f"offending: {bad!r}", 400)
        items = [{"command": c, "k8s_rule": None, "host_rule": None} for c in commands]

    duration = (body or {}).get("duration")
    if duration == "now":
        return _err("duration=now is not valid for pre-approve; use 1h, 8h, 24h, 7d, permanent, or Nh/Nd", 400)
    ok, expires_at = _parse_expires_at(duration)
    if not ok:
        return _err(f"invalid duration '{duration}'; use 1h, 8h, 24h, 7d, permanent, or Nh/Nd", 400)

    active = _active("approved")
    active_commands = {a["command"] for a in active if not a.get("host_rule")}
    active_rules = [a.get("k8s_rule") for a in active if a.get("k8s_rule")]
    active_host_rules = [a.get("host_rule") for a in active if a.get("host_rule")]
    now = _iso()
    reviewer = user.get("username") or user.get("user_id", "")
    created = []
    skipped = []

    for item in items:
        command, rule, host_rule = item["command"], item["k8s_rule"], item["host_rule"]
        if is_k8s:
            already = rule in active_rules
        elif host_rule is not None:
            already = host_rule in active_host_rules
        else:
            already = command in active_commands
        if already:
            skipped.append({"command": command, "reason": "already_approved"})
            continue
        approval = {
            "approval_id": "appr_" + secrets.token_urlsafe(12),
            "tenant_id": user["tenant_id"],
            "agent_id": scope_agent_id,
            "fleet_id": scope_fleet_id,
            "command": command,
            "k8s_rule": rule,
            "host_rule": host_rule,
            "requested_by": user.get("user_id", ""),
            "requester_name": reviewer,
            "job_id": None,
            "status": "approved",
            "created_at": now,
            "reviewed_at": now,
            "reviewed_by": reviewer,
            "expires_at": expires_at,
        }
        approvals_repo.create(approval)
        created.append(approval)

    if created:
        audit.write(
            "approval.pre_approved",
            tenant_id=user["tenant_id"],
            actor_id=user.get("user_id", ""),
            actor_name=reviewer,
            actor_role=user.get("role", ""),
            resource_type="approval",
            resource_id=scope_agent_id or scope_fleet_id,
            metadata={"commands": [a["command"] for a in created], **scope_meta, "expires_at": expires_at, "count": len(created)},
        )
    logger.info("Pre-approved %d commands for %s by user=%s (%d skipped)", len(created), scope_meta, user.get("user_id"), len(skipped))

    if bulk:
        return _ok({"created": created, "skipped": skipped})
    if skipped:
        return _err("command already has an active approval here", 409)
    return _ok(created[0])


def handle_tenant_delete_approval(approval_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    approval = approvals_repo.get(approval_id)
    if not approval or approval.get("tenant_id") != user["tenant_id"]:
        return _err("approval not found", 404)
    # A scoped operator can only delete approvals for the agents/fleets they can
    # access. A fleet approval has no agent_id - check the fleet instead.
    if approval.get("fleet_id"):
        fleet = fleets_repo.get(approval["fleet_id"])
        if not fleet or not can_access_fleet(user, fleet):
            return _err("approval not found", 404)
    else:
        agent = agents_repo.get(approval.get("agent_id"))
        if not agent or not can_access_agent(user, agent):
            return _err("approval not found", 404)

    approvals_repo.delete(approval_id)
    audit.write(
        "approval.deleted",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="approval",
        resource_id=approval_id,
        metadata={"command": approval.get("command"), "agent_id": approval.get("agent_id"), "status": approval.get("status")},
    )
    logger.info("Approval %s deleted by user=%s", approval_id, user.get("user_id"))
    return _ok({"deleted": True})


def list_all_approvals_handler(event, context):
    logger.info("GET /tenant/approvals")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    query = event.get("queryStringParameters") or {}
    return handle_tenant_list_all_approvals(query, token)


def pre_approve_handler(event, context):
    import json
    logger.info("POST /tenant/approvals")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_tenant_create_approval(body, token)


def review_approval_handler(event, context):
    import json
    path = event.get("pathParameters") or {}
    approval_id = path.get("approval_id", "")
    action = path.get("action", "")
    logger.info("PUT /tenant/approvals/%s/%s", approval_id, action)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_tenant_review_approval(approval_id, action, token, body)


def delete_approval_handler(event, context):
    logger.info("DELETE /tenant/approvals/{approval_id}")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    approval_id = (event.get("pathParameters") or {}).get("approval_id", "")
    return handle_tenant_delete_approval(approval_id, token)


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
