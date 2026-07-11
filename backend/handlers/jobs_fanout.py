"""Ad-hoc fan-out: run one command on every standalone agent carrying a tag.

Parallels the fleet fan-out (handlers/cli_fleets.py) but for a loose, tag-selected
set of standalone agents instead of a fleet. All jobs from one call share a
`run_id` so they group as a run.

Fan-out is **type-homogeneous**: a command that spans both host and k8s agents is
nonsensical (a shell command is not a kubectl command), so a tag that matches both
types is rejected unless the caller picks one with `type`. k8s writes are gated at
submission just like `POST /jobs`: an unapproved write in `approved` mode is skipped
(never dispatched), not silently run.
"""
import json
import logging
import secrets

import shared.audit as audit
from shared.access import can_access_agent, can_write_agent, is_agent_restricted
from shared.auth import _bearer, _verify_tenant_token
from shared.fanout import deterministic_run_id, new_run_row, order_and_limit, parse_max_targets, run_summary_view
from shared.settings import effective_settings
from shared.waves import assign_waves, plan_waves, resolve_policy
from shared.policy import _is_blocked, _is_readonly_blocked, is_k8s_command_approved, is_k8s_write
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, approvals_repo, jobs_repo, runs_repo, tenants_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_fanout_by_tag(body: dict, raw_token: str, ip: str = "") -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    tag = (body.get("tag") or "").strip()
    command = (body.get("command") or "").strip()
    want_type = (body.get("type") or "").strip().lower() or None
    if not tag:
        return _err("tag required")
    if not command:
        return _err("command required")
    if len(command) > 4096:
        return _err("command too long (max 4096 characters)", 400)
    if want_type not in (None, "host", "k8s"):
        return _err("type must be host or k8s", 400)
    if _is_blocked(command):
        return _err("command is blocked by safety policy", 403)
    max_targets, mt_err = parse_max_targets(body.get("max_targets"))
    if mt_err:
        return _err(mt_err, 400)

    # Idempotency: a key maps to a deterministic run_id, so a retried fan-out reuses
    # the same run instead of dispatching twice.
    idem = (body.get("idempotency_key") or "").strip() or None
    if idem:
        idem_run_id = deterministic_run_id(user["tenant_id"], idem)
        existing = runs_repo.get(idem_run_id)
        if existing:
            return _ok({
                "tag": tag, "type": want_type, "command": command,
                "run_id": idem_run_id, "dispatched": existing.get("dispatched", 0), "deduplicated": True,
                "state": existing.get("state"), "counts": existing.get("counts"),
                "skipped": [],
            })

    # Candidates: standalone agents the user can see, carrying the tag.
    candidates = [
        a for a in agents_repo.list_by_tenant(user["tenant_id"])
        if a.get("status") != "DELETED"
        and not a.get("fleet_id")
        and tag in (a.get("tags") or [])
        and can_access_agent(user, a)
    ]
    if not candidates:
        return _err(f"no accessible standalone agents with tag '{tag}'", 404)

    # Type-homogeneous: never mix host + k8s in one fan-out.
    present = {(a.get("type") or "host") for a in candidates}
    if want_type is None:
        if len(present) > 1:
            return _err("tag matches both host and k8s agents; pass type=host or type=k8s", 409)
        want_type = present.pop()
    targets = [a for a in candidates if (a.get("type") or "host") == want_type]
    if not targets:
        return _err(f"no {want_type} agents with tag '{tag}'", 404)

    now = _now()
    is_k8s = want_type == "k8s"
    is_write = is_k8s_write(command) if is_k8s else _is_readonly_blocked(command)
    # First pass: who is eligible (active, writable, mode/approval allows it). Second
    # pass caps the blast radius, then we dispatch.
    eligible: list = []
    skipped: list = []
    for a in targets:
        aid = a["agent_id"]
        entry = {"agent_id": aid, "hostname": a.get("hostname")}
        if a.get("status") != "ACTIVE":
            skipped.append({**entry, "reason": f"not active ({a.get('status')})"})
            continue
        if is_write and not can_write_agent(user, a):
            skipped.append({**entry, "reason": "read-only access"})
            continue
        mode = a.get("mode", "wild")
        if mode == "readonly" and is_write:
            skipped.append({**entry, "reason": "readonly mode"})
            continue
        # k8s writes are gated here (backend-side): an unapproved write in approved
        # mode is skipped, never dispatched (the agent would otherwise run it).
        if is_k8s and mode == "approved" and is_write:
            rules = [r["k8s_rule"] for r in approvals_repo.list_by_agent(aid, status="approved") if r.get("k8s_rule")]
            if not is_k8s_command_approved(command, rules):
                skipped.append({**entry, "reason": "not pre-approved (k8s rule)"})
                continue
        eligible.append(a)

    # Tag fan-outs have no fleet, so the wave size = the tenant's fanout_cap (a policy
    # concurrency or per-call max_targets may lower it, never raise it). EVERY eligible
    # agent runs, across waves of this size - there is no capping/dropping. The tenant tag
    # policy (or the defaults) sets advancement (auto/manual) and failure handling.
    tenant = tenants_repo.get(user["tenant_id"])
    cap = effective_settings(tenant)["fanout_cap"]
    ordered, wave_size, lim_err = order_and_limit(eligible, max_targets, cap)
    if lim_err:
        return _err(lim_err, 409)
    meta = resolve_policy(is_write, tenant, "tag")
    if meta.get("concurrency"):
        wave_size = min(wave_size, meta["concurrency"])
    wave_sizes, plan_err = plan_waves(len(ordered), {"batch": wave_size})
    if plan_err:
        return _err(plan_err, 400)
    wave_total = len(wave_sizes)
    # Every fan-out is a wave-based run (a small one is just "wave 1 of 1").
    rollout_plan = {"waves": wave_sizes, "mode": meta["mode"], "on_failure": meta["on_failure"]}

    # Dry run: return the resolved plan so the CLI/MCP can show an interactive preview.
    if body.get("dry_run"):
        return _ok({
            "dry_run": True,
            "tag": tag, "type": want_type, "command": command,
            "matched": len(ordered),
            "wave_size": wave_size,
            "wave_strategy": meta["mode"],          # auto | manual
            "failure_policy": meta["on_failure"],   # stop | continue
            "wave_total": wave_total,
            "is_write": is_write,
            "skipped": skipped,
        })

    run_id = idem_run_id if idem else "run_" + secrets.token_urlsafe(12)
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
            "run_tag": tag,
            "wave": wave,
            "created_by": user["user_id"],
            "command": command,
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
        if wave == 0:
            agents_repo.set_active_until(aid, now + 120)
        created.append({"agent_id": aid, "hostname": a.get("hostname"), "job_id": job_id,
                        "status": status, "wave": wave})

    dispatched_now = sum(1 for j in created if j["status"] == "PENDING")

    if created:
        runs_repo.create(new_run_row(
            run_id, user["tenant_id"], user["user_id"], command, _iso(),
            dispatched=len(created), skipped=skipped, tag=tag, idempotency_key=idem,
            rollout=rollout_plan, wave_total=wave_total))
        # One audit event per run (its member jobs link back via run_id) so the trail
        # records who fanned out what command, across which tag, and how wide.
        audit.write("run.dispatched", tenant_id=user["tenant_id"],
                    actor_id=user["user_id"], actor_name=user.get("username"),
                    actor_role=user.get("role"), resource_type="run", resource_id=run_id,
                    metadata={"scope": "tag", "tag": tag, "type": want_type,
                              "command": command[:200], "dispatched": len(created),
                              "wave_total": wave_total, "is_write": is_write}, ip_address=ip)

    return _ok({
        "tag": tag,
        "type": want_type,
        "command": command,
        "run_id": run_id if created else None,
        "dispatched": dispatched_now,
        "total": len(created),
        "wave_total": wave_total,
        "jobs": created,
        "skipped": skipped,
    }, 201)


def _run_outcome(job: dict) -> str:
    status = job.get("status")
    if status in ("PENDING", "RUNNING"):
        return "pending"
    if status == "SUCCEEDED" and (job.get("exit_code") in (0, None)):
        return "ok"
    return "failed"


def handle_list_tag_runs(raw_token: str, limit: int = 20, cursor: str = None) -> dict:
    """Fan-out **runs** across standalone (non-fleet) agents - tag fan-outs, newest
    first. Reads the runs table (O(runs), not a job scan) and keeps only tag runs
    (fleet runs show under their fleet). Survives job retention.

    Access-scoped: an unrestricted user (admin / tenant-wide) sees every tag run; a
    restricted user sees only the runs they created (a tag run targets a loose set of
    standalone agents, so we scope by creator rather than re-resolving each run's
    now-ephemeral member set).

    Cursor-paginated by created_at over the tenant's runs. A page may show fewer than
    `limit` tag runs (fleet runs in the same window are filtered out), but `next_cursor`
    is set from the raw window so Next keeps paging through every tag run."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    restricted = is_agent_restricted(user)
    rows = runs_repo.list_by_tenant(user["tenant_id"], limit=limit, cursor=cursor)
    tag_runs = [
        run_summary_view(r) for r in rows
        if not r.get("fleet_id") and (not restricted or r.get("created_by") == user["user_id"])
    ]
    next_cursor = rows[-1]["created_at"] if len(rows) == limit and rows else None
    return _ok({"runs": tag_runs, "next_cursor": next_cursor})


def fanout_by_tag_handler(event, context):
    logger.info("POST /jobs/fanout")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    return handle_fanout_by_tag(body, token, ip)


def list_tag_runs_handler(event, context):
    logger.info("GET /jobs/runs")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return handle_list_tag_runs(token, limit=limit, cursor=qs.get("cursor") or None)
