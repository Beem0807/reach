import re
import time
from typing import Optional

import requests
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
        "that combines mode with whether the agent runs as root. Each agent also has a "
        "'writable' flag for YOUR access: if writable is false you have read-only access and "
        "any write command will be rejected (403) - only run read commands on it.\n\n"
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
        "the command needs admin approval - and the block already filed a pending request "
        "automatically. Use list_pending_approvals to confirm it and list_approved_commands to "
        "see what is already allowed, then tell the user an operator/admin must approve it (in "
        "the console or via the reach CLI). You have NO tools to create, approve, or deny "
        "approvals - that review is a human control and must not be done on the user's behalf.\n\n"
        "EXEC TIPS: exec_command waits for the result. Use get_job only to check a job "
        "submitted earlier with --no-wait. Prefer read-only checks (ps, logs, df) before "
        "write or restart commands. Explain what you are about to do before running "
        "destructive commands.\n\n"
        "FLEETS: a fleet is a group of identical hosts. list_fleets / list_fleet_agents / "
        "list_fleet_jobs / list_fleet_runs / list_fleet_run / list_fleet_approved are read-only. "
        "fleet_exec runs a command on EVERY member - in waves of the fan-out cap - and is "
        "confirm-gated: call it first with confirm=false for a dry-run preview (matched members + "
        "how it will roll out: wave size, strategy auto/manual, failure policy stop/continue; "
        "nothing runs), show that to the user, get explicit approval, then call again with "
        "confirm=true. Never pass confirm=true without the user's approval of that exact command "
        "and fleet. Watch a run with run_status and drive a staged one with run_pause / run_resume "
        "/ run_cancel.\n\n"
        "TAG FAN-OUT: exec_by_tag runs a command on every standalone agent with a given tag "
        "(e.g. env:prod), the same way (waves + confirm-gate + preview). High impact - treat like "
        "fleet_exec. list_tag_runs / list_tag_run are the read-only history of those tag fan-outs."
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
                "type": agent.get("type"),
                "hostname": agent.get("hostname"),
                "mode": agent.get("mode"),
                "access_level": agent.get("access_level"),
                "writable": agent.get("writable"),
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
def exec_command(command: str, agent_id: str = "", confirm: bool = False, timeout: int = 60) -> dict:
    """Execute a shell command on a remote agent and wait for the result.

    If the agent's 'writable' flag is false you have read-only access to it: write
    commands are rejected with 403 regardless of the agent's mode, so only run read
    commands. Check writable via get_context / list_agents / get_agent first.

    **Writes are confirm-gated** (like fleet_exec / exec_by_tag): a command classified as a
    write is two-step - call first with confirm=False (default) for a dry-run preview
    (nothing runs), show the user, get explicit approval, then call again with confirm=True.
    Read commands run straight through (confirm is ignored). Never pass confirm=True for a
    write without the user's approval of that exact command.

    Args:
        command: The shell command to run (e.g. 'df -h', 'docker ps').
        agent_id: Agent ID or alias to target. Uses the default agent if omitted.
        confirm: Must be True to actually run a *write*. False (default) returns a preview
                 for writes; reads run regardless.
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

    # Classify first (dry-run - nothing runs, same auth/access/mode gates). Writes need
    # explicit confirm so an AI can't run a destructive single-agent command unapproved.
    try:
        preview = client.create_job(resolved, command, dry_run=True)
    except requests.HTTPError as e:
        resp = e.response
        if resp is not None and resp.content:
            detail = (resp.json() or {}).get("error")
            if detail:
                return {"error": detail}
        raise
    if preview.get("is_write") and not confirm:
        host = preview.get("hostname") or resolved
        mode = preview.get("mode")
        is_host = preview.get("type") != "k8s"
        # For hosts is_write is a best-effort regex; the agent's Landlock sandbox is the
        # real gate for readonly/approved, and wild mode is unsandboxed. For k8s it's exact.
        caveat = (" (host classification is best-effort; the agent's sandbox is the real gate"
                  + (", and wild mode is unsandboxed - writes aren't blocked on the agent" if mode == "wild" else "")
                  + ")") if is_host else ""
        return {
            "preview": True, "confirmed": False, "agent_id": resolved,
            "hostname": preview.get("hostname"), "command": preview.get("command"),
            "is_write": True, "type": preview.get("type"), "mode": mode,
            "approval_required": preview.get("approval_required"),
            "message": (f"DRY RUN - nothing has run. `{command}` is a WRITE command on {host} "
                        f"(mode {mode}"
                        + (", will be queued for approval" if preview.get("approval_required") else "")
                        + f"){caveat}. Show the user, get explicit approval, then call again with confirm=true."),
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
                "stdout_truncated": bool(result.get("stdout_truncated")),
                "stderr_truncated": bool(result.get("stderr_truncated")),
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


def _resolve_fleet_id(client: ReachClient, identifier: str) -> Optional[str]:
    """Resolve a fleet id-or-name to its fleet_id via /fleets, or None if no match."""
    for f in client.list_fleets().get("fleets", []):
        if f["fleet_id"] == identifier or f.get("name") == identifier:
            return f["fleet_id"]
    return None


@mcp.tool()
def list_fleets() -> dict:
    """List the fleets you can access. A fleet is a group of identical hosts (e.g. an
    autoscaling group) that share a mode and approvals. Each entry has a member_count
    and a 'writable' flag - if writable is false you have read-only access to the fleet."""
    client, _ = _client()
    return client.list_fleets()


@mcp.tool()
def list_fleet_agents(fleet: str) -> dict:
    """List the member agents of a fleet.

    Args:
        fleet: Fleet id or name (from list_fleets).
    """
    client, _ = _client()
    fleet_id = _resolve_fleet_id(client, fleet)
    if not fleet_id:
        return {"error": f"fleet not found: {fleet}"}
    return client.list_fleet_agents(fleet_id)


@mcp.tool()
def list_fleet_jobs(fleet: str, limit: int = 20) -> dict:
    """List recent jobs across all members of a fleet.

    Args:
        fleet: Fleet id or name.
        limit: Number of jobs to return (max 100, default 20).
    """
    client, _ = _client()
    fleet_id = _resolve_fleet_id(client, fleet)
    if not fleet_id:
        return {"error": f"fleet not found: {fleet}"}
    return client.list_jobs(fleet_id=fleet_id, limit=min(limit, 100))


@mcp.tool()
def list_fleet_runs(fleet: str, limit: int = 20) -> dict:
    """List fan-out runs for a fleet - one entry per `fleet_exec`, grouped by batch,
    with per-run member/ok/failed/pending counts. Use `list_fleet_jobs` (which carries
    each job's run_id) or `get_job` to drill into a specific run's member output.

    Args:
        fleet: Fleet id or name.
        limit: Number of runs to return (max 100, default 20).
    """
    client, _ = _client()
    fleet_id = _resolve_fleet_id(client, fleet)
    if not fleet_id:
        return {"error": f"fleet not found: {fleet}"}
    return client.list_fleet_runs(fleet_id, limit=min(limit, 100))


@mcp.tool()
def list_fleet_run(run_id: str) -> dict:
    """Show the per-member results of one fan-out run (batch), from `list_fleet_runs`.
    Returns each member's job (status, exit_code, job_id); use get_job(job_id) for a
    member's full stdout/stderr.

    Args:
        run_id: The run id (e.g. run_...).
    """
    client, _ = _client()
    return client.list_jobs(run_id=run_id, limit=100)


@mcp.tool()
def list_tag_runs(limit: int = 20) -> dict:
    """List tag fan-out runs across STANDALONE agents - one entry per `exec_by_tag`,
    grouped by batch, each with the tag it targeted and per-run member/ok/failed/pending
    counts. The standalone counterpart to `list_fleet_runs`. Drill into a run with
    `list_tag_run(run_id)`.

    Args:
        limit: Number of runs to return (max 100, default 20).
    """
    client, _ = _client()
    return client.list_tag_runs(limit=min(limit, 100))


@mcp.tool()
def list_tag_run(run_id: str) -> dict:
    """Show the per-agent results of one tag fan-out run (batch), from `list_tag_runs`.
    Returns each agent's job (status, exit_code, job_id); use get_job(job_id) for an
    agent's full stdout/stderr.

    Args:
        run_id: The run id (e.g. run_...).
    """
    client, _ = _client()
    return client.list_jobs(run_id=run_id, limit=100)


@mcp.tool()
def list_fleet_approved(fleet: str, status: str = "approved") -> dict:
    """List a fleet's approval records (shared by every member).

    status='approved' (default) returns the effective approved commands the whole
    fleet may run. status='pending'|'denied'|'expired' returns your own records in
    that state. Check this before a write on a fleet member - if it is not approved
    the agent will block it and raise a fleet-scoped pending request.

    Args:
        fleet: Fleet id or name.
        status: approved (default), pending, denied, or expired.
    """
    client, _ = _client()
    fleet_id = _resolve_fleet_id(client, fleet)
    if not fleet_id:
        return {"error": f"fleet not found: {fleet}"}
    return client.list_fleet_approved(fleet_id, status=status)


def _redact_failures(failures: list) -> list:
    for f in failures:
        f["stderr"] = _redact(f.get("stderr"))
    return failures


@mcp.tool()
def fleet_exec(command: str, fleet: str, confirm: bool = False, timeout: int = 60,
               max_targets: int = 0, idempotency_key: str = "") -> dict:
    """Run a command on the active members of a fleet (fan-out) and return a bounded
    run summary.

    HIGH IMPACT - runs on many hosts (in waves of the fan-out cap). Because there is no
    interactive prompt over MCP, this is a **two-step, confirm-gated** tool:

    1. Call with confirm=False (default) for a DRY-RUN preview - the fleet, the command,
       and the hosts it would target. NOTHING runs.
    2. Show the preview to the user, get explicit approval, then call again confirm=True.

    BLAST RADIUS: EVERY eligible member runs - nothing is dropped - but never more than the
    fan-out cap at a time. Above the cap the run automatically proceeds in **waves** of the
    cap (see STAGED ROLLOUT). Never call confirm=True unless the user just approved this
    exact command on this fleet.

    RESULTS: returns a summary (state, member counts, terminal) plus a BOUNDED list of
    failed members - not every host's output. Poll `run_status(run_id)` for progress, or
    `get_job` for one member's full output. Pass `idempotency_key` (any stable string) so
    a retry does not dispatch the command twice.

    STAGED ROLLOUT: when a fleet has more eligible members than the fan-out cap (or the
    tenant/fleet configured a wave policy for this read vs write command), the run is staged
    into waves of the cap - the first wave runs, later waves are HELD. With mode "auto" the
    next wave releases once a wave finishes; with mode "manual" it pauses after every wave
    for you to resume. On a failing wave, failure policy "stop" auto-pauses (default) and
    "continue" keeps going. Watch progress with `run_status` (current_wave / wave_total /
    staged) and drive it with `run_pause`, `run_resume`, `run_cancel`. `max_targets` lowers
    the wave size (hosts per wave) for this call.

    Args:
        command: The shell command to run.
        fleet: Fleet id or name (from list_fleets).
        confirm: Must be True to actually run. False (default) returns a preview only.
        timeout: Seconds to wait for the run to finish before returning (default 60).
        max_targets: Lower the wave size (hosts per wave) for this call; can't exceed the cap.
        idempotency_key: Stable key so a retried call reuses the same run.
    """
    client, _ = _client()
    fleet_id = _resolve_fleet_id(client, fleet)
    if not fleet_id:
        return {"error": f"fleet not found: {fleet}"}

    if not confirm:
        # Server-resolved plan: matched members + how it will roll out (wave size, strategy,
        # failure policy, approval need). Nothing runs.
        try:
            p = client.fleet_fanout(fleet_id, command, max_targets=max_targets or None, dry_run=True)
        except requests.HTTPError as e:
            resp = e.response
            if resp is not None and resp.status_code == 409:
                detail = (resp.json() or {}).get("error") if resp.content else None
                return {"error": detail or "request rejected (409)"}
            raise
        return {
            "preview": True, "confirmed": False, "fleet_id": fleet_id, "command": p.get("command"),
            "mode": p.get("mode"),
            "matched": p.get("matched"),
            "wave_size": p.get("wave_size"),
            "wave_strategy": p.get("wave_strategy"),      # auto | manual
            "failure_policy": p.get("failure_policy"),    # stop | continue
            "wave_total": p.get("wave_total"),
            "approval_required": p.get("approval_required"),
            "skipped": len(p.get("skipped") or []),
            "message": (f"DRY RUN - nothing has run. This would run `{command}` on {p.get('matched')} "
                        f"matched member(s), {p.get('wave_size')} per wave (strategy "
                        f"{(p.get('wave_strategy') or 'auto').upper()}, on failure "
                        f"{(p.get('failure_policy') or 'stop').upper()}). Show the user, get explicit "
                        f"approval, then call again with confirm=true. Use max_targets to shrink the wave."),
        }

    try:
        dispatch = client.fleet_fanout(fleet_id, command,
                                       max_targets=max_targets or None,
                                       idempotency_key=idempotency_key or None)
    except requests.HTTPError as e:
        resp = e.response
        if resp is not None and resp.status_code == 409:
            # 409 = max_targets above the fan-out cap, or a read-only fleet rejecting a
            # write. Surface the server's reason; add the cap hint only when it applies.
            detail = (resp.json() or {}).get("error") if resp.content else None
            out = {"error": detail or "request rejected (409)"}
            if detail and "cap" in detail:
                out["hint"] = "max_targets lowers the wave size and can't exceed the fan-out cap."
            return out
        raise

    run_id = dispatch.get("run_id")
    if dispatch.get("deduplicated"):
        summary = client.get_run(run_id) if run_id else {}
        summary["failures"] = _redact_failures(summary.get("failures", []))
        return {"confirmed": True, "deduplicated": True, "run_id": run_id, **summary}

    # Poll the BOUNDED run summary until terminal or timeout - never dump per-member output.
    deadline = time.monotonic() + timeout
    summary = client.get_run(run_id) if run_id else {}
    while run_id and not summary.get("terminal") and time.monotonic() <= deadline:
        time.sleep(2)
        summary = client.get_run(run_id)
    staged = (dispatch.get("wave_total") or 1) > 1
    return {
        "confirmed": True,
        "run_id": run_id,
        "dispatched": dispatch.get("dispatched"),
        "skipped": dispatch.get("skipped", []),
        "state": summary.get("state"),
        "counts": summary.get("counts"),
        "terminal": summary.get("terminal", False),
        "failures": _redact_failures(summary.get("failures", [])),
        **({"staged": True, "wave_total": dispatch.get("wave_total"),
            "current_wave": summary.get("current_wave"), "held": summary.get("staged"),
            "rollout_note": "staged rollout; it advances per its strategy (auto/manual) and "
                            "failure policy (stop/continue). Poll run_status; drive with "
                            "run_pause / run_resume / run_cancel."}
           if staged else {}),
        "note": None if summary.get("terminal") else f"still running; call run_status('{run_id}') to poll",
    }


@mcp.tool()
def run_status(run_id: str) -> dict:
    """Status of a fan-out run (from fleet_exec or a tag fan-out): its state
    (pending/running/succeeded/partial/failed), member counts (ok/failed/pending/
    running), whether it's terminal, and a BOUNDED list of failed members (host,
    exit code, stderr snippet). Use this to poll a run you dispatched, or to see what
    failed - it never dumps every host's output.

    Args:
        run_id: The run id returned by fleet_exec (or a tag fan-out).
    """
    client, _ = _client()
    try:
        summary = client.get_run(run_id)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {"error": f"run not found: {run_id}"}
        raise
    summary["failures"] = _redact_failures(summary.get("failures", []))
    return summary


def _run_control(fn, run_id: str) -> dict:
    client, _ = _client()
    try:
        return fn(client, run_id)
    except requests.HTTPError as e:
        resp = e.response
        if resp is not None and resp.status_code == 404:
            return {"error": f"run not found: {run_id}"}
        if resp is not None and resp.status_code == 409:
            detail = (resp.json() or {}).get("error") if resp.content else None
            return {"error": detail or "run cannot be controlled from its current state (409)"}
        raise


@mcp.tool()
def run_pause(run_id: str) -> dict:
    """Pause a STAGED fan-out run (one that runs in multiple waves): hold its not-yet-released
    waves. In-flight jobs finish; later waves wait until you `run_resume`. Only staged,
    still-running runs can be paused (a single-wave run has nothing held).

    Args:
        run_id: The run id from a staged fleet_exec / exec_by_tag (wave_total > 1).
    """
    return _run_control(lambda c, r: c.pause_run(r), run_id)


@mcp.tool()
def run_resume(run_id: str) -> dict:
    """Resume a PAUSED staged run: release the next wave and continue the rollout.

    Args:
        run_id: The run id of a paused run.
    """
    return _run_control(lambda c, r: c.resume_run(r), run_id)


@mcp.tool()
def run_cancel(run_id: str) -> dict:
    """Cancel a staged run: drop every not-yet-released wave (already-running jobs still
    finish). Use when an early wave went wrong and you do not want the remaining waves to run.

    Args:
        run_id: The run id to cancel.
    """
    return _run_control(lambda c, r: c.cancel_run(r), run_id)


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


@mcp.tool()
def exec_by_tag(command: str, tag: str, confirm: bool = False, timeout: int = 60, agent_type: str = "") -> dict:
    """Run a command on EVERY active standalone agent carrying `tag` (ad-hoc fan-out),
    returning a bounded run summary. Fleet members are excluded (use fleet_exec for a fleet).

    HIGH IMPACT and confirm-gated exactly like fleet_exec:
    1. Call with confirm=False (default) for a DRY-RUN preview - the matched agents and how
       it will roll out (wave size, strategy auto/manual, failure policy stop/continue).
       NOTHING runs.
    2. Show the preview to the user, get explicit approval, then call again confirm=True.

    Like fleet_exec, it runs EVERY eligible agent but never more than the fan-out cap at a
    time - above the cap it proceeds in waves. Poll `run_status(run_id)` for progress and
    drive a staged run with run_pause/run_resume/run_cancel. Never call confirm=True unless
    the user just approved this exact command and tag.

    Args:
        command: The shell command to run on each matching agent.
        tag: The tag to match (e.g. env:prod).
        confirm: Must be True to actually run. False (default) returns a preview only.
        timeout: Seconds to wait for the run to finish before returning (default 60).
        agent_type: "host" or "k8s" - required only if the tag matches both.
    """
    client, _ = _client()

    def _dispatch(dry):
        try:
            return client.fanout_by_tag(tag, command, agent_type=agent_type or None, dry_run=dry), None
        except requests.HTTPError as e:
            resp = e.response
            if resp is not None and resp.status_code in (404, 409):
                detail = (resp.json() or {}).get("error") if resp.content else None
                return None, {"error": detail or f"request rejected ({resp.status_code})"}
            raise

    if not confirm:
        p, err = _dispatch(True)
        if err:
            return err
        return {
            "preview": True, "confirmed": False, "tag": tag, "type": p.get("type"),
            "command": p.get("command"), "matched": p.get("matched"),
            "wave_size": p.get("wave_size"), "wave_strategy": p.get("wave_strategy"),
            "failure_policy": p.get("failure_policy"), "wave_total": p.get("wave_total"),
            "skipped": len(p.get("skipped") or []),
            "message": (f"DRY RUN - nothing has run. This would run `{command}` on {p.get('matched')} "
                        f"{p.get('type')} agent(s) tagged {tag}, {p.get('wave_size')} per wave (strategy "
                        f"{(p.get('wave_strategy') or 'auto').upper()}, on failure "
                        f"{(p.get('failure_policy') or 'stop').upper()}). Show the user, get explicit "
                        f"approval, then call again with confirm=true."),
        }

    dispatch, err = _dispatch(False)
    if err:
        return err
    run_id = dispatch.get("run_id")
    deadline = time.monotonic() + timeout
    summary = client.get_run(run_id) if run_id else {}
    while run_id and not summary.get("terminal") and time.monotonic() <= deadline:
        time.sleep(2)
        summary = client.get_run(run_id)
    staged = (dispatch.get("wave_total") or 1) > 1
    return {
        "confirmed": True, "run_id": run_id, "tag": tag, "type": dispatch.get("type"),
        "dispatched": dispatch.get("dispatched"), "skipped": dispatch.get("skipped", []),
        "state": summary.get("state"), "counts": summary.get("counts"),
        "terminal": summary.get("terminal", False),
        "failures": _redact_failures(summary.get("failures", [])),
        **({"staged": True, "wave_total": dispatch.get("wave_total"),
            "current_wave": summary.get("current_wave"), "held": summary.get("staged")} if staged else {}),
        "note": None if summary.get("terminal") else f"still running; call run_status('{run_id}') to poll",
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
