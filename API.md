# Reach API Reference

All API calls use HTTPS. The base URL is whatever you set as `API_URL` during deployment.

---

## Authentication

Four types of credentials:

| Credential | How to get it | Used for |
|---|---|---|
| Admin session token | `POST /admin/login` with `ADMIN_PASSWORD` | Platform admin **console** (`/admin/*`) - tenant provisioning, cross-tenant users |
| Tenant session token | `POST /tenant/login` with username + password | Tenant **console** (`/tenant/*`) - the web UI |
| API token (`tok_...`) | Tenant console → **API Tokens → New token** | **CLI and MCP server** (`/me`, `/jobs`, `/agents`, `/approvals/*`) |
| Agent token (`agent_...`) | Issued at claim time, managed automatically by the agent | Agent sync, job results |

Both session tokens expire after 8 hours; the console reissues them on login. API tokens do not expire automatically - revoke them explicitly when no longer needed.

> **Rate limits** are enforced on every endpoint in the **Docker / FastAPI** deployment and shown in the `Rate limit` column of each table below. Lambda relies on API Gateway's built-in throttling instead. The rate key is the Bearer token when present (API token, session token, or agent token), falling back to client IP. See [Rate limits](#rate-limits) for the full model.

---

## Platform admin endpoints

Require an admin session token obtained via `POST /admin/login`.

**Scope**: tenant provisioning and cross-tenant user management. The platform admin cannot manage agents, policies, or approvals - those belong to a tenant's operators and admins.

### Authentication

```
POST /admin/login
Body: {"password": "<ADMIN_PASSWORD>"}
Response: {"token": "<session_token>", "expires_at": "..."}
```

Rate limit: **10/min per IP**.

All limits below are **per session token**.

### Tenants

| Method | Path | Rate limit | Description |
|---|---|---|---|
| `POST` | `/admin/tenants` | 20/min | Create a tenant. Body: `{"name": "..."}` |
| `GET` | `/admin/tenants` | 120/min | List all tenants |
| `POST` | `/admin/tenants/{id}/enable` | 20/min | Enable a disabled tenant |
| `POST` | `/admin/tenants/{id}/disable` | 20/min | Disable a tenant |
| `DELETE` | `/admin/tenants/{id}` | 10/min | Permanently delete a tenant and its data. Irreversible - prefer disable unless you intend to remove it entirely. |

### Users (cross-tenant provisioning)

| Method | Path | Rate limit | Description |
|---|---|---|---|
| `POST` | `/admin/tenants/{id}/admin-users` | 20/min | Create the first (or any) user in a tenant. Body: `{"username": "...", "role": "admin\|operator\|developer", "name": "..."}`. `name` and `role` are optional - `name` defaults to `username`, `role` defaults to `developer`. Returns a `temp_password` the user must change on first login. |
| `GET` | `/admin/tenants/{id}/users` | 120/min | List users in a tenant |
| `POST` | `/admin/tenants/{id}/users/{user_id}/reset-password` | 10/min | Issue a temporary password for a user |
| `POST` | `/admin/tenants/{id}/users/{user_id}/disable` | 20/min | Disable a user. (Re-enabling is done from the tenant console, or `POST /tenant/users/{user_id}/enable`.) |
| `PATCH` | `/admin/tenants/{id}/users/{user_id}/role` | 20/min | Change user role. Body: `{"role": "admin\|operator\|developer"}` |
| `PATCH` | `/admin/tenants/{id}/users/{user_id}/name` | 20/min | Update user name. Body: `{"name": "..."}` |

### Agents (read-only overview)

| Method | Path | Rate limit | Description |
|---|---|---|---|
| `GET` | `/admin/agents` | 120/min | List agents for a tenant (`?tenant_id=` required, `?tag=key:value` optional). Read-only - agent management is done by tenant admins. |

### Audit logs (platform-wide)

| Method | Path | Rate limit | Description |
|---|---|---|---|
| `GET` | `/admin/audit-logs` | 60/min | Platform-wide audit log across **all** tenants (there is no tenant filter). Filters: `?action=` (exact, e.g. `agent.created`), `?actor=` (actor **name**, case-insensitive substring), `?resource=` (resource id, case-insensitive substring), `?ip=` (case-insensitive substring), `?since=`/`?until=` (ISO timestamps), `?limit=`, `?cursor=`. Default limit 100, max 200. |

---

These endpoints back the **web console**. They are authenticated with the **console session
token** returned by `POST /tenant/login` (an 8-hour token, distinct from an API token). The
`Auth` column shows the **minimum role** required.

The **CLI and MCP server do not use these** - they authenticate with an **API token** (`tok_…`,
created in the console under **API Tokens**) and call the smaller [User (CLI) endpoints](#user-cli-endpoints)
below (`/me`, `/jobs`, `/agents`, `/approvals/pending`).

Role determines access level within the tenant:

| Role | Can do |
|---|---|
| `developer` | Submit jobs, view agents, request/view approvals, manage own API tokens |
| `operator` | All developer ops + manage agents (create, revoke, policy mode, tags) + review and manage approvals |
| `admin` | All operator ops + manage users and view audit logs |

**Agent-level access.** On top of role, `developer` and `operator` users can be scoped to a
subset of agents via `allowed_agent_ids` (see `PUT /tenant/users/{id}/agents`). The scope is
enforced uniformly: an out-of-scope agent is invisible on `GET /agents`, and every operation on
it returns `404` - reading the agent, jobs, approvals (list/create/review/delete), job history,
**and agent management** (revoke, delete, reissue install token, set tags/mode, request rotation,
acknowledge capability). **Admins are always tenant-wide and cannot be scoped** - user
management is admin-only, so the account that grants access always holds every agent. An
operator who creates an agent is automatically granted access to it (so a scoped operator can
manage what they just created).

Rate limits below are **per session token**.

### Session

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `POST` | `/tenant/login` | username + password | 10/min | Log in and receive a **console session token**. Body: `{"tenant_name": "...", "username": "...", "password": "..."}` (`tenant_id` may be used instead of `tenant_name`). Must change temp password on first login. |
| `POST` | `/tenant/me/password` | any role | 10/min | Change own password. Body: `{"current_password": "...", "new_password": "..."}` |
| `GET` | `/tenant/me` | any role | 120/min | Get own user info (user_id, tenant_id, role) |

### Users

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `GET` | `/tenant/users` | admin | 60/min | List users in the tenant |
| `POST` | `/tenant/users` | admin | 20/min | Create a user. Body: `{"name": "...", "username": "...", "role": "...", "allowed_agent_ids": null}`. `allowed_agent_ids` is optional (`null` = all agents); a non-null list is rejected for `role: admin`. |
| `POST` | `/tenant/users/{user_id}/disable` | admin | 20/min | Disable a user (→ `REVOKED`). Cuts their existing sessions and API tokens immediately. |
| `POST` | `/tenant/users/{user_id}/enable` | admin | 20/min | Enable a user |
| `DELETE` | `/tenant/users/{user_id}` | admin | 20/min | Permanently delete a user (and their API tokens). Two-step: the user must be **disabled** first (`409` otherwise); cannot delete yourself. Writes a `user.deleted` audit event. |
| `POST` | `/tenant/users/{user_id}/revoke-tokens` | admin | 20/min | Revoke all of a user's API tokens (account stays active) |
| `PUT` | `/tenant/users/{user_id}/role` | admin | 20/min | Change user role. Body: `{"role": "admin\|operator\|developer"}`. Promoting to `admin` clears any agent scope (admins are tenant-wide). |
| `POST` | `/tenant/users/{user_id}/reset-password` | admin | 10/min | Issue a temporary password |
| `GET` | `/tenant/users/{user_id}/agents` | admin | 60/min | Get user's agent access list |
| `PUT` | `/tenant/users/{user_id}/agents` | admin | 30/min | Set user's agent access list. Body: `{"allowed_agent_ids": [...]}` - `["*"]` or `null` = unrestricted, `[]` = locked out, list = restricted to those agents. Rejected for `admin` targets (admins are always tenant-wide). |

### Agents

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `GET` | `/tenant/agent-versions` | operator+ | - | Installable versions for the create dropdown. Query: `?type=host\|k8s` (defaults to `host`). Returns `{"type", "default": "latest", "versions": [...]}` newest-first, discovered live (host: `agent/versions.json`; k8s: the chart repo `index.yaml`). Empty `versions` means only "Latest" is offered. |
| `POST` | `/tenant/agents` | operator+ | 20/min | Create an agent. Body: `{"type": "host\|k8s", "mode": "wild\|readonly\|approved", "version": "latest", "grant_service_mgmt": true, "grant_docker": false, "grant_user_ids": []}`. `type` defaults to `host`; `version` (optional) pins the install to a published version (host binary / Helm chart) - omit or `"latest"` installs the newest; a non-`latest` value must be a valid `X.Y.Z[-…]` version or the request is rejected (`400`). `grant_service_mgmt`/`grant_docker` are host-only and forced off for `k8s` (access is RBAC-driven). `grant_user_ids` (optional) grants the new agent to those **restricted** users' access lists; unrestricted users and users in other tenants are skipped. Returns the install command for the chosen type. |
| `POST` | `/tenant/agents/{id}/reissue-install-token` | operator+ | 10/min | Reissue install token - resets to CREATED. Body (optional): `{"force": true, "version": "latest", "grant_service_mgmt": false, "grant_docker": false}`. `version` pins the reissued install command (same rules as create). Blocked for ACTIVE agents without `force`. Blocked for DELETED. |
| `POST` | `/tenant/agents/{id}/revoke` | operator+ | 30/min | Revoke agent (CREATED/ACTIVE/INACTIVE → REVOKED). Cuts sync, removes from user access lists. |
| `DELETE` | `/tenant/agents/{id}` | operator+ | 30/min | Soft-delete (REVOKED → DELETED). Record stays in database. |
| `DELETE` | `/tenant/agents/{id}/remove` | operator+ | 20/min | Permanently remove agent record (DELETED only). Irreversible. |
| `PUT` | `/tenant/agents/{id}/tags` | operator+ | 30/min | Replace tag list. `{"tags": []}` clears all. |
| `PUT` | `/tenant/agents/{id}/policy/mode` | operator+ | 30/min | Set policy mode. Body: `{"mode": "wild\|readonly\|approved"}` |
| `POST` | `/tenant/agents/{id}/request-rotation` | operator+ | 10/min | Request out-of-band token rotation. Agent self-rotates on next sync. |
| `POST` | `/tenant/agents/{id}/acknowledge-capability` | operator+ | 30/min | Acknowledge a detected capability. Body: `{"capability": "docker\|service_mgmt\|k8s_permissions"}` |
| `GET` | `/tenant/agents/{id}/history` | any role | 60/min | Agent status change history |

### Approvals

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `GET` | `/tenant/approvals` | operator+ | 60/min | List/search approvals in the tenant. Server-side filters: `?agent_id=` `?status=pending\|approved\|denied\|expired` `?type=host\|k8s` (host = command approvals, k8s = structured rules) `?q=` (case-insensitive `LIKE` over the command/rule text and requester). Pagination: `?limit=` (default 20, max 100) `?offset=`. Response: `{"approvals": [...], "total": N, "limit": L, "offset": O}` where `total` is the full match count for the current filters. An **agent-scoped operator** only sees approvals for the agents they can access; admins are tenant-wide. |
| `POST` | `/tenant/approvals` | developer+ | 30/min | Create an approval for an agent. **Developers** create a `pending` request; **operators/admins** create an `approved` record directly (and support bulk + `duration`). **Host agents** use a command: single `{"agent_id","command","duration":"8h"}`, bulk `{"agent_id","commands":[...]}`. **k8s agents** use a structured rule: single `{"agent_id","k8s_rule":{"verb","resource","namespace","name"}}`, bulk `{"agent_id","k8s_rules":[...]}`. Rule fields accept `*` (wildcard); `verb` is required and must be a write verb - a single verb like `scale`/`delete`, a compound "double verb" like `rollout restart` or `auth reconcile`, or `*`. Bulk is idempotent → `{"created":[...],"skipped":[...]}`. |
| `PUT` | `/tenant/approvals/{id}/approve` | operator+ | 60/min | Approve (`pending`) or update duration (`approved`). Body: `{"duration": "permanent\|1h\|8h\|24h\|7d\|Nh\|Nd\|now"}`. Named presets are `1h`, `8h`, `24h`, `7d`; any other window is expressed as `Nh`/`Nd` (e.g. `30d`, `90d`). `duration=now` instantly expires an already-approved record. |
| `PUT` | `/tenant/approvals/{id}/deny` | operator+ | 60/min | Deny a pending approval. Terminal - cannot be reversed. |
| `DELETE` | `/tenant/approvals/{id}` | operator+ | 30/min | Permanently delete an approval record. Removing an approved record takes effect on the next agent sync. |

### API tokens

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `GET` | `/tenant/api-tokens` | any role | 60/min | List your own named API tokens |
| `POST` | `/tenant/api-tokens` | any role | 20/min | Create a named API token. Body: `{"name": "..."}`. Token value shown once in response. |
| `PATCH` | `/tenant/api-tokens/{token_id}` | any role | 30/min | Rename a token |
| `DELETE` | `/tenant/api-tokens/{token_id}` | any role | 20/min | Revoke a token (two-step: revoke an `ACTIVE` token, then delete the `REVOKED` record) |

### Audit logs

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `GET` | `/tenant/audit-logs` | admin | 60/min | Tenant-scoped audit log, automatically limited to the caller's tenant. Same filter params as `/admin/audit-logs`: `?action=` (exact), `?actor=`/`?resource=`/`?ip=` (case-insensitive substring), `?since=` `?until=` `?limit=` `?cursor=`. Default limit 100, max 200. |

---

## User (CLI) endpoints

Authenticated with an API token. These are the endpoints the CLI and MCP server use. All limits below are **per API token**.

| Method | Path | Rate limit | Description |
|---|---|---|---|
| `GET` | `/me` | 120/min | Current user identity (`user_id`, `tenant_id`, `name`, `role`) |
| `POST` | `/jobs` | 30/min | Create a job (submit a command to an agent). Body: `{"agent_id": "...", "command": "..."}` (command max 4096 chars; blocked commands are rejected `403`). |
| `GET` | `/jobs` | 120/min | List your own jobs. Filters: `?agent_id=` `?limit=` `?cursor=` |
| `GET` | `/jobs/{id}` | 120/min | Get job result, stdout, stderr, and exit code |
| `GET` | `/agents` | 60/min | List accessible agents. Filter: `?tag=key:value` |
| `GET` | `/agents/{id}` | 120/min | Get agent details, policy, and tags |
| `GET` | `/agents/{id}/approved-commands` | 60/min | Approval records. `?status=approved` (default) returns agent-wide effective list. Other statuses return only your own records. |
| `GET` | `/approvals/pending` | 60/min | Your own approval activity. `?status=pending` (default) returns the pending requests **you submitted** (across all agents you can access); `?status=approved` returns the agent-wide **approved** commands - for a specific `?agent_id=`, or across all agents you can access when no agent is given. Other filters/pagination like `/tenant/approvals`: `?agent_id=` `?type=host\|k8s` `?q=` `?limit=` `?offset=`; response `{"approvals":[...], "total": N, "limit": L, "offset": O}`. |

---

## Agent endpoints

Called by the agent process automatically - not intended for manual use.

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| `POST` | `/agent/claim` | install token | 5/hour per IP | One-time agent registration |
| `POST` | `/agent/sync` | agent token | 60/min per agent token | Poll for jobs, record heartbeat, receive policy updates |
| `POST` | `/agent/jobs/{id}/result` | agent token | 60/min per agent token | Post command result |
| `POST` | `/agent/rotate-token` | agent token | 10/hour per agent token | Self-service token rotation (called automatically every 30 days) |

These are **credential-only**: the agent never sends an `agent_id`. `claim` carries the **install token** (the backend resolves the agent by `install_token_hash`), a `machine_fingerprint`, and `type` (`host` or `k8s`), and returns the long-lived **agent token**. Every later call carries that agent token as the Bearer credential, and the backend resolves the agent by `agent_token_hash` - so identity is never taken from a client-supplied field. In `k8s` mode `sync` also reports the agent's effective RBAC (for acknowledge/drift in the console).

---

## Rate limits

Every endpoint is rate limited in the **Docker / FastAPI** deployment (via `slowapi`). The exact per-endpoint limit is listed in the `Rate limit` column of each table above. Lambda relies on API Gateway's built-in throttling and does not apply these per-route limits.

**Storage** - counters default to in-memory (per-process), which only enforces correctly on a single instance. To rate limit across multiple backend replicas, set `RATE_LIMIT_STORAGE_URI` to a shared store (e.g. `redis://host:6379`) on every replica - see [SELF_HOSTING.md → Running multiple replicas](SELF_HOSTING.md#running-multiple-replicas).

**Rate key** - limits are counted against the Bearer token when one is present: the tenant session token for `/tenant/*`; the API token for the CLI endpoints (`/me`, `/jobs`, `/agents`, `/approvals/*`); the platform-admin session token for `/admin/*`; the agent token for `/agent/sync`, `/agent/jobs/{id}/result`, and `/agent/rotate-token`. Where no usable token exists (`POST /admin/login`, `POST /tenant/login`, `POST /agent/claim`, `GET /health`), the key falls back to client IP.

**Limit tiers at a glance:**

| Tier | Typical limit | Examples |
|---|---|---|
| Hot reads | 120/min | `GET /me`, `GET /jobs/{id}`, `GET /agents/{id}`, `GET /jobs`, `GET /admin/tenants`, `GET /admin/agents` |
| Standard reads | 60/min | `GET /agents`, `GET /tenant/users`, `GET /tenant/approvals`, `GET /tenant/audit-logs`, `GET /admin/audit-logs` |
| Standard writes | 20–30/min | `POST /jobs` (30), agent/tag/policy/approval mutations (30), user & token mutations (20) |
| Sensitive / destructive | 10/min | `POST /admin/login`, `POST /tenant/login`, password changes, reissue/request-rotation, user disable |
| Agent sync | 60/min per agent token | `POST /agent/sync`, `POST /agent/jobs/{id}/result` |
| One-time / rare | 5–10/hour | `POST /agent/claim` (5/hr per IP), `POST /agent/rotate-token` (10/hr) |

**Meta endpoints:**

| Endpoint | Rate limit | Notes |
|---|---|---|
| `GET /health` | 120/min per IP | Liveness/readiness probe |
| `GET /` | 120/min per IP | 301 redirect to `/ui/` |

Exceeding a limit returns `429 {"error": "rate limit exceeded"}`. The agent's sync loop treats 429 as a transient error and retries on the next poll interval. Clients should back off and retry; the limits are sized so normal CLI, console, and agent usage never hits them.

---

## Pagination

Paginated endpoints return `next_cursor` when more results exist:

```json
{
  "items": [...],
  "next_cursor": "MjAyNi0wNi0xN1QxMDowNTowMCswMDowMA=="
}
```

Pass `?cursor=<next_cursor>` on the next request. The cursor is absent on the last page. Default page size is 20, max 100.

**Audit-log endpoints differ:** they return the array under `logs` (not `items`), default to 100 per page (max 200), and `next_cursor` is the `created_at` timestamp of the last row:

```json
{
  "logs": [...],
  "next_cursor": "2026-06-17T10:05:00+00:00"
}
```

---

## Audit log actions

Every mutating action writes one audit record. Audit writes never block the primary operation - if the write fails, the action still succeeds.

**Record shape** (one entry from the `logs` array):

```json
{
  "log_id": "log_a1b2c3...",
  "tenant_id": "tenant_...",        // null for platform-level events
  "actor_id": "user_...",            // "platform_admin" for ADMIN_PASSWORD actions
  "actor_name": "Alice",
  "actor_role": "admin",             // or "PLATFORM_ADMIN"
  "action": "agent.created",
  "resource_type": "agent",
  "resource_id": "agent_...",
  "event_metadata": { },             // action-specific details
  "ip_address": "203.0.113.7",       // null when unavailable
  "created_at": "2026-06-17T10:05:00+00:00"
}
```

| Action | Triggered by |
|---|---|
| `admin.login` | Platform admin login succeeded |
| `admin.login_failed` | Platform admin login rejected (wrong password) |
| `tenant.created` | Platform admin creates a tenant |
| `tenant.enabled` / `tenant.disabled` | Platform admin enables/disables a tenant |
| `tenant.deleted` | Tenant deleted |
| `user.created` | User added to a tenant |
| `user.disabled` / `user.enabled` | User account disabled/enabled |
| `user.deleted` | User permanently deleted (after being disabled) |
| `user.role_changed` | User role updated |
| `user.name_changed` | User display name updated |
| `user.password_reset` | Temporary password issued by an admin |
| `user.password_changed` | User changed their own password |
| `user.agents_changed` | User's per-user agent access list updated |
| `user.login` | Tenant user login succeeded |
| `user.login_failed` | Tenant user login rejected (bad password, unknown tenant/user, or disabled account - see `metadata.reason`) |
| `agent.created` | New agent registered |
| `agent.revoked` | Agent access cut |
| `agent.deleted` | Agent soft-deleted |
| `agent.removed` | Agent record permanently deleted |
| `agent.install_token_reissued` | Fresh install token issued |
| `agent.rotation_requested` | Out-of-band token rotation requested |
| `agent.mode_changed` | Policy mode changed |
| `agent.tags_changed` | Tag list updated |
| `agent.capability_detected` | Agent reported a new capability (Docker, service management) |
| `agent.capability_acknowledged` | Detected capability acknowledged |
| `agent.unreachable` | Agent missed heartbeats and was marked `INACTIVE` |
| `agent.recovered` | Agent resumed syncing after being `INACTIVE` |
| `approval.requested` | Write command blocked in `approved` mode; pending record created |
| `approval.pre_approved` | Command pre-approved by an operator or admin |
| `approval.approved` | Pending approval approved by an operator or admin |
| `approval.denied` | Pending approval denied |
| `approval.expired` | Approved command instantly expired via `duration=now` |
| `approval.deleted` | Approval record deleted |
| `api_token.created` | Named API token created |
| `api_token.renamed` | Named API token renamed |
| `api_token.revoked` | Named API token revoked |

Platform admin logins are audited as `admin.login` (success) and `admin.login_failed` (wrong password), each capturing the source IP; the `ADMIN_PASSWORD`-not-configured (500) case is not logged. Approval reviews are written as `approval.approved`, `approval.denied`, or `approval.expired` (the latter when an approved record is instantly expired with `duration=now`); the approval record additionally carries `reviewed_by`, `reviewed_at`, and `status`. A natural time-based expiry (the scheduler sweeping a record past its `expires_at`) updates the record's status but does not write a separate audit action.
