# reach

Give your AI agents controlled access to every machine you own - without SSH, VPNs, or open ports.

```bash
reach exec -- hostname
reach exec --agent prod -- docker ps
```

---

## What can I use this for?

- **Let Claude Code inspect a remote dev box** - ask Claude to check what's running, tail logs, or diff configs without leaving your editor
- **Debug Docker containers without SSH** - `reach exec -- docker ps`, `docker logs`, `docker inspect` from anywhere
- **Check Kubernetes pods from an in-cluster agent** - install the agent inside the cluster, run `kubectl` commands through it from your laptop
- **Run approved operational commands on production machines** - lock agents to `approved` mode so only allowlisted commands can execute; everything else is blocked and queued for admin review
- **Give AI tools controlled machine access without exposing SSH** - no open ports, no VPN, no key distribution; the agent makes outbound HTTPS calls to your backend

---

## Why Reach?

AI agents can reason about your code, but they cannot safely operate your remote machines by default.

Reach gives any AI agent - Claude Code, Cursor, custom LLM workflows, or your own automation - a controlled command bridge to your machines without requiring SSH, VPNs, public IPs, or inbound firewall rules.

---

## How it works

1. You deploy the Reach backend (Lambda or Docker).
2. You open the admin console at `/ui`, create a tenant, add users, and register agents - the console gives you ready-to-paste CLI and agent install commands.
3. You install the CLI on your local machine.
4. You install the agent on each remote machine.
5. The agent makes outbound HTTPS requests to your backend - no inbound ports needed.
6. Commands are queued via the CLI, the agent picks them up and runs them, results come back.

On the local and Lambda options, the interactive setup script does steps 1–3 for you (and even prints the agent install command for step 4) - see [Getting started](#getting-started).

---

## Admin console

The backend ships with a built-in web UI served at `/ui`. It has two separate consoles - choose at the login screen:

**Platform admin console** - log in with `ADMIN_PASSWORD`:
- **Tenants** - create tenants, enable/disable them, see agent and user counts
- **Users** - view and manage users across all tenants; reset passwords, change roles, disable accounts
- **Audit logs** - platform-wide event log covering every action across all tenants

**Tenant console** - log in with a username and password (created by the platform admin):
- **Dashboard** - agent health bar, pending approvals, recent activity
- **Agents** - register agents, set policy mode, manage tags, view detected capabilities (Docker, service management), request token rotation, full lifecycle management
- **Users** - create users with roles (admin / operator / developer), set per-user agent access restrictions, reset passwords (admin role only)
- **Jobs** - browse job history with full stdout/stderr output
- **Approvals** - operators review and approve/deny write requests; developers see their own pending requests and can request approval
- **API Tokens** - create and manage named API tokens for the CLI and MCP server
- **Audit logs** - tenant-scoped event log (admin role)

The CLI and MCP server authenticate with API tokens, not the admin password. Create tokens in the tenant console under **API Tokens**.

---

## Getting started

Reach is self-hosted - you deploy your own backend. The fastest path is the **interactive setup script**: one command walks you through a few prompts and bootstraps everything end to end - it deploys the backend, provisions your workspace, admin user, API key, and first agent, then installs the CLI and logs you in. When it finishes, you can run `reach exec -- hostname` straight away. No console clicks, no manual token copying.

**Local machine** (no cloud account needed):
```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

**AWS Lambda + DynamoDB** (low cost, AWS-native):
```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash
```

Both scripts are interactive and safe to re-run - every prompt has a sensible default, and they double as management tools afterward (`--update`, `--down`, and more; see [SELF_HOSTING.md](SELF_HOSTING.md)).

**Docker + PostgreSQL** (any cloud) - this path is a plain container, so you finish setup yourself in the admin console at `http://<your-api-url>/ui`:
```bash
docker run -d -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e SESSION_SIGNING_KEY="<your-session-key>" \
  -e ADMIN_PASSWORD="<your-admin-password>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:0.1.0
```

See [SELF_HOSTING.md](SELF_HOSTING.md) for the full setup guide for all three options.

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

Log in with an API token from the tenant console (**API Tokens → New token**):

```bash
reach login --api-url "<your-api-url>" --token "<your-api-token>"
```

---

## Add a machine

In the tenant console, go to **Agents → New agent**, choose a policy mode, and click Create. Copy the install command shown and run it on the target machine.

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
| `--no-grant-docker` | Skip the docker group grant | - |
| `--background` | macOS only - install as a LaunchDaemon (starts on boot) | - |
| `--force` | Overwrite an existing agent config without prompting (used for reinstall) | - |

Flags can be combined with `--yes` to override specific defaults, e.g. `--yes --grant-docker` or `--yes --no-grant-service-mgmt`. The install command generated by the console already includes `--yes --force`; the `--api-url`, `--agent-id`, and `--install-token` values are filled in for you.

Set it as your default:

```bash
reach agents use agent_xxx
```

**To decommission an agent:** open the tenant console, go to **Agents**, select the agent, and use the decommission action. To uninstall the binary from the machine first:

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/agent/latest/install.sh | sudo bash -s -- --uninstall
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

Everything in this section is available through the admin console at `/ui`. The platform admin console covers cross-tenant operations (agents, users, audit logs). The tenant console covers per-tenant operations (agents, users, jobs, approvals, API tokens).

| Operation | Where in the console |
|---|---|
| View all agents for a tenant | Tenant console → Agents |
| View job history | Tenant console → Jobs |
| Restrict a user to specific agents | Tenant console → Users → [user] → Agent Access |
| Change an agent's policy mode | Tenant console → Agents → [agent] → Policy |
| Manage approvals | Tenant console → Approvals |
| Platform-wide audit log | Platform admin → Audit Logs |
| Tenant-scoped audit log | Tenant console → Audit Logs |

See [API.md](API.md) for the full endpoint reference if you need to automate any of these operations.

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
| `get_context` | **Call first.** Returns your identity, default agent (with mode and access_level), and aliases - full session orientation in one call |
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

Each agent runs in one of three modes, set in the tenant console (or via the API). The mode determines how commands are evaluated before execution.

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
3. An operator or admin reviews pending approvals and approves or denies them in the tenant console under **Approvals**.
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
| `elevated` | wild (non-root) or approved (root) | - |
| `managed` | approved (non-root) or readonly (root) | - |
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
- Policy modes are configured server-side in the tenant console

**Always blocked - regardless of mode:**

Catastrophic filesystem destruction (`rm -rf /`, `mkfs`, `dd if=`, `wipefs`), fork bombs, privileged container and host escapes (`docker run --privileged`, `nsenter --target 1`), credential exfiltration (`env | curl`), and reverse shells are rejected by the server before the agent ever sees them.

**Blocked in readonly mode and unapproved writes in approved mode:**

File writes and deletes, process kills, service restarts, reboots and shutdowns, package installs, container mutations (`docker run/stop/rm`), IaC destroys, cloud destructive operations, firewall changes, user management, and privilege escalation (`sudo`).

See [SELF_HOSTING.md](SELF_HOSTING.md) for the full blocked command reference.

---

## Production usage

For production machines, use the **Approved** policy mode - set it in the tenant console when creating the agent, or change it afterward under **Agents → [agent] → Policy**.

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

## Documentation

| Doc | What's in it |
|---|---|
| [SELF_HOSTING.md](SELF_HOSTING.md) | Deploy and operate your own backend (Local, AWS Lambda, Docker), first-time setup, agent lifecycle, sudo/docker grants, policy and approval management |
| [API.md](API.md) | Complete HTTP endpoint reference - platform admin, tenant admin, user/CLI, and agent endpoints, plus rate limits, pagination, and audit-log actions |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the pieces fit together - command flow, token model, storage split, policy enforcement, approvals, multi-tenancy |
| [SECURITY.md](SECURITY.md) | Threat model, token storage and rotation, revoking access, audit history, and production hardening |

---

## License

MIT - see [LICENSE](LICENSE).
