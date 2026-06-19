# reach

Give your AI agents controlled access to every machine you own - without SSH, VPNs, or open ports.

```bash
reach exec -- hostname
reach exec --agent prod -- docker ps
```

---

## What can I use this for?

- **Let Claude Code inspect a remote dev box** — ask Claude to check what's running, tail logs, or diff configs without leaving your editor
- **Debug Docker containers without SSH** — `reach exec -- docker ps`, `docker logs`, `docker inspect` from anywhere
- **Check Kubernetes pods from an in-cluster agent** — install the agent inside the cluster, run `kubectl` commands through it from your laptop
- **Run approved operational commands on production machines** — lock agents to `approved` mode so only allowlisted commands can execute; everything else is blocked and queued for admin review
- **Give AI tools controlled machine access without exposing SSH** — no open ports, no VPN, no key distribution; the agent makes outbound HTTPS calls to your backend

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
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash
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

**To decommission an agent (three-step sequence):**

Uninstall the binary from the machine first (optional but recommended):

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/agent/latest/install.sh | sudo bash -s -- --uninstall
```

Then follow the three-step admin API sequence:

```bash
# Step 1 - revoke: cuts access immediately, removes from user access lists
curl -s -X POST "$API_URL/admin/agents/agent_xxx/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Step 2 - soft-delete: marks DELETED, record stays in database
curl -s -X DELETE "$API_URL/admin/agents/agent_xxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Step 3 - remove: permanently deletes the record
curl -s -X DELETE "$API_URL/admin/agents/agent_xxx/remove" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Each step requires the previous one to have been completed. To undo a revoke before soft-deleting, reissue an install token - this resets the agent back to CREATED:

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxx/reissue-install-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
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

**Decommission an agent (three-step sequence):**

```bash
# Step 1 - revoke: cuts sync access immediately, removes from all user access lists
# Can be undone by reissuing an install token (see below)
curl -s -X POST "$API_URL/admin/agents/agent_xxx/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Step 2 - soft-delete: marks DELETED, record stays in database for audit
# Requires REVOKED status
curl -s -X DELETE "$API_URL/admin/agents/agent_xxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Step 3 - remove: permanently deletes the record
# Requires DELETED status
curl -s -X DELETE "$API_URL/admin/agents/agent_xxx/remove" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**Restore a revoked agent (undo revoke):**

```bash
# Reissues a fresh install token and resets status to CREATED
# Works on REVOKED agents only - DELETED agents cannot be reissued
curl -s -X POST "$API_URL/admin/agents/agent_xxx/reissue-install-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

**Manage agent policy mode:**

```bash
# Set an agent to approved mode
curl -s -X PUT "$API_URL/admin/agents/agent_xxx/policy/mode" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "approved"}'

# Pre-approve a single command
curl -s -X POST "$API_URL/admin/approvals" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent_xxx", "command": "docker restart app", "duration": "8h"}'

# Bulk pre-approve (idempotent - skips any that are already approved)
curl -s -X POST "$API_URL/admin/approvals" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent_xxx", "commands": ["docker ps", "docker logs app", "kubectl get pods -A"]}'

# View pending approval requests (paginated - default 20, max 100; use ?cursor=<next_cursor> for next page)
curl -s "$API_URL/admin/approvals?agent_id=agent_xxx&status=pending" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Approve permanently
curl -s -X PUT "$API_URL/admin/approvals/appr_xxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Approve for a limited time (1h / 8h / 24h / 7d / permanent / custom Nh or Nd)
curl -s -X PUT "$API_URL/admin/approvals/appr_xxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"duration": "8h"}'

# Deny a command
curl -s -X PUT "$API_URL/admin/approvals/appr_xxx/deny" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Permanently delete an approval record (any status)
curl -s -X DELETE "$API_URL/admin/approvals/appr_xxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Time-limited approvals expire silently - the record stays in the database for history, but once the expiry passes the command is no longer in the effective approved list. The next blocked attempt creates a new pending record.

See [SELF_HOSTING.md](SELF_HOSTING.md) for the full admin API reference.

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
| `get_context` | **Call first.** Returns your identity, default agent (with mode and access_level), and aliases — full session orientation in one call |
| `whoami` | Show current authenticated user and tenant |
| `list_agents` | List all registered machines with mode and access level |
| `get_agent(agent_id)` | Get status of a specific machine |
| `exec_command(command, agent_id?, timeout?)` | Run a command and wait for the result |
| `get_job(job_id)` | Fetch result of a previously submitted job |
| `list_history(agent_id?, limit?)` | Browse recent job history |
| `list_approved_commands(agent_id?)` | List pre-approved write commands for an agent (approved mode) |
| `list_pending_approvals(agent_id?)` | List your blocked commands awaiting admin approval |

---

## Policies

Each agent runs in one of three modes, configured via the admin API. The mode determines how commands are evaluated before execution.

### Wild mode

Wild mode is intentionally permissive. It is designed for personal machines, dev environments, break-glass debugging, and power users who want full command flexibility.

Reach still rejects a small set of commands in wild mode - those that are catastrophic or abuse-like regardless of context: raw disk wipes (`mkfs`, `dd if=`, `wipefs`), recursive deletion of the root filesystem (`rm -rf /`), privileged container and host escapes (`docker run --privileged`, `nsenter --target 1`, `chroot /`), credential exfiltration (`env | curl`), fork bombs, and reverse shells (`/dev/tcp/`, `nc -e`, `socat exec:`). Everything else runs, including reboots, shutdowns, IaC destroys, cloud resource deletions, and package installs.

For production machines, use **Approved** mode and explicitly allowlist the write operations Reach is allowed to perform.

### Readonly mode

Readonly mode blocks any command that writes, deletes, installs, or mutates system state. This includes: file writes and deletes, process kills, service restarts, reboots and shutdowns, package managers, container mutations (`docker run/stop/rm`), firewall changes, user management, IaC destroys (`terraform destroy`, `pulumi destroy`), and cloud destructive operations (`aws ec2 terminate-instances`, `gcloud instances delete`).

Read-only commands (`ls`, `cat`, `git log`, `docker ps`, `kubectl get`, `aws describe-*`, `journalctl`) always pass.

Shell-chained commands are checked segment by segment - `ls && rm file.txt` is blocked even though the `ls` segment is safe.

On Linux, readonly mode enforcement is backed by Landlock (a kernel sandbox) on the agent. Commands run in a sandboxed subprocess that cannot write outside `/tmp`, providing defence-in-depth beyond the pattern-based check.

On macOS, Landlock is not available. Readonly mode relies entirely on the server-side blocked-command list - the server rejects blocked writes before they are queued, so the agent never receives them.

### Approved mode

Reads are always allowed - you do not need to add read commands to any list. Write and destructive operations (anything blocked in readonly mode) are only permitted if the exact command has been pre-approved for this agent.

**How it works:**

1. When a command is submitted, the server classifies it as a write or read (`is_write: true/false`) using the same pattern list as readonly mode, and queues it to the agent.
2. The agent checks whether the command matches the approved list.
   - **Approved match** - runs the command normally (write explicitly permitted).
   - **Not approved, Linux** - runs under Landlock. If Landlock blocks the write, the agent returns a structured error and the backend creates a pending approval record.
   - **Not approved, macOS** - no Landlock available. The agent uses the server-supplied `is_write` flag: if the command is a write and not approved, it is refused immediately and a pending approval record is created. Read commands always run.
3. The admin can review pending approvals and approve or deny them via the admin API.
4. Once approved, the command prefix is included in the approved list on the next sync and runs without restriction.

The match is prefix-based with word boundary: approving `docker logs` permits `docker logs myapp --tail 100` but not `docker rm myapp`.

**Viewing approvals from the CLI:**

```bash
reach approvals                      # effective approved commands (default agent)
reach approvals --agent prod         # effective approved commands for a specific agent
reach approvals --pending            # your pending requests (default agent)
reach approvals --denied             # your denied requests (default agent)
reach approvals --expired            # your expired approvals (default agent)
reach approvals --agent prod --pending  # any of the above for a specific agent
```

`--pending`, `--denied`, and `--expired` show only your own records. Expired entries are visually marked so you can see why a command stopped working.

### Access level

Each agent has an `access_level` label that combines its policy mode with whether the agent process is running as root. This is shown in `reach agents list` and `reach status`.

| access_level | Mode | Running as root |
|---|---|---|
| `open` | wild | yes |
| `elevated` | wild | no - or - approved + root |
| `managed` | approved | no - or - readonly + root |
| `restricted` | readonly | no |

These are factual descriptors, not risk scores. An `open` agent in a personal dev environment is intentional.

---

## Safety

Reach is designed for controlled command execution:

- No inbound ports are opened
- No SSH server is required
- Agents only make outbound HTTPS requests
- Commands have a default timeout of 60 seconds
- Job history is recorded for 7 days
- Policy modes are configured server-side via the admin API

**Always blocked - regardless of mode:**

Catastrophic filesystem destruction (`rm -rf /`, `mkfs`, `dd if=`, `wipefs`), fork bombs, privileged container and host escapes (`docker run --privileged`, `nsenter --target 1`), credential exfiltration (`env | curl`), and reverse shells are rejected by the server before the agent ever sees them.

**Blocked in readonly mode and unapproved writes in approved mode:**

File writes and deletes, process kills, service restarts, reboots and shutdowns, package installs, container mutations (`docker run/stop/rm`), IaC destroys, cloud destructive operations, firewall changes, user management, and privilege escalation (`sudo`).

See [SELF_HOSTING.md](SELF_HOSTING.md) for the full blocked command reference.

---

## Production usage

For production machines, use the **Approved** policy mode - set it via the admin API after creating the agent.

Wild mode is for personal machines, dev environments, and break-glass access. Do not use it on shared production machines.

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
| `reach man` | Show full command reference in the terminal |
| **Agents** | |
| `reach agents list` | List all machines with mode and access level |
| `reach agents list --tag <key:value>` | Filter machines by tag |
| `reach agents use <id\|alias>` | Set default machine |
| `reach status` | Show default machine status and access level |
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
| **Approvals** | |
| `reach approvals` | Show effective approved commands for the default agent |
| `reach approvals --agent <id\|alias>` | Show effective approved commands for a specific agent |
| `reach approvals --pending` | Your pending requests for the default agent |
| `reach approvals --denied` | Your denied requests for the default agent |
| `reach approvals --expired` | Your expired approvals for the default agent |
| `reach approvals --agent <id\|alias> --pending` | Any of the above for a specific agent |
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
