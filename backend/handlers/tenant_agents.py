"""Tenant admin: manage agents within the tenant."""
import logging
import os
import secrets
from typing import Optional

import shared.audit as audit
from shared.auth import INSTALL_TOKEN_PREFIX, _hmac_token, _verify_tenant_token
from shared.policy import compute_access_level
from shared.response import _err, _iso, _iso_offset, _now, _ok
from shared.store import agent_history_repo, agents_repo, approvals_repo, users_repo
from shared.tags import validate_tags

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_S3_BASE = os.environ.get("RELEASES_S3_BASE", "https://reach-releases.s3.amazonaws.com")
_AGENT_VERSION = os.environ.get("AGENT_VERSION", "latest")
_S3_VERSIONED = f"{_S3_BASE}/agent/{_AGENT_VERSION}"
# Kubernetes release artifact, independent of the host's AGENT_VERSION above.
# The Helm chart repo serves index.yaml + reach-agent-<version>.tgz.
# Single-version model: chart version == appVersion == agent image, released
# together. CHART_VERSION pins the chart via --version; the image then follows the
# chart's appVersion (no --set image.tag). Empty = install the latest chart.
_CHART_REPO_URL = os.environ.get("RELEASES_CHART_REPO", f"{_S3_BASE}/charts/reach-agent")
_CHART_VERSION = os.environ.get("CHART_VERSION", "").strip()
INSTALL_TOKEN_TTL = 86400
VALID_MODES = ("wild", "readonly", "approved")
_ROLE_RANK = {"admin": 3, "operator": 2, "developer": 1}


def _require_role(user: dict, min_role: str) -> bool:
    return _ROLE_RANK.get(user.get("role", "developer"), 0) >= _ROLE_RANK.get(min_role, 0)


def _get_agent(agent_id: str, user: dict) -> Optional[dict]:
    agent = agents_repo.get(agent_id)
    if not agent or agent.get("tenant_id") != user["tenant_id"]:
        return None
    return agent


def _build_install_commands(
    api_url: str,
    agent_id: str,
    raw_install_token: str,
    agent_type: str = "host",
    grant_service_mgmt: bool = True,
    grant_docker: bool = False,
) -> dict:
    # Kubernetes agents install via Helm; access is controlled by RBAC, so the
    # host-only docker / service-management grants do not apply.
    if agent_type == "k8s":
        version_flag = f" --version {_CHART_VERSION}" if _CHART_VERSION else ""
        helm = (
            f"helm repo add reach {_CHART_REPO_URL} --force-update && "
            "helm install reach-agent reach/reach-agent "
            "--namespace reach --create-namespace"
            f"{version_flag} "
            f'--set reach.apiUrl="{api_url}" '
            f'--set reach.installToken="{raw_install_token}"'
        )
        return {"helm": helm, "cli_use": f"reach agents use {agent_id}"}

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
        "agent": f"curl -fsSL {_S3_VERSIONED}/install.sh | sudo bash -s -- {flags}",
        "cli_use": f"reach agents use {agent_id}",
    }


def handle_create_tenant_agent(body: dict, raw_token: str, api_url: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    if not _require_role(user, "operator"):
        return _err("forbidden", 403)

    mode = body.get("mode", "wild").strip()
    if mode not in VALID_MODES:
        return _err("mode must be wild, readonly, or approved")

    agent_type = (body.get("type") or "host").strip().lower()
    if agent_type not in ("host", "k8s"):
        return _err("type must be host or k8s")
    # Docker / service-management grants are host-only; k8s access is RBAC-driven.
    if agent_type == "k8s":
        grant_service_mgmt = False
        grant_docker = False
    else:
        grant_service_mgmt = bool(body.get("grant_service_mgmt", False))
        grant_docker = bool(body.get("grant_docker", False))

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
        "created_at": _iso(),
    })

    audit.write(
        "agent.created",
        tenant_id=user["tenant_id"],
        actor_id=user.get("user_id", ""),
        actor_name=user.get("username") or user.get("user_id", ""),
        actor_role=user.get("role", ""),
        resource_type="agent",
        resource_id=agent_id,
        metadata={"mode": mode, "type": agent_type, "grant_service_mgmt": grant_service_mgmt, "grant_docker": grant_docker},
    )
    logger.info("Created agent=%s type=%s tenant=%s by user=%s", agent_id, agent_type, user["tenant_id"], user.get("user_id"))
    return _ok({
        "agent_id": agent_id,
        "tenant_id": user["tenant_id"],
        "type": agent_type,
        "install_token": raw_install_token,
        "install_token_expires_at": _iso_offset(INSTALL_TOKEN_TTL),
        "mode": mode,
        "commands": _build_install_commands(api_url, agent_id, raw_install_token, agent_type, grant_service_mgmt, grant_docker),
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

    force = bool(body.get("force", False))
    grant_service_mgmt = bool(body.get("grant_service_mgmt", False))
    grant_docker = bool(body.get("grant_docker", False))

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
            agent.get("type", "host"), grant_service_mgmt, grant_docker,
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
    prev_mode = agent.get("mode")
    agents_repo.update_policy(agent_id, mode)
    if prev_mode != mode:
        audit.write(
            "agent.mode_changed",
            tenant_id=user["tenant_id"],
            actor_id=user.get("user_id", ""),
            actor_name=user.get("username") or user.get("user_id", ""),
            actor_role=user.get("role", ""),
            resource_type="agent",
            resource_id=agent_id,
            metadata={"from_mode": prev_mode, "to_mode": mode, "hostname": agent.get("hostname")},
        )
    logger.info("Set agent=%s mode=%s by user=%s", agent_id, mode, user.get("user_id"))
    return _ok({"agent_id": agent_id, "mode": mode})


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
    if capability == "docker":
        agents_repo.update_grants(agent_id, grant_docker=True)
        label = "Docker"
    elif capability == "k8s_permissions":
        # Acknowledge the agent's currently-reported RBAC: pin the acked hash to
        # the current one, so drift clears until the permissions change again.
        cur = agent.get("k8s_permissions_hash")
        if not cur:
            return _err("no reported permissions to acknowledge")
        agents_repo.acknowledge_k8s_permissions(agent_id, cur)
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


def handle_get_agent_history(agent_id: str, raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)
    agent = _get_agent(agent_id, user)
    if not agent:
        return _err("agent not found", 404)
    history = agent_history_repo.list_by_agent(agent_id, limit=50)
    return _ok({"history": history})


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


def agent_history_handler(event, context):
    logger.info("GET /tenant/agents/{agent_id}/history")
    token = _token(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_get_agent_history(agent_id, token)
