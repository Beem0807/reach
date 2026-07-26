"""Tenant admin: manage agents within the tenant."""
import logging
import os
import re
import secrets
from typing import Optional

import shared.audit as audit
from shared.access import can_access_agent, is_agent_restricted
from shared.auth import INSTALL_TOKEN_PREFIX, _hmac_token, _verify_tenant_token
from shared.mode import mode_duration_expiry
from shared.policy import compute_access_level
from shared.response import _err, _iso, _iso_offset, _now, _ok
from shared.store import agent_history_repo, agents_repo, approvals_repo, audit_repo, users_repo
from shared.tags import validate_tags
from shared.versions import available_versions, valid_version

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def _grant_agent_to_user(target: dict, agent_id: str, readonly: bool) -> bool:
    """Append agent_id to a restricted user's read-write (or read-only) agent list,
    keeping the two disjoint. Unrestricted users (readwrite_agent_ids is None - i.e.
    admins) already see every agent, so they're skipped. Returns True if granted."""
    rw = target.get("readwrite_agent_ids")
    if rw is None:
        return False
    rw = list(rw)
    ro = list(target.get("readonly_agent_ids") or [])
    if readonly:
        if agent_id not in ro:
            ro.append(agent_id)
        rw = [a for a in rw if a != agent_id]
    else:
        if agent_id not in rw:
            rw.append(agent_id)
        ro = [a for a in ro if a != agent_id]
    users_repo.set_agent_access(target["user_id"], rw, ro,
                                target.get("readwrite_fleet_ids"), target.get("readonly_fleet_ids"))
    return True


_S3_BASE = os.environ.get("RELEASES_S3_BASE", "https://reach-releases.s3.amazonaws.com")
# The default (unpinned) host install always tracks the newest release under
# agent/latest/; a specific version is chosen per-agent at create time.
_S3_LATEST = f"{_S3_BASE}/agent/latest"
# The Helm chart repo serves index.yaml + reach-agent-<version>.tgz. Chart version
# == appVersion == agent image, released together; the image follows the chart's
# appVersion (no --set image.tag). An unpinned install always takes the newest chart.
_CHART_REPO_URL = os.environ.get("RELEASES_CHART_REPO", f"{_S3_BASE}/charts/reach-agent")
INSTALL_TOKEN_TTL = 86400
VALID_MODES = ("wild", "readonly", "approved")
_ROLE_RANK = {"admin": 3, "operator": 2, "developer": 1}


def _require_role(user: dict, min_role: str) -> bool:
    return _ROLE_RANK.get(user.get("role", "developer"), 0) >= _ROLE_RANK.get(min_role, 0)


def _get_agent(agent_id: str, user: dict) -> Optional[dict]:
    # Enforce tenant boundary and per-user agent scope together. An agent-scoped
    # operator can only manage the agents they're assigned to; out of scope reads as
    # "not found" (same as a read via GET /agents). Admins are always tenant-wide.
    agent = agents_repo.get(agent_id)
    if not agent or agent.get("tenant_id") != user["tenant_id"]:
        return None
    if not can_access_agent(user, agent):
        return None
    return agent


def _build_install_commands(
    api_url: str,
    agent_id: str,
    raw_install_token: str,
    agent_type: str = "host",
    grant_service_mgmt: bool = True,
    grant_docker: bool = False,
    version: Optional[str] = None,
) -> dict:
    # A caller-picked version pins the install; anything unset/invalid/"latest"
    # falls back to the platform default. valid_version enforces a strict format
    # since this value is interpolated straight into a shell command.
    picked = valid_version(version)
    # Kubernetes agents install via Helm; access is controlled by RBAC, so the
    # host-only docker / service-management grants do not apply.
    if agent_type == "k8s":
        version_flag = f" --version {picked}" if picked else ""
        helm = (
            f"helm repo add reach {_CHART_REPO_URL} --force-update && "
            "helm install reach-agent reach/reach-agent "
            "--namespace reach --create-namespace"
            f"{version_flag} "
            f'--set reach.apiUrl="{api_url}" '
            f'--set reach.installToken="{raw_install_token}"'
        )
        return {"helm": helm, "cli_use": f"reach agents use {agent_id}"}

    # Released binaries live under agent/v<version>/ (the `v` prefix); an unpinned
    # install uses agent/latest/.
    base = f"{_S3_BASE}/agent/v{picked}" if picked else _S3_LATEST
    flags = (
        f'--api-url "{api_url}" '
        f'--install-token "{raw_install_token}" '
        f"--yes --force"
    )
    if not grant_service_mgmt:
        flags += " --no-grant-service-mgmt"
    if grant_docker:
        flags += " --grant-docker"
    return {
        "agent": f"curl -fsSL {base}/install.sh | sudo bash -s -- {flags}",
        "cli_use": f"reach agents use {agent_id}",
    }


def handle_create_tenant_agent(body: dict, raw_token: str, api_url: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    # Default to the safest mode: a new agent can look but not touch until an operator
    # deliberately opts into `approved` (writes need sign-off) or `wild` (unrestricted).
    # Defaults become deployments, so we never default to executing writes.
    mode = body.get("mode", "readonly").strip()
    if mode not in VALID_MODES:
        return _err("mode must be wild, readonly, or approved")

    agent_type = (body.get("type") or "host").strip().lower()
    if agent_type not in ("host", "k8s"):
        return _err("type must be host or k8s")

    # Optional pinned version; empty/"latest" installs the platform default.
    version = (body.get("version") or "").strip() or None
    if version and version.lower() != "latest" and valid_version(version) is None:
        return _err("invalid version")

    grant_user_ids = body.get("grant_user_ids") or []            # granted read-write
    grant_readonly_user_ids = body.get("grant_readonly_user_ids") or []  # granted read-only
    for lst, name in ((grant_user_ids, "grant_user_ids"), (grant_readonly_user_ids, "grant_readonly_user_ids")):
        if not isinstance(lst, list) or not all(isinstance(i, str) for i in lst):
            return _err(f"{name} must be a list of user ID strings")
    # Docker / service-management grants are host-only; k8s access is RBAC-driven.
    if agent_type == "k8s":
        grant_service_mgmt = False
        grant_docker = False
    else:
        grant_service_mgmt = bool(body.get("grant_service_mgmt", False))
        grant_docker = bool(body.get("grant_docker", False))

    # Host OS hint (host-only): macOS has no kernel filesystem sandbox (Landlock is Linux-only),
    # so a macOS agent fails closed in readonly/approved. When the creator says it's macOS we
    # pre-acknowledge the exception so it runs unsandboxed from the start (audited; revocable).
    sandbox_ack = False
    if agent_type != "k8s" and (body.get("os") or "").strip().lower() in ("mac", "macos", "darwin"):
        sandbox_ack = True

    agent_id = "agent_" + secrets.token_urlsafe(12)
    raw_install_token = INSTALL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = _now() + INSTALL_TOKEN_TTL

    agents_repo.create({
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "status": "CREATED",
        "type": agent_type,
        "fleet_id": None,
        "mode": mode,
        "install_token_hash": _hmac_token(raw_install_token),
        "install_token_expires_at": expires_at,
        "grant_service_mgmt": grant_service_mgmt,
        "grant_docker": grant_docker,
        "sandbox_ack": sandbox_ack,
        "created_at": _iso(),
    })

    # A restricted creator (a scoped operator) must be able to manage the agent they
    # just created, so grant themselves read-write access. Admins are tenant-wide.
    if is_agent_restricted(user):
        _grant_agent_to_user(user, agent_id, readonly=False)

    # Optionally grant the new agent to specific restricted users, read-only or
    # read-write. Unrestricted users (readwrite_agent_ids is None) already see every
    # agent, so they're skipped. A read-write grant wins over a read-only one.
    granted_user_ids: list = []
    rw_ids = list(dict.fromkeys(grant_user_ids))
    for uid in rw_ids:
        target = users_repo.get(uid)
        if target and target.get("tenant_id") == user["tenant_id"] and _grant_agent_to_user(target, agent_id, readonly=False):
            granted_user_ids.append(uid)
    for uid in dict.fromkeys(grant_readonly_user_ids):
        if uid in rw_ids:
            continue
        target = users_repo.get(uid)
        if target and target.get("tenant_id") == user["tenant_id"] and _grant_agent_to_user(target, agent_id, readonly=True):
            granted_user_ids.append(uid)

    audit.write(
        "agent.created",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"mode": mode, "type": agent_type, "version": version or "latest",
                  "grant_service_mgmt": grant_service_mgmt,
                  "grant_docker": grant_docker, "granted_user_ids": granted_user_ids,
                  "sandbox_ack": sandbox_ack},
    )
    logger.info("Created agent=%s type=%s tenant=%s by user=%s", agent_id, agent_type, user["tenant_id"], user.get("user_id"))
    return _ok({
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "type": agent_type,
        "install_token": raw_install_token,
        "install_token_expires_at": _iso_offset(INSTALL_TOKEN_TTL),
        "mode": mode,
        "commands": _build_install_commands(api_url, agent_id, raw_install_token, agent_type, grant_service_mgmt, grant_docker, version),
    }, 201)


def handle_reissue_tenant_install_token(agent_id: str, body: dict, raw_token: str, api_url: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    if agent.get("fleet_id"):
        return _err("fleet agents enroll via the fleet join token; reissue is not available", 409)

    force = bool(body.get("force", False))
    # Docker / service-mgmt grants are host-only (k8s access is RBAC-driven), so force them
    # off for k8s agents - mirroring create-agent, and matching the console's reissue modal
    # which doesn't offer them for k8s.
    if (agent.get("type") or "host") == "k8s":
        grant_service_mgmt = False
        grant_docker = False
    else:
        grant_service_mgmt = bool(body.get("grant_service_mgmt", False))
        grant_docker = bool(body.get("grant_docker", False))
    version = (body.get("version") or "").strip() or None
    if version and version.lower() != "latest" and valid_version(version) is None:
        return _err("invalid version")

    status = agent.get("status")
    if status == "DELETED":
        return _err("agent is DELETED and cannot be reissued", 409)
    if status == "ACTIVE" and not force:
        return _err(
            'agent is currently ACTIVE - reissuing will disconnect it immediately. '
            'Revoke first, or pass {"force": true} to proceed anyway.',
            409,
        )

    raw_install_token = INSTALL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = _now() + INSTALL_TOKEN_TTL
    now_iso = _iso()
    agents_repo.reissue_install_token(
        agent_id, _hmac_token(raw_install_token), expires_at,
        grant_service_mgmt=grant_service_mgmt, grant_docker=grant_docker,
    )
    agent_history_repo.create({
        "history_id": "agenthistory_" + secrets.token_urlsafe(8),
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "from_status": status,
        "to_status": "CREATED",
        "triggered_by": user.get("user_id"),
        "note": "install token reissued",
        "created_at": now_iso,
    })

    audit.write(
        "agent.install_token_reissued",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"hostname": agent.get("hostname"), "grant_service_mgmt": grant_service_mgmt, "grant_docker": grant_docker},
    )
    logger.info("Reissued install token for agent=%s by user=%s", agent_id, user.get("user_id"))
    return _ok({
        "agent_id": agent_id,
        "install_token": raw_install_token,
        "install_token_expires_at": _iso_offset(INSTALL_TOKEN_TTL),
        "commands": _build_install_commands(
            api_url, agent_id, raw_install_token,
            agent.get("type", "host"), grant_service_mgmt, grant_docker, version,
        ),
    })


def handle_revoke_tenant_agent(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)

    status = agent.get("status")
    if status == "REVOKED":
        return _err("agent is already REVOKED", 409)
    if status == "DELETED":
        return _err("agent is already DELETED", 409)

    agents_repo.set_status(agent_id, "REVOKED")
    users_repo.remove_agent_from_all_users(agent_id, user["tenant_id"])
    agent_history_repo.create({
        "history_id": "agenthistory_" + secrets.token_urlsafe(8),
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "from_status": status,
        "to_status": "REVOKED",
        "triggered_by": user.get("user_id"),
        "note": None,
        "created_at": _iso(),
    })
    audit.write(
        "agent.revoked",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"hostname": agent.get("hostname"), "from_status": status},
    )
    logger.info("Revoked agent=%s by user=%s", agent_id, user.get("user_id"))
    return _ok({"agent_id": agent_id, "status": "REVOKED"})


def handle_delete_tenant_agent(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)

    status = agent.get("status")
    if status == "DELETED":
        return _err("agent is already DELETED", 409)
    if status != "REVOKED":
        return _err(f"agent must be REVOKED before deleting (current: {status})", 409)

    agents_repo.set_status(agent_id, "DELETED")
    agent_history_repo.create({
        "history_id": "agenthistory_" + secrets.token_urlsafe(8),
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "from_status": "REVOKED",
        "to_status": "DELETED",
        "triggered_by": user.get("user_id"),
        "note": None,
        "created_at": _iso(),
    })
    audit.write(
        "agent.deleted",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"hostname": agent.get("hostname")},
    )
    logger.info("Soft-deleted agent=%s by user=%s", agent_id, user.get("user_id"))
    return _ok({"agent_id": agent_id, "status": "DELETED"})


def handle_remove_tenant_agent(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)

    if agent.get("status") != "DELETED":
        return _err(f"agent must be DELETED before removing (current: {agent.get('status')})", 409)

    agents_repo.delete(agent_id)
    # The record is truly gone now - purge its approvals so a future id reuse can't
    # inherit a stale pre-approval.
    approvals_repo.delete_by_agent(agent_id)
    audit.write(
        "agent.removed",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"hostname": agent.get("hostname")},
    )
    logger.info("Permanently removed agent=%s by user=%s", agent_id, user.get("user_id"))
    return _ok({"agent_id": agent_id, "removed": True})


def handle_set_tenant_agent_tags(agent_id: str, body: dict, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    if agent.get("fleet_id"):
        return _err("fleet agents inherit tags from the fleet; set them on the fleet instead", 409)
    tags = body.get("tags", [])
    err = validate_tags(tags)
    if err:
        return _err(err, 400)
    prev_tags = agent.get("tags") or []
    agents_repo.set_tags(agent_id, tags)
    audit.write(
        "agent.tags_changed",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"hostname": agent.get("hostname"), "from": prev_tags, "to": tags},
    )
    return _ok({"agent_id": agent_id, "tags": tags})


def handle_request_agent_rotation(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    if agent.get("status") != "ACTIVE":
        return _err("agent must be ACTIVE to request token rotation", 409)
    agents_repo.request_rotation(agent_id)
    audit.write(
        "agent.rotation_requested",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"hostname": agent.get("hostname")},
    )
    logger.info("Token rotation requested for agent=%s by user=%s", agent_id, user.get("user_id"))
    return _ok({"agent_id": agent_id, "rotation_requested": True})


# Duration parsing lives in shared.mode (shared with the fleet set-mode handler).
_mode_expiry = mode_duration_expiry


def handle_set_tenant_agent_mode(agent_id: str, body: dict, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    mode = body.get("mode", "").strip()
    if mode not in VALID_MODES:
        return _err(f"mode must be one of: {', '.join(VALID_MODES)}")
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    if agent.get("fleet_id"):
        return _err("fleet agents inherit mode from the fleet; change it on the fleet instead", 409)
    prev_mode = agent.get("mode")

    # A `duration` (only meaningful for wild) makes it a TEMPORARY escape hatch that
    # auto-reverts to the previous mode when it elapses. Any non-wild mode, or wild with no
    # duration, is permanent - update_policy(None, None) clears any prior schedule.
    expires_at = None
    revert_to = None
    if mode == "wild":
        ok, expires_at = _mode_expiry(str(body.get("duration") or ""))
        if not ok:
            return _err("invalid duration (use e.g. 1h, 4h, 1d, 1w, or 'permanent')")
        if expires_at:
            # Fall back to the previous safe mode (or readonly if it was already wild).
            revert_to = prev_mode if prev_mode in ("readonly", "approved") else "readonly"

    agents_repo.update_policy(agent_id, mode, mode_expires_at=expires_at, mode_revert_to=revert_to)
    if prev_mode != mode or expires_at:
        audit.write(
            "agent.mode_changed",
            tenant_id=user["tenant_id"],
            actor_id=user.get("user_id", ""),
            actor_name=user.get("username") or user.get("user_id", ""),
            actor_role=user.get("role", ""),
            resource_type="agent",
            resource_id=agent_id,
            metadata={"from_mode": prev_mode, "to_mode": mode, "hostname": agent.get("hostname"),
                      **({"expires_at": expires_at, "revert_to": revert_to} if expires_at else {})},
        )
    logger.info("Set agent=%s mode=%s%s by user=%s", agent_id, mode,
                f" until {expires_at} (revert {revert_to})" if expires_at else "", user.get("user_id"))
    return _ok({"agent_id": agent_id, "mode": mode, "mode_expires_at": expires_at, "mode_revert_to": revert_to})


def handle_acknowledge_capability(agent_id: str, body: dict, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    capability = body.get("capability", "").strip()
    if capability not in ("docker", "service_mgmt", "k8s_permissions"):
        return _err("capability must be docker, service_mgmt, or k8s_permissions")
    # docker/service_mgmt acks set an INDIVIDUAL host grant. Fleet members inherit their
    # grants from the fleet, so acking one here would immediately diverge from the fleet
    # and re-raise a grant mismatch (reconcile → ack → mismatch → reconcile loop). Grants
    # for members are resolved on the fleet (reconcile / accept), not per-agent.
    if agent.get("fleet_id") and capability in ("docker", "service_mgmt"):
        return _err("fleet agents inherit grants from the fleet; manage them on the fleet instead", 409)
    if capability == "docker":
        agents_repo.update_grants(agent_id, grant_docker=True)
        label = "Docker"
    elif capability == "k8s_permissions":
        # Acknowledge the agent's currently-reported RBAC: pin the acked hash to the
        # current one (drift clears until the permissions change again) and snapshot
        # the current permissions as the acknowledged baseline so the console can
        # later diff current vs acknowledged and show *what* drifted.
        cur = agent.get("k8s_permissions_hash")
        if not cur:
            return _err("no reported permissions to acknowledge")
        agents_repo.acknowledge_k8s_permissions(agent_id, cur, agent.get("k8s_permissions"))
        label = "Kubernetes permissions"
    else:
        agents_repo.update_grants(agent_id, grant_service_mgmt=True)
        label = "service management"
    audit.write(
        "agent.capability_acknowledged",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"capability": capability, "label": label, "hostname": agent.get("hostname")},
    )
    logger.info("Acknowledged capability=%s for agent=%s by user=%s", capability, agent_id, user.get("user_id"))
    return _ok({"agent_id": agent_id, "capability": capability, "acknowledged": True})


def handle_acknowledge_sandbox(agent_id: str, body: dict, raw_token: str) -> dict:
    """Acknowledge - or revoke - running readonly/approved WITHOUT the Landlock kernel sandbox on
    a host agent whose kernel lacks it. Acknowledged (`{"acknowledged": true}`) tells the agent to
    run unsandboxed (fail open, at the operator's explicit risk) instead of failing closed;
    `false` reverts to fail-closed. Host-only (k8s has no Landlock). Delivered on the next sync."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    if (agent.get("type") or "host") == "k8s":
        return _err("the sandbox exception is host-only - k8s agents don't use Landlock", 400)
    acknowledged = bool(body.get("acknowledged", True))
    agents_repo.set_sandbox_ack(agent_id, acknowledged)
    audit.write(
        "agent.sandbox_acknowledged" if acknowledged else "agent.sandbox_ack_revoked",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"acknowledged": acknowledged, "hostname": agent.get("hostname"),
                  "landlock_status": agent.get("landlock_status")},
    )
    logger.info("Sandbox ack=%s for agent=%s by user=%s", acknowledged, agent_id, user.get("user_id"))
    return _ok({"agent_id": agent_id, "sandbox_ack": acknowledged})


# Agent-timeline EDITS (audit-log side). These are NOT status transitions - those already live
# in agent_history - so merging the two never double-lists an event.
_AGENT_EDIT_ACTIONS = {
    "agent.mode_changed", "agent.mode_reverted", "agent.tags_changed", "agent.sandbox_acknowledged",
    "agent.sandbox_ack_revoked", "agent.capability_acknowledged", "agent.rotation_requested",
}


def _agent_edit_note(action: str, meta: dict) -> str:
    meta = meta or {}
    if action == "agent.mode_changed":
        base = f"mode {meta.get('from_mode')} → {meta.get('to_mode')}"
        if meta.get("expires_at"):
            base += f" (temporary; reverts to {meta.get('revert_to')})"
        return base
    if action == "agent.mode_reverted":
        return f"mode auto-reverted wild → {meta.get('to_mode')} (temporary window ended)"
    if action == "agent.tags_changed":
        def _fmt(t):
            return ", ".join(str(x) for x in t) if t else "none"
        return f"tags: {_fmt(meta.get('from'))} → {_fmt(meta.get('to'))}"
    if action == "agent.sandbox_acknowledged":
        return "write protection: allowed to run unsandboxed"
    if action == "agent.sandbox_ack_revoked":
        return "write protection: re-required (fail-closed)"
    if action == "agent.capability_acknowledged":
        return f"capability acknowledged: {meta.get('capability') or meta.get('label') or ''}".strip()
    if action == "agent.rotation_requested":
        return "token rotation requested"
    return action.replace("agent.", "").replace("_", " ")


# Fleet config changes a member *inherits* from its fleet (recorded on the fleet,
# not the agent). Surfaced on a member's timeline as `kind: fleet`, read-time, so
# a member's history explains its inherited state without any duplication.
_FLEET_INHERITED_ACTIONS = {"fleet.updated"}


def _merge_history(status_rows: list, audit_rows: list, edit_actions: set, note_fn, limit=50) -> list:
    """One timeline: status transitions (from a *_history repo) + edit events (from the audit
    log), normalized and sorted newest-first. Edits carry the actor + a human note.
    Pass limit=None to skip the final slice (caller sorts/slices after adding more entries)."""
    entries = [
        {"kind": "status", "from_status": h.get("from_status"), "to_status": h.get("to_status"),
         "note": h.get("note"), "by": h.get("triggered_by"), "created_at": h.get("created_at")}
        for h in status_rows
    ]
    for a in audit_rows:
        if a.get("action") in edit_actions:
            entries.append({
                "kind": "edit", "action": a.get("action"),
                "note": note_fn(a.get("action"), a.get("event_metadata") or a.get("metadata")),
                "by": a.get("actor_name") or a.get("actor_id"),
                "created_at": a.get("created_at"),
            })
    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return entries if limit is None else entries[:limit]


def handle_get_agent_history(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    # One timeline: status transitions + edit events (mode/tags/grants/acks) from the audit log.
    status_rows = agent_history_repo.list_by_agent(agent_id, limit=50)
    audit_rows = audit_repo.list_by_tenant(user["tenant_id"], limit=100, resource=agent_id)
    entries = _merge_history(status_rows, audit_rows, _AGENT_EDIT_ACTIONS, _agent_edit_note, limit=None)
    # A fleet member inherits config from its fleet; fold in the fleet's inherited
    # edits since this agent joined so the member's timeline explains its state.
    fleet_id = agent.get("fleet_id")
    if fleet_id:
        from handlers.tenant_fleets import _fleet_history_note  # lazy: avoids an import cycle
        joined = agent.get("created_at") or ""
        fleet_audit = audit_repo.list_by_tenant(user["tenant_id"], limit=100, resource=fleet_id)
        entries += [
            {"kind": "fleet", "action": a.get("action"),
             "note": "via fleet - " + _fleet_history_note(a.get("action"), a.get("event_metadata") or a.get("metadata")),
             "by": a.get("actor_name") or a.get("actor_id"), "created_at": a.get("created_at")}
            for a in fleet_audit
            if a.get("action") in _FLEET_INHERITED_ACTIONS and (a.get("created_at") or "") >= joined
        ]
        entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return _ok({"history": entries[:50]})


def handle_list_agent_versions(agent_type: str, raw_token: str) -> dict:
    """Installable versions for the create dropdown, newest-first. The UI shows
    a "Latest" default on top of these; empty is fine (dropdown = Latest only)."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)
    t = "k8s" if (agent_type or "").strip().lower() == "k8s" else "host"
    return _ok({"type": t, "default": "latest", "versions": available_versions(t)})


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

def _token(event: dict) -> str:
    from shared.auth import _bearer
    return _bearer(event) or ""


def create_tenant_agent_handler(event, context):
    import json
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    api_url = f"https://{(event.get('headers') or {}).get('host', '')}"
    return handle_create_tenant_agent(body, token, api_url)


def reissue_tenant_install_token_handler(event, context):
    import json
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    api_url = f"https://{(event.get('headers') or {}).get('host', '')}"
    return handle_reissue_tenant_install_token(agent_id, body, token, api_url)


def revoke_tenant_agent_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_revoke_tenant_agent(agent_id, token)


def delete_tenant_agent_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_delete_tenant_agent(agent_id, token)


def remove_tenant_agent_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_remove_tenant_agent(agent_id, token)


def set_tenant_agent_tags_handler(event, context):
    import json
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_set_tenant_agent_tags(agent_id, body, token)


def set_tenant_agent_mode_handler(event, context):
    import json
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_set_tenant_agent_mode(agent_id, body, token)


def request_agent_rotation_handler(event, context):
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_request_agent_rotation(agent_id, token)


def acknowledge_capability_handler(event, context):
    import json
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_acknowledge_capability(agent_id, body, token)


def acknowledge_sandbox_handler(event, context):
    import json
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_acknowledge_sandbox(agent_id, body, token)


def agent_history_handler(event, context):
    logger.info("GET /tenant/agents/{agent_id}/history")
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_get_agent_history(agent_id, token)


def list_agent_versions_handler(event, context):
    logger.info("GET /tenant/agent-versions")
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_type = (event.get("queryStringParameters") or {}).get("type", "host")
    return handle_list_agent_versions(agent_type, token)
