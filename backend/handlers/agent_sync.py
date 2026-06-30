import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import shared.audit as audit
from shared.auth import _bearer, _verify_agent_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agent_history_repo, agents_repo, approvals_repo, jobs_repo

TOKEN_MAX_AGE_DAYS = 30

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _audit_capability_changes(agent: dict, docker_detected: Optional[bool], service_mgmt_detected: Optional[bool]) -> None:
    """Write an audit entry only when a detected capability state changes (not on every heartbeat)."""
    for cap, detected, prev_key, granted_key, label in (
        ("docker",       docker_detected,       "docker_detected",       "grant_docker",       "Docker"),
        ("service_mgmt", service_mgmt_detected, "service_mgmt_detected", "grant_service_mgmt", "service management"),
    ):
        if detected is None:
            continue
        prev = agent.get(prev_key)
        granted = bool(agent.get(granted_key, False))
        changed = (prev is None and detected) or (prev is not None and detected != prev)
        if not changed:
            continue
        out_of_band = detected and not granted
        audit.write(
            "agent.capability_detected",
            tenant_id=agent.get("tenant_id"),
            actor_id=agent["agent_id"],
            actor_name=agent.get("hostname") or agent["agent_id"],
            actor_role="agent",
            resource_type="agent",
            resource_id=agent["agent_id"],
            metadata={
                "capability": cap,
                "label": label,
                "detected": detected,
                "previously_detected": prev,
                "granted": granted,
                "out_of_band": out_of_band,
            },
        )
        if out_of_band:
            logger.warning(
                "Out-of-band capability detected: agent=%s capability=%s granted=%s",
                agent["agent_id"], cap, granted,
            )


def handle_agent_sync(body: dict, raw_token: str) -> dict:
    machine_fp = body.get("machine_fingerprint", "").strip()
    agent_version = body.get("agent_version", "").strip() or None
    running_as_root = body.get("running_as_root")  # bool from agent, None if old agent
    docker_detected = body.get("docker_detected")          # bool, None if old agent
    service_mgmt_detected = body.get("service_mgmt_detected")  # bool, None if old agent

    if not machine_fp:
        return _err("machine_fingerprint required")

    # Credential-only: the agent token identifies the agent; no agent_id is sent.
    agent = _verify_agent_token(raw_token)
    if not agent:
        return _err("unauthorized", 401)
    agent_id = agent["agent_id"]

    agent_status = agent.get("status")
    if agent_status not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    token_issued_at = agent.get("token_issued_at")
    if token_issued_at:
        issued = datetime.fromisoformat(token_issued_at)
        if datetime.now(tz=timezone.utc) - issued >= timedelta(days=TOKEN_MAX_AGE_DAYS):
            return _err("token_expired", 403)

    now = _now()
    next_poll = 2 if int(agent.get("active_until") or 0) > now else 15

    docker_detected_bool = docker_detected if isinstance(docker_detected, bool) else None
    service_mgmt_detected_bool = service_mgmt_detected if isinstance(service_mgmt_detected, bool) else None

    reactivating = agent_status == "INACTIVE"
    agents_repo.update_heartbeat(
        agent_id,
        reactivate=reactivating,
        now_iso=_iso(),
        agent_version=agent_version,
        running_as_root=running_as_root if isinstance(running_as_root, bool) else None,
        docker_detected=docker_detected_bool,
        service_mgmt_detected=service_mgmt_detected_bool,
    )

    if reactivating:
        agent_history_repo.create({
            "history_id": "agenthistory_" + secrets.token_urlsafe(8),
            "agent_id": agent_id,
            "tenant_id": agent.get("tenant_id", ""),
            "from_status": "INACTIVE",
            "to_status": "ACTIVE",
            "triggered_by": "heartbeat",
            "note": "heartbeat resumed",
            "created_at": _iso(),
        })
        audit.write(
            "agent.recovered",
            tenant_id=agent.get("tenant_id", ""),
            actor_id=agent_id,
            actor_name=agent.get("hostname") or agent_id,
            actor_role="agent",
            resource_type="agent",
            resource_id=agent_id,
            metadata={"hostname": agent.get("hostname")},
        )
        logger.info("Agent %s recovered (was INACTIVE, now ACTIVE)", agent_id)

    _audit_capability_changes(agent, docker_detected_bool, service_mgmt_detected_bool)

    # k8s effective RBAC: the agent only sends the full rule set on change, so
    # store whatever it sends. Drift vs the acknowledged hash is computed on read.
    k8s_permissions = body.get("k8s_permissions")
    if isinstance(k8s_permissions, dict):
        perm_hash = (k8s_permissions.get("hash") or "").strip()
        if perm_hash and perm_hash != agent.get("k8s_permissions_hash"):
            agents_repo.set_k8s_permissions(agent_id, k8s_permissions, perm_hash)

    pending_jobs = jobs_repo.get_pending_for_agent(agent_id)
    approved_commands: list = []
    if any(j.get("mode") == "approved" for j in pending_jobs):
        approved_commands = [a["command"] for a in approvals_repo.list_by_agent(agent_id, status="approved")]

    jobs_payload = []
    for job in pending_jobs:
        if jobs_repo.set_running(job["job_id"], _iso()):
            mode = job.get("mode", "wild")
            jobs_payload.append({
                "job_id": job["job_id"],
                "command": job["command"],
                "mode": mode,
                "is_write": job.get("is_write", False),
                "approved_commands": approved_commands if mode == "approved" else [],
            })

    if jobs_payload:
        next_poll = 2
    resp: dict = {"jobs": jobs_payload, "next_poll_seconds": next_poll}
    if agent.get("rotation_requested"):
        resp["rotate_token"] = True
    return _ok(resp)


def agent_sync_handler(event, context):
    logger.info("POST /agent/sync")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_sync(body, token)
