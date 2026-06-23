import re
import time
from typing import Optional

from mcp.server.fastmcp import FastMCP

from reach import config as cfg_module
from reach.client import ReachClient

# ---------------------------------------------------------------------------
# Best-effort redaction - mirrors shared/redact.py in the backend.
# Applied here so data already in the DB is also sanitised before Claude sees it.
# ---------------------------------------------------------------------------
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bAKIA[A-Z0-9]{16}\b'), '[AWS_KEY_ID]'),
    (re.compile(r'(?i)(aws_secret_access_key|aws_secret)\s*[=:]\s*\S+'), r'\1=[AWS_SECRET]'),
    (re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', re.DOTALL), '[PRIVATE_KEY_REDACTED]'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b'), '[JWT_REDACTED]'),
    (re.compile(r'(?i)(https?|postgres(?:ql)?|mysql|mongodb|redis|amqp)://[^:@\s]+:[^@\s]+@'), r'\1://[CREDENTIALS_REDACTED]@'),
    (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*'), 'Bearer [TOKEN_REDACTED]'),
    (re.compile(r'(?i)(?<![A-Za-z])(password|passwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token|private[_-]?key|secret[_-]?key|client[_-]?secret|db[_-]?password|database[_-]?password|smtp[_-]?password|token[_-]?pepper|token[_-]?secret)\s*[=:]\s*\S+'), r'\1=[REDACTED]'),
    (re.compile(r'\b(ghp|ghs|gho|github_pat|glpat|xoxb|xoxp)[_-][A-Za-z0-9_-]{10,}\b'), r'\1_[TOKEN_REDACTED]'),
    (re.compile(r'\bAIza[0-9A-Za-z\-_]{35}\b'), '[GOOGLE_API_KEY]'),
    (re.compile(r'\bya29\.[0-9A-Za-z\-_]+\b'), '[GOOGLE_OAUTH_TOKEN]'),
    (re.compile(r'\b(sk_live|sk_test|rk_live)_[A-Za-z0-9]{10,}\b'), r'\1_[STRIPE_KEY]'),
    (re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b'), '[OPENAI_KEY]'),
    (re.compile(r'\bhv[sbr]\.[A-Za-z0-9_-]{10,}\b'), '[VAULT_TOKEN]'),
    (re.compile(r'\bnpm_[A-Za-z0-9]{36}\b'), '[NPM_TOKEN]'),
    (re.compile(r'\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b'), '[SENDGRID_KEY]'),
    (re.compile(r'(?i)(?<![A-Za-z])(key|token|secret|password)\s*[=:]\s*[0-9a-f]{32,}\b'), r'\1=[HEX_SECRET_REDACTED]'),
]


def _redact(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text

mcp = FastMCP(
    "reach",
    instructions=(
        "Use these tools to run commands on remote machines via reach agents.\n\n"
        "INIT: Call get_context at the start of every session. It returns your identity, "
        "the default agent (if configured), and any aliases. Use this to orient yourself "
        "before calling other tools.\n\n"
        "DISCOVERY: If no default agent is set, call list_agents to find available targets. "
        "Each agent has a 'mode' (wild / readonly / approved) and an 'access_level' label "
        "that combines mode with whether the agent runs as root.\n\n"
        "MODES:\n"
        "- wild: all commands run (except a small global blocklist of catastrophic operations).\n"
        "- readonly: write and destructive commands are rejected by the server before the agent "
        "ever receives them. Reads always pass.\n"
        "- approved: reads always run. Write commands only run if pre-approved by an admin. "
        "If a write is not on the approved list the agent blocks it and creates a pending "
        "approval record - the command does NOT run silently.\n\n"
        "ACCESS LEVELS (mode + root privilege combined):\n"
        "- open: wild mode + root. Maximum blast radius - any command runs with full system "
        "privileges. Treat every write or destructive command as irreversible. Always explain "
        "what you are about to do and prefer read-only checks first.\n"
        "- elevated: wild non-root OR approved root. Either no policy gate but limited OS "
        "privileges, or write approval required but approved commands run as root. Still "
        "high-impact - proceed carefully.\n"
        "- managed: approved non-root OR readonly root. Either writes need approval with "
        "limited privileges, or reads-only with root visibility. Moderate risk.\n"
        "- restricted: readonly + non-root. Safest configuration - writes are always rejected "
        "by the server, no root access. Read freely.\n\n"
        "WHEN BLOCKED: if exec_command returns a stderr containing 'Blocked: approval required', "
        "the command needs admin approval. Use list_pending_approvals to see it, and "
        "list_approved_commands to see what is already allowed on that agent.\n\n"
        "EXEC TIPS: exec_command waits for the result. Use get_job only to check a job "
        "submitted earlier with --no-wait. Prefer read-only checks (ps, logs, df) before "
        "write or restart commands. Explain what you are about to do before running "
        "destructive commands."
    ),
)

_TERMINAL = {"SUCCEEDED", "FAILED", "REJECTED", "EXPIRED"}


def _client() -> tuple[ReachClient, str]:
    cfg = cfg_module.load()
    api_url = cfg.get("api_url")
    token = cfg.get("tenant_token")
    if not api_url or not token:
        raise RuntimeError(
            "reach is not configured. Run 'reach login --api-url <url> --api-key <key>' first."
        )
    default_agent = cfg.get("default_agent_id", "")
    return ReachClient(api_url, token), default_agent


@mcp.tool()
def get_context() -> dict:
    """Return your current session context: identity, default agent, and aliases.

    Call this at the start of every session to orient yourself before using other tools.
    Returns who you are authenticated as, which agent is the default target (if any),
    its current mode and access_level, and any configured aliases.
    """
    client, default_agent = _client()
    cfg = cfg_module.load_profile()

    me = client.get_me()
    aliases = cfg.get("aliases") or {}

    result: dict = {
        "user": me,
        "default_agent_id": default_agent or None,
        "aliases": aliases,
    }

    if default_agent:
        try:
            agent = client.get_agent(default_agent)
            result["default_agent"] = {
                "agent_id": agent.get("agent_id"),
                "status": agent.get("status"),
                "hostname": agent.get("hostname"),
                "mode": agent.get("mode"),
                "access_level": agent.get("access_level"),
                "tags": agent.get("tags") or [],
            }
        except Exception:
            result["default_agent"] = {"error": "could not fetch default agent details"}

    return result


@mcp.tool()
def whoami() -> dict:
    """Return the current authenticated user and tenant."""
    client, _ = _client()
    return client.get_me()


@mcp.tool()
def list_agents() -> dict:
    """List all remote agents registered in your reach tenant."""
    client, _ = _client()
    return client.list_agents()


@mcp.tool()
def get_agent(agent_id: str) -> dict:
    """Get the current status and details of a specific agent.

    Args:
        agent_id: The agent ID (e.g. agent_abc123) or alias (e.g. prod).
    """
    client, _ = _client()
    resolved = cfg_module.resolve_agent(agent_id)
    return client.get_agent(resolved)


@mcp.tool()
def exec_command(command: str, agent_id: str = "", timeout: int = 60) -> dict:
    """Execute a shell command on a remote agent and wait for the result.

    Args:
        command: The shell command to run (e.g. 'df -h', 'docker ps').
        agent_id: Agent ID or alias to target. Uses the default agent if omitted.
        timeout: Seconds to wait before giving up (default 60). The job keeps
                 running - use get_job(job_id) to check it later.
    """
    client, default_agent = _client()
    resolved = cfg_module.resolve_agent(agent_id) if agent_id else default_agent
    if not resolved:
        return {
            "error": (
                "No agent specified and no default agent configured. "
                "Call list_agents() to find available agents, then pass agent_id."
            )
        }

    job = client.create_job(resolved, command)
    job_id = job["job_id"]

    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() > deadline:
            return {
                "job_id": job_id,
                "agent_id": resolved,
                "command": command,
                "status": "PENDING",
                "error": (
                    f"Timed out after {timeout}s waiting for the agent. "
                    f"The job is still queued - call get_job('{job_id}') to check later."
                ),
            }
        result = client.get_job(job_id)
        if result.get("status") in _TERMINAL:
            return {
                "job_id": job_id,
                "agent_id": resolved,
                "command": command,
                "status": result["status"],
                "exit_code": result.get("exit_code"),
                "stdout": _redact(result.get("stdout") or ""),
                "stderr": _redact(result.get("stderr") or ""),
                "duration_ms": result.get("duration_ms"),
            }
        time.sleep(2)


@mcp.tool()
def get_job(job_id: str) -> dict:
    """Fetch the result of a previously submitted job.

    Args:
        job_id: The job ID returned by exec_command.
    """
    client, _ = _client()
    result = client.get_job(job_id)
    result["stdout"] = _redact(result.get("stdout"))
    result["stderr"] = _redact(result.get("stderr"))
    return result


@mcp.tool()
def list_history(agent_id: str = "", limit: int = 20) -> dict:
    """List recent jobs submitted to your tenant.

    Args:
        agent_id: Filter by agent ID or alias. Returns all agents if omitted.
        limit: Number of jobs to return (max 100, default 20).
    """
    client, _ = _client()
    resolved = cfg_module.resolve_agent(agent_id) if agent_id else None
    return client.list_jobs(agent_id=resolved, limit=min(limit, 100))


@mcp.tool()
def list_approved_commands(agent_id: str = "") -> dict:
    """List all pre-approved write commands for an agent (approved mode only).

    Returns the full set of commands an admin has approved for this agent.
    Useful to check before attempting a write command - if it is on this list
    it will run; if not, it will be blocked and create a pending approval record.

    Args:
        agent_id: Agent ID or alias to query. Uses the default agent if omitted.
    """
    client, default_agent = _client()
    resolved = cfg_module.resolve_agent(agent_id) if agent_id else default_agent
    if not resolved:
        return {"error": "No agent specified and no default agent configured."}
    return client.list_agent_approved(resolved)


@mcp.tool()
def list_pending_approvals(agent_id: str = "") -> dict:
    """List your pending approval requests - commands that were blocked and are awaiting admin review.

    Use this after a command is blocked to confirm the approval record was created,
    or to check the approval status of earlier blocked attempts.

    Args:
        agent_id: Agent ID or alias. Defaults to the configured default agent.
    """
    client, default_agent = _client()
    resolved = cfg_module.resolve_agent(agent_id) if agent_id else default_agent
    if not resolved:
        return {"error": "no agent specified and no default agent configured"}
    return client.list_agent_approved(resolved, status="pending")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
