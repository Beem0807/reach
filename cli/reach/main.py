import time
from pathlib import Path
from typing import Optional

import typer
import requests
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from . import config as cfg_module
from .client import ReachClient

app = typer.Typer(
    name="reach",
    help="CLI for remote machine agents",
    no_args_is_help=True,
)
console = Console()

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "REJECTED", "EXPIRED"}
POLL_INTERVAL_SECONDS = 2


# ---------------------------------------------------------------------------
# reach version
# ---------------------------------------------------------------------------
@app.command()
def version():
    """Show the CLI version."""
    try:
        from importlib.metadata import version as _pkg_version
        v = _pkg_version("reach")
    except Exception:
        from reach import __version__
        v = __version__
    console.print(f"reach {v}")


# ---------------------------------------------------------------------------
# reach config
# ---------------------------------------------------------------------------
config_app = typer.Typer(help="Inspect local CLI configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show current CLI configuration (API URL, default agent, aliases)."""
    cfg = cfg_module.load()
    if not cfg:
        console.print("[yellow]No configuration found. Run `reach login` first.[/yellow]")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")

    table.add_row("Config file", str(cfg_module.CONFIG_FILE))
    table.add_row("API URL", cfg.get("api_url") or "[dim]-[/dim]")
    table.add_row("Default agent", cfg.get("default_agent_id") or "[dim]-[/dim]")

    aliases = cfg.get("aliases") or {}
    if aliases:
        alias_str = ", ".join(f"[cyan]{k}[/cyan]={v}" for k, v in sorted(aliases.items()))
        table.add_row("Aliases", alias_str)
    else:
        table.add_row("Aliases", "[dim]none[/dim]")

    console.print(table)


# ---------------------------------------------------------------------------
# reach login
# ---------------------------------------------------------------------------
@app.command()
def login(
    api_url: str = typer.Option(..., "--api-url", help="Backend API URL"),
    token: str = typer.Option(..., "--token", help="Tenant token"),
):
    """Store API URL and tenant token locally."""
    data = cfg_module.load()
    data["api_url"] = api_url.rstrip("/")
    data["tenant_token"] = token
    cfg_module.save(data)
    console.print(f"[green]Logged in.[/green] API: {api_url}")


# ---------------------------------------------------------------------------
# reach use <agent_id>
# ---------------------------------------------------------------------------
@app.command()
def use(agent_id: str = typer.Argument(..., help="Agent ID or alias to set as default")):
    """Set the default agent ID."""
    resolved = cfg_module.resolve_agent(agent_id)
    data = cfg_module.load()
    data["default_agent_id"] = resolved
    cfg_module.save(data)
    console.print(f"[green]Default agent set to:[/green] {resolved}")


# ---------------------------------------------------------------------------
# reach whoami
# ---------------------------------------------------------------------------
@app.command()
def whoami():
    """Show the currently authenticated user."""
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")

    client = ReachClient(api_url, tenant_token)
    try:
        data = client.get_me()
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    console.print(f"[bold]User ID:[/bold]   {data.get('user_id')}")
    console.print(f"[bold]Tenant ID:[/bold] {data.get('tenant_id')}")
    console.print(f"[bold]Name:[/bold]      {data.get('name') or '-'}")
    console.print(f"[bold]Created:[/bold]   {data.get('created_at') or '-'}")


# ---------------------------------------------------------------------------
# reach status
# ---------------------------------------------------------------------------
@app.command()
def status():
    """Show the status of the default agent."""
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")
    agent_id = cfg_module.require("default_agent_id")

    client = ReachClient(api_url, tenant_token)
    try:
        agent = client.get_agent(agent_id)
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")

    table.add_row("Agent ID", agent.get("agent_id", ""))
    table.add_row("Status", _status_color(agent.get("status", "")))
    table.add_row("Hostname", agent.get("hostname") or "-")
    table.add_row("Version", agent.get("agent_version") or "-")
    table.add_row("Fingerprint", (agent.get("machine_fingerprint") or "-")[:24] + "...")
    table.add_row("Claimed at", agent.get("claimed_at") or "-")
    table.add_row("Last heartbeat", agent.get("last_heartbeat_at") or "-")
    table.add_row("Mode", agent.get("mode") or "-")

    console.print(table)


# ---------------------------------------------------------------------------
# reach agents
# ---------------------------------------------------------------------------
@app.command()
def agents():
    """List all agents for your tenant."""
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")

    client = ReachClient(api_url, tenant_token)
    try:
        data = client.list_agents()
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    items = data.get("agents", [])
    if not items:
        console.print("[yellow]No agents found.[/yellow]")
        return

    aliases = cfg_module.list_aliases()
    id_to_alias = {v: k for k, v in aliases.items()}
    default_id = cfg_module.load().get("default_agent_id", "")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Alias")
    table.add_column("Agent ID")
    table.add_column("Status")
    table.add_column("Mode")
    table.add_column("Hostname")
    table.add_column("Version")
    table.add_column("Claimed at")

    for a in items:
        aid = a.get("agent_id", "")
        alias = id_to_alias.get(aid, "")
        alias_label = f"[cyan]{alias}[/cyan]" if alias else "-"
        if aid == default_id:
            alias_label += " [dim](default)[/dim]"
        mode = a.get("mode", "wild")
        mode_colors = {"wild": "[yellow]wild[/yellow]", "readonly": "[cyan]readonly[/cyan]", "approved": "[green]approved[/green]"}
        table.add_row(
            alias_label,
            aid,
            _status_color(a.get("status", "")),
            mode_colors.get(mode, mode),
            a.get("hostname") or "-",
            a.get("agent_version") or "-",
            a.get("claimed_at") or "-",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# reach alias
# ---------------------------------------------------------------------------
alias_app = typer.Typer(help="Manage agent aliases (e.g. prod, staging).")
app.add_typer(alias_app, name="alias")


@alias_app.command("set")
def alias_set(
    name: str = typer.Argument(..., help="Alias name (e.g. prod, staging)"),
    agent_id: str = typer.Argument(..., help="Agent ID to map to"),
):
    """Map an alias to an agent ID."""
    resolved = cfg_module.resolve_agent(agent_id)
    cfg_module.set_alias(name, resolved)
    console.print(f"[green]Alias set:[/green] [cyan]{name}[/cyan] → {resolved}")


@alias_app.command("remove")
def alias_remove(name: str = typer.Argument(..., help="Alias to remove")):
    """Remove an alias."""
    if cfg_module.remove_alias(name):
        console.print(f"[green]Alias removed:[/green] {name}")
    else:
        console.print(f"[yellow]Alias not found:[/yellow] {name}")
        raise typer.Exit(1)


@alias_app.command("list")
def alias_list():
    """List all aliases."""
    aliases = cfg_module.list_aliases()
    if not aliases:
        console.print("[yellow]No aliases configured.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Alias")
    table.add_column("Agent ID")

    for name, agent_id in sorted(aliases.items()):
        table.add_row(f"[cyan]{name}[/cyan]", agent_id)

    console.print(table)


# ---------------------------------------------------------------------------
# reach exec [--agent <id|alias>] -- <command>
# ---------------------------------------------------------------------------
@app.command(
    name="exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def exec_cmd(
    ctx: typer.Context,
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID to target (overrides default)"),
    timeout: int = typer.Option(60, "--timeout", "-t", help="Seconds to wait for a result before giving up"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit the job and exit without waiting for the result"),
):
    """Execute a command on a remote agent."""
    args = ctx.args
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        console.print("[red]Usage:[/red] reach exec [--agent <id|alias>] -- <command>")
        raise typer.Exit(1)

    full_command = " ".join(args)
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")
    agent_id = cfg_module.resolve_agent(agent) if agent else cfg_module.require("default_agent_id")

    client = ReachClient(api_url, tenant_token)

    try:
        job = client.create_job(agent_id, full_command)
    except requests.HTTPError as e:
        console.print(f"[red]Error creating job:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    job_id = job["job_id"]
    console.print(f"[dim]Job ID:[/dim] {job_id}  [dim]Agent:[/dim] {agent_id}")

    if no_wait:
        console.print(f"[dim]Use `reach job {job_id}` to check the result.[/dim]")
        return

    deadline = time.monotonic() + timeout
    with console.status("[bold green]Waiting for result...[/bold green]", spinner="dots"):
        while True:
            if time.monotonic() > deadline:
                console.print(f"\n[red]Timed out[/red] after {timeout}s waiting for agent to respond.")
                console.print(f"[dim]Job {job_id} is still queued - use `reach job {job_id}` to check later.[/dim]")
                raise typer.Exit(1)

            try:
                result = client.get_job(job_id)
            except requests.HTTPError as e:
                console.print(f"[red]Error polling job:[/red] {e.response.text}")
                raise typer.Exit(1)

            job_status = result.get("status", "")
            if job_status in TERMINAL_STATUSES:
                break
            time.sleep(POLL_INTERVAL_SECONDS)

    _print_job_result(result)

    exit_code = result.get("exit_code")
    if job_status != "SUCCEEDED" or (exit_code is not None and exit_code != 0):
        raise typer.Exit(exit_code if exit_code is not None else 1)


# ---------------------------------------------------------------------------
# reach job <job_id>
# ---------------------------------------------------------------------------
@app.command(name="job")
def job_cmd(job_id: str = typer.Argument(..., help="Job ID to fetch")):
    """Fetch the full output of a past job by ID."""
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")

    client = ReachClient(api_url, tenant_token)
    try:
        result = client.get_job(job_id)
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    console.print(f"[dim]Agent:[/dim] {result.get('agent_id', '')}")
    console.print(f"[dim]Command:[/dim] {result.get('command', '')}")
    _print_job_result(result)


# ---------------------------------------------------------------------------
# reach history
# ---------------------------------------------------------------------------
@app.command()
def history(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Filter by agent ID or alias"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of jobs to show (max 100)"),
    cursor: Optional[str] = typer.Option(None, "--cursor", help="Pagination cursor from a previous response"),
):
    """Show your recent jobs."""
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")

    agent_id = cfg_module.resolve_agent(agent) if agent else None

    client = ReachClient(api_url, tenant_token)
    try:
        data = client.list_jobs(agent_id=agent_id, limit=limit, cursor=cursor)
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    items = data.get("jobs", [])
    if not items:
        console.print("[yellow]No jobs found.[/yellow]")
        return

    aliases = cfg_module.list_aliases()
    id_to_alias = {v: k for k, v in aliases.items()}

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Time", style="dim", width=20)
    table.add_column("Agent")
    table.add_column("Command")
    table.add_column("Status")
    table.add_column("Duration", justify="right")

    for j in items:
        aid = j.get("agent_id", "")
        alias = id_to_alias.get(aid)
        agent_label = f"[cyan]{alias}[/cyan]" if alias else aid
        created = (j.get("created_at") or "")[:19].replace("T", " ")
        dur = j.get("duration_ms")
        dur_label = f"{dur}ms" if dur is not None else "-"
        table.add_row(
            created,
            agent_label,
            j.get("command", ""),
            _status_color(j.get("status", "")),
            dur_label,
        )

    console.print(table)

    next_cursor = data.get("next_cursor")
    if next_cursor:
        console.print(f"\n[dim]More results available. Run with --cursor {next_cursor} to see the next page.[/dim]")


# ---------------------------------------------------------------------------
# reach policy
# ---------------------------------------------------------------------------
policy_app = typer.Typer(help="View agent policy (mode and approved commands).")
app.add_typer(policy_app, name="policy")


@policy_app.command("show")
def policy_show(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID or alias (default: default agent)"),
):
    """Show the policy for an agent."""
    api_url = cfg_module.require("api_url")
    tenant_token = cfg_module.require("tenant_token")
    agent_id = cfg_module.resolve_agent(agent) if agent else cfg_module.require("default_agent_id")

    client = ReachClient(api_url, tenant_token)
    try:
        data = client.get_agent(agent_id)
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    mode = data.get("mode", "wild")
    commands = data.get("approved_commands", [])

    mode_colors = {
        "wild": "[yellow]wild[/yellow]",
        "readonly": "[cyan]readonly[/cyan]",
        "approved": "[green]approved[/green]",
    }
    mode_label = mode_colors.get(mode, mode)

    console.print(f"\n[bold]Agent:[/bold]  {agent_id}")
    console.print(f"[bold]Mode:[/bold]   {mode_label}")

    if mode == "approved":
        if commands:
            console.print("\n[bold]Approved commands:[/bold]")
            for c in commands:
                console.print(f"  [dim]•[/dim] {c}")
        else:
            console.print("\n[yellow]Approved commands: (none - all commands blocked)[/yellow]")
    elif mode == "readonly":
        console.print("\n[dim]Write and destructive commands are blocked.[/dim]")
    else:
        console.print("\n[dim]All commands permitted (no restrictions).[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# reach agent-init
# ---------------------------------------------------------------------------
@app.command("agent-init")
def agent_init(
    for_agent: Optional[str] = typer.Option(None, "--for", help="Target agent: claude, cursor, system-prompt, mcp"),
):
    """Generate remote machine context for your AI agent."""
    VALID = ("claude", "cursor", "system-prompt", "mcp")
    if for_agent is None:
        console.print("\n[bold]Select your agent:[/bold]")
        console.print("  [cyan]1[/cyan]  claude        - writes CLAUDE.md")
        console.print("  [cyan]2[/cyan]  cursor        - writes .cursor/rules/reach.mdc")
        console.print("  [cyan]3[/cyan]  system-prompt - prints to stdout, paste anywhere")
        console.print("  [cyan]4[/cyan]  mcp           - prints MCP server config to stdout")
        choice = Prompt.ask("\nChoice", choices=["1", "2", "3", "4"])
        for_agent = {"1": "claude", "2": "cursor", "3": "system-prompt", "4": "mcp"}[choice]
    elif for_agent not in VALID:
        console.print(f"[red]Error:[/red] --for must be one of: {', '.join(VALID)}")
        raise typer.Exit(1)

    if for_agent == "mcp":
        _print_mcp_config()
        return

    cfg = cfg_module.load()
    api_url = cfg.get("api_url")
    tenant_token = cfg.get("tenant_token")
    default_agent_id = cfg.get("default_agent_id", "")

    console.print(f"\n[bold cyan]reach agent-init --for {for_agent}[/bold cyan]\n")

    # Fetch agents from API
    fetched: list[dict] = []
    if api_url and tenant_token:
        try:
            client = ReachClient(api_url, tenant_token)
            fetched = [
                a for a in client.list_agents().get("agents", [])
                if a.get("status") in ("ACTIVE", "INACTIVE")
            ]
        except Exception:
            pass

    agents_config: list[dict] = []

    if fetched:
        console.print(f"Found [bold]{len(fetched)}[/bold] agent(s) in your tenant:\n")
        for a in fetched:
            console.print(f"  [cyan]{a['agent_id']}[/cyan]  {a.get('hostname') or 'unknown'}  {_status_color(a.get('status', ''))}")
        console.print()

        for a in fetched:
            agent_id = a["agent_id"]
            hostname = a.get("hostname") or ""
            console.print(f"[bold]─── {agent_id}[/bold] ({hostname})")
            role = Prompt.ask("  Role / notes (e.g. production, staging, home lab)", default="")
            app_name = Prompt.ask("  Main app name (e.g. my-api)", default="")
            agents_config.append({"agent_id": agent_id, "hostname": hostname, "role": role, "app_name": app_name})
            console.print()

        stack = Prompt.ask("Shared tech stack (e.g. docker, nginx)", default="")
        extra_notes = Prompt.ask("Extra notes for your agent", default="")
    else:
        console.print("[dim]Could not fetch agents from API - entering manually.[/dim]\n")
        hostname = Prompt.ask("Hostname or IP", default="")
        role = Prompt.ask("Role / notes", default="")
        app_name = Prompt.ask("Main app name", default="")
        stack = Prompt.ask("Tech stack", default="")
        extra_notes = ""
        agents_config.append({"agent_id": default_agent_id, "hostname": hostname, "role": role, "app_name": app_name})

    content = _build_agent_context(agents_config, default_agent_id, stack, extra_notes if fetched else "")

    if for_agent == "claude":
        _write_claude_md(content)
    elif for_agent == "cursor":
        _write_cursor_rules(content)
    elif for_agent == "system-prompt":
        console.print("\n[bold]── System prompt ──────────────────────────────────────[/bold]")
        console.print(content)


def _print_mcp_config():
    import json
    config = {
        "mcpServers": {
            "reach": {
                "command": "reach",
                "args": ["mcp"]
            }
        }
    }
    console.print("\n[bold]── MCP server config ──────────────────────────────────[/bold]")
    console.print("\nAdd this to your MCP client settings:\n")
    console.print(json.dumps(config, indent=2))
    console.print("\n[dim]Common locations:[/dim]")
    console.print("  [dim]Claude Code   [/dim] .claude/settings.json  [dim](project)[/dim]  or  ~/.claude.json  [dim](global)[/dim]")
    console.print("  [dim]Claude Desktop[/dim] ~/Library/Application Support/Claude/claude_desktop_config.json")
    console.print("  [dim]Cursor        [/dim] .cursor/mcp.json")
    console.print("\n[dim]Make sure you've run `reach login` first.[/dim]\n")


def _write_claude_md(content: str):
    claude_md = Path("CLAUDE.md")
    if claude_md.exists() and "## Remote Access" in claude_md.read_text():
        if not Confirm.ask("[yellow]CLAUDE.md already has a Remote Access section. Overwrite it?[/yellow]"):
            raise typer.Exit(0)
    if claude_md.exists():
        existing = claude_md.read_text()
        if "## Remote Access" in existing:
            existing = existing[:existing.index("## Remote Access")].rstrip()
        claude_md.write_text(existing + ("\n\n" if existing else "") + content)
        console.print("\n[green]Updated CLAUDE.md[/green]")
    else:
        claude_md.write_text(content)
        console.print("\n[green]Created CLAUDE.md[/green]")
    console.print(f"[dim]{claude_md.resolve()}[/dim]")


def _write_cursor_rules(content: str):
    rules_dir = Path(".cursor/rules")
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_file = rules_dir / "reach.mdc"
    frontmatter = "---\ndescription: Remote machine access via reach\nalwaysApply: true\n---\n\n"
    rules_file.write_text(frontmatter + content)
    console.print(f"\n[green]Created {rules_file}[/green]")
    console.print(f"[dim]{rules_file.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Context builder (shared across all agent formats)
# ---------------------------------------------------------------------------

def _build_agent_context(
    agents_config: list[dict],
    default_agent_id: str,
    stack: str,
    extra_notes: str,
) -> str:
    multi = len(agents_config) > 1
    saved_aliases = cfg_module.list_aliases()
    id_to_alias = {v: k for k, v in saved_aliases.items()}

    agent_rows = ""
    exec_examples = ""
    app_examples = ""

    for a in agents_config:
        aid = a["agent_id"]
        alias = id_to_alias.get(aid, "")
        host = a.get("hostname") or "-"
        role = a.get("role") or "-"
        app = a.get("app_name") or ""
        target = alias or aid
        flag = f"--agent {target} " if multi else ""

        alias_col = f" (`{alias}`)" if alias else ""
        agent_rows += f"| `{aid}`{alias_col} | {host} | {role} |\n"
        exec_examples += f"reach exec {flag}-- hostname\n"
        exec_examples += f"reach exec {flag}-- uptime\n"
        exec_examples += f"reach exec {flag}-- df -h\n"
        if app:
            exec_examples += f"reach exec {flag}-- docker ps\n"
            app_examples += f"reach exec {flag}-- docker logs {app} --tail 100\n"
            app_examples += f"reach exec {flag}-- docker restart {app}\n"

    agents_section = (
        "### Agents\n\n"
        "| Agent ID | Hostname | Role |\n"
        "|---|---|---|\n"
        f"{agent_rows}"
    )

    if multi and default_agent_id:
        default_alias = id_to_alias.get(default_agent_id, "")
        default_label = f"`{default_alias}` ({default_agent_id})" if default_alias else f"`{default_agent_id}`"
        agents_section += f"\n**Default:** {default_label}\n"

    stack_section = f"\n### Stack\n\n{stack}\n" if stack else ""
    notes_section = f"\n### Notes\n\n{extra_notes}\n" if extra_notes else ""

    rule_agent = (
        f"* Use `reach exec --agent <id> -- <command>` to target a specific machine.\n"
        f"* Default agent (no --agent flag): `{default_agent_id}`.\n"
        if multi else
        f"* Always use `reach exec -- <command>` to run commands on the remote machine.\n"
    )

    all_examples = exec_examples + (app_examples if app_examples else "")

    return f"""## Remote Access

Use `reach` for all remote machine operations. Do not use SSH.

{agents_section}{stack_section}{notes_section}
### Common commands

```bash
reach agents
reach status
{all_examples.rstrip()}
```

### Rules

{rule_agent}* Prefer read-only checks (`status`, `logs`, `ps`) before write/restart commands.
* Explain what you are about to do before running restart, delete, or write commands.
* If a command fails, check logs before retrying.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_color(status: str) -> str:
    colors = {
        "ACTIVE": "[green]ACTIVE[/green]",
        "CREATED": "[yellow]CREATED[/yellow]",
        "INACTIVE": "[yellow]INACTIVE[/yellow]",
        "SUSPICIOUS": "[red]SUSPICIOUS[/red]",
        "DISABLED": "[red]DISABLED[/red]",
    }
    return colors.get(status, status)


def _print_job_result(result: dict) -> None:
    status = result.get("status", "")
    exit_code = result.get("exit_code")
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    duration = result.get("duration_ms")

    color = "green" if status == "SUCCEEDED" else "red"
    console.print(f"\n[bold {color}]Status:[/bold {color}] {status}", highlight=False)
    if exit_code is not None:
        console.print(f"[dim]Exit code:[/dim] {exit_code}")
    if duration is not None:
        console.print(f"[dim]Duration:[/dim] {duration}ms")

    if stdout:
        console.print("\n[bold]stdout:[/bold]")
        console.print(stdout, highlight=False, end="")
        if not stdout.endswith("\n"):
            console.print()

    if stderr:
        console.print("\n[bold red]stderr:[/bold red]")
        console.print(stderr, highlight=False, end="")
        if not stderr.endswith("\n"):
            console.print()


# ---------------------------------------------------------------------------
# reach mcp
# ---------------------------------------------------------------------------
@app.command()
def mcp():
    """Start the reach MCP server (stdio transport for any MCP-compatible client)."""
    from reach.mcp_server import main as mcp_main
    mcp_main()


if __name__ == "__main__":
    app()
