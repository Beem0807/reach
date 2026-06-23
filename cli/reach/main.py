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
config_app = typer.Typer(help="Inspect local CLI configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show current CLI configuration (active profile, API URL, default agent, aliases)."""
    active = cfg_module.active_profile_name()
    cfg = cfg_module.load_profile()
    if not cfg:
        console.print("[yellow]No configuration found. Run `reach login` first.[/yellow]")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")

    table.add_row("Config file", str(cfg_module.CONFIG_FILE))
    table.add_row("Active profile", f"[cyan]{active}[/cyan]")
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
    api_key: str = typer.Option(..., "--api-key", help="API key (from the tenant console → API Tokens)"),
    profile: str = typer.Option("default", "--profile", "-p", help="Profile name to save under"),
):
    """Store API URL and API key. Use --profile to manage multiple tenants."""
    full = cfg_module.load()
    existing = full.get("profiles", {}).get(profile, {})
    if existing.get("api_url") or existing.get("api_key") or existing.get("tenant_token"):
        console.print(f"[yellow]Profile '{profile}' already exists (API: {existing.get('api_url')}).[/yellow]")
        if not Confirm.ask("Overwrite?", default=False):
            raise typer.Exit(0)
    profile_data = dict(existing)
    profile_data["api_url"] = api_url.rstrip("/")
    profile_data["api_key"] = api_key
    # Remove legacy key if present
    profile_data.pop("tenant_token", None)
    full.setdefault("profiles", {})[profile] = profile_data
    full["active_profile"] = profile
    cfg_module.save(full)
    console.print(f"[green]Logged in[/green] (profile: [cyan]{profile}[/cyan]). API: {api_url}")




# ---------------------------------------------------------------------------
# reach whoami
# ---------------------------------------------------------------------------
@app.command()
def whoami():
    """Show the currently authenticated user."""
    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")

    client = ReachClient(api_url, api_key)
    try:
        data = client.get_me()
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    console.print(f"[bold]User ID:[/bold]   {data.get('user_id')}")
    console.print(f"[bold]Tenant ID:[/bold] {data.get('tenant_id')}")
    console.print(f"[bold]Name:[/bold]      {data.get('name') or '-'}")
    if data.get('username'):
        console.print(f"[bold]Username:[/bold]  {data.get('username')}")
    if data.get('role'):
        console.print(f"[bold]Role:[/bold]      {data.get('role')}")
    console.print(f"[bold]Created:[/bold]   {data.get('created_at') or '-'}")


# ---------------------------------------------------------------------------
# reach status
# ---------------------------------------------------------------------------
@app.command()
def status():
    """Show the status of the default agent."""
    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")
    agent_id = cfg_module.require("default_agent_id")

    client = ReachClient(api_url, api_key)
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
    table.add_row("Access level", agent.get("access_level") or "-")

    console.print(table)


# ---------------------------------------------------------------------------
# reach agents
# ---------------------------------------------------------------------------
agents_app = typer.Typer(help="Manage and list remote agents.", no_args_is_help=True)
app.add_typer(agents_app, name="agents")


@agents_app.command("list")
def agents_list(
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag (e.g. env:prod)"),
):
    """List all agents for your tenant."""
    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")

    client = ReachClient(api_url, api_key)
    try:
        data = client.list_agents(tag=tag)
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    items = data.get("agents", [])
    if not items:
        console.print("[yellow]No agents found.[/yellow]")
        return

    aliases = cfg_module.list_aliases()
    id_to_alias = {v: k for k, v in aliases.items()}
    default_id = cfg_module.load_profile().get("default_agent_id", "")
    show_tags = any(a.get("tags") for a in items)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Agent ID")
    table.add_column("Alias")
    table.add_column("Status")
    table.add_column("Mode")
    table.add_column("Access")
    table.add_column("Hostname")
    if show_tags:
        table.add_column("Tags")
    table.add_column("Claimed at")

    _mode_colors = {"wild": "[yellow]wild[/yellow]", "readonly": "[cyan]readonly[/cyan]", "approved": "[green]approved[/green]"}
    _access_colors = {
        "open": "[dim]open[/dim]",
        "elevated": "[yellow]elevated[/yellow]",
        "managed": "[cyan]managed[/cyan]",
        "restricted": "[green]restricted[/green]",
    }

    for a in items:
        aid = a.get("agent_id", "")
        alias = id_to_alias.get(aid, "")
        alias_label = f"[cyan]{alias}[/cyan]" if alias else "-"
        marker = " [dim](default)[/dim]" if aid == default_id else ""
        mode = a.get("mode", "wild")
        al = a.get("access_level") or "-"
        row = [
            aid + marker,
            alias_label,
            _status_color(a.get("status", "")),
            _mode_colors.get(mode, mode),
            _access_colors.get(al, al),
            a.get("hostname") or "-",
        ]
        if show_tags:
            tags = a.get("tags") or []
            row.append(", ".join(f"[dim]{t}[/dim]" for t in tags) if tags else "-")
        row.append(a.get("claimed_at") or "-")
        table.add_row(*row)

    console.print(table)


@agents_app.command("use")
def agents_use(agent_id: str = typer.Argument(..., help="Agent ID or alias to set as default")):
    """Set the default agent for the active profile."""
    resolved = cfg_module.resolve_agent(agent_id)
    data = cfg_module.load_profile()
    data["default_agent_id"] = resolved
    cfg_module.save_profile(data)
    console.print(f"[green]Default agent set to:[/green] {resolved}")


# ---------------------------------------------------------------------------
# reach alias
# ---------------------------------------------------------------------------
alias_app = typer.Typer(help="Manage agent aliases (e.g. prod, staging).", no_args_is_help=True)
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
    api_key = cfg_module.require("api_key")
    agent_id = cfg_module.resolve_agent(agent) if agent else cfg_module.require("default_agent_id")

    client = ReachClient(api_url, api_key)

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
    api_key = cfg_module.require("api_key")

    client = ReachClient(api_url, api_key)
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
    api_key = cfg_module.require("api_key")

    agent_id = cfg_module.resolve_agent(agent) if agent else None

    client = ReachClient(api_url, api_key)
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
# reach approvals
# ---------------------------------------------------------------------------
_STATUS_STYLE = {"approved": "green", "denied": "red", "pending": "yellow", "expired": "dim"}


def _expires_label(record: dict) -> str:
    from datetime import datetime, timezone
    raw = record.get("expires_at")
    if not raw:
        return "[dim]permanent[/dim]" if record.get("status") == "approved" else "[dim]-[/dim]"
    try:
        exp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        if exp <= now:
            return "[dim](expired)[/dim]"
        total_secs = int((exp - now).total_seconds())
        if total_secs < 3600:
            label = f"in {total_secs // 60}m"
            return f"[red]{label}[/red]"
        if total_secs < 86400:
            hours = total_secs // 3600
            label = f"in {hours}h"
            return f"[yellow]{label}[/yellow]" if hours <= 2 else label
        days = total_secs // 86400
        hours = (total_secs % 86400) // 3600
        label = f"in {days}d {hours}h" if hours else f"in {days}d"
        return f"[dim]{label}[/dim]"
    except ValueError:
        return f"[dim]{raw[:19].replace('T', ' ')}[/dim]"


@app.command()
def approvals(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID or alias (default: default agent)"),
    pending: bool = typer.Option(False, "--pending", help="Show your pending requests for this agent"),
    denied: bool = typer.Option(False, "--denied", help="Show your denied requests for this agent"),
    expired: bool = typer.Option(False, "--expired", help="Show your expired approvals for this agent"),
):
    """Show approval records for an agent.

    Default: currently effective approved commands (agent-wide).
    --pending / --denied / --expired: your own records filtered by status.
    """
    flags = [pending, denied, expired]
    if sum(flags) > 1:
        console.print("[red]Error:[/red] use only one of --pending, --denied, --expired at a time")
        raise typer.Exit(1)

    if pending:
        status = "pending"
    elif denied:
        status = "denied"
    elif expired:
        status = "expired"
    else:
        status = "approved"

    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")
    agent_id = cfg_module.resolve_agent(agent) if agent else cfg_module.require("default_agent_id")

    client = ReachClient(api_url, api_key)
    try:
        data = client.list_agent_approved(agent_id, status=status)
    except requests.HTTPError as e:
        console.print(f"[red]Error:[/red] {e.response.status_code} {e.response.text}")
        raise typer.Exit(1)

    items = data.get("approvals", [])
    _STATUS_EMPTY = {
        "approved": "No approved commands for this agent.",
        "pending":  "No pending requests for this agent.",
        "denied":   "No denied requests for this agent.",
        "expired":  "No expired approvals for this agent.",
    }
    if not items:
        console.print(f"[yellow]{_STATUS_EMPTY.get(status, 'No records.')}[/yellow]")
        return

    show_status = status != "approved"
    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    table.add_column("Command")
    table.add_column("Requested by", style="dim")
    if show_status:
        table.add_column("Status")
    table.add_column("At", style="dim")
    table.add_column("Expires")

    for a in items:
        at = (a.get("reviewed_at") or a.get("created_at") or "")[:19].replace("T", " ")
        row = [
            a.get("command", ""),
            a.get("requester_name") or a.get("requested_by") or "-",
        ]
        if show_status:
            st = a.get("status", "")
            style = _STATUS_STYLE.get(st, "")
            row.append(f"[{style}]{st}[/{style}]" if style else st)
        row += [at, _expires_label(a)]
        table.add_row(*row)
    console.print(table)


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

    cfg = cfg_module.load_profile()
    api_url = cfg.get("api_url")
    api_key = cfg.get("api_key") or cfg.get("tenant_token")
    default_agent_id = cfg.get("default_agent_id", "")

    console.print(f"\n[bold cyan]reach agent-init --for {for_agent}[/bold cyan]\n")

    # Fetch agents from API
    fetched: list[dict] = []
    if api_url and api_key:
        try:
            client = ReachClient(api_url, api_key)
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
            agents_config.append({
                "agent_id": agent_id,
                "hostname": hostname,
                "role": role,
                "app_name": app_name,
                "mode": a.get("mode", "wild"),
                "access_level": a.get("access_level", ""),
            })
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
        role = a.get("role") or "-"
        app = a.get("app_name") or ""
        target = alias or aid
        flag = f"--agent {target} " if multi else ""

        alias_col = f" (`{alias}`)" if alias else ""
        agent_rows += f"| `{aid}`{alias_col} | {role} |\n"
        exec_examples += f"reach exec {flag}-- hostname\n"
        exec_examples += f"reach exec {flag}-- uptime\n"
        exec_examples += f"reach exec {flag}-- df -h\n"
        if app:
            exec_examples += f"reach exec {flag}-- docker ps\n"
            app_examples += f"reach exec {flag}-- docker logs {app} --tail 100\n"
            app_examples += f"reach exec {flag}-- docker restart {app}\n"

    # Agent table intentionally omits hostname, mode, and access_level - those
    # are live state from the API. Use `reach agents list` or `reach status` to
    # see current values.
    agents_section = (
        "### Agents\n\n"
        "| Agent ID | Role |\n"
        "|---|---|\n"
        f"{agent_rows}"
        "\nRun `reach agents list` for live status, mode, and access level.\n"
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
        "* Always use `reach exec -- <command>` to run commands on the remote machine.\n"
    )

    all_examples = exec_examples + (app_examples if app_examples else "")

    return f"""## Remote Access

Use `reach` for all remote machine operations. Do not use SSH.

{agents_section}{stack_section}{notes_section}
### Modes

Each agent has a **mode** and an **access_level**. Check both with `reach status` before running write or destructive commands.

**Mode** - what the server allows:
- `wild`: all commands run (except a small global blocklist of catastrophic operations like `rm -rf /`, `mkfs`, fork bombs).
- `readonly`: write and destructive commands are rejected by the server before the agent ever receives them. Reads always pass. Do not attempt writes.
- `approved`: reads always run. Write commands only run if pre-approved by an admin. If a write is not on the approved list the agent blocks it and creates a pending approval record - the command does NOT run silently. Use `reach approvals --pending` to see it, then tell the user admin approval is required. Do not retry.

**Access level** - mode combined with whether the agent runs as root:
- `open`: wild + root. Maximum blast radius. Every command runs with full system privileges. Treat all writes as irreversible. Always explain before acting.
- `elevated`: wild (non-root) or approved (root). High impact - proceed carefully.
- `managed`: approved (non-root) or readonly (root). Moderate restrictions.
- `restricted`: readonly + non-root. Safest - writes always rejected, no root access.

### Common commands

```bash
reach agents list
reach status
{all_examples.rstrip()}
```

### Rules

{rule_agent}* Run `reach status` before write or restart commands to confirm the current mode and access level.
* Prefer read-only checks (`status`, `logs`, `ps`) before write/restart commands.
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
        "REVOKED": "[red]REVOKED[/red]",
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
# reach profile
# ---------------------------------------------------------------------------
profile_app = typer.Typer(help="Manage profiles for multiple tenants.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list():
    """List all configured profiles."""
    full = cfg_module.load()
    active = full.get("active_profile", "default")
    profiles = full.get("profiles", {})

    if not profiles:
        console.print("[yellow]No profiles configured. Run `reach login` first.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Profile")
    table.add_column("API URL")
    table.add_column("Default agent")

    for name, p in profiles.items():
        label = f"[cyan]{name}[/cyan]" + (" [bold](active)[/bold]" if name == active else "")
        table.add_row(label, p.get("api_url") or "-", p.get("default_agent_id") or "-")

    console.print(table)


@profile_app.command("use")
def profile_use(name: str = typer.Argument(..., help="Profile name to switch to")):
    """Switch the active profile."""
    try:
        cfg_module.set_active_profile(name)
    except SystemExit as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Switched to profile:[/green] [cyan]{name}[/cyan]")


@profile_app.command("delete")
def profile_delete(name: str = typer.Argument(..., help="Profile name to delete")):
    """Delete a profile."""
    full = cfg_module.load()
    if name not in full.get("profiles", {}):
        console.print(f"[red]Error:[/red] profile '{name}' not found.")
        raise typer.Exit(1)
    if full.get("active_profile") == name:
        console.print(f"[red]Error:[/red] cannot delete the active profile. Run 'reach profile use <other>' first.")
        raise typer.Exit(1)
    if not Confirm.ask(f"Are you sure you want to delete profile '[cyan]{name}[/cyan]'?", default=False):
        raise typer.Exit(0)
    cfg_module.delete_profile(name)
    console.print(f"[green]Deleted profile:[/green] [cyan]{name}[/cyan]")


@profile_app.command("rename")
def profile_rename(
    old: str = typer.Argument(..., help="Current profile name"),
    new: str = typer.Argument(..., help="New profile name"),
):
    """Rename a profile."""
    try:
        cfg_module.rename_profile(old, new)
    except SystemExit as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Renamed profile:[/green] [cyan]{old}[/cyan] → [cyan]{new}[/cyan]")


# ---------------------------------------------------------------------------
# reach mcp
# ---------------------------------------------------------------------------
@app.command()
def mcp():
    """Start the reach MCP server (stdio transport for any MCP-compatible client)."""
    from reach.mcp_server import main as mcp_main
    mcp_main()


# ---------------------------------------------------------------------------
# reach man
# ---------------------------------------------------------------------------
@app.command()
def man():
    """Show a full command reference for the reach CLI."""
    from rich.panel import Panel
    from rich.text import Text

    def section(title: str, rows: list[tuple[str, str]]) -> None:
        table = Table(box=None, padding=(0, 2), show_header=False)
        table.add_column("Command", style="cyan", no_wrap=True)
        table.add_column("Description")
        for cmd, desc in rows:
            table.add_row(cmd, desc)
        console.print(Panel(table, title=f"[bold]{title}[/bold]", title_align="left", border_style="dim"))
        console.print()

    console.print()
    console.print("[bold white]reach[/bold white] - remote machine command bridge\n", highlight=False)

    section("Auth & setup", [
        ("reach login --api-url <url> --api-key <key>",      "Save credentials (default profile)"),
        ("reach login --profile <name> ...",                  "Save credentials under a named profile"),
        ("reach whoami",                                      "Show current user, tenant, and API URL"),
        ("reach version",                                     "Show CLI version"),
        ("reach config show",                                 "Show active profile, default agent, and aliases"),
    ])

    section("Profiles  (multiple tenants / deployments)", [
        ("reach profile list",                               "List all profiles; active one is marked"),
        ("reach profile use <name>",                         "Switch active profile"),
        ("reach profile rename <old> <new>",                 "Rename a profile"),
        ("reach profile delete <name>",                      "Delete a profile"),
    ])

    section("Agents", [
        ("reach agents list",                                "List all machines with mode and access level"),
        ("reach agents list --tag <key:value>",              "Filter machines by tag"),
        ("reach agents use <id|alias>",                      "Set default machine"),
        ("reach status",                                     "Show default machine status and access level"),
        ("reach alias set <name> <id>",                      "Create a friendly alias for an agent"),
        ("reach alias list",                                  "List all aliases"),
        ("reach alias remove <name>",                        "Remove an alias"),
    ])

    section("Execution", [
        ("reach exec -- <cmd>",                              "Run command on default machine"),
        ("reach exec --agent <id|alias> -- <cmd>",           "Run command on a specific machine"),
        ("reach exec --timeout <s> -- <cmd>",                "Override wait timeout (default 60 s)"),
        ("reach exec --no-wait -- <cmd>",                    "Submit and exit; poll later with `reach job <id>`"),
        ("reach job <job_id>",                               "Re-view stdout / stderr of a past job"),
        ("reach history",                                    "Show your recent jobs (default 20)"),
        ("reach history --agent <id|alias>",                 "Filter history to one machine"),
        ("reach history --limit <n>",                        "Show up to N jobs (max 100)"),
        ("reach history --cursor <cursor>",                  "Fetch the next page"),
    ])

    section("Approvals  (approved mode)", [
        ("reach approvals",                                  "Show effective approved commands for the default agent"),
        ("reach approvals --agent <id|alias>",               "Show effective approved commands for a specific agent"),
        ("reach approvals --pending",                        "Show your pending requests for the default agent"),
        ("reach approvals --denied",                         "Show your denied requests for the default agent"),
        ("reach approvals --expired",                        "Show your expired approvals for the default agent"),
        ("reach approvals --agent <id|alias> --pending",     "Filter any of the above to a specific agent"),
    ])

    section("AI integration", [
        ("reach agent-init",                                 "Interactively generate context for your AI agent"),
        ("reach agent-init --for claude",                    "Write CLAUDE.md for Claude Code"),
        ("reach agent-init --for cursor",                    "Write .cursor/rules/reach.mdc for Cursor"),
        ("reach agent-init --for system-prompt",             "Print system prompt snippet to stdout"),
        ("reach mcp",                                        "Start the MCP server (stdio, for MCP-compatible clients)"),
    ])

    console.print("[dim]Tip: every command also accepts --help for full option details.[/dim]\n")


if __name__ == "__main__":
    app()
