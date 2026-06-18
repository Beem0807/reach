# Architecture

## Overview

Reach is a command bridge between AI agents (or any automation) and remote machines. It has three components:

```
┌─────────────────────────────────────────────────────────────────┐
│  Local machine                                                  │
│                                                                 │
│   ┌───────────┐     ┌─────────────┐                            │
│   │  CLI      │     │  MCP server │                            │
│   │ (reach)   │     │ (reach mcp) │                            │
│   └─────┬─────┘     └──────┬──────┘                            │
│         │                  │  stdio (JSON-RPC)                  │
│         │           ┌──────┴──────┐                            │
│         │           │  MCP client │ (Claude Code, Cursor, etc.) │
│         │           └─────────────┘                            │
└─────────┼───────────────────────────────────────────────────────┘
          │ HTTPS
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Backend                                                        │
│                                                                 │
│   ┌────────────────────────────────────────────────────────┐   │
│   │  FastAPI  (Docker)   or   Lambda + API Gateway         │   │
│   └────────────────────────────────────────────────────────┘   │
│   ┌────────────────────────────────────────────────────────┐   │
│   │  PostgreSQL (Docker)  or   DynamoDB (Lambda)           │   │
│   └────────────────────────────────────────────────────────┘   │
└─────────────────────────────▲───────────────────────────────────┘
                              │ HTTPS (outbound from agent)
┌─────────────────────────────┴───────────────────────────────────┐
│  Remote machine                                                 │
│                                                                 │
│   ┌──────────────┐                                             │
│   │  reach-agent │  (systemd / launchd service or foreground)  │
│   └──────────────┘                                             │
└─────────────────────────────────────────────────────────────────┘
```

The agent never accepts inbound connections. It makes outbound HTTPS requests to the backend, polls for jobs, and posts results back. No SSH, no VPN, no open ports.

---

## Command flow

A command goes through four steps:

```
1. Submit    CLI / MCP  →  POST /jobs                    →  Backend stores job (PENDING, is_write annotated)
2. Poll      Agent      →  POST /agent/sync               →  Backend returns pending job + is_write flag
3. Execute   Agent runs the command; enforcement depends on mode and OS (see Policy enforcement)
4. Result    Agent      →  POST /agent/jobs/{id}/result   →  Backend stores output (SUCCEEDED / FAILED)
5. Retrieve  CLI / MCP  →  GET  /jobs/{id}                →  Backend returns output
```

The CLI and MCP server both poll `GET /jobs/{id}` until the job reaches a terminal state or the timeout is hit. The agent has no direct channel back to the submitter - results go through the backend.

**In approved mode**, step 3 may produce a blocked result:

```
3a. Execute  Agent checks approved list and OS:
              Linux  - unapproved write runs under Landlock; kernel blocks it
              macOS  - unapproved write detected via server-supplied is_write flag; blocked early
3b. Result   Agent posts result with blocked=true, is_write=true
3c. Record   Backend updates is_write on the job; creates a pending approval record
3d. Notify   User sees it via `reach approvals --pending`; admin approves or denies via admin API
```

---

## Components

### CLI (`cli/`)

A Python CLI (`reach`) that authenticates with a user token and talks to the backend over HTTPS. Manages a local config file (`~/.reach/config.json`) with the API URL, user token, default agent, and aliases.

Notable commands: `exec`, `job`, `history`, `agents`, `approvals` (with `--pending`/`--denied`/`--expired` flags), `agent-init`, `mcp`, `man`.

### MCP server (`cli/reach/mcp_server.py`)

Launched as a subprocess by an MCP-compatible client (Claude Code, Cursor, etc.) and communicates over stdio using JSON-RPC. Exposes the same operations as the CLI as structured tools: `list_agents`, `exec_command`, `get_job`, `list_history`, `whoami`. The client manages the process lifecycle - no hosting or ports needed.

The MCP server is installed as part of the CLI package (`reach-mcp` entry point).

### Backend (`backend/`)

A FastAPI application with a storage-backend abstraction that supports two databases:

| Deployment | Runtime | Database |
|---|---|---|
| Docker | FastAPI (uvicorn behind nginx) | PostgreSQL (via SQLAlchemy + Alembic) |
| Lambda | API Gateway + Lambda | DynamoDB (boto3) |

The same handler code runs in both deployments. The storage layer is swapped via the `STORAGE_BACKEND` env var (`postgres` or `dynamo`). Handlers import from `shared.store`, which returns the correct repo implementation.

nginx is required in front of uvicorn for the Docker deployment. Long-polling connections from the agent (`POST /agent/sync`) need to be terminated cleanly; uvicorn alone does not handle this correctly under load.

A background scheduler (APScheduler on FastAPI, EventBridge on Lambda) runs every minute to:
- Mark agents `INACTIVE` if no heartbeat in the last 45 seconds
- Expire `PENDING` jobs older than 1 hour to `EXPIRED`

### Agent (`agent/`)

A Go binary installed via `install.sh`. On Linux it runs as a systemd service under a dedicated `reach-agent` system user. On macOS it runs as a foreground process by default (stops when the terminal closes), or with `--background` as a LaunchDaemon under the same dedicated `reach-agent` system user (starts on boot, same security model as Linux). On startup it claims itself using an install token, then enters a poll loop:

1. `POST /agent/sync` - sends heartbeat and `running_as_root` flag, receives pending job (if any) with `is_write` flag and the list of approved commands
2. Runs the command, optionally under a Landlock sandbox on Linux or via `is_write` enforcement on macOS (see [Policy enforcement](#policy-enforcement))
3. `POST /agent/jobs/{id}/result` - posts stdout, stderr, exit code, whether the command was blocked, and `is_write` (set to `true` if blocked)

The agent self-rotates its token every 30 days. See [SELF_HOSTING.md](SELF_HOSTING.md) for the full agent lifecycle.

---

## Storage backend split

Lambda functions are stateless and short-lived. DynamoDB requires no persistent connection - each request opens and closes independently, which is the only viable model for Lambda at scale. Lambda + PostgreSQL is deliberately not supported: ephemeral connections exhaust PostgreSQL's connection limit quickly, and the fix (RDS Proxy) adds cost that defeats the purpose of the serverless option.

FastAPI in Docker holds a connection pool for the lifetime of the process, which is exactly what PostgreSQL expects. DynamoDB outside of AWS requires AWS credentials and doesn't make sense in a self-hosted context.

The storage abstraction (`backend/shared/repos/base.py`) defines a common interface. `sql.py` implements it with SQLAlchemy, `dynamo.py` with boto3. Handlers never import from either directly.

---

## Token model

Three token types, none stored raw - only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted:

| Token | Prefix | Issued by | Used by | Lifetime |
|---|---|---|---|---|
| Install token | `install_` | Admin API | Agent (once, at claim) | 24 hours |
| Agent token | `agent_` | Backend (at claim) | Agent (every sync) | 30 days, auto-rotated |
| User token | `tok_` | Admin API | CLI / MCP server | Until revoked |

The install token is one-time use and is cleared from disk after a successful claim. The agent token is bound to a machine fingerprint - a token replayed from a different machine is rejected. The agent rotates its own token every 30 days with no lockout window (old token is valid until the new one is persisted).

---

## Agent lifecycle

```
CREATED → ACTIVE → INACTIVE → ACTIVE
                ↘
              (reissue install token resets to CREATED)
```

- **CREATED** - registered, never claimed. Install token valid for 24 hours.
- **ACTIVE** - claimed and syncing. Transitions to INACTIVE after 45 seconds without a heartbeat.
- **INACTIVE** - missed heartbeats. Auto-recovers to ACTIVE on next successful sync (no manual intervention needed).

The heartbeat checker runs every minute and scans for ACTIVE agents whose `last_heartbeat_at` is older than 45 seconds.

---

## Adaptive polling

The backend tells the agent how fast to poll on each sync response via `next_poll_seconds`:

- **2s** - active window: a job was dispatched or created recently
- **15s** - idle

This keeps latency low during active use without burning unnecessary requests when idle.

---

## Policy enforcement

Commands pass through two evaluation layers: server-side (before the job is queued) and agent-side (at execution time).

### Server-side checks

The backend runs two checks on every command before storing the job:

**Global blocklist** (`BLOCKED_PATTERNS`) - always rejected regardless of mode. Covers catastrophic and abuse-like operations: raw disk wipes (`mkfs`, `dd if=`, `wipefs`, `shred /dev/`), recursive deletion of the root filesystem, fork bombs, privileged container and host escapes (`docker run --privileged`, `nsenter --target 1`, `chroot /`), credential exfiltration (`env | curl`), and reverse shells (`/dev/tcp/`, `nc -e`, `socat exec:`).

**Readonly blocklist** (`READONLY_BLOCKED`) - checked in `readonly` mode only. Blocks writes, deletes, service management, reboots and shutdowns, IaC destroys, cloud destructive operations, package installs, and privilege escalation. Read-only commands always pass. Chained commands (`ls && rm file`) are split on `;`, `&&`, `||`, and `|` and checked segment by segment.

In `approved` mode, the server does not apply the readonly blocklist - it queues everything to the agent so the agent can enforce and create approval records when needed. The server annotates each job with `is_write: true/false` (from the same `READONLY_BLOCKED` patterns) so the agent knows whether the command is a write without replicating the pattern logic.

### Agent-side enforcement

**Linux (Landlock LSM)**

On Linux, `readonly` and `approved` mode commands run in a sandboxed subprocess:
- The agent re-execs a new process under [Landlock](https://docs.kernel.org/userspace-api/landlock.html) v3, restricting filesystem access to read-only on `/` and read-write on `/tmp`.
- The sandboxed process executes the command via bash.

**macOS (no Landlock)**

macOS does not have Landlock. Enforcement differs by mode:
- `readonly` - fully server-side. The server rejects write commands before queuing; the agent never receives them.
- `approved` - the agent uses the `is_write` flag from the sync response. If `is_write=true` and the command is not in the approved list, the agent blocks it immediately and returns `blocked=true`. Read commands (`is_write=false`) always run. This matches the Linux Landlock behaviour: reads pass, unapproved writes are blocked and create a pending approval record.

**Approved mode logic (both platforms)**

When the agent receives a job in approved mode, it also receives the current approved command list and the `is_write` flag from the sync response.

- If the command matches an entry in the approved list (prefix match with word boundary), it runs normally - the write is explicitly permitted.
- **Linux:** if not approved, runs under Landlock. If Landlock blocks the command (permission denied), the agent posts `blocked=true, is_write=true` in the job result.
- **macOS:** if not approved and `is_write=true`, the agent returns `blocked=true, is_write=true` immediately without running the command.
- The backend receives `blocked=true`, updates `is_write` on the job record, looks up the requesting user, and creates a pending record in the `approvals` table.
- The admin can review these records via the admin API and approve or deny them.
- Once approved, the command prefix is included in the approved list on the next sync, and the command runs without restriction.

### Approved list matching

The match is prefix-based with a word boundary: an approved entry of `docker logs` permits `docker logs myapp --tail 100` but not `docker rm myapp`. Matching checks `cmd == approved` or `cmd.startswith(approved + " ")`.

---

## Approvals

Approval records can be created two ways: automatically when an agent blocks a command in approved mode, or proactively by an admin via `POST /admin/approvals` (pre-approve without needing a prior block). The schema is the same either way:

```
approvals table:
  approval_id    - unique ID (appr_xxx)
  tenant_id      - which tenant
  agent_id       - which agent blocked the command
  command        - the exact command that was blocked
  requested_by   - user_id of the submitter
  requester_name - display name of the submitter
  job_id         - the job that triggered this record
  status         - pending | approved | denied
  expires_at     - ISO timestamp after which the approval stops being effective (null = permanent)
  created_at     - when the block occurred
  reviewed_at    - when the admin acted
  reviewed_by    - who reviewed it
```

Admins manage approvals via `GET/PUT/DELETE /admin/approvals`. `DELETE /admin/approvals/{id}` permanently removes a record of any status - useful for cleaning up stale pending records or removing an approved command from the effective list immediately. Users manage approvals through `reach approvals` with status flags: default shows effective approved commands (agent-wide); `--pending`, `--denied`, and `--expired` show the current user's own records for the agent.

Only one pending record is created per `(agent_id, command)` pair at a time. If a command is blocked and a pending record already exists for that exact command on that agent, no duplicate is created. Once the existing record is approved or denied, the next block creates a fresh pending record.

### Time-limited approvals

When approving, the admin can supply a `duration` in the request body:

| Value | Meaning |
|---|---|
| `permanent` (default) | Never expires |
| `1h` | 1 hour |
| `8h` | 8 hours |
| `24h` | 24 hours |
| `7d` | 7 days |
| `Nh` / `Nd` | Custom N hours or N days |

`expires_at` is stored as an ISO timestamp. The record stays in the database after expiry (history is preserved), but `list_by_agent(status="approved")` filters it out - `(expires_at IS NULL OR expires_at > now)`. Once expired, the command is no longer in the effective approved list and the next blocked attempt creates a new pending record.

`reach approvals` shows the expiry (or "permanent") for each effective entry. `reach approvals --expired` fetches records where `status=approved` but `expires_at < now` (the inverse set), filtered to the current user's own records.

---

## Agent privilege and access level

The agent detects whether it is running as root (`os.Getuid() == 0`) and includes `running_as_root: true/false` in each sync request. The backend stores this on the agent record.

`access_level` is a computed label combining the agent's current mode and privilege. It is injected into agent responses at read time - not stored separately.

| access_level | Mode | running_as_root |
|---|---|---|
| `open` | wild | true |
| `elevated` | wild | false - or - approved + root |
| `managed` | approved | false - or - readonly + root |
| `restricted` | readonly | false |

This label is shown in `reach agents list` and `reach status`. It is a factual descriptor of how the agent is configured, not a risk score.

---

## Multi-tenancy

Every resource (agent, user, job, approval) belongs to a tenant. The backend enforces tenant isolation at the storage layer - user tokens can only see agents, jobs, and approvals within their own tenant. The admin API (authenticated with `ADMIN_TOKEN`) operates across tenants.

---

## Release structure

Artifacts are published to S3 under component-specific prefixes:

```
s3://reach-releases/
  cli/
    v0.1.0/reach-0.1.0-py3-none-any.whl
    latest/reach-0.1.0-py3-none-any.whl
  agent/
    v0.1.0/reach-agent-linux-amd64
    v0.1.0/reach-agent-linux-arm64
    v0.1.0/reach-agent-darwin-arm64
    v0.1.0/reach-agent-darwin-amd64
    v0.1.0/install.sh        (handles install + uninstall via --uninstall flag)
    latest/  (same files)
  lambda/
    code/     (SAM-packaged function zips, content-addressed)
    v0.1.0/template.yaml
    latest/template.yaml
  local-setup.sh
```

Docker image: `nabeemdev/reach:{version}` and `nabeemdev/reach:latest`.

Each component is versioned independently. Release scripts: `scripts/release_cli.sh`, `scripts/release_agent.sh`, `scripts/release_backend.sh` (covers Docker + Lambda).
