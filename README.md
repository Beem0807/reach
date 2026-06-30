# reach

Give your AI agents controlled access to every machine you own - without SSH, VPNs, or open ports.

```bash
reach exec -- hostname
reach exec --agent prod -- docker ps
```

## ⚡ 2-minute Quick Start

Zero to your **AI agent running commands on a real machine**, in three steps:

**1. Start Reach.** One command runs the backend, creates your tenant and first agent, installs the CLI, and logs you in:

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

**2. Install the agent** on the machine you want to control. The script prints a ready-to-paste command - a `curl … | sudo bash` for a host, or `helm install …` for Kubernetes.

**3. Connect your AI tool.** `reach agent-init` writes the context file for Claude Code / Cursor and prints the MCP server config to drop in - now your agent can drive that machine through Reach:

```bash
reach agent-init
```

Now ask your AI agent to run something - or check it yourself:

```bash
reach exec -- hostname
```

That's it: your AI agent has **controlled, audited** access to the machine - no SSH, no VPN, no open ports. On AWS instead? Swap step 1 for `lambda-setup.sh`. Docker, Kubernetes, and production hardening are in [SELF_HOSTING.md](SELF_HOSTING.md).

---

> ### ⚠️ What Reach is - and isn't
>
> Reach gives AI agents **controlled, audited** command execution on machines you own. It is **not a sandbox for arbitrary untrusted commands.**
>
> - **`wild` mode can damage machines** - reboots, deletes, package installs all run. Use it only on personal/dev boxes where you're the sole user.
> - **For production, use `approved` mode** - only commands you've allowlisted execute; everything else is blocked and queued for review.
> - Reach is **not a security boundary against the machine's own owner/root** - whoever controls the host can read the agent's token.
>
> See **[SECURITY.md](SECURITY.md)** for the full threat model, and **[POLICIES.md](POLICIES.md)** for how the modes work.

---

## What can I use this for?

- **Let Claude Code inspect a remote dev box** - check what's running, tail logs, or diff configs without leaving your editor
- **Debug Docker containers without SSH** - `reach exec -- docker ps`, `docker logs`, `docker inspect` from anywhere
- **Check Kubernetes pods from an in-cluster agent** - install the agent inside the cluster, run `kubectl` through it from your laptop
- **Run approved operational commands on production** - lock agents to `approved` mode so only allowlisted commands run; everything else is blocked and queued for review
- **Give AI tools controlled machine access without exposing SSH** - no open ports, no VPN, no key distribution; the agent makes outbound HTTPS calls to your backend

## Why Reach?

AI agents can reason about your code, but they cannot safely operate your remote machines by default.

Reach gives any AI agent - Claude Code, Cursor, custom LLM workflows, or your own automation - a controlled command bridge to your machines without requiring SSH, VPNs, public IPs, or inbound firewall rules.

## How it works

The agent never accepts inbound connections - it makes outbound HTTPS requests to your backend, polls for jobs, runs them, and posts results back.

1. Deploy the backend (Local, Lambda, or Docker).
2. Register agents in the console (or let the setup script do it) - you get ready-to-paste install commands.
3. Install the CLI locally and the agent on each machine.
4. Queue commands via the CLI or MCP; the agent picks them up and runs them; results come back.

The local and Lambda setup scripts do 1-3 for you. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

---

## Getting started

Reach is self-hosted - you deploy your own backend. The fastest path is the **interactive setup script**: one command bootstraps everything end to end - it deploys the backend, provisions your tenant, tenant admin user, API token, and first agent, then installs the CLI and logs you in.

**Local machine** (no cloud account needed):
```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

**AWS Lambda + DynamoDB** (low cost, AWS-native):
```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash
```

Both scripts are interactive, safe to re-run, and double as management tools (`--update`, `--down`, …).

**Docker + PostgreSQL** (any cloud) - a plain container; finish setup in the console at `http://<your-api-url>/ui`:
```bash
docker run -d -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e SESSION_SIGNING_KEY="<your-session-key>" \
  -e ADMIN_PASSWORD="<your-admin-password>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:0.1.0
```

Full setup guide for all three: [SELF_HOSTING.md](SELF_HOSTING.md).

## Install the CLI

The setup script installs it for you. To install it yourself:

```bash
uv tool install https://reach-releases.s3.amazonaws.com/cli/v0.1.0/reach-0.1.0-py3-none-any.whl
reach login --api-url "<your-api-url>" --api-key "<your-api-token>"   # token from the tenant console → API Tokens
```

Full command reference, profiles, aliases, and MCP setup: **[cli/README.md](cli/README.md)**.

---

## Add a machine

In the tenant console, go to **Agents → New agent**, choose the agent **type** (Host or Kubernetes) and a policy mode, and click Create. The console shows the install command for that type.

### Host

Run the generated `curl … install.sh …` command on the machine. It auto-detects OS/architecture (Linux → systemd service; macOS → foreground or, with `--background`, a LaunchDaemon). The generated command already includes `--yes --force` and your API URL + install token. Optional grants:

| Flag | What it does | Default with `--yes` |
|---|---|---|
| `--grant-service-mgmt` / `--no-grant-service-mgmt` | `systemctl`/`launchctl` start/stop/restart via sudoers | ✅ on |
| `--grant-docker` / `--no-grant-docker` | Add `reach-agent` to the `docker` group | ❌ off |
| `--background` | macOS only - install as a LaunchDaemon | - |

Uninstall: `curl -fsSL …/agent/latest/install.sh | sudo bash -s -- --uninstall`.

### Kubernetes

Run the generated `helm install …` command (it fills in `reach.apiUrl`, a one-time `reach.installToken`, and pins the chart `--version`):

```bash
helm repo add reach https://reach-releases.s3.amazonaws.com/charts/reach-agent --force-update
helm install reach-agent reach/reach-agent \
  --namespace reach --create-namespace \
  --set reach.apiUrl=https://reach.example.com \
  --set reach.installToken=install_xxx
```

Deploys the agent as a **Deployment** - **one logical agent per cluster** (replicas share a cluster-derived identity; a `Lease` elects one leader), so scaling replicas doesn't create more agents. What it can do is bounded by **Kubernetes RBAC** (the chart's `clusterAccess`, default read-only `view`), which the agent self-reports for you to **acknowledge** in the console (later changes surface as **drift**). Uninstall: `helm uninstall reach-agent -n reach`.

Full chart values, RBAC, and execution model: [deploy/helm/reach-agent](deploy/helm/reach-agent) and [agent/README.md](agent/README.md).

### After install

Set an agent as your CLI default: `reach agents use <id|alias>`. Decommission any agent from the tenant console → **Agents**.

---

## Admin console

The backend ships a web UI at `/ui` with two consoles (choose at login):

- **Platform admin** (log in with `ADMIN_PASSWORD`) - cross-tenant administration: tenants, users across tenants, and platform-wide audit logs. It does **not** operate tenant agents or approvals.
- **Tenant console** (username + password) - per-tenant operations: dashboard, agents, users (roles + per-user agent access), jobs, approvals, API tokens, and a tenant-scoped audit log.

The CLI and MCP server authenticate with **API tokens** (created under **API Tokens**), not the admin password.

| Operation | Where |
|---|---|
| View agents / job history | Tenant console → Agents / Jobs |
| Change an agent's policy mode | Tenant console → Agents → [agent] → Policy |
| Manage approvals | Tenant console → Approvals |
| Restrict a user to specific agents | Tenant console → Users → [user] → Agent Access |
| Audit log (tenant / platform) | Tenant console → Audit Logs / Platform admin → Audit Logs |

Automating any of this? See [API.md](API.md).

---

## Using the CLI

```bash
reach agents list                           # your machines, with mode + access level
reach exec -- <command>                     # run on the default machine
reach exec --agent <id|alias> -- <command>  # run on a specific machine
reach exec --no-wait -- <command>           # fire-and-forget; check with `reach job <id>`
reach history                               # recent jobs
```

Aliases, multi-deployment profiles, approvals, and the full command reference are in **[cli/README.md](cli/README.md)**.

**AI agents & MCP.** `reach agent-init` writes context for Claude Code / Cursor and prints the MCP config so your AI tool can call Reach as tools. The MCP server (`reach mcp`) is launched by your MCP client - add `{"mcpServers":{"reach":{"command":"reach","args":["mcp"]}}}` to its settings. See [cli/README.md](cli/README.md#mcp-server).

---

## Policy modes

Each agent runs in one of three modes (set in the tenant console or via the API):

- **`wild`** - runs almost anything; only a catastrophic/abuse set (`rm -rf /`, `mkfs`, privileged escapes, reverse shells) is always blocked. For personal/dev boxes.
- **`readonly`** - only reads run; any write/delete/restart/install is blocked.
- **`approved`** - reads run; writes run only if pre-approved for that agent (blocked and queued otherwise).

Host and Kubernetes agents share these modes but enforce them differently - agent-side Landlock vs backend-side `kubectl`-verb gating - and k8s approvals are structured `verb/resource/namespace/name` rules. Full detail (enforcement model, structured rules, `access_level`): **[POLICIES.md](POLICIES.md)**.

## Safety

Built for controlled execution: no inbound ports, no SSH, outbound-HTTPS-only agents, a default 60s command timeout, and a full audit trail.

- **Always blocked (any mode):** catastrophic filesystem destruction, fork bombs, privileged container/host escapes, credential exfiltration, and reverse shells - rejected server-side before the agent sees them.
- **Blocked in `readonly` / unapproved in `approved`:** writes, deletes, service restarts, package installs, container mutations, IaC/cloud destroys, privilege escalation.
- **Kubernetes agents** are bounded differently - no shell, a `kubectl` + read-filters allowlist, no local-file reads, backend verb-gating, and cluster RBAC as the unbypassable floor.

Blocked-command reference: [SELF_HOSTING.md](SELF_HOSTING.md). Threat model: [SECURITY.md](SECURITY.md). Policy detail: [POLICIES.md](POLICIES.md).

## Production usage

Use **`approved`** mode on production machines (set it when creating the agent, or under **Agents → [agent] → Policy**). `wild` is for personal machines, dev, and break-glass - not shared production.

## Observability

- **Audit log** (built-in) - every action recorded: logins, agent lifecycle (create/revoke/rotate/unreachable/recover), policy changes, approvals. Tenant-scoped or platform-wide, in the console or via the API (`GET /tenant/audit-logs`).
- **Prometheus metrics** (opt-in, Kubernetes agents) - `--set metrics.enabled=true` exposes `/metrics` (job/sync/blocked counters, leadership) with a `ServiceMonitor` and a `NetworkPolicy` locking the port to your Prometheus namespace. Off by default. See [agent/README.md → Metrics](agent/README.md#metrics-opt-in).

---

## Documentation

| Doc | What's in it |
|---|---|
| [cli/README.md](cli/README.md) | The `reach` CLI and `reach-mcp` server - install, commands, profiles, aliases, MCP setup |
| [POLICIES.md](POLICIES.md) | Policy modes (wild/readonly/approved), approvals, host vs Kubernetes enforcement, structured k8s rules, `access_level` |
| [agent/README.md](agent/README.md) | How the agent works - host vs Kubernetes, credential-only identity, the poll loop, execution models, leader election, RBAC self-review, metrics |
| [deploy/helm/reach-agent](deploy/helm/reach-agent) | Kubernetes agent Helm chart - install, RBAC (`clusterAccess`), execution allowlist, and all values |
| [SELF_HOSTING.md](SELF_HOSTING.md) | Deploy and operate your own backend (Local, AWS Lambda, Docker), setup, agent lifecycle, grants, blocked-command reference |
| [API.md](API.md) | Complete HTTP endpoint reference, rate limits, pagination, audit-log actions |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the pieces fit - command flow, token model, storage split, policy enforcement, approvals, multi-tenancy |
| [SECURITY.md](SECURITY.md) | Threat model, token storage and rotation, revoking access, audit history, production hardening |

---

## License

MIT - see [LICENSE](LICENSE).
