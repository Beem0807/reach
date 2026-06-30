# reach CLI

The `reach` command-line client - and the `reach-mcp` MCP server - for driving remote machines through a Reach backend. Talks to the backend over HTTPS with an API token; no SSH, no VPN, no open ports.

```bash
reach exec -- hostname
reach exec --agent prod -- docker ps
```

> New to Reach? Start with the [2-minute Quick Start](../README.md#-2-minute-quick-start) in the root README - the setup script installs this CLI and logs you in for you.

## Install

**With uv (recommended):**

```bash
uv tool install https://reach-releases.s3.amazonaws.com/cli/v0.1.0/reach-0.1.0-py3-none-any.whl
```

**With pip:**

```bash
pip install https://reach-releases.s3.amazonaws.com/cli/v0.1.0/reach-0.1.0-py3-none-any.whl
```

Requires Python ≥ 3.9. Installs two entry points: `reach` (the CLI) and `reach-mcp` (the MCP server). From a checkout you can install the local copy with `uv tool install --from ./cli reach` (or `pip install -e ./cli`).

## Log in

Create an API token in the tenant console (**API Tokens → New token**), then:

```bash
reach login --api-url "<your-api-url>" --api-key "<your-api-token>"
```

Credentials are stored in `~/.reach/config.json` (API URL, token, default agent, aliases) under the active profile. Point the CLI at a machine and run something:

```bash
reach agents list                 # your machines, with mode + access level
reach agents use agent_xxx        # set the default machine
reach exec -- hostname            # run on the default machine
```

## Aliases

Give machines friendly names:

```bash
reach alias set prod agent_xxx
reach alias set staging agent_yyy
reach exec --agent prod -- docker ps
reach alias list
```

## Multiple deployments (profiles)

If you use more than one Reach backend (e.g. a home server and a work server), keep each under its own profile:

```bash
reach login --profile home --api-url "<home-url>" --api-key "<home-token>"
reach login --profile work --api-url "<work-url>" --api-key "<work-token>"

reach profile list       # all profiles; the active one is marked
reach profile use home    # switch deployments
```

Each profile has its own API URL, token, default agent, and aliases. Every command runs against the active profile.

## AI-agent integration

`reach agent-init` writes the context your AI tool needs (so it uses `reach exec` automatically) and can print the MCP config:

```bash
reach agent-init                     # interactive: pick claude / cursor / system-prompt / mcp
reach agent-init --for claude        # writes CLAUDE.md
reach agent-init --for cursor        # writes .cursor/rules/reach.mdc
reach agent-init --for system-prompt # prints a snippet to paste anywhere
reach agent-init --for mcp           # prints the MCP server config
```

## MCP server

Reach ships a native [MCP](https://modelcontextprotocol.io) server. Any MCP-compatible client (Claude Code, Claude Desktop, Cursor, …) launches `reach mcp` as a subprocess and calls Reach tools directly - no CLI syntax, no output parsing. You never run it manually.

Add this to your MCP client settings (same block everywhere):

```json
{
  "mcpServers": {
    "reach": {
      "command": "reach",
      "args": ["mcp"]
    }
  }
}
```

Common locations: Claude Code `.claude/settings.json` (project) or `~/.claude.json` (global); Claude Desktop `~/Library/Application Support/Claude/claude_desktop_config.json`; Cursor `.cursor/mcp.json`. The server reads `~/.reach/config.json`, so run `reach login` first.

**Tools:** `get_context` (call first - identity, default agent, aliases in one shot), `whoami`, `list_agents`, `get_agent`, `exec_command`, `get_job`, `list_history`, `list_approved_commands`, `list_pending_approvals`. The MCP server redacts secrets from output before the LLM sees it.

## Command reference

| Command | Description |
|---|---|
| **Auth & setup** | |
| `reach login --api-url <url> --api-key <token>` | Store credentials (saves to `default` profile) |
| `reach login … --profile <name>` | Store credentials under a named profile |
| `reach profile list` | List all profiles |
| `reach profile use <name>` | Switch active profile |
| `reach profile rename <old> <new>` | Rename a profile |
| `reach profile delete <name>` | Delete a profile (not the active one) |
| `reach config show` | Show active profile, API URL, default agent, aliases |
| `reach whoami` | Show current user identity (user_id, tenant_id, name) |
| `reach --version`, `reach -V` | Show CLI version and exit |
| `reach man` | Full command reference in the terminal |
| **Agents** | |
| `reach agents list` | List machines with mode and access level |
| `reach agents list --tag <key:value>` | Filter machines by tag |
| `reach agents use <id\|alias>` | Set default machine |
| `reach status` | Show default machine status and access level |
| `reach alias set <name> <id>` | Create alias |
| `reach alias list` | List aliases |
| `reach alias remove <name>` | Remove alias |
| **Execution** | |
| `reach exec -- <cmd>` | Run on default machine |
| `reach exec --agent <id\|alias> -- <cmd>` | Run on a specific machine |
| `reach exec --timeout <s> -- <cmd>` | Override wait timeout (default 60s) |
| `reach exec --no-wait -- <cmd>` | Submit and exit; check later with `reach job <id>` |
| `reach job <job_id>` | Re-view stdout/stderr of a past job |
| `reach history [--agent <id\|alias>] [--limit <n>] [--cursor <c>]` | Browse recent jobs (max 100, default 20; paginate with the cursor) |
| **Approvals** (host: command; k8s: structured rule) | |
| `reach approvals list [--agent <id\|alias>]` | Effective approved commands/rules |
| `reach approvals list --pending` / `--denied` / `--expired` | Your own records by status |
| **AI integration** | |
| `reach agent-init [--for claude\|cursor\|system-prompt\|mcp]` | Generate agent context / MCP config |
| `reach mcp` | Start the MCP server (stdio; launched by your MCP client) |

Policy modes and how approvals are evaluated are documented in [POLICIES.md](../POLICIES.md).

## Development

```bash
cd cli
pip install -e .           # editable install
python -m pytest tests/    # run the CLI tests
```

The CLI is pure Python (Typer + Rich + requests + the `mcp` SDK). Source lives in [reach/](reach/): `main.py` (commands), `client.py` (HTTP), `config.py` (profiles/aliases), `mcp_server.py` (MCP tools).
