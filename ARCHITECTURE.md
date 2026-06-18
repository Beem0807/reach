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
1. Submit    CLI / MCP  →  POST /jobs                    →  Backend stores job (PENDING)
2. Poll      Agent      →  POST /agent/sync               →  Backend returns pending job
3. Execute   Agent runs the command locally
4. Result    Agent      →  POST /agent/jobs/{id}/result   →  Backend stores output (SUCCEEDED / FAILED)
5. Retrieve  CLI / MCP  →  GET  /jobs/{id}                →  Backend returns output
```

The CLI and MCP server both poll `GET /jobs/{id}` until the job reaches a terminal state or the timeout is hit. The agent has no direct channel back to the submitter - results go through the backend.

---

## Components

### CLI (`cli/`)

A Python CLI (`reach`) that authenticates with a user token and talks to the backend over HTTPS. Manages a local config file (`~/.reach/config.json`) with the API URL, user token, default agent, and aliases.

Notable commands: `exec`, `job`, `history`, `agents`, `policy show`, `agent-init`, `mcp`.

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

1. `POST /agent/sync` - sends heartbeat, receives pending job (if any)
2. Runs the command in a subprocess with a 60-second timeout
3. `POST /agent/jobs/{id}/result` - posts stdout, stderr, exit code

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

Commands are evaluated server-side before being queued. The agent never sees a blocked command.

Three modes per agent: `wild` (allow all), `readonly` (block writes and destructive ops), `approved` (allowlist of exact command prefixes). Modes are set via the admin API and are immutable from the CLI - the CLI can view the policy but not change it.

A global blocklist (fork bombs, `rm -rf /`, `mkfs`, `dd if=`, shutdown/reboot) is enforced regardless of mode.

---

## Multi-tenancy

Every resource (agent, user, job) belongs to a tenant. The backend enforces tenant isolation at the storage layer - user tokens can only see agents and jobs within their own tenant. The admin API (authenticated with `ADMIN_TOKEN`) operates across tenants.

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
