import hmac
import json
import logging
import secrets

from shared.auth import AGENT_TOKEN_PREFIX, _hmac_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_claim(body: dict) -> dict:
    agent_id = body.get("agent_id", "").strip()
    install_token = body.get("install_token", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()
    hostname = body.get("hostname", "").strip()
    agent_version = body.get("agent_version", "").strip()

    if not all([agent_id, install_token, machine_fp]):
        return _err("agent_id, install_token, machine_fingerprint required")

    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    if agent.get("status") != "CREATED":
        return _err("agent already claimed or disabled", 403)
    if _now() > int(agent.get("install_token_expires_at") or 0):
        return _err("install token expired", 403)
    if not hmac.compare_digest(_hmac_token(install_token), agent.get("install_token_hash") or ""):
        return _err("invalid install token", 403)

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
    })

    return _ok({"agent_token": raw_agent_token, "mode": agent.get("mode", "wild")})


def agent_claim_handler(event, context):
    logger.info("POST /agent/claim")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_claim(body)
