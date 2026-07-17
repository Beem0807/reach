import logging
from typing import Optional

from shared.access import can_access_agent, can_write_agent
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import agents_repo, fleets_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _clear_matched_grant_exceptions(tenant_id: str, rows: list) -> None:
    """Strict acceptance: a fleet member's accepted grant-mismatch exception is dropped
    the moment its grants match the fleet's again, so a later return to the *same*
    divergence re-flags (rather than staying silently accepted). Lazy-cleared here on
    read - the only place that reliably sees every member's grants next to its fleet's -
    covering every path uniformly. Costs nothing when no member carries an exception."""
    if not any(a.get("grants_exception") for a in rows):
        return
    fleets = {f["fleet_id"]: f for f in fleets_repo.list_by_tenant(tenant_id)}
    for a in rows:
        fl = fleets.get(a.get("fleet_id") or "")
        if not a.get("grants_exception") or not fl:
            continue
        matches = (bool(a.get("grant_service_mgmt")) == bool(fl.get("grant_service_mgmt"))
                   and bool(a.get("grant_docker")) == bool(fl.get("grant_docker")))
        if matches:
            agents_repo.set_grants_exception(a["agent_id"], None)
            a["grants_exception"] = None


def _matches_query(a: dict, q: str) -> bool:
    """Free-text match over hostname, agent id, and tags (case-insensitive)."""
    return (q in (a.get("hostname") or "").lower()
            or q in (a.get("agent_id") or "").lower()
            or any(q in t.lower() for t in (a.get("tags") or [])))


def _project(a: dict, user: dict) -> dict:
    return {
        "agent_id": a["agent_id"],
        "status": a.get("status"),
        "hostname": a.get("hostname"),
        "agent_version": a.get("agent_version"),
        "created_at": a.get("created_at"),
        "claimed_at": a.get("claimed_at"),
        "last_heartbeat_at": a.get("last_heartbeat_at"),
        "token_issued_at": a.get("token_issued_at"),
        "install_token_expires_at": a.get("install_token_expires_at"),
        "active_until": a.get("active_until"),
        "fleet_id": a.get("fleet_id"),
        "type": a.get("type"),
        "mode": a.get("mode", "wild"),
        "access_level": a.get("access_level") or "open",
        # Whether *this* user may run write commands on the agent (read-only
        # grant → false). Separate from the agent's own mode/access_level.
        "writable": can_write_agent(user, a),
        "running_as_root": a.get("running_as_root"),
        "k8s_permissions_reported": bool(a.get("k8s_permissions_hash")),
        "k8s_permissions_drift": a.get("k8s_permissions_drift", False),
        "k8s_permissions": a.get("k8s_permissions"),  # RBAC snapshot for the detail view (k8s only)
        "k8s_permissions_acked": a.get("k8s_permissions_acked"),  # acknowledged baseline, for the drift diff
        "k8s_allowed_binaries": a.get("k8s_allowed_binaries"),  # self-reported exec allowlist; warns on approving a binary the agent won't run
        "tags": a.get("tags") or [],
        "grant_service_mgmt": a.get("grant_service_mgmt", False),
        "grant_docker": a.get("grant_docker", False),
        "service_mgmt_detected": a.get("service_mgmt_detected"),
        "docker_detected": a.get("docker_detected"),
        # Accepted fleet grant-mismatch exception signature (null = none); lets the
        # console suppress the mismatch flag for members deliberately left divergent.
        "grants_exception": a.get("grants_exception"),
    }


def handle_list_agents(raw_token: str, tag: Optional[str] = None, q: Optional[str] = None,
                       mode: Optional[str] = None, access: Optional[str] = None,
                       agent_type: Optional[str] = None, fleet: Optional[str] = None,
                       limit: Optional[int] = None, offset: int = 0) -> dict:
    """List accessible agents. Filters (all applied server-side, over the full tenant
    set, so pagination walks the *filtered* results):
      - `tag`   comma-separated; matches an agent carrying **any** of the tags
      - `q`     free-text over hostname / id / tags
      - `mode`  exact agent mode (wild/approved/readonly)
      - `access` exact access_level (open/restricted)
      - `agent_type` exact type (host/k8s)
      - `fleet` a fleet_id, or "__none__" for standalone (fleet-less) agents

    Pagination is **opt-in**: pass `limit` and the console gets one page plus a `total`
    and an `all_tags` facet (offset-based, like /tenant/approvals); omit `limit` and it
    returns every match (the CLI/MCP `list_agents` rely on this).

    `all_tags` is computed over every agent the user can see - independent of the active
    filters/page - so the console's tag dropdown lists the full tag universe, never just
    what happens to be on the current page."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    rows = agents_repo.list_by_tenant(user["tenant_id"])
    _clear_matched_grant_exceptions(user["tenant_id"], rows)

    # Base set: non-deleted agents this user may access. Both the tag facet and the
    # filters below derive from this, so options and results stay within their reach.
    visible = [a for a in rows if a.get("status") != "DELETED" and can_access_agent(user, a)]

    tags_wanted = {t.strip() for t in (tag or "").split(",") if t.strip()}
    ql = (q or "").strip().lower() or None

    def _keep(a: dict) -> bool:
        if tags_wanted and not (tags_wanted & set(a.get("tags") or [])):
            return False
        if mode and a.get("mode", "wild") != mode:
            return False
        if access and (a.get("access_level") or "open") != access:
            return False
        if agent_type and (a.get("type") or "host") != agent_type:
            return False
        if fleet == "__none__" and a.get("fleet_id"):
            return False
        if fleet and fleet != "__none__" and a.get("fleet_id") != fleet:
            return False
        if ql is not None and not _matches_query(a, ql):
            return False
        return True

    matched = [a for a in visible if _keep(a)]
    total = len(matched)
    if limit is not None:
        offset = max(0, offset)
        limit = max(1, min(limit, 100))
        matched = matched[offset:offset + limit]

    agents = [_project(a, user) for a in matched]
    result: dict = {"agents": agents}
    if limit is not None:
        all_tags = sorted({t for a in visible for t in (a.get("tags") or [])})
        result.update(total=total, limit=limit, offset=offset, all_tags=all_tags)
    return _ok(result)


def list_agents_handler(event, context):
    logger.info("GET /agents")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    tag = qs.get("tag")
    q = qs.get("q")
    limit = None
    offset = 0
    if "limit" in qs:
        try:
            limit = int(qs.get("limit"))
            offset = int(qs.get("offset") or 0)
        except (ValueError, TypeError):
            return _err("limit and offset must be integers", 400)
    return handle_list_agents(token, tag, q=q, mode=qs.get("mode"), access=qs.get("access"),
                              agent_type=qs.get("type"), fleet=qs.get("fleet"),
                              limit=limit, offset=offset)
