"""Tenant admin: manage fleets - reusable-join-token groups of host agents.

A fleet owns one **reusable** join token (unlike the per-agent one-time install
token). Any host that installs with it auto-enrolls into the fleet
(see agent_claim._claim_into_fleet), inheriting the fleet's mode and grants.

Designed for autoscaling groups of any flavour (AWS ASG, GCP MIG, Azure VMSS, or
any autoscaler): you bake the join token into the group's launch/instance template
(user-data or startup script), and every instance that scales in enrolls itself.
Fleets are host-only. The raw token is returned only at create / rotate time.
"""
import json
import logging
import os
import secrets
from typing import Optional

import shared.audit as audit
from shared.access import can_access_fleet, can_write_fleet
from shared.auth import FLEET_TOKEN_PREFIX, _hmac_token, _verify_tenant_token
from shared.settings import effective_settings, validate_fleet_wave_policy, wave_policy_exceeds_cap
from shared.exceptions import NameTakenError
from shared.response import _err, _iso, _iso_offset, _now, _ok
from shared.store import agent_history_repo, agents_repo, approvals_repo, fleets_repo, tenants_repo
from shared.tags import former_fleet_tag, validate_tags
from handlers.tenant_agents import _build_install_commands, _require_role

logger = logging.getLogger()
logger.setLevel(logging.INFO)

VALID_MODES = ("wild", "readonly", "approved")
# When a fleet doesn't set its own reap_after_seconds, members are reaped this
# long after their last heartbeat (agents go INACTIVE after ~45s; this is the
# longer window before the record is deleted). The cleanup job (Phase 2) uses it.
DEFAULT_REAP_AFTER_SECONDS = int(os.environ.get("FLEET_REAP_AFTER_SECONDS", str(30 * 60)))
# A rotated fleet's previous token stays valid this long, so an autoscaler launch
# template can be updated without dropping instances that launch mid-rotation.
ROTATION_GRACE_SECONDS = 24 * 3600


def _fleet_install(api_url: str, raw_join_token: str,
                   grant_service_mgmt: bool, grant_docker: bool) -> str:
    """The host install line to bake into an autoscaler's launch/instance template (user-data or startup script)."""
    return _build_install_commands(
        api_url, "", raw_join_token, "host", grant_service_mgmt, grant_docker
    )["agent"]


def _fleet_cap_error(user: dict, wave_policy, max_fanout):
    """Validate a fleet's fan-out settings against the tenant's blast-radius ceiling:
      - ``max_fanout`` may only LOWER the tenant's fanout_cap, never raise it above it.
      - a fleet wave override's concurrency can't exceed the fleet's effective cap.
    Returns an error string or None."""
    tenant_cap = effective_settings(tenants_repo.get(user["tenant_id"]))["fanout_cap"]
    if max_fanout and max_fanout > tenant_cap:
        return f"max_fanout {max_fanout} cannot exceed the tenant's fan-out cap ({tenant_cap})"
    if wave_policy:
        fleet_cap = max_fanout or tenant_cap
        over = wave_policy_exceeds_cap(wave_policy, fleet_cap)
        if over is not None:
            return f"wave concurrency {over} cannot exceed this fleet's fan-out cap ({fleet_cap})"
    return None


def _fleet_view(fleet: dict, member_count: Optional[int] = None) -> dict:
    view = {
        "fleet_id": fleet["fleet_id"],
        "tenant_id": fleet["tenant_id"],
        "name": fleet.get("name"),
        "type": "host",
        "mode": fleet.get("mode"),
        "grant_service_mgmt": bool(fleet.get("grant_service_mgmt")),
        "grant_docker": bool(fleet.get("grant_docker")),
        "tags": list(fleet.get("tags") or []),
        "status": fleet.get("status"),
        "reap_after_seconds": fleet.get("reap_after_seconds"),
        # Per-fleet blast-radius ceiling for fan-outs (null = deployment default).
        "max_fanout": fleet.get("max_fanout"),
        # Advanced: fleet-level staged-rollout override ({read/write -> {mode, on_failure}}).
        "wave_policy": fleet.get("wave_policy") or None,
        "created_at": fleet.get("created_at"),
    }
    if member_count is not None:
        view["member_count"] = member_count
    return view


def _coerce_positive_int(body: dict, key: str):
    """None/"" -> None (use default); a positive int -> that int; else "invalid"."""
    raw = body.get(key)
    if raw in (None, ""):
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return "invalid"
    return val if val > 0 else "invalid"


def _coerce_reap(body: dict):
    return _coerce_positive_int(body, "reap_after_seconds")


def _get_owned_fleet(fleet_id: str, user: dict) -> Optional[dict]:
    # Same tenant AND the caller can access it: a scoped operator can't act on a fleet
    # they weren't granted, mirroring the access-filtered list (admins are tenant-wide).
    fleet = fleets_repo.get(fleet_id)
    if not fleet or fleet.get("tenant_id") != user["tenant_id"] or not can_access_fleet(user, fleet):
        return None
    return fleet


def _audit(action: str, user: dict, fleet_id: str, meta: dict) -> None:
    audit.write(
        action,
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="fleet",
        resource_id=fleet_id,
        metadata=meta,
    )


def handle_create_fleet(body: dict, raw_token: str, api_url: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    name = (body.get("name") or "").strip()
    if not name:
        return _err("name is required")
    mode = (body.get("mode") or "readonly").strip()
    if mode not in VALID_MODES:
        return _err("mode must be wild, readonly, or approved")
    reap = _coerce_reap(body)
    if reap == "invalid":
        return _err("reap_after_seconds must be a positive integer")
    max_fanout = _coerce_positive_int(body, "max_fanout")
    if max_fanout == "invalid":
        return _err("max_fanout must be a positive integer")
    wave_policy, wp_err = validate_fleet_wave_policy(body.get("wave_policy"))
    if wp_err:
        return _err(wp_err)
    cap_err = _fleet_cap_error(user, wave_policy, max_fanout)
    if cap_err:
        return _err(cap_err)
    grant_service_mgmt = bool(body.get("grant_service_mgmt", False))
    grant_docker = bool(body.get("grant_docker", False))
    tags = body.get("tags") or []
    tag_err = validate_tags(tags)
    if tag_err:
        return _err(tag_err)

    fleet_id = "fleet_" + secrets.token_urlsafe(10)
    raw_join_token = FLEET_TOKEN_PREFIX + secrets.token_urlsafe(32)

    try:
        fleets_repo.create({
            "fleet_id": fleet_id,
            "tenant_id": user["tenant_id"],
            "name": name,
            "mode": mode,
            "grant_service_mgmt": grant_service_mgmt,
            "grant_docker": grant_docker,
            "tags": tags,
            "join_token_hash": _hmac_token(raw_join_token),
            "status": "ACTIVE",
            "reap_after_seconds": reap,
            "max_fanout": max_fanout,
            "wave_policy": wave_policy,
            "created_at": _iso(),
            "created_by": user.get("user_id"),
        })
    except NameTakenError:
        return _err("a fleet with that name already exists", 409)

    _audit("fleet.created", user, fleet_id, {"name": name, "mode": mode})
    logger.info("Created fleet=%s name=%s tenant=%s", fleet_id, name, user["tenant_id"])

    fleet = fleets_repo.get(fleet_id)
    return _ok({
        **_fleet_view(fleet, member_count=0),
        # Shown once - bake this into the autoscaler's launch/instance template.
        "join_token": raw_join_token,
        "install": _fleet_install(api_url, raw_join_token, grant_service_mgmt, grant_docker),
    }, 201)


def _fleet_stats(fleets: list, groups: list) -> dict:
    """Per-fleet {active, inactive, mismatch} from the grouped member facts, so the
    console can render the fleet list (with the grant-mismatch badge) WITHOUT loading
    every member. `mismatch` mirrors the console's flag: a member whose grants differ
    from the fleet and isn't an accepted exception (ACTIVE/INACTIVE only)."""
    by_id = {f["fleet_id"]: f for f in fleets}
    stats = {f["fleet_id"]: {"active": 0, "inactive": 0, "mismatch": 0} for f in fleets}
    for g in groups:
        s = stats.get(g["fleet_id"])
        fl = by_id.get(g["fleet_id"])
        if s is None or fl is None:
            continue
        cnt = int(g.get("count") or 0)
        status = g.get("status")
        if status == "ACTIVE":
            s["active"] += cnt
        elif status == "INACTIVE":
            s["inactive"] += cnt
        if status in ("ACTIVE", "INACTIVE") and _member_grants_mismatched(g, fl) \
                and g.get("grants_exception") != _grants_signature(g, fl):
            s["mismatch"] += cnt
    return stats


def handle_list_fleets(raw_token: str, q: Optional[str] = None,
                       limit: Optional[int] = None, offset: int = 0) -> dict:
    """List accessible fleets with per-fleet member stats. Optional `q` filters by
    fleet name / id (substring). Pagination is **opt-in**: pass `limit` for one page
    plus a `total` (the CLI and the console dropdowns omit it to get every fleet)."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    counts = fleets_repo.member_counts(user["tenant_id"])
    # Access-scoped: admins (tenant-wide) see every fleet; a scoped operator/developer
    # sees only the fleets granted to them (read-write or read-only). This is what
    # feeds the console's fleet dropdowns, so they mirror the caller's access.
    fleets = [f for f in fleets_repo.list_by_tenant(user["tenant_id"]) if can_access_fleet(user, f)]
    ql = (q or "").strip().lower() or None
    if ql:
        fleets = [f for f in fleets
                  if ql in (f.get("name") or "").lower() or ql in (f.get("fleet_id") or "").lower()]
    # Stable order so offset paging is deterministic (name, then id as a tiebreaker).
    fleets.sort(key=lambda f: ((f.get("name") or "").lower(), f.get("fleet_id") or ""))
    total = len(fleets)
    if limit is not None:
        offset = max(0, offset)
        limit = max(1, min(limit, 100))
        fleets = fleets[offset:offset + limit]
    # Grouped aggregation (one query, tiny result) so the console never loads every
    # member just to show active/inactive/mismatch counts on the fleet list.
    stats = _fleet_stats(fleets, agents_repo.fleet_member_groups(user["tenant_id"]))
    result: dict = {
        "fleets": [
            {**_fleet_view(f, member_count=counts.get(f["fleet_id"], 0)),
             "writable": can_write_fleet(user, f),
             "active_count": stats[f["fleet_id"]]["active"],
             "inactive_count": stats[f["fleet_id"]]["inactive"],
             "mismatch_count": stats[f["fleet_id"]]["mismatch"]}
            for f in fleets
        ],
        # The reap interval a fleet inherits when it doesn't set its own.
        "default_reap_after_seconds": DEFAULT_REAP_AFTER_SECONDS,
        # The fan-out blast-radius cap a fleet inherits (tenant setting) when max_fanout is unset.
        "default_max_fanout": effective_settings(tenants_repo.get(user["tenant_id"]))["fanout_cap"],
    }
    if limit is not None:
        result.update(total=total, limit=limit, offset=offset)
    return _ok(result)


def handle_update_fleet(fleet_id: str, body: dict, raw_token: str) -> dict:
    """Edit fleet settings (name, mode, grants, reap_after_seconds). Only the keys
    present in the body are changed; the join token is untouched."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)

    fields: dict = {}
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            return _err("name cannot be empty")
        fields["name"] = name
    if "mode" in body:
        mode = (body.get("mode") or "").strip()
        if mode not in VALID_MODES:
            return _err("mode must be wild, readonly, or approved")
        fields["mode"] = mode
    if "tags" in body:
        tags = body.get("tags") or []
        tag_err = validate_tags(tags)
        if tag_err:
            return _err(tag_err)
        fields["tags"] = tags
    # Host grants (service-mgmt / docker) are baked into the install command, so they
    # can't be flipped on a running host remotely. Editing them here changes what
    # NEW members enroll with (via a re-issued install command) and marks existing
    # members as drifted until the operator reconciles them (acknowledge-grants) -
    # they are NOT auto-pushed, unlike mode/tags. See handle_acknowledge_fleet_grants.
    if "grant_service_mgmt" in body:
        fields["grant_service_mgmt"] = bool(body.get("grant_service_mgmt"))
    if "grant_docker" in body:
        fields["grant_docker"] = bool(body.get("grant_docker"))
    if "reap_after_seconds" in body:
        reap = _coerce_reap(body)
        if reap == "invalid":
            return _err("reap_after_seconds must be a positive integer")
        fields["reap_after_seconds"] = reap
    if "max_fanout" in body:
        mf = _coerce_positive_int(body, "max_fanout")
        if mf == "invalid":
            return _err("max_fanout must be a positive integer")
        fields["max_fanout"] = mf   # null clears it -> falls back to the tenant cap
    if "wave_policy" in body:
        wp, wp_err = validate_fleet_wave_policy(body.get("wave_policy"))
        if wp_err:
            return _err(wp_err)
        fields["wave_policy"] = wp   # null clears it -> falls back to the tenant default
    # Validate the resulting fan-out settings against the tenant's cap (max_fanout can't
    # exceed it; wave concurrency can't exceed the fleet's effective cap).
    if "max_fanout" in fields or "wave_policy" in fields:
        eff_mf = fields.get("max_fanout", fleet.get("max_fanout"))
        eff_wp = fields.get("wave_policy", fleet.get("wave_policy"))
        cap_err = _fleet_cap_error(user, eff_wp, eff_mf)
        if cap_err:
            return _err(cap_err)

    if not fields:
        return _err("no updatable fields provided")

    try:
        fleets_repo.update_settings(fleet_id, fields)
    except NameTakenError:
        return _err("a fleet with that name already exists", 409)

    # Mode and tags are inherited, so an edit propagates to every current member.
    if "mode" in fields:
        agents_repo.set_mode_by_fleet(fleet_id, fields["mode"])
    if "tags" in fields:
        agents_repo.set_tags_by_fleet(fleet_id, fields["tags"])

    _audit("fleet.updated", user, fleet_id, {k: v for k, v in fields.items()})
    logger.info("Updated fleet=%s fields=%s", fleet_id, list(fields))
    updated = fleets_repo.get(fleet_id)
    counts = fleets_repo.member_counts(user["tenant_id"])
    return _ok(_fleet_view(updated, member_count=counts.get(fleet_id, 0)))


def handle_rotate_fleet_token(fleet_id: str, body: dict, raw_token: str, api_url: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)

    # How long the old token stays valid so the autoscaler's launch/instance template can be updated.
    # 0 = invalidate the old token immediately.
    grace = body.get("grace_seconds", ROTATION_GRACE_SECONDS)
    try:
        grace = int(grace)
    except (TypeError, ValueError):
        return _err("grace_seconds must be a non-negative integer")
    if grace < 0:
        return _err("grace_seconds must be a non-negative integer")

    raw_join_token = FLEET_TOKEN_PREFIX + secrets.token_urlsafe(32)
    prev_hash = fleet.get("join_token_hash") if grace > 0 else None
    prev_expires = _now() + grace if prev_hash else None
    fleets_repo.rotate_token(fleet_id, _hmac_token(raw_join_token), prev_hash, prev_expires)

    _audit("fleet.token_rotated", user, fleet_id, {"grace_seconds": grace})
    logger.info("Rotated join token for fleet=%s grace=%ds", fleet_id, grace)
    return _ok({
        "fleet_id": fleet_id,
        "join_token": raw_join_token,
        "install": _fleet_install(
            api_url, raw_join_token,
            bool(fleet.get("grant_service_mgmt")), bool(fleet.get("grant_docker")),
        ),
        "previous_token_valid_until": _iso_offset(grace) if prev_hash else None,
    })


def handle_revoke_fleet(fleet_id: str, body: dict, raw_token: str) -> dict:
    """Revoke a fleet's join token (no new enrollments), and decide what happens to
    its existing members: "keep" detaches them into standalone agents, "remove"
    deletes their records."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)

    members = (body.get("members") or "keep").strip().lower()
    if members not in ("keep", "remove"):
        return _err('members must be "keep" or "remove"')

    fleets_repo.set_status(fleet_id, "REVOKED")
    if members == "remove":
        affected = agents_repo.delete_by_fleet(fleet_id)
        # Members are gone - drop their fleet-scoped approvals too.
        approvals_repo.delete_by_fleet(fleet_id)
    else:
        # Detached members drop the fleet's operational tags (which would otherwise still
        # match tag fan-outs) and keep a single provenance tag instead.
        affected = agents_repo.detach_fleet(fleet_id, tags=[former_fleet_tag(fleet.get("name") or fleet_id)])

    _audit("fleet.revoked", user, fleet_id, {"members": members, "affected": affected})
    logger.info("Revoked fleet=%s members=%s affected=%d", fleet_id, members, affected)
    return _ok({"fleet_id": fleet_id, "status": "REVOKED", "members": members, "affected": affected})


def handle_delete_fleet(fleet_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)

    # A fleet must be revoked first - that step decides the fate of its members, so
    # by the time it's deletable it has none (detached or removed).
    if fleet.get("status") != "REVOKED":
        return _err("revoke the fleet first, then delete it", 409)
    members = fleets_repo.member_counts(user["tenant_id"]).get(fleet_id, 0)
    if members > 0:
        return _err(f"fleet still has {members} member agent(s)", 409)

    fleets_repo.delete(fleet_id)
    # Sweep any approvals still scoped to the fleet (e.g. left behind when members were
    # detached on revoke rather than removed).
    approvals_repo.delete_by_fleet(fleet_id)
    _audit("fleet.deleted", user, fleet_id, {})
    logger.info("Deleted fleet=%s", fleet_id)
    return _ok({"fleet_id": fleet_id, "deleted": True})


def handle_remove_fleet_member(fleet_id: str, agent_id: str, raw_token: str) -> dict:
    """Detach a member from its fleet: it becomes a standalone individual agent -
    it keeps running, stops inheriting the fleet's mode/tags, and regains the
    individual-agent controls. To fully delete it instead, use the normal agent
    revoke -> delete flow. A history entry records the transition."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)
    agent = agents_repo.get(agent_id)
    if not agent or agent.get("tenant_id") != user["tenant_id"] or agent.get("fleet_id") != fleet_id:
        return _err("agent not found in this fleet", 404)

    # Drop the fleet's operational tags (they'd still match tag fan-outs); keep one
    # provenance tag so the now-standalone agent stays identifiable. The exact fleet-id
    # is recorded in the history entry below.
    agents_repo.detach_from_fleet(agent_id, tags=[former_fleet_tag(fleet.get("name") or fleet_id)])
    agent_history_repo.create({
        "history_id": "agenthistory_" + secrets.token_urlsafe(8),
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "from_status": agent.get("status"),
        "to_status": agent.get("status"),
        "triggered_by": user.get("user_id"),
        "note": f"removed from fleet '{fleet.get('name')}' - now an individual agent",
        "created_at": _iso(),
    })
    _audit("fleet.member_detached", user, fleet_id, {"agent_id": agent_id, "hostname": agent.get("hostname")})
    logger.info("Detached member=%s from fleet=%s", agent_id, fleet_id)
    return _ok({"agent_id": agent_id, "detached": True})


def _member_grants_mismatched(agent: dict, fleet: dict) -> bool:
    """A member's grants **mismatch** the fleet when the grants it enrolled with no
    longer match the fleet's desired grants (an operator edited the fleet grants after
    this host enrolled). Host grants are baked in at install, so the divergence
    persists until the host is re-provisioned/replaced and the operator reconciles it.

    Distinct from a *capability* / *RBAC* acknowledge, which accepts observed reality:
    reconciling asserts the host was actually re-provisioned, so it is **verified
    against the host's reported capabilities** (see `_grants_backing_gap`)."""
    return (bool(agent.get("grant_service_mgmt")) != bool(fleet.get("grant_service_mgmt"))
            or bool(agent.get("grant_docker")) != bool(fleet.get("grant_docker")))


def _grants_signature(agent: dict, fleet: dict) -> str:
    """A compact signature of the **(member grants, fleet grants)** pair. A member's
    accepted mismatch exception stores this, so acceptance is scoped to the *exact*
    divergence the operator saw: it auto-invalidates (re-flags) if EITHER the fleet's
    grants OR the member's own grants change afterwards (e.g. a capability-acknowledge
    flips a member grant to a new value that still differs from the fleet)."""
    b = lambda x: "1" if x else "0"
    return (b(agent.get("grant_service_mgmt")) + b(agent.get("grant_docker")) + "-"
            + b(fleet.get("grant_service_mgmt")) + b(fleet.get("grant_docker")))


def _member_mismatch_accepted(agent: dict, fleet: dict) -> bool:
    """True when the operator has explicitly *accepted* this member's grant mismatch for
    its current grants vs the fleet's current grants (an intentional exception, not a
    fix). Any later change on either side re-flags it (see `_grants_signature`)."""
    return bool(agent.get("grants_exception")) and agent.get("grants_exception") == _grants_signature(agent, fleet)


def _member_mismatch_flagged(agent: dict, fleet: dict) -> bool:
    """A member is *flagged* when its grants mismatch the fleet and the divergence
    hasn't been accepted - i.e. it still needs resolving (reconcile or accept)."""
    return _member_grants_mismatched(agent, fleet) and not _member_mismatch_accepted(agent, fleet)


def _grants_backing_gap(agent: dict, fleet: dict) -> Optional[str]:
    """Verification for a reconcile: for every capability the fleet grants **on**, the
    host must actually **report** it (detected) - you can't reconcile a docker grant
    onto a host that doesn't run docker. Returns the first capability the fleet wants
    but the host doesn't report, or None if the host backs the fleet's grants.

    Grants the fleet wants **off** need no detection (removing a grant is just config),
    so they never block. This is what keeps reconcile honest: an operator can't clear
    a mismatch on a host that was never actually re-provisioned."""
    if fleet.get("grant_service_mgmt") and not agent.get("service_mgmt_detected"):
        return "service management"
    if fleet.get("grant_docker") and not agent.get("docker_detected"):
        return "docker"
    return None


def handle_acknowledge_fleet_grants(fleet_id: str, raw_token: str, agent_id: Optional[str] = None) -> dict:
    """**Reconcile** fleet members to the fleet's grants after a grant edit.

    Grants can't be flipped on a running host remotely - the operator updates the
    launch template (re-issued install command) and re-provisions/replaces the hosts
    out of band. Reconciling records that: a mismatched member's grants are set to
    match the fleet, clearing the mismatch. New members already enroll with the current
    grants, so only existing ones are touched.

    It is **verified against detection**: a member is only reconciled if the host
    actually reports the capabilities the fleet grants (`_grants_backing_gap`). Members
    whose host doesn't report a required capability are returned under `blocked`, not
    silently reconciled - so a mismatch can't be cleared on a host that was never fixed.

    Pass `agent_id` to reconcile a single member (e.g. one host you've re-provisioned
    ahead of the rest); omit it to reconcile every mismatched member at once."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)

    sm = bool(fleet.get("grant_service_mgmt"))
    dk = bool(fleet.get("grant_docker"))
    members = [a for a in agents_repo.list_by_fleet(fleet_id) if a.get("fleet_id") == fleet_id and a.get("tenant_id") == user["tenant_id"]]
    if agent_id:
        members = [a for a in members if a["agent_id"] == agent_id]
        if not members:
            return _err("agent not found in this fleet", 404)
    reconciled = 0
    blocked: list = []
    for a in members:
        # Accepted exceptions are intentional - a bulk reconcile leaves them alone.
        if not _member_mismatch_flagged(a, fleet):
            continue
        gap = _grants_backing_gap(a, fleet)
        if gap:
            blocked.append({"agent_id": a["agent_id"], "hostname": a.get("hostname"),
                            "reason": f"host does not report {gap} yet - re-provision it first"})
            continue
        agents_repo.update_grants(a["agent_id"], grant_service_mgmt=sm, grant_docker=dk)
        if a.get("grants_exception"):
            agents_repo.set_grants_exception(a["agent_id"], None)   # matched now - no exception
        reconciled += 1

    _audit("fleet.grants_reconciled", user, fleet_id,
           {"reconciled": reconciled, "blocked": len(blocked), "agent_id": agent_id,
            "grant_service_mgmt": sm, "grant_docker": dk})
    logger.info("Reconciled fleet=%s grants: %d member(s), %d blocked%s",
                fleet_id, reconciled, len(blocked), f" (agent={agent_id})" if agent_id else "")
    return _ok({"fleet_id": fleet_id, "reconciled": reconciled, "blocked": blocked,
                "agent_id": agent_id, "grant_service_mgmt": sm, "grant_docker": dk})


def handle_accept_fleet_grant_mismatch(fleet_id: str, raw_token: str, agent_id: Optional[str] = None) -> dict:
    """**Accept** a fleet member's grant mismatch as an intentional exception, instead
    of reconciling it. The member keeps its real (divergent) grants - nothing is
    falsified - but it stops being flagged as needing resolution. The acceptance is
    recorded against the fleet's current grant signature, so it **auto-re-flags if the
    fleet grants change again**. Re-provisioning the host (grants then match) clears it
    naturally. Use this when a member is deliberately allowed to differ from the fleet.

    Pass `agent_id` for a single member; omit it to accept every flagged member."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    fleet = _get_owned_fleet(fleet_id, user)
    if not fleet:
        return _err("fleet not found", 404)

    members = [a for a in agents_repo.list_by_fleet(fleet_id) if a.get("fleet_id") == fleet_id and a.get("tenant_id") == user["tenant_id"]]
    if agent_id:
        members = [a for a in members if a["agent_id"] == agent_id]
        if not members:
            return _err("agent not found in this fleet", 404)
    accepted = 0
    for a in members:
        if _member_mismatch_flagged(a, fleet):
            # Signature captures this member's grants + the fleet's, so the acceptance
            # only holds for this exact divergence.
            agents_repo.set_grants_exception(a["agent_id"], _grants_signature(a, fleet))
            accepted += 1

    _audit("fleet.grant_mismatch_accepted", user, fleet_id,
           {"accepted": accepted, "agent_id": agent_id})
    logger.info("Accepted fleet=%s grant mismatch for %d member(s)%s",
                fleet_id, accepted, f" (agent={agent_id})" if agent_id else "")
    return _ok({"fleet_id": fleet_id, "accepted": accepted, "agent_id": agent_id})


def handle_resolve_fleet_grants(fleet_id: str, raw_token: str, resolution: Optional[str],
                                agent_id: Optional[str] = None) -> dict:
    """One surface to resolve a fleet member's grant mismatch, two ways:

      - ``resolution="reconcile"`` - push the fleet's grants onto the member (verified
        against detection; members the host hasn't caught up on come back under
        ``blocked``). Fixes the drift forward.
      - ``resolution="accept"``    - keep the member's divergent grants but record the
        mismatch as an intentional exception, so it stops being flagged (re-flags if the
        fleet grants change again).

    Pass ``agent_id`` to resolve a single member; omit it to resolve every flagged one."""
    if resolution == "reconcile":
        return handle_acknowledge_fleet_grants(fleet_id, raw_token, agent_id=agent_id)
    if resolution == "accept":
        return handle_accept_fleet_grant_mismatch(fleet_id, raw_token, agent_id=agent_id)
    return _err('resolution must be "reconcile" or "accept"')


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

def _token(event: dict) -> str:
    from shared.auth import _bearer
    return _bearer(event) or ""


def _api_url(event: dict) -> str:
    return f"https://{(event.get('headers') or {}).get('host', '')}"


def create_fleet_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_create_fleet(body, token, _api_url(event))


def list_fleets_handler(event, context):
    token = _token(event)
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
    return handle_list_fleets(token, q=qs.get("q"), limit=limit, offset=offset)


def update_fleet_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_update_fleet(fleet_id, body, token)


def rotate_fleet_token_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_rotate_fleet_token(fleet_id, body, token, _api_url(event))


def revoke_fleet_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_revoke_fleet(fleet_id, body, token)


def delete_fleet_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    return handle_delete_fleet(fleet_id, token)


def remove_fleet_member_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    params = event.get("pathParameters") or {}
    return handle_remove_fleet_member(params.get("fleet_id", ""), params.get("agent_id", ""), token)


def resolve_fleet_grants_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    fleet_id = (event.get("pathParameters") or {}).get("fleet_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_resolve_fleet_grants(fleet_id, token, body.get("resolution"), agent_id=body.get("agent_id"))
