from mcp.server.fastmcp import FastMCP
import time

from reach import config as cfg_module
from reach.client import ReachClient

mcp = FastMCP(
    "reach",
    instructions=(
        "Use these tools to run commands on remote machines via reach agents. "
        "Call list_agents first if you don't know which agent to target. "
        "exec_command waits for the result - use get_job only to check a job submitted earlier."
    ),
)

_TERMINAL = {"SUCCEEDED", "FAILED", "REJECTED", "EXPIRED"}


def _client() -> tuple[ReachClient, str]:
    cfg = cfg_module.load()
    api_url = cfg.get("api_url")
    token = cfg.get("tenant_token")
    if not api_url or not token:
        raise RuntimeError(
            "reach is not configured. Run 'reach login --api-url <url> --token <tok>' first."
        )
    default_agent = cfg.get("default_agent_id", "")
    return ReachClient(api_url, token), default_agent


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
                "stdout": result.get("stdout") or "",
                "stderr": result.get("stderr") or "",
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
    return client.get_job(job_id)


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


def main():
    mcp.run()


if __name__ == "__main__":
    main()
