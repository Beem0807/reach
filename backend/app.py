import json
import logging
import os
import re
import time
import hmac
import hashlib
import secrets
from decimal import Decimal
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)

import boto3
from boto3.dynamodb.conditions import Key as DKey, Attr
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# DynamoDB clients
# ---------------------------------------------------------------------------
_ddb = boto3.resource("dynamodb")

TABLE_AGENTS = _ddb.Table("reach-agents")
TABLE_TOKENS = _ddb.Table("reach-tenant-tokens")
TABLE_JOBS = _ddb.Table("reach-jobs")

TOKEN_PEPPER = os.environ["TOKEN_PEPPER"]

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------
AGENT_TOKEN_PREFIX = "agent_"
TENANT_TOKEN_PREFIX = "tok_"
INSTALL_TOKEN_PREFIX = "install_"


def _hmac_token(raw: str) -> str:
    return hmac.new(
        TOKEN_PEPPER.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def _verify_tenant_token(raw: str):
    """Return tenant token record or None."""
    token_hash = _hmac_token(raw)
    resp = TABLE_TOKENS.get_item(Key={"token_hash": token_hash})
    return resp.get("Item")


def _verify_agent_token(raw: str, agent_id: str):
    """Return agent record if token matches, else None."""
    resp = TABLE_AGENTS.get_item(Key={"agent_id": agent_id})
    item = resp.get("Item")
    if not item:
        return None
    token_hash = _hmac_token(raw)
    if not hmac.compare_digest(token_hash, item.get("agent_token_hash", "")):
        return None
    return item


def _bearer(event) -> "str | None":
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


# ---------------------------------------------------------------------------
# Critical command blocklist
# ---------------------------------------------------------------------------
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=",
    r":\(\)\{\s*:\|:\s*&\s*\}",   # fork bomb
    r"shutdown",
    r"reboot",
    r"poweroff",
    r"init\s+0",
    r"init\s+6",
]


def _is_blocked(command: str) -> bool:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


READONLY_BLOCKED = [
    # File operations
    r"\brm\b", r"\bmv\b", r"\bcp\b(?=.*\s/)",
    r"\bchmod\b", r"\bchown\b", r"\bchattr\b",
    r"\btruncate\b", r"\bshred\b", r"\bwipe\b",
    r"\bln\b",
    r"\btee\b",
    r"\bsed\b.*\s-[a-zA-Z]*i",                  # sed -i in-place edit
    r">\s*\S+",                                   # output redirect (> and >>)

    # Process control
    r"\bkill\b", r"\bkillall\b", r"\bpkill\b",

    # System power / init
    r"\breboot\b", r"\bshutdown\b", r"\bpoweroff\b",

    # Service management
    r"\bsystemctl\s+(start|stop|restart|enable|disable|mask|unmask)\b",
    r"\bservice\s+\S+\s+(start|stop|restart|reload)\b",

    # Containers
    r"\bdocker\s+(start|stop|restart|rm|kill|exec|run|pull|build|push|rmi)\b",
    r"\bdocker-compose\s+(up|down|restart|pull|rm)\b",
    r"\bkubectl\s+(apply|delete|create|replace|patch|scale|rollout|exec|run)\b",

    # Package managers
    r"\bapt(-get)?\s+(install|remove|purge|upgrade|autoremove)\b",
    r"\byum\s+(install|remove|update|erase)\b",
    r"\bdnf\s+(install|remove|update|erase)\b",
    r"\bpacman\s+-[A-Za-z]*[SR]\b",
    r"\bapk\s+(add|del|upgrade)\b",
    r"\bsnap\s+(install|remove|refresh)\b",
    r"\bflatpak\s+(install|remove|update)\b",
    r"\bbrew\s+(install|uninstall|upgrade|remove)\b",
    r"\bpip3?\s+install\b",
    r"\bnpm\s+(install|uninstall|update)\b",
    r"\byarn\s+(add|remove|upgrade|install)\b",
    r"\bgem\s+(install|uninstall|update)\b",
    r"\bcargo\s+install\b",

    # File download / execution
    r"\bcurl\b.*\s-[a-zA-Z]*o\b", r"\bwget\b",

    # Disk / filesystem
    r"\bdd\b", r"\bmkfs\b",
    r"\bfdisk\b", r"\bparted\b", r"\bgdisk\b",
    r"\bmount\b", r"\bumount\b",

    # Networking / firewall
    r"\biptables\b", r"\bip6tables\b",
    r"\bufw\s+(allow|deny|enable|disable|delete|reject)\b",

    # User / auth management
    r"\buseradd\b", r"\buserdel\b", r"\busermod\b",
    r"\bgroupadd\b", r"\bgroupdel\b",
    r"\bpasswd\b",
    r"\bsu\b",

    # Scheduled jobs
    r"\bcrontab\b",

    # Privilege escalation
    r"\bsudo\b",
]


def _is_readonly_blocked(command: str) -> bool:
    for pattern in READONLY_BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def _is_approved(command: str, approved_commands: list) -> bool:
    cmd = command.strip()
    return any(cmd.startswith(allowed.strip()) for allowed in approved_commands)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------
def _now() -> int:
    return int(time.time())


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
def _ok(body: dict, status: int = 200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def _err(msg: str, status: int = 400):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_agent_claim(body: dict) -> dict:
    agent_id = body.get("agent_id", "").strip()
    install_token = body.get("install_token", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()
    hostname = body.get("hostname", "").strip()
    agent_version = body.get("agent_version", "").strip()

    if not all([agent_id, install_token, machine_fp]):
        return _err("agent_id, install_token, machine_fingerprint required")

    resp = TABLE_AGENTS.get_item(Key={"agent_id": agent_id})
    agent = resp.get("Item")

    if not agent:
        return _err("agent not found", 404)
    if agent.get("status") != "CREATED":
        return _err("agent already claimed or disabled", 403)
    if _now() > int(agent.get("install_token_expires_at", 0)):
        return _err("install token expired", 403)

    token_hash = _hmac_token(install_token)
    if not hmac.compare_digest(token_hash, agent.get("install_token_hash", "")):
        return _err("invalid install token", 403)

    # Generate agent_token
    raw_agent_token = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    agent_token_hash = _hmac_token(raw_agent_token)

    TABLE_AGENTS.update_item(
        Key={"agent_id": agent_id},
        UpdateExpression=(
            "SET #st = :s, agent_token_hash = :h, machine_fingerprint = :fp,"
            " hostname = :hn, agent_version = :av, claimed_at = :ca,"
            " active_until = :au, last_heartbeat_at = :hb"
        ),
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":s": "ACTIVE",
            ":h": agent_token_hash,
            ":fp": machine_fp,
            ":hn": hostname,
            ":av": agent_version,
            ":ca": _iso(),
            ":au": _now() + 120,
            ":hb": _iso(),
        },
    )

    return _ok({"agent_token": raw_agent_token, "mode": "wild"})


def handle_agent_sync(body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()

    if not agent_id or not machine_fp:
        return _err("agent_id and machine_fingerprint required")

    agent = _verify_agent_token(raw_token, agent_id)
    if not agent:
        return _err("unauthorized", 401)

    agent_status = agent.get("status")
    if agent_status not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    now = _now()
    active_until = int(agent.get("active_until", 0))
    next_poll = 5 if active_until > now else 30

    # Record heartbeat; auto-reactivate if the cron marked this agent INACTIVE
    update_expr = "SET last_heartbeat_at = :hb"
    expr_names: dict = {}
    expr_values: dict = {":hb": _iso()}
    if agent_status == "INACTIVE":
        update_expr += ", #st = :active"
        expr_names["#st"] = "status"
        expr_values[":active"] = "ACTIVE"
        logger.info("Auto-reactivating agent %s", agent_id)

    update_kwargs: dict = {
        "Key": {"agent_id": agent_id},
        "UpdateExpression": update_expr,
        "ExpressionAttributeValues": expr_values,
    }
    if expr_names:
        update_kwargs["ExpressionAttributeNames"] = expr_names
    TABLE_AGENTS.update_item(**update_kwargs)

    # Find a PENDING job for this agent
    resp = TABLE_JOBS.query(
        IndexName="agent-status-index",
        KeyConditionExpression=DKey("agent_id").eq(agent_id) & DKey("status").eq("PENDING"),
        Limit=1,
    )
    items = resp.get("Items", [])

    jobs_payload = []
    if items:
        job = items[0]
        job_id = job["job_id"]
        # Atomically mark RUNNING
        try:
            TABLE_JOBS.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #st = :r, started_at = :sa",
                ConditionExpression="#st = :p",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":r": "RUNNING",
                    ":p": "PENDING",
                    ":sa": _iso(),
                },
            )
            jobs_payload.append({
                "job_id": job_id,
                "command": job["command"],
                "mode": job.get("mode", "wild"),
            })
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    return _ok({"jobs": jobs_payload, "next_poll_seconds": next_poll})


def handle_agent_job_result(job_id: str, body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    machine_fp = body.get("machine_fingerprint", "").strip()
    status = body.get("status", "").strip()
    exit_code = body.get("exit_code")
    stdout = body.get("stdout", "")
    stderr = body.get("stderr", "")
    duration_ms = body.get("duration_ms", 0)

    if status not in ("SUCCEEDED", "FAILED", "REJECTED"):
        return _err("status must be SUCCEEDED, FAILED, or REJECTED")
    if not agent_id or not machine_fp:
        return _err("agent_id and machine_fingerprint required")

    agent = _verify_agent_token(raw_token, agent_id)
    if not agent:
        return _err("unauthorized", 401)
    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    resp = TABLE_JOBS.get_item(Key={"job_id": job_id})
    job = resp.get("Item")
    if not job:
        return _err("job not found", 404)
    if job.get("agent_id") != agent_id:
        return _err("job does not belong to this agent", 403)
    if job.get("status") not in ("RUNNING", "PENDING"):
        return _err(f"job already in terminal state: {job.get('status')}", 409)

    # Truncate output to 50 KB
    max_bytes = 50_000
    if len(stdout.encode()) > max_bytes:
        stdout = stdout.encode()[:max_bytes].decode(errors="replace") + "\n[TRUNCATED]"
    if len(stderr.encode()) > max_bytes:
        stderr = stderr.encode()[:max_bytes].decode(errors="replace") + "\n[TRUNCATED]"

    TABLE_JOBS.update_item(
        Key={"job_id": job_id},
        UpdateExpression=(
            "SET #st = :s, exit_code = :ec, stdout = :out, stderr = :err,"
            " duration_ms = :dur, completed_at = :ca"
        ),
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":ec": exit_code,
            ":out": stdout,
            ":err": stderr,
            ":dur": duration_ms,
            ":ca": _iso(),
        },
    )

    return _ok({"ok": True})


def handle_create_job(body: dict, raw_token: str) -> dict:
    agent_id = body.get("agent_id", "").strip()
    command = body.get("command", "").strip()

    if not agent_id or not command:
        return _err("agent_id and command required")

    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    if _is_blocked(command):
        return _err("command is blocked by safety policy", 403)

    resp = TABLE_AGENTS.get_item(Key={"agent_id": agent_id})
    agent = resp.get("Item")
    if not agent:
        return _err("agent not found", 404)
    if agent.get("tenant_id") != tenant.get("tenant_id"):
        return _err("agent does not belong to your tenant", 403)
    if agent.get("status") != "ACTIVE":
        return _err("agent is not active", 409)

    # Policy enforcement
    mode = agent.get("mode", "wild")
    if mode == "readonly" and _is_readonly_blocked(command):
        return _err("command not permitted in readonly mode", 403)
    if mode == "approved":
        approved = agent.get("approved_commands", [])
        if not _is_approved(command, approved):
            return _err(f"command not in approved list for this agent", 403)

    job_id = "job_" + secrets.token_urlsafe(16)
    now = _now()
    expires_at = now + 604800  # 7 day TTL

    TABLE_JOBS.put_item(Item={
        "job_id": job_id,
        "tenant_id": tenant["tenant_id"],
        "agent_id": agent_id,
        "command": command,
        "status": "PENDING",
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "duration_ms": None,
        "created_at": _iso(),
        "started_at": None,
        "completed_at": None,
        "expires_at": expires_at,
        "mode": mode,
    })

    # Activate agent polling
    TABLE_AGENTS.update_item(
        Key={"agent_id": agent_id},
        UpdateExpression="SET active_until = :au",
        ExpressionAttributeValues={":au": now + 120},
    )

    return _ok({"job_id": job_id, "status": "PENDING"}, 201)


def handle_list_jobs(raw_token: str, agent_id: "str | None", limit: int) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    query_kwargs: dict = {
        "IndexName": "tenant-history-index",
        "KeyConditionExpression": DKey("tenant_id").eq(tenant["tenant_id"]),
        "ScanIndexForward": False,  # newest first
        "Limit": limit,
    }
    if agent_id:
        query_kwargs["FilterExpression"] = Attr("agent_id").eq(agent_id)

    resp = TABLE_JOBS.query(**query_kwargs)

    jobs = [
        {
            "job_id": j["job_id"],
            "agent_id": j["agent_id"],
            "command": j["command"],
            "status": j["status"],
            "exit_code": j.get("exit_code"),
            "duration_ms": j.get("duration_ms"),
            "created_at": j.get("created_at"),
            "completed_at": j.get("completed_at"),
        }
        for j in resp.get("Items", [])
    ]

    return _ok({"jobs": jobs})


def handle_get_job(job_id: str, raw_token: str) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    resp = TABLE_JOBS.get_item(Key={"job_id": job_id})
    job = resp.get("Item")
    if not job:
        return _err("job not found", 404)
    if job.get("tenant_id") != tenant.get("tenant_id"):
        return _err("not found", 404)

    # Check TTL expiry
    if job.get("status") == "PENDING" and _now() > int(job.get("expires_at", 0)):
        TABLE_JOBS.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "EXPIRED"},
        )
        job["status"] = "EXPIRED"

    return _ok({
        "job_id": job["job_id"],
        "agent_id": job["agent_id"],
        "command": job["command"],
        "status": job["status"],
        "exit_code": job.get("exit_code"),
        "stdout": job.get("stdout"),
        "stderr": job.get("stderr"),
        "duration_ms": job.get("duration_ms"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
    })


def handle_list_agents(raw_token: str) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    resp = TABLE_AGENTS.query(
        IndexName="tenant-index",
        KeyConditionExpression=DKey("tenant_id").eq(tenant["tenant_id"]),
    )

    agents = [
        {
            "agent_id": a["agent_id"],
            "status": a.get("status"),
            "hostname": a.get("hostname"),
            "agent_version": a.get("agent_version"),
            "claimed_at": a.get("claimed_at"),
            "mode": a.get("mode", "wild"),
        }
        for a in resp.get("Items", [])
    ]

    return _ok({"agents": agents})


def handle_get_agent(agent_id: str, raw_token: str) -> dict:
    tenant = _verify_tenant_token(raw_token)
    if not tenant:
        return _err("unauthorized", 401)

    resp = TABLE_AGENTS.get_item(Key={"agent_id": agent_id})
    agent = resp.get("Item")
    if not agent:
        return _err("agent not found", 404)
    if agent.get("tenant_id") != tenant.get("tenant_id"):
        return _err("not found", 404)

    return _ok({
        "agent_id": agent["agent_id"],
        "status": agent["status"],
        "hostname": agent.get("hostname"),
        "agent_version": agent.get("agent_version"),
        "machine_fingerprint": agent.get("machine_fingerprint"),
        "claimed_at": agent.get("claimed_at"),
        "last_heartbeat_at": agent.get("last_heartbeat_at"),
        "active_until": agent.get("active_until"),
        "mode": agent.get("mode", "wild"),
        "approved_commands": agent.get("approved_commands", []),
    })


# ---------------------------------------------------------------------------
# Heartbeat checker (invoked by EventBridge schedule)
# ---------------------------------------------------------------------------

def handle_heartbeat_check() -> int:
    """Scan for ACTIVE agents with no heartbeat in the last 5 minutes, mark INACTIVE."""
    cutoff_iso = datetime.fromtimestamp(_now() - 300, tz=timezone.utc).isoformat()

    marked = 0
    scan_kwargs: dict = {
        "FilterExpression": (
            Attr("status").eq("ACTIVE")
            & Attr("last_heartbeat_at").exists()
            & Attr("last_heartbeat_at").lt(cutoff_iso)
        ),
    }

    while True:
        resp = TABLE_AGENTS.scan(**scan_kwargs)
        for agent in resp.get("Items", []):
            try:
                TABLE_AGENTS.update_item(
                    Key={"agent_id": agent["agent_id"]},
                    UpdateExpression="SET #st = :inactive",
                    ConditionExpression="#st = :active",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":inactive": "INACTIVE", ":active": "ACTIVE"},
                )
                logger.info(
                    "Marked agent %s INACTIVE (last_heartbeat_at=%s)",
                    agent["agent_id"],
                    agent.get("last_heartbeat_at"),
                )
                marked += 1
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    return marked


def heartbeat_handler(event, context):
    """EventBridge scheduled entry point."""
    marked = handle_heartbeat_check()
    logger.info("Heartbeat check complete: %d agent(s) marked INACTIVE", marked)
    return {"marked_inactive": marked}


# ---------------------------------------------------------------------------
# Lambda entrypoint
# ---------------------------------------------------------------------------
def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "").upper()
    path = event.get("rawPath", "")
    logger.info("%s %s", method, path)
    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return _err("invalid JSON body")

    raw_token = _bearer(event)
    path_params = event.get("pathParameters") or {}

    # --- Agent routes ---
    if method == "POST" and path == "/agent/claim":
        return handle_agent_claim(body)

    if method == "POST" and path == "/agent/sync":
        if not raw_token:
            return _err("missing Authorization header", 401)
        return handle_agent_sync(body, raw_token)

    if method == "POST" and re.match(r"^/agent/jobs/[^/]+/result$", path):
        job_id = path_params.get("job_id") or path.split("/")[3]
        if not raw_token:
            return _err("missing Authorization header", 401)
        return handle_agent_job_result(job_id, body, raw_token)

    # --- User (CLI) routes ---
    if method == "POST" and path == "/jobs":
        if not raw_token:
            return _err("missing Authorization header", 401)
        return handle_create_job(body, raw_token)

    if method == "GET" and path == "/jobs":
        if not raw_token:
            return _err("missing Authorization header", 401)
        qs = event.get("queryStringParameters") or {}
        agent_filter = qs.get("agent_id")
        try:
            limit = max(1, min(int(qs.get("limit", 20)), 100))
        except (ValueError, TypeError):
            limit = 20
        return handle_list_jobs(raw_token, agent_filter, limit)

    if method == "GET" and re.match(r"^/jobs/[^/]+$", path):
        job_id = path_params.get("job_id") or path.split("/")[2]
        if not raw_token:
            return _err("missing Authorization header", 401)
        return handle_get_job(job_id, raw_token)

    if method == "GET" and path == "/agents":
        if not raw_token:
            return _err("missing Authorization header", 401)
        return handle_list_agents(raw_token)

    if method == "GET" and re.match(r"^/agents/[^/]+$", path):
        agent_id = path_params.get("agent_id") or path.split("/")[2]
        if not raw_token:
            return _err("missing Authorization header", 401)
        return handle_get_agent(agent_id, raw_token)

    return _err("not found", 404)
