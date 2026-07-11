import json
import sys
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

# Set by the root `--json` flag. When on, commands print the raw API response as
# JSON (for scripting/piping) instead of a Rich table, via `_emit()`.
_JSON = {"on": False}


def _emit(data) -> bool:
    """In --json mode, print `data` as JSON and return True (the caller should then
    return early, skipping table rendering). Returns False in normal mode."""
    if _JSON["on"]:
        typer.echo(json.dumps(data, indent=2, default=str))
        return True
    return False


# Exit-code convention: 0 = success, 1 = a remote command failed, 2 = reach itself
# failed (bad usage, config, or an API error). `_die` is for that last class.
def _die(message: str, code: int = 2) -> None:
    """Report a reach-level error and exit `code`. In --json mode the error is
    emitted as JSON on stdout so scripts can parse failures too."""
    if _JSON["on"]:
        typer.echo(json.dumps({"error": message}))
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code)


_HTTP_HINT = {
    401: "not authenticated - check your API token (`reach login`)",
    403: "not permitted - your role or access grant doesn't allow this",
    404: "not found",
    409: "conflict",
    429: "rate limited - slow down and retry",
}


def _http_die(e: "requests.RequestException") -> None:
    """Turn any request failure into a friendly reach-level error (exit 2).

    HTTP errors surface the API's `{"error": "..."}` message (with a hint for common
    status codes); connection/timeout failures (the backend down or the wrong URL)
    get a plain message instead of a stack trace."""
    resp = getattr(e, "response", None)
    if resp is None:
        # No response = never reached the server (connection refused, DNS, timeout).
        url = getattr(getattr(e, "request", None), "url", None)
        where = f" ({url.split('/')[2]})" if isinstance(url, str) and "//" in url else ""
        if isinstance(e, requests.Timeout):
            _die(f"the backend timed out{where} - is it up and reachable?", 2)
        _die(f"cannot reach the backend{where} - is it running and is your API URL correct? (`reach whoami`)", 2)

    status = getattr(resp, "status_code", None)
    message = None
    try:
        body = resp.json()
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            message = body["error"]
    except Exception:
        pass
    if not message:
        text = getattr(resp, "text", "")
        message = text.strip() if isinstance(text, str) and text.strip() else None
    if not message:
        message = _HTTP_HINT.get(status, "request failed")
    suffix = f" (HTTP {status})" if status else ""
    _die(f"{message}{suffix}", 2)


# ---------------------------------------------------------------------------
# Version - exposed as the flag `reach --version` / `-V`
# ---------------------------------------------------------------------------
def _cli_version() -> str:
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("reach")
    except Exception:
        from reach import __version__
        return __version__


def _version_callback(value: bool):
    if value:
        console.print(f"reach {_cli_version()}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V",
        help="Show the CLI version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    output_json: bool = typer.Option(
        False, "--json",
        help="Output raw JSON instead of tables (for scripting).",
    ),
):
    """CLI for remote machine agents."""
    _JSON["on"] = output_json


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
        raise typer.Exit(2)

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")

    table.add_row("Config file", str(cfg_module.CONFIG_FILE))
    table.add_row("Active profile", f"[cyan]{active}[/cyan]")
    table.add_row("API URL", cfg.get("api_url") or "[dim]-[/dim]")
    table.add_row("Default agent", cfg.get("default_agent_id") or "[dim]-[/dim]")
    table.add_row("Default fleet", cfg.get("default_fleet") or "[dim]-[/dim]")

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
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
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
    except requests.RequestException as e:
        _http_die(e)

    if _emit(agent):
        return
    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")

    table.add_row("Agent ID", agent.get("agent_id", ""))
    table.add_row("Status", _status_color(agent.get("status", "")))
    table.add_row("Type", _type_label(agent.get("type")))
    table.add_row("Hostname", agent.get("hostname") or "-")
    table.add_row("Version", agent.get("agent_version") or "-")
    table.add_row("Fingerprint", (agent.get("machine_fingerprint") or "-")[:24] + "...")
    table.add_row("Claimed at", agent.get("claimed_at") or "-")
    table.add_row("Last heartbeat", agent.get("last_heartbeat_at") or "-")
    table.add_row("Mode", agent.get("mode") or "-")
    table.add_row("Access level", agent.get("access_level") or "-")
    if agent.get("writable") is False:
        table.add_row("Your access", "[cyan]read-only[/cyan] (write commands are blocked)")

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
    """List your standalone agents.

    Fleet members are managed as a group - list them with `reach fleets agents <fleet>`.
    """
    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")

    client = ReachClient(api_url, api_key)
    try:
        data = client.list_agents(tag=tag)
    except requests.RequestException as e:
        _http_die(e)

    # Standalone agents only; fleet members are listed per-fleet via `reach fleets agents`.
    items = [a for a in data.get("agents", []) if not a.get("fleet_id")]

    if _emit({**data, "agents": items}):  # full envelope, collection filtered to standalone
        return
    if not items:
        console.print("[yellow]No standalone agents found.[/yellow] [dim]Try `reach fleets list`.[/dim]")
        return

    aliases = cfg_module.list_aliases()
    id_to_alias = {v: k for k, v in aliases.items()}
    default_id = cfg_module.load_profile().get("default_agent_id", "")
    show_tags = any(a.get("tags") for a in items)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Agent ID")
    table.add_column("Alias")
    table.add_column("Status")
    table.add_column("Type")
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
        access_cell = _access_colors.get(al, al)
        if a.get("writable") is False:
            access_cell += " [cyan](read-only)[/cyan]"
        row = [
            aid + marker,
            alias_label,
            _status_color(a.get("status", "")),
            _type_label(a.get("type")),
            _mode_colors.get(mode, mode),
            access_cell,
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


@agents_app.command("show")
def agents_show(agent_id: str = typer.Argument(..., help="Agent ID or alias")):
    """Show one agent's full detail (mode, access, tags, capabilities)."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    resolved = cfg_module.resolve_agent(agent_id)
    try:
        agent = client.get_agent(resolved)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(agent):
        return

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")
    table.add_row("Agent ID", agent.get("agent_id", ""))
    table.add_row("Status", _status_color(agent.get("status", "")))
    table.add_row("Type", _type_label(agent.get("type")))
    table.add_row("Hostname", agent.get("hostname") or "-")
    table.add_row("Version", agent.get("agent_version") or "-")
    if agent.get("fleet_id"):
        table.add_row("Fleet", f"[magenta]{agent.get('fleet_id')}[/magenta]")
    table.add_row("Mode", agent.get("mode") or "-")
    table.add_row("Access level", agent.get("access_level") or "-")
    if agent.get("writable") is False:
        table.add_row("Your access", "[cyan]read-only[/cyan] (write commands are blocked)")
    tags = agent.get("tags") or []
    table.add_row("Tags", ", ".join(f"[dim]{t}[/dim]" for t in tags) if tags else "-")
    table.add_row("Claimed at", agent.get("claimed_at") or "-")
    table.add_row("Last heartbeat", agent.get("last_heartbeat_at") or "-")
    console.print(table)


# ---------------------------------------------------------------------------
# reach fleets
# ---------------------------------------------------------------------------
fleets_app = typer.Typer(help="List fleets and run commands across their members.", no_args_is_help=True)
app.add_typer(fleets_app, name="fleets")

_FLEET_MODE_COLORS = {
    "wild": "[yellow]wild[/yellow]",
    "readonly": "[cyan]readonly[/cyan]",
    "approved": "[green]approved[/green]",
}


def _resolve_fleet(client: ReachClient, identifier: Optional[str] = None) -> dict:
    """Resolve a fleet id-or-name to its fleet dict (from `/fleets`). When no
    identifier is given, fall back to the profile's default fleet. Exits (1) on no
    match, listing what is available."""
    if not identifier:
        identifier = cfg_module.load_profile().get("default_fleet")
        if not identifier:
            console.print("[red]No fleet given and no default fleet set.[/red] "
                          "[dim]Pass a fleet, or set one with `reach fleets use <fleet>`.[/dim]")
            raise typer.Exit(2)
    try:
        fleets = client.list_fleets().get("fleets", [])
    except requests.RequestException as e:
        _http_die(e)
    for f in fleets:
        if f["fleet_id"] == identifier or f.get("name") == identifier:
            return f
    console.print(f"[red]Fleet not found:[/red] {identifier}")
    if fleets:
        console.print("[dim]Available: " + ", ".join(f.get("name") or f["fleet_id"] for f in fleets) + "[/dim]")
    raise typer.Exit(2)


@fleets_app.command("use")
def fleets_use(fleet: str = typer.Argument(..., help="Fleet id or name to set as default")):
    """Set the default fleet for the active profile (so fleet commands can omit it)."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    prof = cfg_module.load_profile()
    prof["default_fleet"] = fleet_obj["fleet_id"]
    cfg_module.save_profile(prof)
    console.print(f"[green]Default fleet set to:[/green] [magenta]{fleet_obj.get('name') or fleet_obj['fleet_id']}[/magenta]")


@fleets_app.command("list")
def fleets_list():
    """List the fleets you can access, with live member counts."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        data = client.list_fleets()
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("fleets", [])
    if not items:
        console.print("[yellow]No fleets found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Fleet")
    table.add_column("Fleet ID")
    table.add_column("Status")
    table.add_column("Mode")
    table.add_column("Members", justify="right")
    table.add_column("Access")
    for f in items:
        mode = f.get("mode", "wild")
        access = "[cyan]read-only[/cyan]" if f.get("writable") is False else "[green]read-write[/green]"
        table.add_row(
            f"[magenta]{f.get('name') or '-'}[/magenta]",
            f["fleet_id"],
            _status_color(f.get("status", "")),
            _FLEET_MODE_COLORS.get(mode, mode),
            str(f.get("member_count", 0)),
            access,
        )
    console.print(table)


@fleets_app.command("show")
def fleets_show(fleet: Optional[str] = typer.Argument(None, help="Fleet id or name (default: `reach fleets use`)")):
    """Show one fleet's detail (mode, tags, member breakdown, your access)."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)

    # Member status breakdown is best-effort (needs a second call).
    active = inactive = None
    try:
        members = client.list_fleet_agents(fleet_obj["fleet_id"]).get("agents", [])
        active = sum(1 for m in members if m.get("status") == "ACTIVE")
        inactive = sum(1 for m in members if m.get("status") == "INACTIVE")
    except requests.RequestException:
        pass

    if _emit(fleet_obj):
        return

    mode = fleet_obj.get("mode", "wild")
    tags = fleet_obj.get("tags") or []
    count = fleet_obj.get("member_count", 0)
    members_cell = str(count)
    if active is not None:
        members_cell += f" [dim]({active} active, {inactive} inactive)[/dim]"

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan", width=20)
    table.add_column("Value")
    table.add_row("Name", f"[magenta]{fleet_obj.get('name') or '-'}[/magenta]")
    table.add_row("Fleet ID", fleet_obj["fleet_id"])
    table.add_row("Status", _status_color(fleet_obj.get("status", "")))
    table.add_row("Mode", _FLEET_MODE_COLORS.get(mode, mode))
    table.add_row("Members", members_cell)
    table.add_row("Your access", "[cyan]read-only[/cyan]" if fleet_obj.get("writable") is False else "[green]read-write[/green]")
    table.add_row("Tags", ", ".join(f"[dim]{t}[/dim]" for t in tags) if tags else "-")
    console.print(table)
    console.print("[dim]Members: `reach fleets agents`. Approvals: `reach fleets approvals list`.[/dim]")


@fleets_app.command("agents")
def fleets_agents(fleet: Optional[str] = typer.Argument(None, help="Fleet id or name (default: `reach fleets use`)")):
    """List the member agents of a fleet."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    try:
        data = client.list_fleet_agents(fleet_obj["fleet_id"])
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("agents", [])
    console.print(f"[bold]Fleet:[/bold] [magenta]{fleet_obj.get('name') or fleet_obj['fleet_id']}[/magenta]  [dim]({len(items)} members)[/dim]")
    if not items:
        console.print("[yellow]No members yet - install a host with this fleet's join token to enroll it.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Agent ID")
    table.add_column("Status")
    table.add_column("Mode")
    table.add_column("Hostname")
    table.add_column("Version")
    for a in items:
        mode = a.get("mode", "wild")
        table.add_row(
            a.get("agent_id", ""),
            _status_color(a.get("status", "")),
            _FLEET_MODE_COLORS.get(mode, mode),
            a.get("hostname") or "-",
            a.get("agent_version") or "-",
        )
    console.print(table)


# `fleets exec` flags whose presence/value must be skipped when scanning the tokens
# before `--` for the (optional) fleet positional.
_EXEC_VALUE_FLAGS = {"-t", "--timeout"}
_EXEC_BOOL_FLAGS = {"-y", "--yes", "--no-wait"}


def _post_exec_tokens(argv: list) -> Optional[list]:
    """The raw tokens the user typed after the `exec` subcommand, or None if `exec`
    isn't in argv (e.g. under the test harness, where the command is invoked directly).
    Used to recover the `--` separator, which click strips before the command runs."""
    try:
        i = argv.index("exec")
    except ValueError:
        return None
    return argv[i + 1:]


def _fleet_before_separator(before: list) -> Optional[str]:
    """The fleet positional among the tokens before `--` - i.e. the first bare token
    that isn't an option flag (or a value consumed by `-t/--timeout`)."""
    skip = False
    for tok in before:
        if skip:
            skip = False
            continue
        if tok in _EXEC_VALUE_FLAGS:
            skip = True
            continue
        if tok in _EXEC_BOOL_FLAGS or tok.startswith("-"):
            continue
        return tok
    return None


def split_fleet_command(raw_tokens: list) -> tuple:
    """Split the post-`exec` tokens into (fleet_or_None, command_string) using the `--`
    separator: everything after `--` is the command; an optional fleet id/name precedes
    it. This makes `fleets exec -- <multi word cmd>` (default fleet) unambiguous, which a
    positional `fleet` argument can't be once click has discarded the `--`."""
    sep = raw_tokens.index("--")
    fleet = _fleet_before_separator(raw_tokens[:sep])
    command = " ".join(raw_tokens[sep + 1:])
    return fleet, command


def _print_fanout_preview(p: dict) -> None:
    """Render a fan-out dry-run preview: what it matched and how it will roll out
    (wave size / strategy / failure policy), shown before the Proceed? prompt."""
    if p.get("fleet_name") or p.get("fleet_id"):
        console.print(f"[dim]Fleet:[/dim] [magenta]{p.get('fleet_name') or p.get('fleet_id')}[/magenta]")
    if p.get("tag"):
        typ = f"  [dim]type={p.get('type')}[/dim]" if p.get("type") else ""
        console.print(f"[dim]Tag:[/dim] [cyan]{p.get('tag')}[/cyan]{typ}")
    console.print(f"[dim]Matched agents:[/dim] {p.get('matched', 0)}")
    console.print(f"[dim]Command:[/dim] {p.get('command', '')}")
    if p.get("mode"):
        console.print(f"[dim]Mode:[/dim] {p['mode']}")
    console.print(f"[dim]Wave size:[/dim] {p.get('wave_size')}")
    console.print(f"[dim]Wave strategy:[/dim] {(p.get('wave_strategy') or 'auto').upper()}")
    fp = (p.get("failure_policy") or "stop").lower()
    console.print(f"[dim]Failure policy:[/dim] {'STOP_ON_FAILURE' if fp == 'stop' else 'CONTINUE'}")
    if p.get("approval_required"):
        console.print("[dim]Approval required:[/dim] [yellow]yes[/yellow]")
    skipped = p.get("skipped") or []
    if skipped:
        console.print(f"[dim]Skipped (won't run):[/dim] {len(skipped)}")
    matched = p.get("matched", 0)
    ws = p.get("wave_size") or matched
    if (p.get("wave_total") or 1) > 1:
        console.print(f"\n[dim]This will create {matched} child jobs, but only {ws} will be released per wave.[/dim]")


@fleets_app.command(
    name="exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def fleets_exec(
    ctx: typer.Context,
    fleet: Optional[str] = typer.Argument(None, help="Fleet id or name (or `reach fleets use`)"),
    yes: bool = typer.Option(False, "--yes", "-y", "--force", help="Skip the interactive preview + confirmation (force)"),
    timeout: int = typer.Option(60, "--timeout", "-t", help="Seconds to wait for each member's result"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Dispatch and exit without waiting for results"),
    max_targets: int = typer.Option(0, "--max-targets", "-n", help="Cap how many members to run on per wave (0 = the fan-out cap; required above the server's safety cap)"),
):
    """Run a command on every member of a fleet.

    Example: reach fleets exec web-asg -- systemctl restart app
             reach fleets exec -- systemctl restart app   (any command on the `fleets use` default)

    If the tenant/fleet configured a wave policy for this kind of command, the run is
    staged automatically (waves of the fan-out cap); control it with `reach runs`.
    """
    # Prefer splitting on the real `--` from argv: click strips it, so a positional
    # `fleet` can't tell `-- <multi word cmd>` (default fleet) from `<fleet> -- <cmd>`.
    # Fall back to click's binding when argv is unavailable (test harness) or no `--`.
    raw = _post_exec_tokens(sys.argv)
    if raw is not None and "--" in raw:
        fleet, full_command = split_fleet_command(raw)
    else:
        args = ctx.args
        if args and args[0] == "--":
            args = args[1:]
        # No `--`: click bound the first token to `fleet`. If nothing is left for the
        # command, the user meant the default fleet and that lone token IS the command.
        if fleet is not None and not args:
            args = [fleet]
            fleet = None
        full_command = " ".join(args)
    if not full_command:
        console.print("[red]Usage:[/red] reach fleets exec [<fleet>] -- <command>")
        raise typer.Exit(2)

    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    fleet_id = fleet_obj["fleet_id"]
    fleet_label = fleet_obj.get("name") or fleet_id
    count = fleet_obj.get("member_count", 0)

    if fleet_obj.get("writable") is False:
        console.print(f"[red]You have read-only access to fleet[/red] {fleet_label}.")
        raise typer.Exit(2)
    if count == 0:
        console.print(f"[yellow]Fleet {fleet_label} has no members.[/yellow]")
        raise typer.Exit(2)

    if not yes:
        # Interactive: fetch the server-resolved plan (matched members, wave size, strategy,
        # failure policy, approval need) and confirm before dispatching. Skip with --force.
        try:
            preview = client.fleet_fanout(fleet_id, full_command, max_targets=max_targets or None, dry_run=True)
        except requests.RequestException as e:
            _http_die(e)
        _print_fanout_preview(preview)
        if not Confirm.ask("Proceed?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(2)

    try:
        result = client.fleet_fanout(fleet_id, full_command, max_targets=max_targets or None)
    except requests.RequestException as e:
        _http_die(e)

    jobs = result.get("jobs", [])
    skipped = result.get("skipped", [])
    run_id = result.get("run_id")
    wave_total = result.get("wave_total") or 1
    dispatched_now = result.get("dispatched", len(jobs))
    if wave_total > 1:
        held = result.get("total", len(jobs)) - dispatched_now
        console.print(f"[dim]Staged rollout: wave 1 of {wave_total} - dispatched {dispatched_now}, "
                      f"{held} held for later waves; {len(skipped)} skipped."
                      + (f"  Run: [magenta]{run_id}[/magenta]" if run_id else "") + "[/dim]")
        if run_id:
            console.print(f"[dim]Control it with `reach runs pause|resume|cancel {run_id}`.[/dim]")
    else:
        console.print(f"[dim]Dispatched to {len(jobs)} member(s); {len(skipped)} skipped."
                      + (f"  Batch: [magenta]{run_id}[/magenta]" if run_id else "") + "[/dim]")
    for s in skipped:
        console.print(f"  [yellow]skip[/yellow] {s.get('hostname') or s.get('agent_id')} - {s.get('reason')}")

    # A staged run advances across waves server-side over time, so a single blocking
    # poll doesn't fit - dispatch and let the user watch it via `reach runs run`.
    if no_wait or not jobs or wave_total > 1:
        for j in jobs:
            tag = " [dim](held)[/dim]" if j.get("status") == "HELD" else ""
            console.print(f"  [dim]{j.get('hostname') or j['agent_id']}: job {j['job_id']}[/dim]{tag}")
        if run_id:
            hint = "reach runs status" if wave_total > 1 else "reach fleets run"
            console.print(f"[dim]Check results later with `{hint} {run_id}`.[/dim]")
        return

    # Poll every dispatched job until terminal (or the shared deadline).
    pending = {j["job_id"]: j for j in jobs}
    results: dict = {}
    deadline = time.monotonic() + timeout
    with console.status(f"[bold green]Waiting for {len(pending)} result(s)...[/bold green]", spinner="dots"):
        while pending and time.monotonic() <= deadline:
            for job_id in list(pending):
                try:
                    r = client.get_job(job_id)
                except requests.RequestException:
                    continue
                if r.get("status") in TERMINAL_STATUSES:
                    results[job_id] = r
                    del pending[job_id]
            if pending:
                time.sleep(POLL_INTERVAL_SECONDS)

    # Fan-out means one job per member. Rather than dump every member's stdout
    # (unbounded on a big fleet), show a compact per-member status table; the Job ID
    # column lets you pull any single member's full output with `reach job <id>`.
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Member")
    table.add_column("Status")
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Job ID", no_wrap=True)
    any_failed = False
    for j in jobs:
        label = j.get("hostname") or j["agent_id"]
        job_id = j["job_id"]
        r = results.get(job_id)
        if not r:
            table.add_row(label, "[yellow]TIMEOUT[/yellow]", "-", "-", job_id)
            any_failed = True
            continue
        status = r.get("status", "")
        code = r.get("exit_code")
        dur = r.get("duration_ms")
        if status != "SUCCEEDED" or (code is not None and code != 0):
            any_failed = True
        table.add_row(
            label,
            _status_color(status),
            "-" if code is None else str(code),
            f"{dur}ms" if dur is not None else "-",
            job_id,
        )
    console.print(table)
    console.print(f"[dim]Full output of any member: `reach job <job-id>`. All fleet jobs: `reach fleets jobs {fleet_label}`.[/dim]")
    if any_failed:
        raise typer.Exit(1)


@fleets_app.command("jobs")
def fleets_jobs(
    fleet: Optional[str] = typer.Argument(None, help="Fleet id or name (default: `reach fleets use`)"),
    member: Optional[str] = typer.Option(None, "--member", "-m", help="Show only one member's jobs (agent id or hostname)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of jobs to show (max 100)"),
    cursor: Optional[str] = typer.Option(None, "--cursor", help="Pagination cursor from a previous response"),
):
    """Show recent jobs across a fleet's members, or one member with --member."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    fleet_label = fleet_obj.get("name") or fleet_obj["fleet_id"]

    member_id = None
    if member:
        # Resolve the member within this fleet (accept agent id or hostname).
        try:
            members = client.list_fleet_agents(fleet_obj["fleet_id"]).get("agents", [])
        except requests.RequestException as e:
            _http_die(e)
        match = next((a for a in members if a.get("agent_id") == member or a.get("hostname") == member), None)
        if not match:
            console.print(f"[red]{member} is not a member of fleet {fleet_label}.[/red]")
            if members:
                console.print("[dim]Members: " + ", ".join(a.get("hostname") or a["agent_id"] for a in members) + "[/dim]")
            raise typer.Exit(2)
        member_id = match["agent_id"]

    try:
        if member_id:
            data = client.list_jobs(agent_id=member_id, limit=limit, cursor=cursor)
        else:
            data = client.list_jobs(fleet_id=fleet_obj["fleet_id"], limit=limit, cursor=cursor)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("jobs", [])
    scope = f" · member [cyan]{member}[/cyan]" if member else ""
    console.print(f"[bold]Fleet jobs:[/bold] [magenta]{fleet_label}[/magenta]{scope}")
    if not items:
        console.print("[yellow]No jobs found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Job ID", no_wrap=True)
    table.add_column("Time", style="dim", width=20)
    table.add_column("Member")
    table.add_column("Command")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Batch", no_wrap=True)
    for j in items:
        created = (j.get("created_at") or "")[:19].replace("T", " ")
        dur = j.get("duration_ms")
        batch = j.get("run_id")
        table.add_row(
            j.get("job_id", ""),
            created,
            j.get("agent_hostname") or j.get("agent_id", ""),
            j.get("command", ""),
            _status_color(j.get("status", "")),
            f"{dur}ms" if dur is not None else "-",
            f"[magenta]{batch}[/magenta]" if batch else "[dim]-[/dim]",
        )
    console.print(table)
    console.print("[dim]Full output: `reach job <job-id>`. Group a fan-out: `reach fleets runs <fleet>`.[/dim]")

    next_cursor = data.get("next_cursor")
    if next_cursor:
        console.print(f"\n[dim]More results available. Run with --cursor {next_cursor} to see the next page.[/dim]")


@fleets_app.command("runs")
def fleets_runs(
    fleet: Optional[str] = typer.Argument(None, help="Fleet id or name (default: `reach fleets use`)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of runs to show (max 100)"),
):
    """List fan-out runs for a fleet - one row per `fleets exec`, with pass/fail counts."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    try:
        data = client.list_fleet_runs(fleet_obj["fleet_id"], limit=limit)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    runs = data.get("runs", [])
    console.print(f"[bold]Fleet runs:[/bold] [magenta]{fleet_obj.get('name') or fleet_obj['fleet_id']}[/magenta]")
    if not runs:
        console.print("[yellow]No fan-out runs yet.[/yellow] [dim]Create one with `reach fleets exec <fleet> -- <cmd>`.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Batch", no_wrap=True)
    table.add_column("Time", style="dim", width=20)
    table.add_column("Command")
    table.add_column("Members", justify="right")
    table.add_column("OK", justify="right")
    table.add_column("Fail", justify="right")
    table.add_column("Pending", justify="right")
    for r in runs:
        created = (r.get("created_at") or "")[:19].replace("T", " ")
        fail = r.get("failed", 0)
        pend = r.get("pending", 0)
        table.add_row(
            r.get("run_id", ""),
            created,
            r.get("command", ""),
            str(r.get("members", 0)),
            f"[green]{r.get('ok', 0)}[/green]",
            f"[red]{fail}[/red]" if fail else "0",
            f"[yellow]{pend}[/yellow]" if pend else "0",
        )
    console.print(table)
    console.print("[dim]Expand a run with `reach fleets run <batch-id>`.[/dim]")


@fleets_app.command("run")
def fleets_run(run_id: str = typer.Argument(..., help="Batch id from `reach fleets runs`")):
    """Show the per-member results of one fan-out run (batch)."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        data = client.list_jobs(run_id=run_id, limit=100)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("jobs", [])
    console.print(f"[bold]Run:[/bold] [magenta]{run_id}[/magenta]")
    if not items:
        console.print("[yellow]No jobs found for this batch.[/yellow]")
        return

    command = items[0].get("command", "")
    console.print(f"[dim]Command:[/dim] {command}")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Member")
    table.add_column("Status")
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Job ID", no_wrap=True)
    for j in items:
        code = j.get("exit_code")
        dur = j.get("duration_ms")
        table.add_row(
            j.get("agent_hostname") or j.get("agent_id", ""),
            _status_color(j.get("status", "")),
            "-" if code is None else str(code),
            f"{dur}ms" if dur is not None else "-",
            j.get("job_id", ""),
        )
    console.print(table)
    console.print("[dim]Full output of a member: `reach job <job-id>`.[/dim]")


# --- Runs: list tag fan-outs + staged-rollout status/control ---
# `runs` is a group: bare `reach runs` lists tag fan-out runs (its no-subcommand
# callback), and `status`/`pause`/`resume`/`cancel` inspect and control a run.
runs_app = typer.Typer(help="List tag fan-out runs; inspect/control staged rollouts.",
                       invoke_without_command=True)
app.add_typer(runs_app, name="runs")


@runs_app.callback(invoke_without_command=True)
def runs_main(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of runs to show (max 100)"),
):
    """List tag fan-out runs across standalone agents - one row per `exec --tag`.

    The standalone counterpart to `reach fleets runs`: each row is one fan-out
    (grouped by batch), with the tag it targeted and pass/fail counts. Fleet
    fan-outs show under `reach fleets runs <fleet>`.
    """
    if ctx.invoked_subcommand is not None:
        return
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        data = client.list_tag_runs(limit=limit)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("runs", [])
    console.print("[bold]Tag runs[/bold] [dim](standalone fan-outs)[/dim]")
    if not items:
        console.print("[yellow]No tag fan-out runs yet.[/yellow] [dim]Create one with `reach exec --tag <tag> -- <cmd>`.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Batch", no_wrap=True)
    table.add_column("Time", style="dim", width=20)
    table.add_column("Tag", style="cyan")
    table.add_column("Command")
    table.add_column("Agents", justify="right")
    table.add_column("OK", justify="right")
    table.add_column("Fail", justify="right")
    table.add_column("Pending", justify="right")
    for r in items:
        created = (r.get("created_at") or "")[:19].replace("T", " ")
        fail = r.get("failed", 0)
        pend = r.get("pending", 0)
        table.add_row(
            r.get("run_id", ""),
            created,
            r.get("tag") or "-",
            r.get("command", ""),
            str(r.get("members", 0)),
            f"[green]{r.get('ok', 0)}[/green]",
            f"[red]{fail}[/red]" if fail else "0",
            f"[yellow]{pend}[/yellow]" if pend else "0",
        )
    console.print(table)
    console.print("[dim]Expand a run with `reach run <batch-id>`.[/dim]")


def _print_run_status(s: dict) -> None:
    state = s.get("state", "")
    counts = s.get("counts") or {}
    rollout = s.get("rollout") or {}
    waves = rollout.get("waves") or []
    wave_total = s.get("wave_total") or 1
    wave_size = max(waves) if waves else (s.get("total") or 0)
    cur = min((s.get("current_wave") or 0) + 1, wave_total)
    console.print(f"[bold]Run:[/bold] [magenta]{s.get('run_id')}[/magenta]  {_status_color(state)}")
    console.print(f"[dim]Command:[/dim] {s.get('command', '')}")
    # Every run is wave-based: show its wave size, strategy, and failure policy.
    console.print(f"[dim]Wave size:[/dim] {wave_size}   "
                  f"[dim]Strategy:[/dim] {(rollout.get('mode') or 'auto').upper()}   "
                  f"[dim]On failure:[/dim] {(rollout.get('on_failure') or 'stop').upper()}")
    wtxt = f"  [dim]{waves}[/dim]" if wave_total > 1 and waves else ""
    held = f"  [dim]{s.get('staged', 0)} held[/dim]" if s.get("staged") else ""
    console.print(f"[cyan]Wave {cur} of {wave_total}[/cyan]{held}{wtxt}")
    console.print(f"[green]{counts.get('ok', 0)} ok[/green]  "
                  f"[red]{counts.get('failed', 0)} failed[/red]  "
                  f"[yellow]{counts.get('pending', 0)} pending[/yellow]  "
                  f"{counts.get('running', 0)} running")


@runs_app.command("status")
def runs_status(run_id: str = typer.Argument(..., help="Run id (from `reach fleets runs` or `fleets exec`)")):
    """Show a run's status, incl. staged-rollout wave progress."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        s = client.get_run(run_id)
    except requests.RequestException as e:
        _http_die(e)
    if _emit(s):
        return
    _print_run_status(s)


def _run_control_cmd(action: str, run_id: str, fn) -> None:
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        result = fn(client, run_id)
    except requests.RequestException as e:
        _http_die(e)
    if _emit(result):
        return
    console.print(f"[green]Run {action}.[/green] [magenta]{run_id}[/magenta] "
                  f"-> {_status_color(result.get('state', ''))}")


@runs_app.command("pause")
def runs_pause(run_id: str = typer.Argument(..., help="Run id of a staged run")):
    """Pause a staged run - hold its not-yet-released waves."""
    _run_control_cmd("paused", run_id, lambda c, r: c.pause_run(r))


@runs_app.command("resume")
def runs_resume(run_id: str = typer.Argument(..., help="Run id of a paused run")):
    """Resume a paused staged run - release the next wave."""
    _run_control_cmd("resumed", run_id, lambda c, r: c.resume_run(r))


@runs_app.command("cancel")
def runs_cancel(run_id: str = typer.Argument(..., help="Run id of a staged run")):
    """Cancel a staged run - drop its not-yet-released waves."""
    _run_control_cmd("canceled", run_id, lambda c, r: c.cancel_run(r))


# Fleet approvals are their own group (separate from standalone `reach approvals`).
fleets_approvals_app = typer.Typer(help="View or request a fleet's shared approvals.", no_args_is_help=True)
fleets_app.add_typer(fleets_approvals_app, name="approvals")


@fleets_approvals_app.command("list")
def fleets_approvals_list(
    fleet: Optional[str] = typer.Argument(None, help="Fleet id or name (default: `reach fleets use`)"),
    pending: bool = typer.Option(False, "--pending", help="Show your pending requests for this fleet"),
    denied: bool = typer.Option(False, "--denied", help="Show your denied requests for this fleet"),
    expired: bool = typer.Option(False, "--expired", help="Show your expired approvals for this fleet"),
):
    """Show a fleet's approval records (shared by every member).

    Default: currently effective approved commands, fleet-wide.
    --pending / --denied / --expired: your own records filtered by status.
    """
    if sum([pending, denied, expired]) > 1:
        console.print("[red]Error:[/red] use only one of --pending, --denied, --expired at a time")
        raise typer.Exit(2)
    status = "pending" if pending else "denied" if denied else "expired" if expired else "approved"

    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    try:
        data = client.list_fleet_approved(fleet_obj["fleet_id"], status=status)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("approvals", [])
    label = fleet_obj.get("name") or fleet_obj["fleet_id"]
    _empty = {
        "approved": f"No approved commands for fleet {label}.",
        "pending":  f"No pending requests for fleet {label}.",
        "denied":   f"No denied requests for fleet {label}.",
        "expired":  f"No expired approvals for fleet {label}.",
    }
    console.print(f"[bold]Fleet approvals:[/bold] [magenta]{label}[/magenta]")
    if not items:
        console.print(f"[yellow]{_empty.get(status, 'No records.')}[/yellow]")
        return

    # Fleets are host-only, so approvals are always command strings (no k8s rules).
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
        row = [a.get("command", ""), a.get("requester_name") or a.get("requested_by") or "-"]
        if show_status:
            st = a.get("status", "")
            style = _STATUS_STYLE.get(st, "")
            row.append(f"[{style}]{st}[/{style}]" if style else st)
        row += [at, _expires_label(a)]
        table.add_row(*row)
    console.print(table)


@fleets_approvals_app.command("request")
def fleets_approvals_request(
    fleet: str = typer.Argument(..., help="Fleet id or name"),
    command: str = typer.Argument(..., help="The command to request approval for (quote if multi-word)"),
    duration: Optional[str] = typer.Option(None, "--duration", "-d", help="For operators: approve for this long (e.g. 8h, 7d, permanent)"),
):
    """Request approval for a command on a whole fleet (developer), or pre-approve it (operator).

    Every member shares the fleet's approvals, so this covers current and future members.
    """
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    fleet_obj = _resolve_fleet(client, fleet)
    try:
        r = client.create_approval(command, fleet_id=fleet_obj["fleet_id"], duration=duration)
    except requests.RequestException as e:
        _http_die(e)
    if _emit(r):
        return
    label = fleet_obj.get("name") or fleet_obj["fleet_id"]
    if r.get("status") == "approved":
        console.print(f"[green]Pre-approved for fleet[/green] [magenta]{label}[/magenta]. [dim]{r.get('approval_id','')}[/dim]")
    else:
        console.print(f"[yellow]Requested for fleet[/yellow] [magenta]{label}[/magenta] - pending review. [dim]{r.get('approval_id','')}[/dim]")
    console.print("[dim]Operators review with `reach approvals approve/deny <approval-id>`.[/dim]")


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
        raise typer.Exit(2)


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


# Verb aliases so muscle memory works both ways (`alias add`/`rm` == `set`/`remove`).
# Hidden so `--help` isn't cluttered with duplicate rows.
alias_app.command("add", hidden=True, help="Alias for `set`.")(alias_set)
alias_app.command("rm", hidden=True, help="Alias for `remove`.")(alias_remove)


# ---------------------------------------------------------------------------
# reach exec [--agent <id|alias>] -- <command>
# ---------------------------------------------------------------------------
def _poll_jobs(client: ReachClient, jobs: list, timeout: int) -> dict:
    """Poll a list of {job_id, ...} until each is terminal or the deadline passes.
    Returns {job_id: result}. Missing ids timed out."""
    results: dict = {}
    pending = {j["job_id"]: j for j in jobs}
    deadline = time.monotonic() + timeout
    with console.status(f"[bold green]Waiting for {len(pending)} result(s)...[/bold green]", spinner="dots"):
        while pending and time.monotonic() <= deadline:
            for job_id in list(pending):
                try:
                    r = client.get_job(job_id)
                except requests.RequestException:
                    continue
                if r.get("status") in TERMINAL_STATUSES:
                    results[job_id] = r
                    del pending[job_id]
            if pending:
                time.sleep(POLL_INTERVAL_SECONDS)
    return results


def _render_fanout_results(jobs: list, results: dict) -> bool:
    """Print a per-target result table (Target/Status/Exit/Duration/Job ID).
    Returns True if any target failed or timed out."""
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Job ID", no_wrap=True)
    any_failed = False
    for j in jobs:
        label = j.get("hostname") or j.get("agent_id", "")
        r = results.get(j["job_id"])
        if not r:
            table.add_row(label, "[yellow]TIMEOUT[/yellow]", "-", "-", j["job_id"])
            any_failed = True
            continue
        status = r.get("status", "")
        code = r.get("exit_code")
        dur = r.get("duration_ms")
        if status != "SUCCEEDED" or (code is not None and code != 0):
            any_failed = True
        table.add_row(label, _status_color(status), "-" if code is None else str(code),
                      f"{dur}ms" if dur is not None else "-", j["job_id"])
    console.print(table)
    console.print("[dim]Full output of any target: `reach job <job-id>`.[/dim]")
    return any_failed


def _exec_fanout_by_tag(client: ReachClient, tag: str, command: str, *, agent_type: Optional[str],
                        yes: bool, timeout: int, no_wait: bool) -> None:
    """Fan a command out to standalone agents carrying `tag`, via the server batch
    endpoint. Type-homogeneous: a shell command isn't a kubectl command, so a tag
    that spans host + k8s must be disambiguated with --type. Fleet members are
    excluded - fan out to a fleet with `reach fleets exec`."""
    # Preview client-side so we can confirm before dispatching (the server is
    # authoritative and re-checks on dispatch).
    try:
        agents = client.list_agents(tag=tag).get("agents", [])
    except requests.RequestException as e:
        _http_die(e)
    standalone = [a for a in agents if not a.get("fleet_id") and a.get("status") == "ACTIVE"
                  and a.get("writable") is not False]
    present = {(a.get("type") or "host") for a in standalone}
    resolved_type = agent_type
    if resolved_type is None:
        if len(present) > 1:
            _die("tag matches both host and k8s agents - pass --type host or --type k8s", 2)
        resolved_type = present.pop() if present else "host"
    targets = [a for a in standalone if (a.get("type") or "host") == resolved_type]
    if not targets:
        _die(f"no writable, active standalone {resolved_type} agents with tag '{tag}'", 2)

    if not yes and not _JSON["on"]:
        # Interactive: server-resolved plan (matched agents, wave size/strategy/failure
        # policy) before the Proceed? prompt. Skip with --force (or --json for scripting).
        try:
            preview = client.fanout_by_tag(tag, command, agent_type=resolved_type, dry_run=True)
        except requests.RequestException as e:
            _http_die(e)
        _print_fanout_preview(preview)
        if not Confirm.ask("Proceed?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(2)

    try:
        result = client.fanout_by_tag(tag, command, agent_type=resolved_type)
    except requests.RequestException as e:
        _http_die(e)

    jobs = result.get("jobs", [])
    skipped = result.get("skipped", [])
    run_id = result.get("run_id")

    if no_wait or not jobs:
        if _emit(result):
            return
        console.print(f"[dim]Dispatched to {len(jobs)} agent(s); {len(skipped)} skipped."
                      + (f"  Batch: [magenta]{run_id}[/magenta]" if run_id else "") + "[/dim]")
        for s in skipped:
            console.print(f"  [yellow]skip[/yellow] {s.get('hostname') or s.get('agent_id')} - {s.get('reason')}")
        for j in jobs:
            console.print(f"  [dim]{j.get('hostname') or j['agent_id']}: job {j['job_id']}[/dim]")
        if run_id:
            console.print(f"[dim]Check results later with `reach run {run_id}` (or `reach runs`).[/dim]")
        return

    results = _poll_jobs(client, jobs, timeout)
    if _emit({**result, "results": [
        {**j, **{k: results.get(j["job_id"], {}).get(k) for k in ("status", "exit_code", "duration_ms")}}
        for j in jobs]}):
        return
    console.print(f"[dim]Dispatched to {len(jobs)} {resolved_type} agent(s); {len(skipped)} skipped."
                  + (f"  Batch: [magenta]{run_id}[/magenta]" if run_id else "") + "[/dim]")
    for s in skipped:
        console.print(f"  [yellow]skip[/yellow] {s.get('hostname') or s.get('agent_id')} - {s.get('reason')}")
    if _render_fanout_results(jobs, results):
        raise typer.Exit(1)


@app.command(
    name="exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def exec_cmd(
    ctx: typer.Context,
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID to target (overrides default)"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Fan out to all standalone agents with this tag (e.g. env:prod)"),
    agent_type: Optional[str] = typer.Option(None, "--type", help="With --tag: pick host or k8s when the tag spans both"),
    yes: bool = typer.Option(False, "--yes", "-y", "--force", help="Skip the confirmation prompt (fan-out preview, or the single-agent write confirm)"),
    timeout: int = typer.Option(60, "--timeout", help="Seconds to wait for a result before giving up"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit the job and exit without waiting for the result"),
):
    """Execute a command on a remote agent, or fan out to a tag with --tag.

    Single agent: reach exec [--agent <id|alias>] -- <command>
    Fan-out:      reach exec --tag env:prod -- <command>   (standalone agents with that tag;
                  add --type host|k8s if the tag matches both)
    """
    args = ctx.args
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        _die("usage: reach exec [--agent <id|alias>] -- <command>", 2)

    full_command = " ".join(args)
    if agent_type and agent_type.lower() not in ("host", "k8s"):
        _die("--type must be host or k8s", 2)
    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")
    client = ReachClient(api_url, api_key)

    if tag:
        if agent:
            _die("use only one of --agent or --tag", 2)
        _exec_fanout_by_tag(client, tag, full_command,
                            agent_type=agent_type.lower() if agent_type else None,
                            yes=yes, timeout=timeout, no_wait=no_wait)
        return

    agent_id = cfg_module.resolve_agent(agent) if agent else cfg_module.require("default_agent_id")

    # Single agent: confirm before a *write* (destructive) command - the blast radius is
    # one host, but `rm -rf` still shouldn't run unprompted. Mirrors the fan-out
    # preview+confirm. Reads run straight through; --yes/--force and --json skip the prompt.
    if not yes and not _JSON["on"]:
        try:
            preview = client.create_job(agent_id, full_command, dry_run=True)
        except requests.RequestException as e:
            _http_die(e)
        if preview.get("is_write"):
            host = preview.get("hostname") or agent_id
            mode = preview.get("mode", "?")
            is_host = preview.get("type") != "k8s"
            label = "Write command" + (" (host heuristic)" if is_host else "")
            console.print(f"[yellow]{label}[/yellow] on [cyan]{host}[/cyan] ([dim]mode:[/dim] {mode}):")
            console.print(f"  [bold]{full_command}[/bold]")
            if preview.get("approval_required"):
                console.print("[dim]This will be queued for approval (not pre-approved).[/dim]")
            elif is_host and mode == "wild":
                # Wild = unsandboxed: nothing on the agent blocks the write.
                console.print("[dim]Wild mode: runs unsandboxed - writes aren't blocked on the agent.[/dim]")
            if not Confirm.ask("Proceed?", default=False):
                console.print("[yellow]Aborted.[/yellow]")
                raise typer.Exit(2)

    try:
        job = client.create_job(agent_id, full_command)
    except requests.RequestException as e:
        _http_die(e)

    job_id = job["job_id"]
    if not _JSON["on"]:
        console.print(f"[dim]Job ID:[/dim] {job_id}  [dim]Agent:[/dim] {agent_id}")

    if no_wait:
        if _emit({"job_id": job_id, "agent_id": agent_id, "status": job.get("status", "PENDING")}):
            return
        console.print(f"[dim]Use `reach job {job_id}` to check the result.[/dim]")
        return

    def _poll_single() -> dict:
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                _die(f"timed out after {timeout}s waiting for the agent; job {job_id} is still queued "
                     f"- check later with `reach job {job_id}`", 2)
            try:
                r = client.get_job(job_id)
            except requests.RequestException as e:
                _http_die(e)
            if r.get("status", "") in TERMINAL_STATUSES:
                return r
            time.sleep(POLL_INTERVAL_SECONDS)

    if _JSON["on"]:
        result = _poll_single()  # no spinner in --json mode (keeps stdout clean)
    else:
        with console.status("[bold green]Waiting for result...[/bold green]", spinner="dots"):
            result = _poll_single()

    job_status = result.get("status", "")
    exit_code = result.get("exit_code")
    if not _emit(result):
        _print_job_result(result)
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
    except requests.RequestException as e:
        _http_die(e)

    if _emit(result):
        return
    console.print(f"[dim]Agent:[/dim] {result.get('agent_id', '')}")
    console.print(f"[dim]Command:[/dim] {result.get('command', '')}")
    _print_job_result(result)


# ---------------------------------------------------------------------------
# reach jobs
# ---------------------------------------------------------------------------
@app.command(name="jobs")
def jobs(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Filter by agent ID or alias"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (PENDING/RUNNING/SUCCEEDED/FAILED/REJECTED/EXPIRED)"),
    failed: bool = typer.Option(False, "--failed", help="Shortcut for failed/rejected jobs"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of jobs to show (max 100)"),
    cursor: Optional[str] = typer.Option(None, "--cursor", help="Pagination cursor from a previous response"),
):
    """Show your recent jobs on standalone agents (or one member with --agent).

    The unfiltered list is standalone-only; fleet-member jobs are grouped under the
    fleet (`reach fleets jobs <fleet>`). Targeting a member with --agent shows just
    that member's jobs.
    """
    api_url = cfg_module.require("api_url")
    api_key = cfg_module.require("api_key")

    agent_id = cfg_module.resolve_agent(agent) if agent else None

    client = ReachClient(api_url, api_key)
    try:
        data = client.list_jobs(agent_id=agent_id, limit=limit, cursor=cursor)
    except requests.RequestException as e:
        _http_die(e)

    items = data.get("jobs", [])
    hidden_fleet_jobs = False
    if not agent_id:
        # The cross-agent list is standalone-only; fleet-member jobs live under the
        # fleet (a single fan-out would otherwise flood this view). An explicit
        # --agent <member> query is bounded to one agent, so it's shown as-is.
        before = len(items)
        items = [j for j in items if not j.get("agent_fleet_id")]
        hidden_fleet_jobs = len(items) < before

    if failed:
        items = [j for j in items if j.get("status") in ("FAILED", "REJECTED")
                 or (j.get("exit_code") not in (0, None))]
    elif status:
        want = status.upper()
        items = [j for j in items if (j.get("status") or "").upper() == want]

    if _emit({**data, "jobs": items}):  # full envelope (keeps next_cursor), filtered to what's shown
        return
    if not items:
        console.print("[yellow]No jobs found.[/yellow]")
        if hidden_fleet_jobs:
            console.print("[dim]Fleet jobs are hidden here - see `reach fleets jobs <fleet>`.[/dim]")
        return

    aliases = cfg_module.list_aliases()
    id_to_alias = {v: k for k, v in aliases.items()}

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Job ID", no_wrap=True)
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
            j.get("job_id", ""),
            created,
            agent_label,
            j.get("command", ""),
            _status_color(j.get("status", "")),
            dur_label,
        )

    console.print(table)
    console.print("[dim]See a job's full output with `reach job <job-id>`.[/dim]")

    if hidden_fleet_jobs:
        console.print("[dim]Fleet-member jobs are hidden here - see `reach fleets jobs <fleet>`.[/dim]")

    next_cursor = data.get("next_cursor")
    if next_cursor:
        console.print(f"\n[dim]More results available. Run with --cursor {next_cursor} to see the next page.[/dim]")


@app.command(name="run")
def run(run_id: str = typer.Argument(..., help="Batch id from `reach runs`")):
    """Show the per-agent results of one tag fan-out run (batch)."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        data = client.list_jobs(run_id=run_id, limit=100)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(data):
        return
    items = data.get("jobs", [])
    console.print(f"[bold]Run:[/bold] [magenta]{run_id}[/magenta]")
    if not items:
        console.print("[yellow]No jobs found for this batch.[/yellow]")
        return

    tag = next((j.get("run_tag") for j in items if j.get("run_tag")), None)
    console.print(f"[dim]Command:[/dim] {items[0].get('command', '')}")
    if tag:
        console.print(f"[dim]Tag:[/dim] [cyan]{tag}[/cyan]")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Job ID", no_wrap=True)
    for j in items:
        code = j.get("exit_code")
        dur = j.get("duration_ms")
        table.add_row(
            j.get("agent_hostname") or j.get("agent_id", ""),
            _status_color(j.get("status", "")),
            "-" if code is None else str(code),
            f"{dur}ms" if dur is not None else "-",
            j.get("job_id", ""),
        )
    console.print(table)
    console.print("[dim]Full output of an agent: `reach job <job-id>`.[/dim]")


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


approvals_app = typer.Typer(help="View approval records for an agent.", no_args_is_help=True)
app.add_typer(approvals_app, name="approvals")


@approvals_app.command("list")
def approvals_list(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID or alias (default: default agent)"),
    pending: bool = typer.Option(False, "--pending", help="Show your pending requests for this agent"),
    denied: bool = typer.Option(False, "--denied", help="Show your denied requests for this agent"),
    expired: bool = typer.Option(False, "--expired", help="Show your expired approvals for this agent"),
):
    """Show approval records for an agent.

    Default: currently effective approved commands (agent-wide).
    --pending / --denied / --expired: your own records filtered by status.

    Host agents show the command; Kubernetes agents show the structured rule
    (verb / resource / namespace / name, where ✱ means "any").
    """
    flags = [pending, denied, expired]
    if sum(flags) > 1:
        console.print("[red]Error:[/red] use only one of --pending, --denied, --expired at a time")
        raise typer.Exit(2)

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
    except requests.RequestException as e:
        _http_die(e)

    # Fleet members share their fleet's approvals - view them under the fleet.
    if data.get("agent_fleet_id"):
        console.print(f"[yellow]{agent_id} is a fleet member.[/yellow] Its approvals are shared by the "
                      f"fleet - view them with [cyan]reach fleets approvals {data['agent_fleet_id']}[/cyan].")
        raise typer.Exit(2)

    if _emit(data):
        return
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

    # A single agent is one type, so the result set is homogeneous. Kubernetes
    # approvals are structured rules - render verb/resource/namespace/name as
    # columns; host approvals render the command string.
    is_k8s = any(a.get("k8s_rule") for a in items)
    show_status = status != "approved"

    if is_k8s:
        console.print("[dim]Kubernetes agent - showing structured rules (✱ = any)[/dim]")

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    if is_k8s:
        table.add_column("Verb")
        table.add_column("Resource")
        table.add_column("Namespace")
        table.add_column("Name")
    else:
        table.add_column("Command")
    table.add_column("Requested by", style="dim")
    if show_status:
        table.add_column("Status")
    table.add_column("At", style="dim")
    table.add_column("Expires")

    def _rule_cell(v) -> str:
        # Show a wildcard as a dim asterisk so it reads as "any".
        return "[dim]✱[/dim]" if v in ("*", "", None) else str(v)

    for a in items:
        at = (a.get("reviewed_at") or a.get("created_at") or "")[:19].replace("T", " ")
        if is_k8s:
            rule = a.get("k8s_rule") or {}
            row = [
                _rule_cell(rule.get("verb")),
                _rule_cell(rule.get("resource")),
                _rule_cell(rule.get("namespace")),
                _rule_cell(rule.get("name")),
            ]
        else:
            row = [a.get("command", "")]
        row.append(a.get("requester_name") or a.get("requested_by") or "-")
        if show_status:
            st = a.get("status", "")
            style = _STATUS_STYLE.get(st, "")
            row.append(f"[{style}]{st}[/{style}]" if style else st)
        row += [at, _expires_label(a)]
        table.add_row(*row)
    console.print(table)


@approvals_app.command("request")
def approvals_request(
    command: str = typer.Argument(..., help="The command to request approval for (quote if multi-word)"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Target agent id or alias (default: default agent)"),
    duration: Optional[str] = typer.Option(None, "--duration", "-d", help="For operators: approve for this long (e.g. 8h, 7d, permanent)"),
):
    """Request approval for a command on a standalone agent (developer), or pre-approve it (operator).

    For a whole fleet, use `reach fleets approvals request <fleet> <cmd>`.
    Developers create a pending request; operators/admins create it approved.
    """
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    agent_id = cfg_module.resolve_agent(agent) if agent else cfg_module.require("default_agent_id")

    try:
        r = client.create_approval(command, agent_id=agent_id, duration=duration)
    except requests.RequestException as e:
        _http_die(e)

    if _emit(r):
        return
    aid = r.get("approval_id", "")
    if r.get("status") == "approved":
        console.print(f"[green]Pre-approved.[/green] [dim]{aid}[/dim]")
    else:
        console.print(f"[yellow]Requested - pending operator/admin review.[/yellow] [dim]{aid}[/dim]")


@approvals_app.command("approve")
def approvals_approve(
    approval_id: str = typer.Argument(..., help="Approval id (from `reach approvals list --pending`)"),
    duration: Optional[str] = typer.Option(None, "--duration", "-d", help="Approve for this long (e.g. 8h, 7d, permanent, now to expire)"),
):
    """Approve a pending request, or change an approved record's duration (operator+)."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        r = client.approve_approval(approval_id, duration=duration)
    except requests.RequestException as e:
        _http_die(e)
    if _emit(r):
        return
    console.print(f"[green]Approved.[/green] [dim]{approval_id}[/dim]")


@approvals_app.command("deny")
def approvals_deny(
    approval_id: str = typer.Argument(..., help="Approval id (from `reach approvals list --pending`)"),
):
    """Deny a pending approval request (operator+). Terminal - cannot be reversed."""
    client = ReachClient(cfg_module.require("api_url"), cfg_module.require("api_key"))
    try:
        r = client.deny_approval(approval_id)
    except requests.RequestException as e:
        _http_die(e)
    if _emit(r):
        return
    console.print(f"[red]Denied.[/red] [dim]{approval_id}[/dim]")


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
        raise typer.Exit(2)

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
- `approved`: reads always run. Write commands only run if pre-approved by an admin. If a write is not on the approved list the agent blocks it and automatically files a pending approval record - the command does NOT run silently. Use `reach approvals list --pending` to confirm it, then tell the user an operator/admin must approve it. **Never approve or pre-approve it yourself** (do not run `reach approvals approve` / `request` / `deny`) - approval is a human review control. Do not retry the blocked command.

**Access level** - mode combined with whether the agent runs as root:
- `open`: wild + root. Maximum blast radius. Every command runs with full system privileges. Treat all writes as irreversible. Always explain before acting.
- `elevated`: wild (non-root) or approved (root). High impact - proceed carefully.
- `managed`: approved (non-root) or readonly (root). Moderate restrictions.
- `restricted`: readonly + non-root. Safest - writes always rejected, no root access.

**Your access** - separate from the agent's mode, your account may be granted **read-only** or **read-write** access to each agent. If your access is read-only, write commands are rejected (403) no matter what the agent's mode is - only reads run. `reach status` shows "Your access: read-only" and `reach agents list` marks such agents `(read-only)`. Do not attempt writes on a read-only agent.

### Common commands

```bash
reach agents list                       # standalone machines
reach agents show <id>                  # one agent's mode, access, tags, capabilities
reach status
{all_examples.rstrip()}
reach jobs                              # recent jobs on standalone agents (--failed to filter)
reach job <job-id>                      # full output of one job
reach runs                              # tag fan-out runs (exec --tag), with pass/fail counts
reach run <batch-id>                    # per-agent results of one tag fan-out run
```

### Fleets

A **fleet** is a group of identical hosts (e.g. an autoscaling group) that share a
mode and approvals. Fleet members are managed as a group, not shown in `reach agents list`.

```bash
reach fleets list                       # fleets you can access
reach fleets agents <fleet>             # a fleet's members
reach fleets exec <fleet> -- <command>  # run on EVERY member (high impact - confirm first)
reach fleets jobs <fleet>               # jobs across all members (--member <id|host> for one)
reach fleets runs <fleet>               # fan-out runs, with pass/fail counts
reach fleets run <batch-id>             # per-member results of one run
reach fleets approvals list <fleet>     # a fleet's approved commands (shared by members)
```

### Rules

{rule_agent}* Run `reach status` before write or restart commands to confirm the current mode and access level.
* Do not attempt writes on an agent where your access is **read-only** (shown by `reach status` / `reach agents list`) - they are rejected with 403.
* `reach fleets exec <fleet>` (all members of a fleet) and `reach exec --tag <key:value>` (all standalone agents with a tag) are **fan-outs** - they run on many machines at once. Both ask for confirmation; show the user the targets and command, and never pass `-y` unless the user explicitly approved that exact fan-out. Prefer a read-only check on one machine first. Tag fan-out is host-vs-k8s homogeneous - if a tag spans both, it asks you to pass `--type host` or `--type k8s` (a shell command is not a kubectl command).
* (Via the MCP server, the equivalent `fleet_exec` and `exec_by_tag` tools are confirm-gated: call with `confirm=false` for a dry-run preview, show the user, and only pass `confirm=true` after they approve.)
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


def _type_label(agent_type: Optional[str]) -> str:
    colors = {
        "k8s": "[blue]k8s[/blue]",
        "host": "[cyan]host[/cyan]",
    }
    return colors.get(agent_type or "", "-")


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
        raise typer.Exit(2)
    console.print(f"[green]Switched to profile:[/green] [cyan]{name}[/cyan]")


@profile_app.command("delete")
def profile_delete(name: str = typer.Argument(..., help="Profile name to delete")):
    """Delete a profile."""
    full = cfg_module.load()
    if name not in full.get("profiles", {}):
        console.print(f"[red]Error:[/red] profile '{name}' not found.")
        raise typer.Exit(2)
    if full.get("active_profile") == name:
        console.print(f"[red]Error:[/red] cannot delete the active profile. Run 'reach profile use <other>' first.")
        raise typer.Exit(2)
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
        raise typer.Exit(2)
    console.print(f"[green]Renamed profile:[/green] [cyan]{old}[/cyan] → [cyan]{new}[/cyan]")


# ---------------------------------------------------------------------------
# reach mcp
# ---------------------------------------------------------------------------
@app.command()
def mcp():
    """Start the reach MCP server (stdio transport for any MCP-compatible client)."""
    try:
        from reach.mcp_server import main as mcp_main
    except ImportError:
        _die("the MCP server needs the 'mcp' package - reinstall reach (it's a "
             "dependency): `pip install --force-reinstall reach` or `uv tool install reach`.", 2)
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
        ("reach --version  (or -V)",                          "Show CLI version"),
        ("reach config show",                                 "Show active profile, default agent/fleet, and aliases"),
        ("reach --json <command>",                            "Output raw JSON instead of tables (for scripting)"),
        ("reach man",                                         "This full command reference"),
    ])

    section("Profiles  (multiple tenants / deployments)", [
        ("reach profile list",                               "List all profiles; active one is marked"),
        ("reach profile use <name>",                         "Switch active profile"),
        ("reach profile rename <old> <new>",                 "Rename a profile"),
        ("reach profile delete <name>",                      "Delete a profile"),
    ])

    section("Agents", [
        ("reach agents list",                                "List your standalone machines (fleet members: `reach fleets agents`)"),
        ("reach agents list --tag <key:value>",              "Filter machines by tag"),
        ("reach agents show <id|alias>",                     "Full detail of one agent (mode, access, tags, capabilities)"),
        ("reach agents use <id|alias>",                      "Set default machine"),
        ("reach status",                                     "Show default machine status and access level"),
        ("reach alias set <name> <id>",                      "Create a friendly alias for an agent (or `add`)"),
        ("reach alias list",                                  "List all aliases"),
        ("reach alias remove <name>",                        "Remove an alias (or `rm`)"),
    ])

    section("Fleets  (autoscaling groups of hosts sharing a join token)", [
        ("reach fleets list",                                "List fleets you can access, with member counts and access"),
        ("reach fleets use <id|name>",                       "Set the default fleet (so fleet commands can omit it)"),
        ("reach fleets show [<id|name>]",                    "One fleet's detail (mode, tags, member breakdown, access)"),
        ("reach fleets agents [<id|name>]",                  "List a fleet's member agents"),
        ("reach fleets exec [<id|name>] -- <cmd>",           "Run a command on every member (confirms first; -y to skip)"),
        ("reach fleets jobs [<id|name>]",                    "Recent jobs across all members of the fleet"),
        ("reach fleets jobs [<id|name>] --member <id|host>", "Jobs for one member of the fleet"),
        ("reach fleets runs [<id|name>]",                    "Fan-out runs (one row per `fleets exec`) with pass/fail counts"),
        ("reach fleets run <batch-id>",                      "Per-member results of one fan-out run"),
        ("reach fleets approvals list [<id|name>]",          "A fleet's approved commands (shared by members); --pending/--denied/--expired"),
        ("reach fleets approvals request <id|name> <cmd>",   "Request/pre-approve a command for the whole fleet"),
    ])

    section("Execution", [
        ("reach exec -- <cmd>",                              "Run command on default machine"),
        ("reach exec --agent <id|alias> -- <cmd>",           "Run command on a specific machine"),
        ("reach exec --tag <key:value> -- <cmd>",            "Fan out to standalone agents with a tag (confirms; -y; --type host|k8s)"),
        ("reach exec --timeout <s> -- <cmd>",                "Override wait timeout (default 60 s)"),
        ("reach exec --no-wait -- <cmd>",                    "Submit and exit; poll later with `reach job <id>`"),
        ("reach job <job_id>",                               "Re-view stdout / stderr of a past job"),
        ("reach jobs",                                       "Recent jobs on standalone agents (fleet: `reach fleets jobs`)"),
        ("reach jobs --agent <id|alias>",                    "Filter to one machine (a member shows its own jobs)"),
        ("reach jobs --failed / --status <S>",               "Filter by outcome / status"),
        ("reach jobs --limit <n>  /  --cursor <c>",          "Page size (max 100) / next page"),
        ("reach runs",                                       "Tag fan-out runs across standalone agents (fleet: `reach fleets runs`)"),
        ("reach run <batch-id>",                             "Per-agent results of one tag fan-out run"),
    ])

    section("Approvals  (standalone agents; fleet approvals: `reach fleets approvals`)", [
        ("reach approvals list [--agent <id|alias>]",        "Effective approved commands/rules (default or a specific agent)"),
        ("reach approvals list --pending / --denied / --expired", "Your own records by status"),
        ("reach approvals request <cmd> [--agent <id>]",     "Request/pre-approve for a standalone agent (fleets: `reach fleets approvals request`)"),
        ("reach approvals approve <approval-id> [--duration <d>]", "Approve a pending request, agent or fleet (operator+)"),
        ("reach approvals deny <approval-id>",               "Deny a pending request, agent or fleet (operator+)"),
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
