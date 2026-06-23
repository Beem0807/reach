# Architecture

## Overview

Reach is a command bridge between AI agents (or any automation) and remote machines. It has three components:

```
┌─────────────────────────────────────────────────────────────────┐
│  Local machine                                                  │
│                                                                 │
│   ┌───────────┐     ┌─────────────┐                             │
│   │  CLI      │     │  MCP server │                             │
│   │ (reach)   │     │ (reach mcp) │                             │
│   └─────┬─────┘     └──────┬──────┘                             │
│         │                  │  stdio (JSON-RPC)                  │
│         │           ┌──────┴──────┐                             │
│         │           │  MCP client │ (Claude Code, Cursor, etc.) │
│         │           └─────────────┘                             │
└─────────┼───────────────────────────────────────────────────────┘
          │ HTTPS
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Backend                                                        │
│                                                                 │
│   ┌────────────────────────────────────────────────────────┐    │
│   │  FastAPI  (Docker)   or   Lambda + API Gateway         │    │
│   └────────────────────────────────────────────────────────┘    │
│   ┌────────────────────────────────────────────────────────┐    │
│   │  PostgreSQL (Docker)  or   DynamoDB (Lambda)           │    │
│   └────────────────────────────────────────────────────────┘    │
└─────────────────────────────▲───────────────────────────────────┘
                              │ HTTPS (outbound from agent)
┌─────────────────────────────┴───────────────────────────────────┐
│  Remote machine                                                 │
│                                                                 │
│   ┌──────────────┐                                              │
│   │  reach-agent │  (systemd / launchd service or foreground)   │
│   └──────────────┘                                              │
└─────────────────────────────────────────────────────────────────┘
```

The agent never accepts inbound connections. It makes outbound HTTPS requests to the backend, polls for jobs, and posts results back. No SSH, no VPN, no open ports.

---

## Command flow

A command goes through five steps:

```
1. Submit    CLI / MCP  →  POST /jobs                    →  Backend stores job (PENDING, is_write annotated)
2. Poll      Agent      →  POST /agent/sync               →  Backend returns pending job + is_write flag
3. Execute   Agent runs the command; enforcement depends on mode and OS (see Policy enforcement)
4. Result    Agent      →  POST /agent/jobs/{id}/result   →  Backend redacts secrets, stores output (SUCCEEDED / FAILED)
5. Retrieve  CLI / MCP  →  GET  /jobs/{id}                →  Backend returns output (MCP redacts again before the LLM sees it)
```

The CLI and MCP server both poll `GET /jobs/{id}` until the job reaches a terminal state or the timeout is hit. The agent has no direct channel back to the submitter - results go through the backend.

**In approved mode**, step 3 may produce a blocked result:

```
3a. Execute  Agent checks approved list and OS:
              Linux  - unapproved write runs under Landlock; kernel blocks it
              macOS  - unapproved write detected via server-supplied is_write flag; blocked early
3b. Result   Agent posts result with blocked=true, is_write=true
3c. Record   Backend updates is_write on the job; creates a pending approval record
3d. Notify   User sees it via `reach approvals --pending`; an operator or admin approves or denies in the tenant console (or via `PUT /tenant/approvals/{id}/approve`)
```

---

## Components

### CLI (`cli/`)

A Python CLI (`reach`) that authenticates with an API token (`tok_`) and talks to the backend over HTTPS. Manages a local config file (`~/.reach/config.json`) with the API URL, API token, default agent, and aliases.

Notable commands: `exec`, `job`, `history`, `agents`, `approvals` (with `--pending`/`--denied`/`--expired` flags), `agent-init`, `mcp`, `man`.

### MCP server (`cli/reach/mcp_server.py`)

Launched as a subprocess by an MCP-compatible client (Claude Code, Cursor, etc.) and communicates over stdio using JSON-RPC. Exposes the same operations as the CLI as structured tools: `get_context`, `whoami`, `list_agents`, `get_agent`, `exec_command`, `get_job`, `list_history`, `list_approved_commands`, `list_pending_approvals`. The client manages the process lifecycle - no hosting or ports needed.

`get_context` is the entry point for each session - it returns the authenticated user, the configured default agent (with live mode and access_level), and local aliases in a single call, so the LLM is oriented before it submits any commands.

The MCP server is installed as part of the CLI package (`reach-mcp` entry point).

### Backend (`backend/`)

A FastAPI application with a storage-backend abstraction that supports two databases:

| Deployment | Runtime | Database |
|---|---|---|
| Docker | FastAPI (uvicorn behind nginx) | PostgreSQL (via SQLAlchemy + Alembic) - default |
| Docker on AWS | FastAPI (uvicorn behind nginx) | DynamoDB (boto3) - opt-in with `STORAGE_BACKEND=dynamo` |
| Lambda | API Gateway + Lambda | DynamoDB (boto3) |

The same handler code runs in every deployment. The storage layer is swapped via the `STORAGE_BACKEND` env var (`postgres` or `dynamo`). Handlers import from `shared.store`, which returns the correct repo implementation.

nginx is required in front of uvicorn for the Docker deployment. Long-polling connections from the agent (`POST /agent/sync`) need to be terminated cleanly; uvicorn alone does not handle this correctly under load.

A background scheduler (APScheduler on FastAPI, EventBridge on Lambda) runs every minute to:
- Mark agents `INACTIVE` if no heartbeat in the last 45 seconds
- Expire `PENDING` jobs older than 1 hour to `EXPIRED`
- At the top of every hour: mark `approved` approval records with `expires_at` in the past as `expired`
- At midnight UTC: delete records past their retention window - `denied`/`expired` approvals older than `APPROVAL_RETENTION_DAYS` (7), terminal jobs older than `JOB_RETENTION_DAYS` (7), audit logs older than `AUDIT_RETENTION_DAYS` (90), and agent status history older than `AGENT_HISTORY_RETENTION_DAYS` (30)

### Agent (`agent/`)

A Go binary installed via `install.sh`. On Linux it runs as a systemd service under a dedicated `reach-agent` system user. On macOS it runs as a foreground process by default (stops when the terminal closes), or with `--background` as a LaunchDaemon under the same dedicated `reach-agent` system user (starts on boot, same security model as Linux). On startup it claims itself using an install token, then enters a poll loop:

1. `POST /agent/sync` - sends heartbeat and `running_as_root` flag, receives pending job (if any) with `is_write` flag and the list of approved commands
2. Runs the command, optionally under a Landlock sandbox on Linux or via `is_write` enforcement on macOS (see [Policy enforcement](#policy-enforcement))
3. `POST /agent/jobs/{id}/result` - posts stdout, stderr, exit code, whether the command was blocked, and `is_write` (set to `true` if blocked)

The agent self-rotates its token every 30 days. Tenant admins can also trigger an out-of-band rotation via `POST /tenant/agents/{id}/request-rotation`, which sets a `rotation_requested` flag; the agent picks it up on its next sync and self-rotates without any connection interruption. See [SELF_HOSTING.md](SELF_HOSTING.md) for the full agent lifecycle.

---

## Storage backend split

Lambda functions are stateless and short-lived. DynamoDB requires no persistent connection - each request opens and closes independently, which is the only viable model for Lambda at scale. Lambda + PostgreSQL is deliberately **not** supported: ephemeral connections exhaust PostgreSQL's connection limit quickly, and the fix (RDS Proxy) adds cost that defeats the purpose of the serverless option.

FastAPI in Docker holds a connection pool for the lifetime of the process, which is exactly what PostgreSQL expects - the default for the container image. **FastAPI + DynamoDB is also supported when the container runs on AWS** (ECS/Fargate/EKS): a long-lived process talking to DynamoDB is fine - boto3 reuses HTTP connections, so the connection-limit problem that rules out Lambda + PostgreSQL does not apply in reverse. This lets you run the container without managing an RDS instance. It is scoped to AWS-hosted containers because the boto3 client uses the standard AWS credential/region chain (task role, IRSA, instance profile, or env vars); off-AWS DynamoDB is not a supported target.

Unlike Postgres (tables created by Alembic) or Lambda (tables created by CloudFormation), the Docker + DynamoDB path creates its tables with an idempotent bootstrap (`shared/dynamo_bootstrap.py`) that runs from the same canonical schema (`shared/dynamo_schema.py`) on container start. See [SELF_HOSTING.md](SELF_HOSTING.md#dynamodb-on-aws) for the deployment steps and IAM policy.

The storage abstraction (`backend/shared/repos/base.py`) defines a common interface. `sql.py` implements it with SQLAlchemy, `dynamo.py` with boto3. Handlers never import from either directly.

---

## Token model

Three token types, none stored raw - only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted:

| Token | Prefix | Issued by | Used by | Lifetime |
|---|---|---|---|---|
| Install token | `install_` | `POST /tenant/agents` (tenant admin) | Agent (once, at claim) | 24 hours |
| Agent token | `agent_` | Backend (at claim) | Agent (every sync) | 30 days, auto-rotated |
| API token | `tok_` | `POST /tenant/api-tokens` (any tenant user) | CLI / MCP server | Until revoked |

The install token is one-time use and is cleared from disk after a successful claim. The agent token is bound to a machine fingerprint - a token replayed from a different machine is rejected. The agent rotates its own token every 30 days with no lockout window (old token is valid until the new one is persisted). Tenant admins can also request an immediate rotation via `POST /tenant/agents/{id}/request-rotation`; the flag is cleared atomically by `update_agent_token_hash` when the new token is stored.

---

## Agent lifecycle

```
CREATED ──(claim)──► ACTIVE ──(heartbeat gap)──► INACTIVE
   ▲                    │                              │
   │               (revoke)                       (revoke)
   │                    │                              │
   │                    ▼                              │
   └──(reissue     REVOKED ◄───────────────────────────┘
      install           │
      token)       (delete)
                        │
                        ▼
                   DELETED ──(remove)──► [gone]
```

- **CREATED** - registered, never claimed. Install token valid for 24 hours.
- **ACTIVE** - claimed and syncing. Transitions to INACTIVE after 45 seconds without a heartbeat.
- **INACTIVE** - missed heartbeats. Auto-recovers to ACTIVE on next successful sync (no manual intervention needed).
- **REVOKED** - access permanently cut. The agent can no longer sync (the sync endpoint rejects non-ACTIVE/INACTIVE status with 403). Removed from all users' allowed-agent lists at revoke time. Can be resurrected to CREATED by reissuing an install token (`POST /tenant/agents/{id}/reissue-install-token`), which clears the agent token, machine fingerprint, and claimed-at fields so the machine can re-install.
- **DELETED** - soft-deleted. Record stays in the database for audit purposes. Cannot sync or be reissued. Advance to this state only after REVOKED. Hidden from the user-facing endpoints (`GET /agents`, `GET /agents/{id}` return 404) but still actionable by tenant admins so the remove step can be completed.
- **[gone]** - permanently removed from the database via the remove action. No record remains.

The three-step decommission sequence prevents accidental hard-deletes:

| Step | Endpoint | Requires | Effect |
|---|---|---|---|
| 1. Revoke | `POST /tenant/agents/{id}/revoke` | Any active/inactive/created status | Sets REVOKED, removes from user access lists |
| 2. Soft-delete | `DELETE /tenant/agents/{id}` | REVOKED | Sets DELETED, record stays in table |
| 3. Remove | `DELETE /tenant/agents/{id}/remove` | DELETED | Permanently removes from database |

To undo a revoke: call `POST /tenant/agents/{id}/reissue-install-token`. This resets the agent to CREATED with a fresh install token and is the only way to restore a REVOKED agent. DELETED agents cannot be reissued - remove and create a new agent instead.

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
- An operator or admin can review these records (tenant console → Approvals, or the `/tenant/approvals` endpoints) and approve or deny them.
- Once approved, the command prefix is included in the approved list on the next sync, and the command runs without restriction.

### Approved list matching

The match is prefix-based with a word boundary: an approved entry of `docker logs` permits `docker logs myapp --tail 100` but not `docker rm myapp`. Matching checks `cmd == approved` or `cmd.startswith(approved + " ")`.

---

## Approvals

Approval records can be created two ways: automatically when an agent blocks a command in approved mode, or proactively by an operator or admin via `POST /tenant/approvals` (pre-approve without needing a prior block). The schema is the same either way:

```
approvals table:
  approval_id    - unique ID (appr_xxx)
  tenant_id      - which tenant
  agent_id       - which agent blocked the command
  command        - the exact command that was blocked
  requested_by   - user_id of the submitter
  requester_name - display name of the submitter
  job_id         - the job that triggered this record
  status         - pending | approved | denied | expired
  expires_at     - ISO timestamp after which the approval stops being effective (null = permanent)
  created_at     - when the block occurred
  reviewed_at    - when the admin acted
  reviewed_by    - who reviewed it
```

### Approval lifecycle

```
              block occurs / pre-approve
                           │
                           ▼
                       [pending]
                  ┌────────┴────────┐
                  │                 │
               approve            deny
                  │                 │
                  ▼                 ▼
             [approved]         [denied]  ◄── terminal (no further transitions)
                  │
         ┌────────┼───────────┐
         │        │           │
      extend   reduce   duration=now
     duration duration  or expires_at
         │        │        passes
         └───┬────┘           ▼
             ▼            [expired]       ◄── terminal (no further transitions)
        [approved]
   (expires_at updated)
```

| Current status | approve | deny | Notes |
|---|---|---|---|
| `pending` | ✓ → `approved` | ✓ → `denied` | Initial review; `duration=now` not allowed |
| `approved` | ✓ updates `expires_at` | 409 | Use `duration=now` to instantly move to `expired` |
| `denied` | 409 | 409 | Terminal - delete and let a new block create a fresh one |
| `expired` | 409 | 409 | Terminal - delete and let a new block create a fresh one |

Operators and admins manage approvals via `GET /tenant/approvals`, `PUT /tenant/approvals/{id}/approve`, `PUT /tenant/approvals/{id}/deny`, and `DELETE /tenant/approvals/{id}`. `DELETE /tenant/approvals/{id}` permanently removes a record of any status - useful for cleaning up stale pending records or removing an approved command from the effective list immediately. Users manage approvals through `reach approvals` with status flags: default shows effective approved commands (agent-wide); `--pending`, `--denied`, and `--expired` show the current user's own records for the agent.

Only one pending record is created per `(agent_id, command)` pair at a time. If a command is blocked and a pending record already exists for that exact command on that agent, no duplicate is created. Once the existing record is approved or denied, the next block creates a fresh pending record.

If multiple `approved` records accumulate for the same command on the same agent (can happen if a pending request is approved after a pre-approve was already issued), the repo deduplicates on read: the record with the longest remaining duration is kept (`permanent` beats any timed; latest `expires_at` wins among timed), and duplicates are deleted.

### Time-limited approvals

When approving a `pending` record or updating duration on an `approved` record, the reviewer (operator or admin) can supply a `duration` in the request body:

| Value | Meaning | Allowed on |
|---|---|---|
| `permanent` (default) | Never expires | `pending` → approve, `approved` update |
| `1h` | 1 hour | `pending` → approve, `approved` update |
| `8h` | 8 hours | `pending` → approve, `approved` update |
| `24h` | 24 hours | `pending` → approve, `approved` update |
| `7d` | 7 days | `pending` → approve, `approved` update |
| `Nh` / `Nd` | Custom N hours or N days | `pending` → approve, `approved` update |
| `now` | Instantly expire | `approved` update only - sets status to `expired` immediately |

Once a record reaches `expired` status, the command is no longer in the effective approved list and the next blocked attempt creates a new pending record.

`reach approvals` shows the expiry (or "permanent") for each effective entry. `reach approvals --expired` shows the current user's own records with `status=expired`.

### Automatic expiry and cleanup

Expiry happens through two mechanisms that run in parallel:

**Lazy expiry on read** - whenever `list_by_agent` or `list_by_tenant` is called with `status="approved"`, the repo checks the returned records against the current time. Any record with `expires_at <= now` is immediately marked `expired` in the database before the response is returned. This keeps the effective list accurate between scheduled sweeps.

**Scheduled sweeps** - the heartbeat checker (running every minute) performs two time-based sweeps:

- **Top of every hour** - scans for all `approved` records with `expires_at < now` and bulk-marks them `expired`. Catches any records missed between lazy-expiry reads.
- **Start of every day (00:00 UTC)** - deletes terminal records (`denied` and `expired`) older than `APPROVAL_RETENTION_DAYS` (default 7, configurable via env var). Prevents unbounded table growth.

The lazy expiry and the hourly sweep are idempotent and safe to run concurrently - both use conditional writes (`status = 'approved'` guard) so double-processing a record is harmless.

---

## Agent privilege and access level

The agent detects whether it is running as root (`os.Getuid() == 0`) and includes `running_as_root: true/false` in each sync request. The backend stores this on the agent record.

`access_level` is a computed label combining the agent's current mode and privilege. It is injected into agent responses at read time - not stored separately.

| access_level | Mode | running_as_root |
|---|---|---|
| `open` | wild | true |
| `elevated` | wild (non-root) or approved (root) | - |
| `managed` | approved (non-root) or readonly (root) | - |
| `restricted` | readonly | false |

This label is shown in `reach agents list` and `reach status`, and is included in the `GET /agents` and `GET /agents/{id}` API responses so MCP clients receive it directly. It is a factual descriptor of how the agent is configured, not a risk score.

---

## Multi-tenancy

Every resource (agent, user, job, approval) belongs to a tenant. The backend enforces tenant isolation at the storage layer - user API tokens can only see agents, jobs, and approvals within their own tenant. The platform admin API (authenticated with a session token from `ADMIN_PASSWORD`) operates across tenants for provisioning only.

---

## Agent access control

Within a tenant, individual users can be further restricted to a subset of agents. This is separate from tenant isolation and is enforced at the handler layer via `can_access_agent(user, agent)` in `shared/access.py`.

A user record can carry two optional restriction fields:

| Field | Effect |
|---|---|
| `allowed_agent_ids` | User can only access agents whose `agent_id` is in this list |
| `allowed_fleet_ids` | User can only access agents whose `fleet_id` is in this list |

If both fields are `None` (the default), the user is unrestricted and can access any agent in their tenant.

`can_access_agent` is called on every operation that touches a specific agent, including:

- **Agent listing** (`GET /agents`) - filtered to accessible agents only; DELETED agents are excluded entirely
- **Agent detail** (`GET /agents/{id}`) - returns 404 for inaccessible or DELETED agents
- **Job submission** (`POST /jobs`) - rejects the job if the user cannot access the target agent
- **Job detail** (`GET /jobs/{id}`) - returns 404 if the job's agent is inaccessible
- **Job listing** (`GET /jobs`) - when `agent_id` filter is specified, validates access before querying; when listing all, post-filters to exclude jobs on inaccessible agents
- **Approved-command lookup** (`GET /agents/{id}/approved-commands`) - returns 404 for inaccessible agents
- **Pending approval list** (`GET /approvals/pending`) - when `agent_id` is specified, validates access before querying; when listing across all agents, post-filters results to only include agents the user can access

`can_access_agent` governs the user-facing data endpoints listed above (`/me`, `/jobs`, `/agents`, `/approvals/pending`). The platform admin API (`GET /admin/agents`, read-only) operates across tenants and is not subject to it. Tenant management endpoints (`/tenant/*`, admin/operator role) operate on every agent in their own tenant and are gated by role, not by per-user agent restrictions.

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
