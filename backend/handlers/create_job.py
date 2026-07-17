import json
import logging
import secrets
import shlex

from shared.access import can_access_agent, can_write_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.policy import (
    _is_blocked,
    _is_readonly_blocked,
    derive_k8s_rule,
    is_host_argv_approved,
    is_k8s_command_approved,
    is_k8s_write,
    k8s_nonkubectl_argv,
    k8s_uses_unapprovable_binary,
    needs_shell,
    normalize_argv,
    normalize_host_rule,
    to_argv,
)
import shared.audit as audit
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, approvals_repo, jobs_repo, users_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_create_job(body: dict, raw_token: str, ip: str = "") -> dict:
    agent_id = body.get("agent_id", "").strip()
    # Structured exec: `argv` is a bin+args list the agent runs with execve (no shell).
    # `command` is derived for display/classification. Freeform string path is unchanged.
    argv = body.get("argv")
    if argv is not None:
        argv = normalize_argv(argv)
        if argv is None:
            return _err("argv must be a non-empty list of strings", 400)
        command = shlex.join(argv)
    else:
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
    if argv is not None and is_k8s:
        return _err("structured exec (argv) is for host agents; k8s uses kubectl commands", 400)
    is_write = is_k8s_write(command) if is_k8s else _is_readonly_blocked(command)
    # Host WRITES are structured (argv, no shell) so approval is JSON-rule-based - no
    # command strings. A write that needs the shell (pipe/redirect/glob/expansion) can't be
    # a structured rule: in **approved** mode it's unapprovable, so it's rejected; in **wild**
    # mode there's no approval and no sandbox, so it runs freeform (blocking it there is pure
    # friction - backups, config writes). readonly writes are refused below regardless.
    # READS always run as-is (freeform, Landlock-gated) and never need approval.
    if not is_k8s and is_write and argv is None:
        if needs_shell(command):
            if mode == "approved":
                return _err("write commands can't use shell operators (| ; && $() ` > < * ?) "
                            "in approved mode - it can't be approved as a structured rule; "
                            "run a single command per job", 400)
            # wild -> freeform (argv stays None); readonly -> refused by the readonly check below
        else:
            argv = to_argv(command)

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
        if mode == "approved" and is_write:
            if is_k8s:
                approved = approvals_repo.list_by_agent(agent_id, status="approved")
                k8s_argv = k8s_nonkubectl_argv(command)
                if k8s_argv:   # helm/flux/… - approved via a {bin,args} rule on the argv
                    host_rules = [a["host_rule"] for a in approved if a.get("host_rule")]
                    approval_required = not is_host_argv_approved(k8s_argv, host_rules)
                else:
                    rules = [a["k8s_rule"] for a in approved if a.get("k8s_rule")]
                    approval_required = not is_k8s_command_approved(command, rules)
            elif argv is not None:
                # Structured host write: approved only by a JSON host rule (no strings).
                host_rules = [a["host_rule"] for a in approvals_repo.list_by_agent(agent_id, status="approved") if a.get("host_rule")]
                approval_required = not is_host_argv_approved(argv, host_rules)
        # `type` lets a client convey how authoritative is_write is: for k8s it's an
        # exact verb parse; for a host it's a best-effort regex and the agent's Landlock
        # sandbox (readonly/approved) is the real gate - and wild mode is unsandboxed.
        # For structured host exec, is_write is still the heuristic but there is no shell.
        return _ok({"dry_run": True, "agent_id": agent_id, "hostname": agent.get("hostname"),
                    "command": command, "mode": mode, "type": ("k8s" if is_k8s else "host"),
                    "structured": argv is not None, "argv": argv,
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
        approved = approvals_repo.list_by_agent(agent_id, status="approved")
        k8s_argv = k8s_nonkubectl_argv(command)
        if k8s_argv:
            # Non-kubectl tool (helm, flux, …): approve via a positional {bin, args[]} rule
            # on its argv - the same structured model as host approvals - rather than a
            # kubectl {verb,resource,namespace,name} rule.
            host_rules = [a["host_rule"] for a in approved if a.get("host_rule")]
            if not is_host_argv_approved(k8s_argv, host_rules):
                return _reject_for_approval(tenant, agent_id, command, mode, now, k8s_argv=k8s_argv)
        else:
            rules = [a["k8s_rule"] for a in approved if a.get("k8s_rule")]
            if not is_k8s_command_approved(command, rules):
                return _reject_for_approval(tenant, agent_id, command, mode, now, is_k8s=True)

    job_id = "job_" + secrets.token_urlsafe(16)
    jobs_repo.create({
        "job_id": job_id,
        "tenant_id": tenant["tenant_id"],
        "agent_id": agent_id,
        "created_by": tenant["user_id"],
        "command": command,
        "argv": argv,
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
                          "command": command[:200], "is_write": is_write, "mode": mode,
                          "structured": argv is not None},
                ip_address=ip)

    return _ok({"job_id": job_id, "status": "PENDING"}, 201)


def _reject_for_approval(tenant: dict, agent_id: str, command: str, mode: str, now: int,
                         is_k8s: bool = False, k8s_argv: list = None) -> dict:
    """Record a REJECTED job + a pending approval for a blocked k8s write. The pending
    approval carries the structured rule derived from the command: a kubectl write gets a
    {verb,resource,namespace,name} k8s_rule; a non-kubectl write (helm/flux/…) gets a
    {bin,args[]} host_rule derived from `k8s_argv`. The operator reviews (and can widen with
    `*`) either.

    k8s-only (fleets are host-only), so this is always agent-scoped - a fleet
    member's approvals are raised at the fleet by agent_job_result instead."""
    host_rule = normalize_host_rule({"bin": k8s_argv[0], "args": k8s_argv[1:]}) if k8s_argv else None
    k8s_rule = derive_k8s_rule(command) if (is_k8s and not host_rule) else None
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
            "host_rule": host_rule,
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
