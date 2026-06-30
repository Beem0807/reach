import json
import logging
import secrets

from shared.auth import AGENT_TOKEN_PREFIX, _hmac_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agent_history_repo, agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_claim(body: dict) -> dict:
    install_token = body.get("install_token", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()
    hostname = body.get("hostname", "").strip()
    agent_version = body.get("agent_version", "").strip()
    agent_type = (body.get("type") or "").strip().lower()

    if not all([install_token, machine_fp]):
        return _err("install_token, machine_fingerprint required")

    # The agent self-reports its environment: "k8s" in a cluster, else "host".
    # The agent record itself is the cluster's identity - we store no cluster id.
    if agent_type not in ("k8s", "host"):
        agent_type = "host"

    # Credential-only: the install token identifies the agent. We look it up by
    # the token hash, so the agent never sends an agent_id. The unique-hash lookup
    # is itself the token check; we still enforce one-time-use and expiry.
    agent = agents_repo.get_by_install_token_hash(_hmac_token(install_token))
    if not agent:
        return _err("invalid install token", 403)
    agent_id = agent["agent_id"]
    if agent.get("status") != "CREATED":
        return _err("agent already claimed or disabled", 403)
    if _now() > int(agent.get("install_token_expires_at") or 0):
        return _err("install token expired", 403)

    # Bind the install token to the type the agent was created for: a k8s agent's
    # token cannot be redeemed by the host installer, or vice-versa. The created
    # type is authoritative - it drove the install command shown and the agent's
    # RBAC (k8s) / capability grants (host), so a mismatch is a misuse.
    created_type = (agent.get("type") or "host").strip().lower()
    if agent_type != created_type:
        return _err(f"install token is for a '{created_type}' agent, not '{agent_type}'", 403)

    raw_agent_token = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    now_iso = _iso()

    agents_repo.claim(agent_id, {
        "agent_token_hash": _hmac_token(raw_agent_token),
        "machine_fingerprint": machine_fp,
        "hostname": hostname,
        "agent_version": agent_version,
        "claimed_at": now_iso,
        "active_until": _now() + 120,
        "token_issued_at": now_iso,
        "type": created_type,
    })

    agent_history_repo.create({
        "history_id": "agenthistory_" + secrets.token_urlsafe(8),
        "agent_id": agent_id,
        "tenant_id": agent.get("tenant_id", ""),
        "from_status": "CREATED",
        "to_status": "ACTIVE",
        "triggered_by": "agent",
        "note": hostname or None,
        "created_at": now_iso,
    })

    return _ok({"agent_token": raw_agent_token, "mode": agent.get("mode", "wild")})


def agent_claim_handler(event, context):
    logger.info("POST /agent/claim")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_claim(body)
