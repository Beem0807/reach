"""CLI/API-token surface for fleets.

The console fleet endpoints (`/tenant/fleets/*`) require an operator+ session
token and expose full management. These endpoints are the smaller, access-scoped
view the CLI and MCP server use with an **API token** (any role): list the fleets
you can access, list a fleet's members, and fan a command out to every member.

Access mirrors agents: a fleet is visible when `can_access_fleet` is true, and
fan-out (a write to every member) requires `can_write_fleet`.
"""
import json
import logging
import secrets
from typing import Optional

import shared.audit as audit
from shared.access import can_access_fleet, can_write_fleet
from shared.auth import _bearer, _verify_tenant_token
from shared.fanout import deterministic_run_id, new_run_row, order_and_limit, parse_max_targets, run_summary_view
from shared.policy import _is_blocked, _is_readonly_blocked, needs_shell, to_argv
from shared.response import _err, _iso, _now, _ok
from shared.settings import effective_settings
from shared.store import agents_repo, approvals_repo, fleets_repo, jobs_repo, runs_repo, tenants_repo
from shared.waves import assign_waves, plan_waves, resolve_policy

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _fleet_summary(fleet: dict, member_count: int, writable: bool) -> dict:
    return {
        "fleet_id": fleet["fleet_id"],
        "name": fleet.get("name"),
        "mode": fleet.get("mode"),
        "tags": fleet.get("tags") or [],
        "status": fleet.get("status"),
        "member_count": member_count,
        "writable": writable,
    }


def _resolve_fleet(user: dict, fleet_id: str) -> Optional[dict]:
    fleet = fleets_repo.get(fleet_id)
    if not fleet or fleet.get("tenant_id") != user["tenant_id"] or not can_access_fleet(user, fleet):
        return None
    return fleet


def _members(tenant_id: str, fleet_id: str) -> list:
    # Query by fleet_id directly (indexed / fleet-index GSI) rather than scanning the
    # whole tenant. The fleet_id/tenant_id checks are defensive belt-and-suspenders -
    # list_by_fleet already returns only this fleet's members.
    return [a for a in agents_repo.list_by_fleet(fleet_id)
            if a.get("fleet_id") == fleet_id and a.get("tenant_id") == tenant_id]


def handle_cli_list_fleets(raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    counts = fleets_repo.member_counts(user["tenant_id"])
    fleets = [f for f in fleets_repo.list_by_tenant(user["tenant_id"]) if can_access_fleet(user, f)]
    return _ok({"fleets": [
        _fleet_summary(f, counts.get(f["fleet_id"], 0), can_write_fleet(user, f)) for f in fleets
    ]})


def handle_cli_list_fleet_agents(fleet_id: str, raw_token: str, q=None,
                                 limit=None, offset=0) -> dict:
    """Members of a fleet. Optional `q` filters by hostname / agent id (substring).
    Pagination is **opt-in**: pass `limit` for one page plus a `total` (the CLI omits
    it to get every member); a fleet backing an autoscaling group can have thousands."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    fleet = _resolve_fleet(user, fleet_id)
    if not fleet:
        return _err("fleet not found", 404)
    writable = can_write_fleet(user, fleet)

    members = [a for a in _members(user["tenant_id"], fleet_id) if a.get("status") != "DELETED"]
    ql = (q or "").strip().lower() or None
    if ql:
        members = [a for a in members
                   if ql in (a.get("hostname") or "").lower() or ql in (a.get("agent_id") or "").lower()]
    # Stable order for deterministic paging (hostname, then id).
    members.sort(key=lambda a: ((a.get("hostname") or "").lower(), a.get("agent_id") or ""))
    total = len(members)
    if limit is not None:
        offset = max(0, offset)
        limit = max(1, min(limit, 100))
        members = members[offset:offset + limit]

    agents = [{
        "agent_id": a["agent_id"],
        "hostname": a.get("hostname"),
        "status": a.get("status"),
        "type": a.get("type") or "host",
        "mode": a.get("mode"),
        "fleet_id": fleet_id,
        "agent_version": a.get("agent_version"),
        "claimed_at": a.get("claimed_at"),
        "last_heartbeat_at": a.get("last_heartbeat_at"),
        "writable": writable,
        # Grant state, so the console can flag/resolve grant mismatch per member without
        # a separate agents fetch (see the Fleets page reconcile/accept flows).
        "grant_service_mgmt": bool(a.get("grant_service_mgmt")),
        "grant_docker": bool(a.get("grant_docker")),
        "service_mgmt_detected": a.get("service_mgmt_detected"),
        "docker_detected": a.get("docker_detected"),
        "grants_exception": a.get("grants_exception"),
    } for a in members]
    result = {"fleet_id": fleet_id, "fleet_name": fleet.get("name"), "agents": agents}
    if limit is not None:
        result.update(total=total, limit=limit, offset=offset)
    return _ok(result)


def handle_cli_list_fleet_approved(fleet_id: str, raw_token: str, status: str = "approved") -> dict:
    """Approval records for a fleet (every member shares them).

    status="approved" (default): effective approved commands, fleet-wide.
    status="pending"|"denied"|"expired": the caller's own records in that state.
    """
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    fleet = _resolve_fleet(user, fleet_id)
    if not fleet:
        return _err("fleet not found", 404)

    if status == "approved":
        items = approvals_repo.list_by_fleet(fleet_id, status="approved")
    elif status in ("pending", "denied", "expired"):
        items = approvals_repo.list_by_fleet(fleet_id, status=status, requested_by=user["user_id"])
    else:
        return _err(f"invalid status '{status}'; use approved, pending, denied, or expired", 400)

    approved_commands = [a["command"] for a in items] if status == "approved" else []
    return _ok({
        "fleet_id": fleet_id,
        "fleet_name": fleet.get("name"),
        "approved_commands": approved_commands,
        "approvals": items,
    })


def _run_outcome(job: dict) -> str:
    status = job.get("status")
    if status in ("PENDING", "RUNNING"):
        return "pending"
    if status == "SUCCEEDED" and (job.get("exit_code") in (0, None)):
        return "ok"
    return "failed"


def handle_cli_list_fleet_runs(fleet_id: str, raw_token: str, limit: int = 20, cursor: str = None) -> dict:
    """Fan-out **runs** for a fleet: one entry per `fleets exec`, newest first, with
    per-run pass/fail counts. Reads the runs table (O(runs)), not a job scan - so a run
    stays listed for its full retention even after its member jobs are purged.
    Cursor-paginated by created_at (`next_cursor` set while more pages remain)."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    fleet = _resolve_fleet(user, fleet_id)
    if not fleet:
        return _err("fleet not found", 404)
    rows = runs_repo.list_by_fleet(fleet_id, limit=limit, cursor=cursor)
    runs = [run_summary_view(r) for r in rows]
    next_cursor = runs[-1]["created_at"] if len(rows) == limit and runs else None
    return _ok({"fleet_id": fleet_id, "fleet_name": fleet.get("name"),
                "runs": runs, "next_cursor": next_cursor})


def handle_cli_fleet_fanout(fleet_id: str, body: dict, raw_token: str, ip: str = "") -> dict:
    """Create a job on every ACTIVE member of the fleet. Members are host-only, so
    write-classification uses the host readonly heuristic; the agent (Landlock +
    fleet-scoped approvals) is the final gate for approved-mode writes."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    command = (body.get("command") or "").strip()
    if not command:
        return _err("command required")
    if len(command) > 4096:
        return _err("command too long (max 4096 characters)", 400)

    fleet = _resolve_fleet(user, fleet_id)
    if not fleet:
        return _err("fleet not found", 404)
    if not can_write_fleet(user, fleet):
        return _err("you have read-only access to this fleet", 403)
    if _is_blocked(command):
        return _err("command is blocked by safety policy", 403)
    max_targets, mt_err = parse_max_targets(body.get("max_targets"))
    if mt_err:
        return _err(mt_err, 400)

    # Idempotency: a key maps to a deterministic run_id, so a retried fan-out (agents
    # retry on timeout) reuses the same run instead of dispatching a second time.
    idem = (body.get("idempotency_key") or "").strip() or None
    if idem:
        run_id = deterministic_run_id(user["tenant_id"], idem)
        existing = runs_repo.get(run_id)
        if existing:
            return _ok({
                "fleet_id": fleet_id, "fleet_name": fleet.get("name"), "command": command,
                "run_id": run_id, "dispatched": existing.get("dispatched", 0), "deduplicated": True,
                "state": existing.get("state"), "counts": existing.get("counts"),
                "skipped": [],
            })
    else:
        run_id = "run_" + secrets.token_urlsafe(12)

    now = _now()
    is_write = _is_readonly_blocked(command)
    # Mode is a fleet property (members inherit it uniformly), so a write to a read-only
    # fleet is a fleet-level "no" - reject it upfront with a clear reason instead of
    # silently skipping every member. (The agent is still the authoritative gate; this
    # heuristic just avoids dispatching an obvious write to a read-only fleet.)
    if is_write and fleet.get("mode") == "readonly":
        return _err("this fleet is read-only; write commands are rejected", 409)

    # Fleets are host-only. A WRITE is structured into an argv (execve, no shell) so approval
    # is by JSON host rule; a shell/pipe write can't be a rule, so it's rejected in an
    # approved-mode fleet (unapprovable) and runs freeform in a wild fleet. READS run as-is.
    argv = None
    if is_write:
        if needs_shell(command):
            if fleet.get("mode") == "approved":
                return _err("write commands can't use shell operators (| ; && $() ` > < * ?) "
                            "in an approved-mode fleet - it can't be approved as a structured "
                            "rule; run a single command per job", 400)
            # wild fleet -> freeform (argv stays None)
        else:
            argv = to_argv(command)

    # Eligible members (active). Inactive ones are recorded as skipped so it's clear why
    # they didn't run. Every eligible member runs - in waves of the fan-out cap.
    eligible: list = []
    skipped: list = []
    for a in _members(user["tenant_id"], fleet_id):
        if a.get("status") != "ACTIVE":
            skipped.append({"agent_id": a["agent_id"], "hostname": a.get("hostname"),
                            "reason": f"not active ({a.get('status')})"})
            continue
        eligible.append(a)

    # Wave size = the fan-out cap (fleet max_fanout, else the tenant's fanout_cap); a policy
    # concurrency or per-call max_targets may lower it, never raise it. EVERY eligible member
    # runs, across waves of this size - there is no capping/dropping. The wave policy (or the
    # defaults) sets how the rollout advances (auto/manual) and handles failures (stop/continue).
    tenant = tenants_repo.get(user["tenant_id"])
    # A fleet's max_fanout can only LOWER the tenant's fan-out cap, never raise it above the
    # tenant-admin's blast-radius ceiling (clamped here in case the tenant cap was later lowered).
    tenant_cap = effective_settings(tenant)["fanout_cap"]
    cap = min(fleet.get("max_fanout") or tenant_cap, tenant_cap)
    ordered, wave_size, lim_err = order_and_limit(eligible, max_targets, cap)
    if lim_err:
        return _err(lim_err, 409)
    meta = resolve_policy(is_write, tenant, "fleet", fleet)
    if meta.get("concurrency"):
        wave_size = min(wave_size, meta["concurrency"])
    wave_sizes, plan_err = plan_waves(len(ordered), {"batch": wave_size})
    if plan_err:
        return _err(plan_err, 400)
    wave_total = len(wave_sizes)
    # Every fan-out is a wave-based run (a small one is just "wave 1 of 1"), so the run
    # always records its rollout plan and advancement policy.
    rollout_plan = {"waves": wave_sizes, "mode": meta["mode"], "on_failure": meta["on_failure"]}

    # Dry run: return the resolved plan (matched members, wave size/strategy/failure policy,
    # approval need) so the CLI/MCP can show an interactive preview before dispatching.
    if body.get("dry_run"):
        return _ok({
            "dry_run": True,
            "fleet_id": fleet_id, "fleet_name": fleet.get("name"), "command": command,
            "mode": fleet.get("mode"),
            "matched": len(ordered),
            "wave_size": wave_size,
            "wave_strategy": meta["mode"],          # auto | manual
            "failure_policy": meta["on_failure"],   # stop | continue
            "wave_total": wave_total,
            "is_write": is_write,
            # A write to an approved-mode fleet needs per-command approval (the agent gates it).
            "approval_required": bool(is_write and fleet.get("mode") == "approved"),
            "skipped": skipped,
        })

    # All jobs from this one fan-out share the run_id (the "run" id) set above.
    created: list = []
    for a, wave in assign_waves(ordered, wave_sizes):
        aid = a["agent_id"]
        job_id = "job_" + secrets.token_urlsafe(16)
        status = "PENDING" if wave == 0 else "HELD"
        jobs_repo.create({
            "job_id": job_id,
            "tenant_id": user["tenant_id"],
            "agent_id": aid,
            "run_id": run_id,
            "run_fleet_id": fleet_id,
            "wave": wave,
            "created_by": user["user_id"],
            "command": command,
            "argv": argv,
            "status": status,
            "stdout": None,
            "stderr": None,
            "exit_code": None,
            "duration_ms": None,
            "created_at": _iso(),
            "started_at": None,
            "completed_at": None,
            "expires_at": now + 604800,
            "mode": a.get("mode", "wild"),
            "is_write": is_write,
        })
        # Only reactivate agents for the wave dispatching now; held waves reactivate
        # their agents when the wave is released.
        if wave == 0:
            agents_repo.set_active_until(aid, now + 120)
        created.append({"agent_id": aid, "hostname": a.get("hostname"), "job_id": job_id,
                        "status": status, "wave": wave})

    dispatched_now = sum(1 for j in created if j["status"] == "PENDING")

    # Record the run as a first-class entity (survives job retention; holds the
    # dispatched/skipped intent + the who/why). Only when something dispatched.
    if created:
        runs_repo.create(new_run_row(
            run_id, user["tenant_id"], user["user_id"], command, _iso(),
            dispatched=len(created), skipped=skipped, fleet_id=fleet_id, idempotency_key=idem,
            rollout=rollout_plan, wave_total=wave_total))
        # One audit event per run (its member jobs link back via run_id) so the trail
        # records who fanned out what command, where, and how wide.
        audit.write("run.dispatched", tenant_id=user["tenant_id"],
                    actor_id=user["user_id"], actor_name=user.get("username"),
                    actor_role=user.get("role"), resource_type="run", resource_id=run_id,
                    metadata={"scope": "fleet", "fleet_id": fleet_id,
                              "fleet_name": fleet.get("name"), "command": command[:200],
                              "dispatched": len(created), "wave_total": wave_total,
                              "is_write": is_write}, ip_address=ip)

    return _ok({
        "fleet_id": fleet_id,
        "fleet_name": fleet.get("name"),
        "command": command,
        "run_id": run_id if created else None,
        "dispatched": dispatched_now,
        "total": len(created),
        "wave_total": wave_total,
        "jobs": created,
        "skipped": skipped,
    }, 201)


# --- Lambda handlers ---------------------------------------------------------

def list_fleets_handler(event, context):
    logger.info("GET /fleets")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_cli_list_fleets(token)


def list_fleet_agents_handler(event, context):
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    logger.info("GET /fleets/%s/agents", fleet_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    limit = None
    if qs.get("limit") is not None:
        try:
            limit = max(1, min(int(qs["limit"]), 100))
        except (ValueError, TypeError):
            limit = 20
    try:
        offset = max(0, int(qs.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0
    return handle_cli_list_fleet_agents(fleet_id, token, q=qs.get("q"), limit=limit, offset=offset)


def list_fleet_approved_handler(event, context):
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    logger.info("GET /fleets/%s/approvals", fleet_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    status = ((event.get("queryStringParameters") or {}).get("status") or "approved")
    return handle_cli_list_fleet_approved(fleet_id, token, status=status)


def list_fleet_runs_handler(event, context):
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    logger.info("GET /fleets/%s/runs", fleet_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return handle_cli_list_fleet_runs(fleet_id, token, limit=limit, cursor=qs.get("cursor") or None)


def fleet_fanout_handler(event, context):
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    logger.info("POST /fleets/%s/jobs", fleet_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_cli_fleet_fanout(fleet_id, body, token, ip)
