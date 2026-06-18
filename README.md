# reach

Give your AI agents controlled access to every machine you own - without SSH, VPNs, or open ports.

```bash
reach exec -- hostname
reach exec --agent prod -- docker ps
```

---

## Why Reach?

AI agents can reason about your code, but they cannot safely operate your remote machines by default.

Reach gives any AI agent - Claude Code, Cursor, custom LLM workflows, or your own automation - a controlled command bridge to your machines without requiring SSH, VPNs, public IPs, or inbound firewall rules.

---

## How it works

1. You deploy the Reach backend (Lambda or Docker).
2. You create a tenant, a user (for the CLI), and an agent (per machine) via the admin API.
3. You install the CLI on your local machine.
4. You install the agent on each remote machine.
5. The agent makes outbound HTTPS requests to your backend - no inbound ports needed.
6. Commands are queued via the CLI, the agent picks them up and runs them, results come back.

---

## Getting started

Reach is self-hosted - you deploy your own backend. Choose one:

**Local machine** (no cloud account needed):
```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

**AWS Lambda + DynamoDB** (low cost, AWS-native):
```bash
export TOKEN_PEPPER=$(openssl rand -hex 32)
export ADMIN_TOKEN=$(openssl rand -hex 32)
aws cloudformation create-stack \
  --stack-name reach-platform \
  --template-url https://reach-releases.s3.amazonaws.com/lambda/latest/template.yaml \
  --parameters \
    ParameterKey=TokenPepper,ParameterValue="$TOKEN_PEPPER" \
    ParameterKey=AdminToken,ParameterValue="$ADMIN_TOKEN" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND
```

**Docker + PostgreSQL** (any cloud):
```bash
docker run -d -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e ADMIN_TOKEN="<your-admin-token>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:0.1.0
```

Once deployed, create a tenant, a user under it, and an agent under it:

```bash
curl -s -X POST "$API_URL/admin/tenants" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

curl -s -X POST "$API_URL/admin/tenants/tenant_xxxxx/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "alice"}' | python3 -m json.tool

curl -s -X POST "$API_URL/admin/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "tenant_xxxxx"}' | python3 -m json.tool
```

Each returns ready-to-paste commands for the CLI and agent. Repeat the user step for each person who needs access - everyone gets their own token. See [SELF_HOSTING.md](SELF_HOSTING.md) for the full setup guide for all three deployment options.

---

## Install the CLI

**With uv (recommended):**

```bash
uv tool install https://reach-releases.s3.amazonaws.com/cli/v0.1.0/reach-0.1.0-py3-none-any.whl
```

**With pip:**

```bash
pip install https://reach-releases.s3.amazonaws.com/cli/v0.1.0/reach-0.1.0-py3-none-any.whl
```

Log in with the token from `/admin/tenants/{id}/users`:

```bash
reach login --api-url "<your-api-url>" --token "<your-token>"
```

---

## Add a machine

Use the `agent` install command from the `/admin/agents` response directly. Or manually:

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/agent/latest/install.sh | sudo bash -s -- \
  --api-url       "<your-api-url>" \
  --agent-id      "agent_xxx" \
  --install-token "install_xxx" \
  --yes
```

The script auto-detects your OS and architecture - one command works on Linux and macOS (Intel and Apple Silicon).

- **Linux** - installs as a systemd service, starts on boot, survives reboots.
- **macOS** - runs in the current terminal by default. Add `--background` to install as a LaunchDaemon under a dedicated system user (starts on boot, no terminal needed).

**`--yes`** skips all optional prompts and applies their defaults. Without it, the script prompts interactively for each choice.

| Flag | What it does | Default with `--yes` |
|---|---|---|
| `--yes` | Non-interactive mode - skip all prompts | - |
| `--grant-service-mgmt` | Grant `systemctl`/`launchctl` restart/start/stop via sudoers | ✅ on |
| `--no-grant-service-mgmt` | Skip the sudoers grant | - |
| `--grant-docker` | Add `reach-agent` to the `docker` group | ❌ off |
| `--background` | macOS only - install as a LaunchDaemon (starts on boot) | - |

Flags can be combined with `--yes` to override specific defaults, e.g. `--yes --grant-docker` or `--yes --no-grant-service-mgmt`.

Set it as your default:

```bash
reach agents use agent_xxx
```

**To remove an agent:**

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/agent/latest/install.sh | sudo bash -s -- --uninstall
```

Then delete the agent record via the admin API:

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

---

## Usage

```bash
reach agents list                           # list all your machines
reach agents list --tag env:prod            # filter by tag
reach status                                # show default agent status
reach exec -- <command>                     # run on default machine
reach exec --agent <id|alias> -- <command>  # run on specific machine
reach exec --no-wait -- <command>           # fire-and-forget; check with `reach job <id>`
```

### Aliases

Give your machines friendly names:

```bash
reach alias set prod agent_xxx
reach alias set staging agent_yyy

reach exec --agent prod -- docker ps
reach exec --agent staging -- uptime
reach alias list
```

### Multiple tenants (profiles)

If you access more than one reach deployment (e.g. a home server and a work server), use profiles to hold multiple credentials:

```bash
reach login --profile home --api-url "<home-url>" --token "<home-token>"
reach login --profile work --api-url "<work-url>" --token "<work-token>"

reach profile list       # see all profiles, active one is marked
reach profile use home   # switch to home deployment
reach profile use work   # switch to work deployment
```

Each profile has its own API URL, token, default agent, and aliases. All commands (`exec`, `agents list`, `history`, etc.) operate against the active profile.

---

## Admin operations

The admin API (authenticated with `ADMIN_TOKEN`) gives you visibility across all tenants.

**List agents for a tenant:**

```bash
curl -s "$API_URL/admin/agents?tenant_id=tenant_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**View job history** - filter by tenant, agent, or the user (`created_by`) who ran the command:

```bash
# All jobs for a tenant
curl -s "$API_URL/admin/jobs?tenant_id=tenant_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# All jobs by a specific user
curl -s "$API_URL/admin/jobs?created_by=user_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Paginate with cursor from previous response
curl -s "$API_URL/admin/jobs?tenant_id=tenant_xxxxx&cursor=<next_cursor>" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Every job record includes `created_by` (the `user_id` of whoever submitted it), so you can see who ran what and when.

**Control which agents a user can see** - by default every user sees all agents. Restrict a user to specific machines:

```bash
# Restrict alice to staging only
curl -s -X PUT "$API_URL/admin/tenants/tenant_xxxxx/users/user_alice/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_ids": ["agent_staging1", "agent_staging2"]}'

# Grant one more
curl -s -X POST "$API_URL/admin/tenants/tenant_xxxxx/users/user_alice/agents/agent_prod1" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Restore full access
curl -s -X PUT "$API_URL/admin/tenants/tenant_xxxxx/users/user_alice/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_ids": ["*"]}'
```

See [SELF_HOSTING.md](SELF_HOSTING.md) for the full admin API reference.

---

## Example use cases

```bash
reach exec --agent prod -- docker ps
reach exec --agent staging -- journalctl -u app --no-pager -n 100
reach exec --agent devbox -- git status
reach exec --agent k8s -- kubectl get pods -A
```

---

## AI agent integration

Run `reach agent-init` inside any project to generate context for your AI agent. It fetches your machines, prompts for a role for each, and writes a file that tells the agent to use `reach exec` automatically.

```bash
reach agent-init
```

```
Select your agent:
  1  claude        - writes CLAUDE.md
  2  cursor        - writes .cursor/rules/reach.mdc
  3  system-prompt - prints to stdout, paste anywhere
```

Or pass `--for` directly to skip the prompt:

```bash
reach agent-init --for claude        # CLAUDE.md for Claude Code
reach agent-init --for cursor        # .cursor/rules/reach.mdc for Cursor
reach agent-init --for system-prompt # paste into any agent or API call
```

---

## MCP server

Reach ships a native [MCP](https://modelcontextprotocol.io) server. Any MCP-compatible client (Claude Code, Claude Desktop, Cursor, or your own tooling) can call reach tools directly - no CLI syntax, no output parsing.

The client launches `reach mcp` as a subprocess and manages its lifecycle automatically. You don't run anything manually.

**Configure your MCP client** - the config block is the same for all clients:

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

Common locations:
- **Claude Code** - `.claude/settings.json` (project) or `~/.claude.json` (global)
- **Claude Desktop** - `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Cursor** - `.cursor/mcp.json`

The MCP server reads from `~/.reach/config.json` - make sure you've run `reach login` first.

**Available MCP tools:**

| Tool | Description |
|---|---|
| `whoami` | Show current authenticated user and tenant |
| `list_agents` | List all registered machines |
| `get_agent(agent_id)` | Get status of a specific machine |
| `exec_command(command, agent_id?, timeout?)` | Run a command and wait for the result |
| `get_job(job_id)` | Fetch result of a previously submitted job |
| `list_history(agent_id?, limit?)` | Browse recent job history |

---

## Policies

Each machine runs in one of three modes, configured via the admin API:

- **Wild** - allow all commands
- **Readonly** - block write and destructive commands
- **Approved** - only approved command patterns can run

Use the CLI to view the active policy:

```bash
reach policy show
reach policy show --agent prod
```

---

## Safety

Reach is designed for controlled command execution:

- No inbound ports are opened
- No SSH server is required
- Agents only make outbound HTTPS requests
- Commands have a default timeout of 60 seconds
- Job history is recorded for 7 days
- Policies are configured server-side - the CLI can view them but cannot change them

**Always blocked - regardless of mode:**

Destructive filesystem operations (`rm -rf /`, `mkfs`, `dd if=`), shutdown/reboot/poweroff, and fork bombs are rejected by the server before the agent ever sees them.

**Blocked in readonly mode:**

File writes and deletes, process kills, service restarts, package installs, container mutations (`docker run/stop/rm`), firewall changes, user management, and privilege escalation (`sudo`).

See [SELF_HOSTING.md](SELF_HOSTING.md) for the full blocked command reference.

---

## Production usage

For production machines, use the **Approved** policy mode - set it via the admin API after creating the agent.

Avoid running production agents in Wild mode unless you fully trust the environment and understand the risk.

---

## Commands

| Command | Description |
|---|---|
| **Auth & setup** | |
| `reach login --api-url <url> --token <token>` | Store credentials (saves to `default` profile) |
| `reach login --api-url <url> --token <token> --profile <name>` | Store credentials under a named profile |
| `reach profile list` | List all profiles |
| `reach profile use <name>` | Switch active profile |
| `reach profile rename <old> <new>` | Rename a profile |
| `reach profile delete <name>` | Delete a profile (cannot delete the active profile) |
| `reach config show` | Show active profile, API URL, default agent, and aliases |
| `reach whoami` | Show current user identity (user_id, tenant_id, name) |
| `reach version` | Show CLI version |
| **Agents** | |
| `reach agents list` | List all machines (Tags column shown automatically when agents have tags) |
| `reach agents list --tag <key:value>` | Filter machines by tag |
| `reach agents use <id\|alias>` | Set default machine |
| `reach status` | Show default machine status |
| `reach alias set <name> <id>` | Create alias |
| `reach alias list` | List aliases |
| `reach alias remove <name>` | Remove alias |
| **Execution** | |
| `reach exec -- <cmd>` | Run command on default machine |
| `reach exec --agent <id\|alias> -- <cmd>` | Run command on specific machine |
| `reach exec --timeout <s> -- <cmd>` | Override wait timeout (default 60s) |
| `reach exec --no-wait -- <cmd>` | Submit job and exit immediately; use `reach job <id>` to check later |
| `reach job <job_id>` | Re-view stdout/stderr of a past job |
| `reach history` | Show your recent jobs |
| `reach history --agent <id\|alias>` | Filter your history by machine |
| `reach history --limit <n>` | Show up to N jobs (max 100, default 20) |
| `reach history --cursor <cursor>` | Fetch the next page (cursor from previous response) |
| **Policy** | |
| `reach policy show` | Show mode and approved commands for default agent |
| `reach policy show --agent <id\|alias>` | Show policy for a specific machine |
| **AI integration** | |
| `reach agent-init` | Interactively generate context for your AI agent |
| `reach agent-init --for claude` | Write CLAUDE.md for Claude Code |
| `reach agent-init --for cursor` | Write .cursor/rules/reach.mdc for Cursor |
| `reach agent-init --for system-prompt` | Print system prompt snippet to stdout |
| `reach agent-init --for mcp` | Print MCP server config to stdout |
| `reach mcp` | Start the MCP server (stdio transport for any MCP-compatible client) |

---

## License

MIT - see [LICENSE](LICENSE).
