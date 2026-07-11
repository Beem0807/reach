import json
import logging
import secrets

from shared.access import can_access_agent, can_write_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.policy import (
    _is_blocked,
    _is_readonly_blocked,
    derive_k8s_rule,
    is_k8s_command_approved,
    is_k8s_write,
)
import shared.audit as audit
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, approvals_repo, jobs_repo, users_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_create_job(body: dict, raw_token: str, ip: str = "") -> dict:
    agent_id = body.get("agent_id", "").strip()
    command = body.get("command", "").strip()

    if not agent_id or not command:
        return _err("agent_id and command required")
    if len(command) > 4096:
        return _err("command too long (max 4096 characters)", 400)

    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    if _is_blocked(command):
        return _err("command is blocked by safety policy", 403)

    agent = agents_repo.get(agent_id)
    if not agent or not can_access_agent(tenant, agent):
        return _err("agent not found", 404)
    if agent.get("status") != "ACTIVE":
        return _err("agent is not active", 409)

    mode = agent.get("mode", "wild")

    # k8s agents are gated here, before dispatch: jobs run without a shell, so the
    # backend classifies the kubectl verb (default-deny, mirroring the agent) and
    # write-ness is authoritative. Host agents keep the regex heuristic and are
    # gated by the agent (Landlock + approvals).
    is_k8s = (agent.get("type") or "host") == "k8s"
    is_write = is_k8s_write(command) if is_k8s else _is_readonly_blocked(command)

    # Per-user read-only scope: a user granted read-only access to this agent may
    # never write, in any mode. This only narrows - it never bypasses the mode
    # checks below (a writable user is still gated by readonly/approved mode).
    if is_write and not can_write_agent(tenant, agent):
        return _err("you have read-only access to this agent", 403)

    if mode == "readonly" and is_write:
        return _err("command not permitted in readonly mode", 403)

    now = _now()

    # dry_run: classify the command (after the same auth/access/mode gates) without
    # creating a job, so a client can confirm before running a *write*. Mirrors the
    # fan-out dry_run preview.
    if body.get("dry_run"):
        approval_required = False
        if is_k8s and mode == "approved" and is_write:
            rules = [a["k8s_rule"] for a in approvals_repo.list_by_agent(agent_id, status="approved") if a.get("k8s_rule")]
            approval_required = not is_k8s_command_approved(command, rules)
        # `type` lets a client convey how authoritative is_write is: for k8s it's an
        # exact verb parse; for a host it's a best-effort regex and the agent's Landlock
        # sandbox (readonly/approved) is the real gate - and wild mode is unsandboxed.
        return _ok({"dry_run": True, "agent_id": agent_id, "hostname": agent.get("hostname"),
                    "command": command, "mode": mode, "type": ("k8s" if is_k8s else "host"),
                    "is_write": is_write, "approval_required": approval_required})

    # k8s + approved: a write that is not permitted by an approved rule is blocked
    # at submission - it never dispatches. We record a REJECTED job and raise a
    # pending approval (with the structured rule derived from the command) so the
    # operator can approve it and the user can re-run. Matching is rule-based
    # ({verb, resource, namespace, name}), not text prefix.
    #
    # This path is k8s-only, and fleets are host-only, so a fleet member never
    # reaches it. A member's approvals are resolved at the fleet by the agent-facing
    # paths instead: agent_sync draws its approved-command list from the fleet, and a
    # blocked write raises a fleet-scoped pending request in agent_job_result.
    if is_k8s and mode == "approved" and is_write:
        rules = [a["k8s_rule"] for a in approvals_repo.list_by_agent(agent_id, status="approved") if a.get("k8s_rule")]
        if not is_k8s_command_approved(command, rules):
            return _reject_for_approval(tenant, agent_id, command, mode, now, is_k8s=True)

    job_id = "job_" + secrets.token_urlsafe(16)
    jobs_repo.create({
        "job_id": job_id,
        "tenant_id": tenant["tenant_id"],
        "agent_id": agent_id,
        "created_by": tenant["user_id"],
        "command": command,
        "status": "PENDING",
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "duration_ms": None,
        "created_at": _iso(),
        "started_at": None,
        "completed_at": None,
        "expires_at": now + 604800,
        "mode": mode,
        "is_write": is_write,
    })

    agents_repo.set_active_until(agent_id, now + 120)

    # Audit the single-agent execution (fan-outs get run.dispatched instead).
    audit.write("job.dispatched", tenant_id=tenant["tenant_id"],
                actor_id=tenant["user_id"], actor_name=tenant.get("username"),
                actor_role=tenant.get("role"), resource_type="job", resource_id=job_id,
                metadata={"agent_id": agent_id, "hostname": agent.get("hostname"),
                          "command": command[:200], "is_write": is_write, "mode": mode},
                ip_address=ip)

    return _ok({"job_id": job_id, "status": "PENDING"}, 201)


def _reject_for_approval(tenant: dict, agent_id: str, command: str, mode: str, now: int, is_k8s: bool = False) -> dict:
    """Record a REJECTED job + a pending approval for a blocked k8s write. The
    pending approval carries the structured rule derived from the command so the
    operator reviews (and can widen) verb/resource/namespace/name.

    k8s-only (fleets are host-only), so this is always agent-scoped - a fleet
    member's approvals are raised at the fleet by agent_job_result instead."""
    k8s_rule = derive_k8s_rule(command) if is_k8s else None
    job_id = "job_" + secrets.token_urlsafe(16)
    jobs_repo.create({
        "job_id": job_id,
        "tenant_id": tenant["tenant_id"],
        "agent_id": agent_id,
        "created_by": tenant["user_id"],
        "command": command,
        "status": "REJECTED",
        "stdout": None,
        "stderr": "Blocked: approval required - a request has been sent to your admin.",
        "exit_code": 126,
        "duration_ms": 0,
        "created_at": _iso(),
        "started_at": None,
        "completed_at": _iso(),
        "expires_at": now + 604800,
        "mode": mode,
        "is_write": True,
    })
    if not approvals_repo.exists_pending(agent_id, command):
        user = users_repo.get(tenant["user_id"])
        approvals_repo.create({
            "approval_id": "appr_" + secrets.token_urlsafe(12),
            "tenant_id": tenant["tenant_id"],
            "agent_id": agent_id,
            "command": command,
            "k8s_rule": k8s_rule,
            "requested_by": tenant["user_id"],
            "requester_name": user.get("name") if user else None,
            "job_id": job_id,
            "status": "pending",
            "created_at": _iso(),
            "reviewed_at": None,
            "reviewed_by": None,
        })
    return _ok({"job_id": job_id, "status": "REJECTED", "approval_required": True}, 201)


def create_job_handler(event, context):
    logger.info("POST /jobs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_create_job(body, token, ip)
