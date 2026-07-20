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

**Tools:** `get_context` (call first - identity, default agent, aliases in one shot), `whoami`, `list_agents`, `get_agent`, `exec_command`, `exec_by_tag` (confirm-gated tag fan-out), `list_tag_runs`/`list_tag_run` (tag fan-out history), `get_job`, `list_history`; for fleets `list_fleets`, `list_fleet_agents`, `list_fleet_jobs`, `list_fleet_runs`, `list_fleet_run`, `list_fleet_approved`, `fleet_exec` (confirm-gated fan-out); plus `list_approved_commands`, `list_pending_approvals`. It is deliberately **read-only for approvals** (no create/approve/deny tool - an AI can't approve its own request), and the fan-out tools require a `confirm=true` after a dry-run preview. The MCP server redacts secrets from output before the LLM sees it.

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
| `reach --json <command>` | Emit the result as a JSON object instead of a table (for scripting/piping); errors too. See [Scripting](#scripting). |
| `reach man` | Full command reference in the terminal |
| **Agents** | |
| `reach agents list` | List your **standalone** machines with mode and access level (`(read-only)` marks agents you can't write to). Fleet members are listed per-fleet with `reach fleets agents <fleet>`. |
| `reach agents list --tag <key:value>` | Filter machines by tag |
| `reach agents show <id\|alias>` | Full detail of one agent (mode, access, tags, capabilities) |
| `reach agents use <id\|alias>` | Set default machine |
| `reach status` | Show default machine status, access level, and whether your access is read-only |
| `reach alias set <name> <id>` | Create alias (`add` also works) |
| `reach alias list` | List aliases |
| `reach alias remove <name>` | Remove alias (`rm` also works) |
| **Execution** | |
| `reach exec -- <cmd>` | Run on default machine |
| `reach exec --agent <id\|alias> -- <cmd>` | Run on a specific machine |
| `reach exec --tag <key:value> [--type host\|k8s] -- <cmd>` | **Fan out** to standalone agents with a tag (server-batched; confirms first, `-y` to skip). Type-homogeneous - pass `--type` if the tag matches both host and k8s. |
| `reach exec --timeout <s> -- <cmd>` | Override wait timeout (default 60s) |
| `reach exec --no-wait -- <cmd>` | Submit and exit; check later with `reach job <id>` |
| `reach job <job_id>` | Re-view stdout/stderr of a past job |
| `reach jobs [--agent <id\|alias>] [--failed] [--status <S>] [--limit <n>] [--cursor <c>]` | Recent jobs on **standalone** agents (a member with `--agent` shows its own; fleet-member list: `reach fleets jobs <fleet>`) |
| `reach runs [--limit <n>]` | **Tag fan-out runs** across standalone agents - one row per `exec --tag`, with the tag and member/OK/fail counts (the standalone counterpart to `reach fleets runs`) |
| `reach run <batch-id>` | Per-agent results of one tag fan-out run; drill into any with `reach job <id>` |
| **Fleets** | |
| `reach fleets list` | List the fleets you can access, with member counts and read-only/read-write access |
| `reach fleets use <id\|name>` | Set the default fleet (fleet commands can then omit the fleet arg) |
| `reach fleets show [<id\|name>]` | One fleet's detail (mode, tags, member breakdown, your access) |
| `reach fleets agents [<id\|name>]` | List a fleet's member agents |
| `reach fleets exec [<id\|name>] -- <cmd>` | Run a command on **every** member (confirms first; `-y` to skip, `--no-wait`/`--timeout` like `reach exec`). The fleet may be omitted to use the `reach fleets use` default - `reach fleets exec -- systemctl restart app` works for any command, single- or multi-word. |
| `reach fleets jobs [<id\|name>] [--limit <n>] [--cursor <c>]` | Recent jobs across all members of a fleet (with Job ID + Batch columns) |
| `reach fleets jobs [<id\|name>] --member <id\|host>` | Jobs for a **single** member of the fleet |
| `reach fleets runs [<id\|name>]` | Fan-out **runs** - one row per `fleets exec`, with member/OK/fail counts |
| `reach fleets run <batch-id>` | Per-member results of one fan-out run; drill into any with `reach job <id>` |
| `reach fleets approvals list [<id\|name>] [--pending\|--denied\|--expired]` | A fleet's approval records (shared by every member) |
| `reach fleets approvals request <id\|name> <cmd> [--duration <d>]` | Request/pre-approve a command for the whole fleet |
| **Approvals** (standalone agents) | |
| `reach approvals list [--agent <id\|alias>] [--pending\|--denied\|--expired]` | Effective approved commands/rules, or your own records by status (fleets: `reach fleets approvals list`) |
| `reach approvals request <cmd> [--agent <id>] [--duration <d>]` | Request/pre-approve for a **standalone agent** (fleets: `reach fleets approvals request`). Give a plain command for either agent type: for a **k8s** agent the CLI structures it into the rule the backend requires (`kubectl …` → a `verb/resource/namespace/name` rule, a non-kubectl tool like `helm` → a `{bin, args[]}` rule) |
| `reach approvals approve <approval-id> [--duration <d>]` | Approve a pending request - agent **or** fleet, by id (operator+) |
| `reach approvals deny <approval-id>` | Deny a pending request - agent **or** fleet, by id (operator+) |
| **AI integration** | |
| `reach agent-init [--for claude\|cursor\|system-prompt\|mcp]` | Generate agent context / MCP config |
| `reach mcp` | Start the MCP server (stdio; launched by your MCP client) |

Policy modes and how approvals are evaluated are documented in [POLICIES.md](../POLICIES.md).

### Scripting

- **`--json`** (global): emits the command's data as a JSON **object** instead of a table, e.g. `reach --json jobs | jq '.jobs[].status'`. The shape is the API response envelope, with any client-side-filtered collection (e.g. `jobs`/`agents` narrowed to standalone) replaced in place, so metadata like `next_cursor` is preserved and the JSON matches what the table shows. `exec` emits the final job result object. Errors are emitted as `{"error": "..."}` on stdout too, so failures are parseable.
- **Exit codes**: `0` success · `1` a **remote command** ran and failed (single `exec` passes the command's own exit code through; a fan-out returns `1` if any target failed) · `2` **reach itself** failed (bad usage, missing config, an API error, or an unreachable backend). So a script can tell "the command failed" from "reach couldn't run it." Unreachable backend / timeouts give a plain one-line message, not a stack trace.
- **Shell completion**: `reach --install-completion` (bash/zsh/fish) then restart your shell; `reach --show-completion` prints the script without installing.

> **Fleets** (reusable-join-token groups for autoscaling hosts) are *created* and configured in the tenant console. From the CLI you can list the fleets you can access (`reach fleets list`), see their members (`reach fleets agents <fleet>`), fan a command out to the whole fleet (`reach fleets exec <fleet> -- <cmd>`), and view fleet-wide jobs (`reach fleets jobs <fleet>`). Fleet members are **not** shown by `reach agents list` (which is standalone-only) - list them with `reach fleets agents <fleet>` - but a single member is still targetable one-off with `reach exec --agent <member>`. Fan-out requires **read-write** access to the fleet. See [SELF_HOSTING → Managing fleets](../SELF_HOSTING.md#managing-fleets).

## Development

```bash
cd cli
pip install -e .           # editable install
python -m pytest tests/    # run the CLI tests
```

The CLI is pure Python (Typer + Rich + requests + the `mcp` SDK). Source lives in [reach/](reach/): `main.py` (commands), `client.py` (HTTP), `config.py` (profiles/aliases), `mcp_server.py` (MCP tools).
