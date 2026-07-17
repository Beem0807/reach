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
1. Submit    CLI / MCP  →  POST /jobs                     →  Backend stores job (PENDING, is_write annotated)
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
3d. Notify   User sees it via `reach approvals list --pending`; an operator or admin approves or denies in the tenant console (or via `PUT /tenant/approvals/{id}/approve`)
```

---

## Components

### CLI (`cli/`)

A Python CLI (`reach`) that authenticates with an API token (`tok_`) and talks to the backend over HTTPS. Manages a local config file (`~/.reach/config.json`) with the API URL, API token, default agent, and aliases.

Notable commands: `exec` (single agent, or `--tag` to fan out to standalone agents; type-homogeneous host/k8s), `job`, `jobs`, `runs`/`run` (tag fan-out runs across standalone agents), `agents` (`list`/`show`/`use`), `fleets` (`list`/`show`/`use`/`agents`/`exec`/`jobs`/`runs`/`run`/`approvals`), `approvals` (`list`/`request`/`approve`/`deny`), `agent-init`, `mcp`, `man`. Fan-outs (`reach fleets exec`, `reach exec --tag`) confirm first and create a first-class **run** (a row in the `runs` table with a `run_id`), so a run's identity, intent (dispatched/skipped), and status survive independently of its member jobs (which are purged on retention). A **single-agent `reach exec`** also confirms before a **write** (destructive) command - a `dry_run` classifies it server-side and the CLI prompts unless `--force`/`--json` - so `rm -rf` on one host isn't unprompted either; reads run straight through. **Every** eligible member runs, but never more than the per-fleet fan-out cap (`max_fanout`, operator-set) at a time: above the cap the run proceeds in **waves** of the cap (`--max-targets` lowers the wave size, can't raise it above the cap), advancing per the tenant/fleet wave policy (auto/manual, stop/continue). Poll a run with `GET /tenant/runs/{run_id}` and control a staged one with pause/resume/cancel; an `idempotency_key` makes a retried fan-out reuse the same run instead of double-dispatching. Global `--json` emits raw JSON for scripting; exit codes are `0` ok / `1` remote command failed / `2` reach-level error.

### MCP server (`cli/reach/mcp_server.py`)

Launched as a subprocess by an MCP-compatible client (Claude Code, Cursor, etc.) and communicates over stdio using JSON-RPC. Exposes the same operations as the CLI as structured tools: `get_context`, `whoami`, `list_agents`, `get_agent`, `exec_command`, `exec_by_tag` (confirm-gated tag fan-out), `list_tag_runs`/`list_tag_run` (tag fan-out history), `get_job`, `list_history`; and for fleets `list_fleets`, `list_fleet_agents`, `list_fleet_jobs`, `list_fleet_runs`, `list_fleet_run`, `list_fleet_approved`, `fleet_exec` (confirm-gated fan-out); plus `list_approved_commands`, `list_pending_approvals`. Destructive commands are two-step: a `confirm=false` dry-run preview must be shown to the user before a `confirm=true` dispatch - this gates the fan-outs (`fleet_exec`, `exec_by_tag`) and also a single-agent **write** via `exec_command` (reads run straight through). The MCP surface is deliberately **read-only for approvals** - it exposes no create/approve/deny tool, so an AI can't file a request and approve it itself (approval stays a human control; use the console or CLI). The client manages the process lifecycle - no hosting or ports needed.

`get_context` is the entry point for each session - it returns the authenticated user, the configured default agent (with live mode and access_level), and local aliases in a single call, so the LLM is oriented before it submits any commands.

The MCP server is installed as part of the CLI package (`reach-mcp` entry point).

### Console (`ui/`)

A React/Vite single-page app served at `/ui`, with two audiences behind separate session
logins. The **tenant console** (admin/operator/developer) manages agents, fleets, approvals,
API tokens, users, and settings, and browses jobs, fan-out runs, and the tenant audit log. It
can also **launch work**: a single-agent job from an agent row, a fleet fan-out from a fleet,
and a tag fan-out - plus top-level **Create job / New run** launchers on the Jobs page. These
are **write-gated** (only targets the user has read-write access to are runnable; inactive
agents are shown disabled), and **every** run - single-agent job or fan-out - shows a
**dry-run preview + confirm** before dispatching (the single-agent preview classifies the
command read/write and shows the agent's mode; fan-outs show the blast radius + wave plan).
After a **single-agent** dispatch the launcher **polls the job to completion and shows the exit
code + stdout/stderr inline** (mirroring `reach exec`); fan-outs link to the run view. The **platform-admin console** manages tenants (create/enable/disable),
can **override a tenant's settings** past its bounds (audit-logged), and reads the platform-wide
audit log with a tenant filter. Both consoles use the same JSON API as the CLI (session-token
auth); the console never gets a capability the API doesn't already enforce server-side.

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
- **Reap dead fleet members** whose last heartbeat is older than their fleet's `reap_after_seconds` (default `FLEET_REAP_AFTER_SECONDS`, 30 min)
- Expire `PENDING` jobs older than 1 hour to `EXPIRED`
- At the top of every hour: mark `approved` approval records with `expires_at` in the past as `expired`
- At midnight UTC: sweep each tenant and delete records past **that tenant's** retention settings - `denied`/`expired` approvals, terminal jobs, fan-out runs, tenant audit entries, and agent status history (defaults 7/7/30/90/30 days). The platform-level audit trail (`tenant_id IS NULL`) is swept separately with the deployment-wide `AUDIT_RETENTION_DAYS` (90)

### Agent (`agent/`)

A single dependency-light Go binary (standard library only - no `client-go`) that
runs in one of two auto-detected modes. The poll loop is the same in both: claim
(if needed) → `sync` (heartbeat + receive jobs) → execute → `POST /agent/jobs/{id}/result`.
See [agent/README.md](agent/README.md) for the full design; the short version:

- **Host (Linux/macOS)** - installed via `install.sh` as a systemd service or
  (macOS) a foreground process / LaunchDaemon under a dedicated `reach-agent` user.
  Identity is a machine fingerprint. Jobs run via `/bin/bash -lc`, sandboxed with
  **Landlock** on Linux in readonly/approved mode.
- **Kubernetes** - installed via Helm (`deploy/helm/reach-agent`) as a Deployment
  running the `nabeemdev/reach-agent` image. Identity is derived from the
  `kube-system` namespace UID, so every replica is the **same** agent; a
  `coordination.k8s.io/Lease` elects one active leader. The agent token lives in a
  managed Secret (nothing on disk). Jobs run **without a shell** - parsed into a
  pipeline, restricted to an allowlist (`kubectl` + read-only filters), with
  local-file reads blocked. The agent self-reports its cluster-wide RBAC for
  drift/acknowledge in the console.

The agent **never sends or stores an agent id** (credential-only - see [Token
model](#token-model)). It self-rotates its token every 30 days; tenant admins can
also request an immediate rotation via `POST /tenant/agents/{id}/request-rotation`,
which the agent picks up on its next sync. See [SELF_HOSTING.md](SELF_HOSTING.md)
for the full agent lifecycle.

---

## Storage backend split

Lambda functions are stateless and short-lived. DynamoDB requires no persistent connection - each request opens and closes independently, which is the only viable model for Lambda at scale. Lambda + PostgreSQL is deliberately **not** supported: ephemeral connections exhaust PostgreSQL's connection limit quickly, and the fix (RDS Proxy) adds cost that defeats the purpose of the serverless option.

FastAPI in Docker holds a connection pool for the lifetime of the process, which is exactly what PostgreSQL expects - the default for the container image. **FastAPI + DynamoDB is also supported when the container runs on AWS** (ECS/Fargate/EKS): a long-lived process talking to DynamoDB is fine - boto3 reuses HTTP connections, so the connection-limit problem that rules out Lambda + PostgreSQL does not apply in reverse. This lets you run the container without managing an RDS instance. It is scoped to AWS-hosted containers because the boto3 client uses the standard AWS credential/region chain (task role, IRSA, instance profile, or env vars); off-AWS DynamoDB is not a supported target.

Unlike Postgres (tables created by Alembic) or Lambda (tables created by CloudFormation), the Docker + DynamoDB path creates its tables with an idempotent bootstrap (`shared/dynamo_bootstrap.py`) that runs from the same canonical schema (`shared/dynamo_schema.py`) on container start. See [SELF_HOSTING.md](SELF_HOSTING.md#dynamodb-on-aws) for the deployment steps and IAM policy.

The storage abstraction (`backend/shared/repos/base.py`) defines a common interface. `sql.py` implements it with SQLAlchemy, `dynamo.py` with boto3. Handlers never import from either directly.

---

## Token model

Four token types, none stored raw - only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted:

| Token | Prefix | Issued by | Used by | Lifetime |
|---|---|---|---|---|
| Install token | `install_` | `POST /tenant/agents` (tenant admin) | Agent (once, at claim) | 24 hours |
| Fleet join token | `fleet_` | `POST /tenant/fleets` (tenant admin) | Any host installer (**reusable**, at claim) | Until rotated or revoked |
| Agent token | `agent_` | Backend (at claim) | Agent (every sync) | 30 days, auto-rotated |
| API token | `tok_` | `POST /tenant/api-tokens` (any tenant user) | CLI / MCP server | Until revoked |

The install token is one-time use and is cleared after a successful claim. The **fleet join token** is the exception: it is deliberately **reusable** - every host that claims with it enrolls into the fleet - and does not expire until you rotate or revoke it (see [Fleets](#fleets)). The agent token is bound to a machine fingerprint - a token replayed from a different machine is rejected. The agent rotates its own token every 30 days with no lockout window (old token is valid until the new one is persisted). Tenant admins can also request an immediate rotation via `POST /tenant/agents/{id}/request-rotation`; the flag is cleared atomically by `update_agent_token_hash` when the new token is stored.

**Credential-only identity.** The agent never sends or stores an `agent_id`. At
claim it presents the **install token**, and the backend looks the agent up by
`install_token_hash`; on every later call it presents the **agent token**, resolved
by `agent_token_hash`. Both hashes are uniquely indexed (Postgres) / GSI'd
(DynamoDB), so a hash maps to exactly one agent. An `agent_id` still exists on the
backend as the record key and operator handle (`reach exec --agent <id>`, access
lists), but it is never trusted from - or even known to - the agent process.

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
- **REVOKED** - access permanently cut. The agent can no longer sync (the sync endpoint rejects non-ACTIVE/INACTIVE status with 403). Removed from all users' agent access lists (read-write and read-only) at revoke time. Can be resurrected to CREATED by reissuing an install token (`POST /tenant/agents/{id}/reissue-install-token`), which clears the agent token, machine fingerprint, and claimed-at fields so the machine can re-install.
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

## Fleets

A **fleet** is a reusable **join token** for host agents, built for autoscaling groups
of any flavour - AWS ASGs, GCP MIGs, Azure VMSS, Nomad, on-prem autoscalers, or any
"cattle, not pets" fleet of identical hosts. You bake the join token into the group's
launch/instance template (user-data or startup script); every instance that scales in
claims with it and auto-enrolls as a host agent, inheriting the fleet's **mode**,
**tags**, and **grants**. Fleets are **host-only** - k8s agents have
one-agent-per-cluster identity, so the reusable-token model doesn't apply.

**Enrollment.** `POST /agent/claim` detects the `fleet_` prefix and routes to fleet
enrollment: it mints a new host agent, or **idempotently re-enrolls** an existing one
keyed on `(fleet_id, machine_fingerprint)`. So an instance that reboots and re-claims
reuses its record (and `agent_id`) instead of creating a duplicate. The claim is
keyed by **IP** and rate-limited at 120/min so a NAT-shared autoscaling group can enroll many
instances at once; the 256-bit token makes that limit purely anti-DoS.

**Inheritance.** Members inherit the fleet's mode, tags, and grants at claim, and are
managed *through the fleet*: changing a fleet's **mode** or **tags** propagates to
every current member, and per-member `set mode`/`set tags`/`reissue` are blocked
(`409`). **Grants** behave differently from mode/tags: they're baked into the host at
install (sudoers / docker group), so editing a fleet's grants can't be pushed to a
running member remotely. An edit changes what *new* instances enroll with (re-issue the
launch-template command by rotating the join token) and marks existing members as a
**grant mismatch** (member grants ≠ fleet grants) - the console flags the count and
per-member. You reconcile out of band (re-provision/replace the host), then `POST
/tenant/fleets/{id}/resolve-grants` with `{"resolution": "reconcile"}` sets the mismatched
members' grants to match the fleet. This is deliberately **distinct from a capability/RBAC
*acknowledge*** (which accepts observed reality): reconciling asserts a fix, so it is
**verified against detection** - a member is only reconciled if the host actually reports
the granted capability (`*_detected`); hosts that don't are returned as `blocked`, so a
mismatch can't be cleared on a host that was never re-provisioned. If a member is
*deliberately* allowed to differ, **accept** it instead (same endpoint, `{"resolution":
"accept"}`): the member keeps its real grants (nothing falsified) but stops being flagged - recorded
against a **(member grants, fleet grants)** signature, so it auto-re-flags if *either*
side changes afterwards (the fleet grants are edited, or the member's own grants shift to
a new mismatch, e.g. a later capability-acknowledge). The exception is also **dropped
the moment the member matches the fleet again** (lazy-cleared on the next agent read), so
a later return to the same divergence must be accepted afresh rather than staying silently
suppressed.
Rotating a member's own agent token and acknowledging capabilities still work per-agent.

**Approvals are fleet-scoped.** A member has no per-agent approvals - approvals target the
**fleet** (`agent_id` is null, `fleet_id` set), and every member inherits them. This keeps
`approved` mode workable for churning autoscaler instances: pre-approve a command once on the fleet
and each new instance picks it up on its first sync. When a member's write is blocked, the
backend raises a **fleet-scoped** pending request (deduplicated per fleet, not per agent), and
a member's sync draws its approved-command allow-list from the fleet. Creating a per-agent
approval for a member's `agent_id` is rejected (`409`). Access follows the same rule as agents:
reviewing/pre-approving a fleet approval requires **read-write** access to that fleet.

**Host writes are structured, and host approvals are JSON rules.** A plain host **write**
command is parsed into an `argv` (`{bin, args}`) and executed with `execve` - **no shell** -
so there is nothing to pipe, chain, substitute, or glob. A write that needs shell features
can't be a rule, so it is **rejected in approved mode** (unapprovable), **runs freeform in
wild mode** (no approval, no sandbox - blocking there is pure friction), and is refused in
readonly. Approvals for host writes are **structured rules** `{bin, args[]}` where each arg
is a literal or `*` (positional wildcard, fixed arity) - matched against the argv, never by
string comparison. This mirrors the k8s `{verb, resource, namespace, name}` model, and applies
to **fleet** fan-outs the same way (fleet writes are structured; the fleet's host rules gate
approved-mode writes). **Reads** are unaffected: they run as-is (freeform shell) under Landlock
in readonly/approved mode and never need approval. The agent gates an approved-mode structured
write on a rule match; an unmatched write is blocked (structured approval error).

**Approvals are cascade-deleted when their target is torn down**, so a stale pre-approval can
never outlive the agent/fleet it was scoped to (and can't be inherited by a future id reuse):
permanently **removing** an agent purges its `agent_id` approvals; **revoking a fleet with
`members=remove`** and **deleting a fleet** purge its `fleet_id` approvals. (Soft-deleting an
agent or revoking a fleet with `members=keep` leaves them - the agent is inert / the members live
on as standalone agents - until the hard-remove/delete step or the retention sweep.)

**Token rotation.** `POST /tenant/fleets/{id}/rotate-token` issues a new join token
while keeping the previous one valid for a grace window (default 24h, `grace_seconds`
configurable, `0` = immediate). This lets you update the launch template before the
old token stops working - stored as `prev_join_token_hash` + `prev_join_token_expires_at`.

**Scale.** A fleet can hold thousands of members (autoscaling cattle), so per-fleet
operations - the member list (`GET /fleets/{id}/agents`), fan-out, fleet-job filtering,
grant reconcile/accept - query by `fleet_id` directly (`agents_repo.list_by_fleet`, an
indexed lookup: the `ix_agents_fleet_id` Postgres index / the `fleet-index` DynamoDB GSI)
rather than scanning and filtering every agent in the tenant. Member **counts** for the
fleet list come from a single `GROUP BY` (`member_counts`), not by loading members.

**Leaving a fleet.** Three paths:

| Path | Trigger | Effect |
|---|---|---|
| Detach | `DELETE /tenant/fleets/{id}/members/{agent_id}` | Member becomes a standalone individual agent, keeping its config/history and regaining individual controls |
| Deregister | Agent calls `POST /agent/deregister` on **machine shutdown** | Member removes itself immediately on autoscaler scale-in (a plain service restart does **not** deregister - see below) |
| Reap | Heartbeat sweep, past the reap window | The backend deletes members that stopped heartbeating (see [Automatic expiry and cleanup](#automatic-expiry-and-cleanup)) |

**Deregister vs. reap.** Both remove a scaled-in instance; deregister is the fast path
and the reaper is the backstop. On graceful shutdown (`SIGTERM`) a host distinguishes a
real machine shutdown from a `systemctl restart` via systemd's manager state
(`systemctl is-system-running` == `stopping`), and only deregisters on a genuine
shutdown - so restarting the service never churns the member record. If the deregister
call is missed (network, crash, non-systemd host), the reaper still removes the member
after its reap window (`reap_after_seconds`, or the deployment default).

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
- `approved` - the agent uses the `is_write` flag from the sync response. A structured write (`argv`) that isn't permitted by an approved host rule is blocked immediately with `blocked=true`; read commands (`is_write=false`) always run. This matches the Linux Landlock behaviour: reads pass, unapproved writes are blocked and create a pending approval record.

**Approved mode logic (both platforms)**

When the agent receives a job in approved mode it also receives, from the sync response, the
`is_write` flag plus the current **approved host rules** (`{bin, args[]}`) to match a structured
write against. Host writes are always structured (an `argv`); reads and wild-mode commands run
freeform. There is no command-string approval list - every approval is a rule.

- **Structured write** (the job carries an `argv`): approved iff some **host rule** matches the
  argv (bin equal, arity equal, each arg literal-or-`*`, or a trailing `...` for the rest). If
  matched it runs directly (execve, no shell); if not, it's a blocked write.
- **Linux:** an unapproved write runs under Landlock and is blocked at the kernel (permission
  denied) → the agent posts `blocked=true, is_write=true`.
- **macOS:** an unapproved write (`is_write=true`) returns `blocked=true` immediately (no Landlock).
- The backend receives `blocked=true`, updates `is_write` on the job, looks up the requester, and
  creates a **pending** record in the `approvals` table - scoped to the agent, or to its **fleet**
  if the agent is a fleet member (see [Fleets](#fleets)).
- An operator/admin reviews these (tenant console → Approvals, or the `/tenant/approvals` endpoints)
  and approves or denies. Once approved, the rule feeds the next sync and the write runs.

### Approved matching

A **structured** write matches a host rule `{bin, args[]}` positionally - bin equal, arity equal,
each arg equal or `*` (a single-arg wildcard); a trailing `...` relaxes arity to match zero or more
remaining args (so `{bin: helm, args: [list, ...]}` covers `helm list` and `helm list -n prod`).
Mirrors the k8s `{verb, resource, namespace, name}` rule. There is no string comparison and no
prefix-match path: a write with shell operators can't be a rule, so it's unapprovable in
`approved` mode (rejected at submission) - which is exactly why an approved action can never be
extended (`approved-cmd | tee`, `… && rm -rf`) to smuggle an unapproved write.

### Kubernetes agents

For `type=k8s` agents the model is different - Landlock (a filesystem sandbox) is irrelevant to `kubectl`'s API calls, so policy mode is enforced **server-side at job submission** instead:

- The backend classifies each command **default-deny**: only `kubectl` read-verbs and pure read-only filters (`grep jq head tail wc sort uniq cut tr`) are reads; every other `kubectl` verb (incl. `exec`/`cp`/`port-forward` and unknown verbs) is a write, and **any non-`kubectl` binary** (`helm`, `flux`, a custom CLI you allow-listed) is also a write. `readonly` rejects writes outright; `approved` records a `REJECTED` job and raises a pending approval (the command never dispatches); pre-approved writes and reads dispatch normally.
- Approvals for k8s agents are **structured rules**: a `kubectl` write matches a `{verb, resource, namespace, name}` rule; a **non-`kubectl` write** matches a `{bin, args[]}` **host rule** (positional `*` and trailing `...`, the same model as host agents). A submitted write is permitted when some approved rule matches; a blocked write's rule is **derived** from the command onto the pending approval for the operator to review. See [Approvals](#approvals).
- The **agent** adds compromise-resistant bounds that are agent-local (not job data, so a malicious backend can't relax them): jobs run **without a shell**, every pipeline stage's binary must be in the agent's **execution allowlist** (`kubectl` + read-only filters + any `extraAllowedBinaries`), arguments that resolve to a local file are rejected (no reading the mounted ServiceAccount token), and arbitrary-exec escapes (`helm --post-renderer`, `helm plugin`) are hard-blocked regardless of approval. The agent **reports this allowlist** to the backend so the console warns/blocks approving a binary it won't run.
- The **API server** enforces **RBAC** as the unbypassable floor.

So three layers compose - RBAC (what's possible) ∩ policy mode (what's allowed) ∩ allowlist/no-shell (blast-radius bound). The agent reports both its effective cluster-wide RBAC (`SelfSubjectRulesReview` across all namespaces) for acknowledge/drift and its execution allowlist; see [agent/README.md](agent/README.md).

#### How `kubectl` commands are classified

Write-ness is decided **fail-closed** - anything that isn't a proven read is a write. A pipeline is a read only when **every** stage is a read: a `kubectl` read-verb, or a pure read-only filter. Any other `kubectl` verb, and **any non-`kubectl` binary** (`helm`, `flux`, a custom tool - classified as a write regardless of its subcommand, since Reach can't reason about arbitrary CLIs), is a write. So an unrecognized/future verb or an unknown tool is gated, never silently allowed. Authoritative in `backend/shared/policy.py` (`_K8S_READ_VERBS`, `_K8S_WRITE_VERBS`, `_K8S_COMPOUND_*`, `_K8S_READ_FILTERS`, `is_k8s_write`, `k8s_nonkubectl_argv`).

- **Reads** run in every mode and never need approval: `get`, `describe`, `logs`, `top`, `explain`, `events`, `diff`, `wait`, `api-resources`, `api-versions`, `version`, `cluster-info`. Plus **cluster-inert utilities** that only render/print locally or touch the local kubeconfig - which the agent never uses for auth (it authenticates via its in-cluster ServiceAccount) and can't write (read-only rootfs): `kustomize`, `options`, `completion`, `plugin`, `config` (all subcommands).
- **Writes** are blocked (`readonly`) or held for approval (`approved`): `create`, `apply`, `delete`, `edit`, `patch`, `replace`, `scale`, `autoscale`, `expose`, `run`, `label`, `annotate`, `drain`, `cordon`/`uncordon`, `taint`, `exec`, `attach`, `cp`, `port-forward`, `proxy`, `debug`, … and anything unrecognized.
- **Dry runs are reads.** A `--dry-run=client|server` (or the deprecated bare `--dry-run`) makes an otherwise-mutating command non-mutating, so it's classified as a read. `--dry-run=none` really applies and stays a write.
- **Double verbs** - where the real operation is `base + sub` (and the sub can flip read↔write) - are keyed as the compound `"<base> <sub>"`, so reads and writes are distinguished and each write is **separately approvable** (e.g. allow `certificate approve` but not `certificate deny`):

  | Command | Reads | Writes |
  |---|---|---|
  | `rollout` | `status`, `history` | `restart`, `undo`, `pause`, `resume` |
  | `auth` | `can-i`, `whoami` | `reconcile` (edits RBAC) |
  | `apply` | `view-last-applied` | `set-last-applied`, `edit-last-applied` |
  | `set` | - | `image`, `env`, `resources`, `selector`, `serviceaccount`, `subject` |
  | `certificate` | - | `approve`, `deny` |

  An unrecognized sub of a known base is treated as a write (fail-closed). The compound verb lands in the structured rule's `verb` field - e.g. blocking `kubectl rollout restart deploy/web` derives `{verb: "rollout restart", resource: "deployments", name: "web", namespace: "*"}` - so no extra column is needed. The UI approval form offers the same write set, and a backend test (`test_ui_verb_dropdown_mirrors_backend_write_verbs`) keeps the two in lockstep.

  **Namespace inference:** an unqualified command is attributed to `default` (the `-n`/`--all-namespaces` value otherwise). In-cluster `kubectl` would otherwise silently target the agent's *own* pod namespace, so the **agent injects `--namespace=default`** into any kubectl stage that doesn't select one (overridable per install via `REACH_K8S_DEFAULT_NAMESPACE`) - making the command run exactly where the backend classified it. Scope approvals precisely with `-n`, or use the `namespace: *` rule.

---

## Approvals

Approval records can be created two ways: automatically when an agent blocks a command in approved mode, or proactively by an operator or admin via `POST /tenant/approvals` (pre-approve without needing a prior block). The schema is the same either way:

```
approvals table:
  approval_id    - unique ID (appr_xxx)
  tenant_id      - which tenant
  agent_id       - which agent blocked the command
  command        - a readable rendering of the structured rule (for display/search)
  host_rule      - structured host rule {bin, args[]} (each arg a literal, `*`, or trailing `...`); used by host agents AND non-kubectl k8s tools (helm/flux); null otherwise
  k8s_rule       - structured kubectl rule {verb, resource, namespace, name} for k8s agents; null otherwise
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

Operators and admins manage approvals via `GET /tenant/approvals`, `PUT /tenant/approvals/{id}/approve`, `PUT /tenant/approvals/{id}/deny`, and `DELETE /tenant/approvals/{id}`. `DELETE /tenant/approvals/{id}` permanently removes a record of any status - useful for cleaning up stale pending records or removing an approved command from the effective list immediately. Users manage approvals through `reach approvals list` with status flags: default shows effective approved commands (agent-wide); `--pending`, `--denied`, and `--expired` show the current user's own records for the agent.

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

`reach approvals list` shows the expiry (or "permanent") for each effective entry. `reach approvals list --expired` shows the current user's own records with `status=expired`.

### Automatic expiry and cleanup

Expiry happens through two mechanisms that run in parallel:

**Lazy expiry on read** - whenever `list_by_agent` or `list_by_tenant` is called with `status="approved"`, the repo checks the returned records against the current time. Any record with `expires_at <= now` is immediately marked `expired` in the database before the response is returned. This keeps the effective list accurate between scheduled sweeps.

**Scheduled sweeps** - the heartbeat checker (running every minute) performs several time-based sweeps:

- **Every minute** - marks ACTIVE agents with no heartbeat in 45s as `INACTIVE`, and **reaps dead fleet members**: any fleet member whose last heartbeat is older than its fleet's `reap_after_seconds` (falling back to `FLEET_REAP_AFTER_SECONDS`, default 30 min) is deleted, writing an `agent.reaped` history + audit entry first. This is the backstop for autoscaler instances that scaled in without a clean `POST /agent/deregister`.
- **Top of every hour** - scans for all `approved` records with `expires_at < now` and bulk-marks them `expired`. Catches any records missed between lazy-expiry reads.
- **Start of every day (00:00 UTC)** - sweeps each tenant and deletes terminal approvals, stale jobs, fan-out runs, tenant audit entries, and agent history past **that tenant's** retention settings (defaults 7/7/30/90/30 days; tenant admin can change them, platform admin can override). The cross-tenant platform audit trail uses the deployment-wide `AUDIT_RETENTION_DAYS`. Prevents unbounded table growth.

The lazy expiry and the hourly sweep are idempotent and safe to run concurrently - both use conditional writes (`status = 'approved'` guard) so double-processing a record is harmless.

---

## Agent privilege and access level

The agent detects whether it is running as root (`os.Getuid() == 0`) and includes `running_as_root: true/false` in each sync request. The backend stores this on the agent record.

`access_level` is a computed label combining the agent's current mode and privilege. It is injected into agent responses at read time - not stored separately (`compute_access_level(mode, running_as_root)` in `shared/policy.py`).

| access_level | Mode | running_as_root |
|---|---|---|
| `open` | wild | true |
| `elevated` | wild (non-root) or approved (root) | - |
| `managed` | approved (non-root) or readonly (root) | - |
| `restricted` | readonly | false |

This label is shown in `reach agents list` and `reach status`, and is included in the `GET /agents` and `GET /agents/{id}` API responses so MCP clients receive it directly. It is a factual descriptor of how the agent is configured, not a risk score.

**This table is host-oriented.** `compute_access_level` does not branch on agent type, so a `type=k8s` agent is also labelled - but the k8s pod runs non-root with a read-only root filesystem, so `running_as_root` is always `false` and the label can only ever be `elevated` / `managed` / `restricted`, never `open`. Root is not what bounds a Kubernetes agent (the console shows it as `n/a`); **cluster RBAC** is. For k8s, treat `access_level` as a reflection of policy mode only, and read the acknowledged RBAC (the chart's `clusterAccess`, self-reported and drift-tracked) as the real privilege bound.

---

## Multi-tenancy

Every resource (agent, user, job, approval) belongs to a tenant. The backend enforces tenant isolation at the storage layer - user API tokens can only see agents, jobs, and approvals within their own tenant. The platform admin API (authenticated with a session token from `ADMIN_PASSWORD`) operates across tenants for provisioning only.

---

## Agent access control

Within a tenant, non-admin users are scoped to a subset of agents/fleets, **read-only or read-write**. This is separate from tenant isolation and is enforced via `can_access_agent(user, agent)` (read) and `can_write_agent(user, agent)` (write) in `shared/access.py`.

**Admins are always tenant-wide** (unrestricted). Every other user has **no access by default** and is granted explicitly. A user record carries four scope lists, partitioned by capability:

| Field | Effect |
|---|---|
| `readwrite_agent_ids` | Agents this user can **read and write** (run write commands, subject to the agent's mode) |
| `readonly_agent_ids` | Agents this user can **read** (read commands + view) but not write |
| `readwrite_fleet_ids` | Fleets whose members are read-write for this user |
| `readonly_fleet_ids` | Fleets whose members are read-only for this user |

Semantics:

- **Read access** = the union of all four lists. **Write access** = the `readwrite_*` lists only.
- **No wildcard.** Only admins are tenant-wide (via `None` lists). Granting a non-admin "all agents" means every agent id listed explicitly, so newly created agents are not auto-included (grant them at creation via `grant_user_ids`, or re-grant). A `*` entry is rejected.
- All four `None` → unrestricted (admins, or an explicitly tenant-wide account). For a non-admin the default is **empty lists = no access**.
- The read-write and read-only lists are a **partition** (an id can't be in both); if it ever is, the read-write grant wins.
- **Fleet members can't be granted by individual `agent_id`** - their ids are ephemeral (they churn as an autoscaler scales), so a specific fleet-member id in the `*_agent_ids` lists is rejected. Grant access to them via their **fleet** (`readwrite_fleet_ids` / `readonly_fleet_ids`) instead.

Both helpers are role-aware only through the data: admins are created with `None` lists (unrestricted); non-admins with empty lists (no access) grow their grants via `PUT /tenant/users/{id}/agents`, which sets all four lists. The read-only cap is enforced at **job submission** and **approval creation** - a read-only user's write is rejected `403` in *any* mode (it never bypasses the agent's own policy mode, it just stops this user attempting the write).

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
  cli/                          (CLI wheel)
    v0.1.0/reach-0.1.0-py3-none-any.whl
    latest/reach-0.1.0-py3-none-any.whl
  agent/                        (host install artifacts)
    v0.1.0/reach-agent-{linux,darwin}-{amd64,arm64}
    v0.1.0/install.sh           (install + uninstall via --uninstall)
    latest/  (same files)
    versions.json               (published host versions - the create dropdown reads this)
  charts/reach-agent/           (Helm repo for the Kubernetes agent)
    index.yaml                  (published chart versions - the create dropdown reads this)
    reach-agent-0.1.0.tgz       (one tarball per published chart version)
  lambda/
    code/                       (SAM-packaged function zips, content-addressed)
    v0.1.0/template.yaml
    latest/template.yaml
  local-setup.sh
  lambda-setup.sh
```

Docker images:
- `nabeemdev/reach:{version}` / `:latest` - the **backend** (FastAPI in Docker, or the Lambda image).
- `nabeemdev/reach-agent:{version}` / `:latest` - the **Kubernetes agent**, pulled by the Helm chart; the chart's `appVersion` selects the tag (see [POLICIES.md](POLICIES.md) / the chart README for the versioning model).

Each component is versioned independently. Release scripts:
- `scripts/release_cli.sh` - the CLI wheel → `cli/`.
- `scripts/release_agent.sh` - host binaries → `agent/` (and updates `agent/versions.json`), plus the multi-arch k8s image → the registry.
- `scripts/release_agent_chart.sh` - the Helm chart → `charts/reach-agent/` (refuses to overwrite an existing version unless `--force`).
- `scripts/release_backend.sh` - the backend Docker image and the Lambda template → `lambda/`.
