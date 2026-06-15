import logging
import os
import secrets

from shared.auth import INSTALL_TOKEN_PREFIX, TENANT_TOKEN_PREFIX, _hmac_token
from shared.response import _err, _iso, _now, _ok
from shared.store import agents_repo, tokens_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOKEN_PEPPER = os.environ["TOKEN_PEPPER"]
S3_BASE = os.environ.get("RELEASES_S3_BASE", "https://reach-releases.s3.amazonaws.com")

INSTALL_TOKEN_TTL = 86400  # 24 hours


def _verify_pepper(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, TOKEN_PEPPER)


def handle_admin_bootstrap(body: dict, raw_token: str, api_url: str) -> dict:
    if not _verify_pepper(raw_token):
        return _err("unauthorized", 401)

    tenant_id = body.get("tenant_id", "").strip()
    hostname = body.get("hostname", "my-machine").strip()
    mode = body.get("mode", "wild").strip()

    if mode not in ("wild", "readonly", "approved"):
        return _err("mode must be wild, readonly, or approved")

    # Create or reuse tenant
    if not tenant_id:
        raw_tenant_token = TENANT_TOKEN_PREFIX + secrets.token_urlsafe(32)
        tenant_id = "tenant_" + secrets.token_hex(8)
        tokens_repo.create({
            "token_hash": _hmac_token(raw_tenant_token),
            "tenant_id": tenant_id,
            "created_at": _iso(),
        })
    else:
        raw_tenant_token = None  # existing tenant — token not re-issued

    # Create agent
    agent_id = "agent_" + secrets.token_urlsafe(12)
    raw_install_token = INSTALL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = _now() + INSTALL_TOKEN_TTL

    agents_repo.create({
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "status": "CREATED",
        "hostname": hostname,
        "mode": mode,
        "approved_commands": [],
        "install_token_hash": _hmac_token(raw_install_token),
        "install_token_expires_at": expires_at,
        "created_at": _iso(),
    })

    agent_flags = (
        f"--api-url \"{api_url}\" "
        f"--agent-id \"{agent_id}\" "
        f"--install-token \"{raw_install_token}\""
    )
    agent_config = (
        f'{{\"api_url\":\"{api_url}\",\"agent_id\":\"{agent_id}\",'
        f'\"install_token\":\"{raw_install_token}\"}}'
    )

    result: dict = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "install_token": raw_install_token,
        "install_token_expires_at": _iso(),
        "mode": mode,
        "commands": {
            "agent_linux": (
                f"curl -fsSL {S3_BASE}/install.sh | sudo bash -s -- {agent_flags}"
            ),
            "agent_mac_arm": (
                f"mkdir -p /tmp/reach-agent\n"
                f"curl -fsSL {S3_BASE}/reach-agent-darwin-arm64 -o /tmp/reach-agent/reach-agent\n"
                f"chmod +x /tmp/reach-agent/reach-agent\n"
                f"echo '{agent_config}' > /tmp/reach-agent/config.json\n"
                f"REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent"
            ),
            "agent_mac_intel": (
                f"mkdir -p /tmp/reach-agent\n"
                f"curl -fsSL {S3_BASE}/reach-agent-darwin-amd64 -o /tmp/reach-agent/reach-agent\n"
                f"chmod +x /tmp/reach-agent/reach-agent\n"
                f"echo '{agent_config}' > /tmp/reach-agent/config.json\n"
                f"REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent"
            ),
            "cli_use": f"reach use {agent_id}",
        },
    }

    if raw_tenant_token:
        result["tenant_token"] = raw_tenant_token
        result["commands"]["cli_login"] = (
            f"reach login --api-url \"{api_url}\" --token \"{raw_tenant_token}\""
        )

    logger.info("Bootstrapped tenant=%s agent=%s", tenant_id, agent_id)
    return _ok(result, 201)


def admin_bootstrap_handler(event, context):
    import json
    logger.info("POST /admin/bootstrap")
    token = (event.get("headers") or {}).get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    headers = event.get("headers") or {}
    host = headers.get("host", "")
    api_url = f"https://{host}" if host else os.environ.get("API_URL", "")
    return handle_admin_bootstrap(body, token, api_url)
