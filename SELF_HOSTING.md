# Self-Hosting reach

Deploy and operate your own reach backend. The CLI and agent are separate - they just need your API URL.

---

## Deployment options

| Option | Runtime | Database | Best for |
|---|---|---|---|
| [Local machine](#option-1-local-machine) | FastAPI | PostgreSQL | Home server, spare machine, no cloud account needed |
| [AWS Lambda](#option-2-aws-lambda) | Lambda | DynamoDB | Small teams, low cost, AWS-native |
| [Docker / FastAPI](#option-3-docker--fastapi) | FastAPI | PostgreSQL | Any cloud, self-hosted VMs, k8s |

### Why DynamoDB with Lambda, and PostgreSQL with Docker?

Lambda functions are stateless and short-lived - each invocation starts fresh with no persistent connections. DynamoDB is a natural fit because it's serverless, has no connection to maintain, and scales to zero when idle. The combination keeps costs near zero for small teams (pay only per request, no always-on database instance).

PostgreSQL with a persistent server (FastAPI in Docker or k8s) is the right choice when you want to run anywhere - any cloud, a VPS, or on-prem - without being tied to AWS. FastAPI keeps a connection pool open for the lifetime of the process, which PostgreSQL handles well. DynamoDB would require AWS credentials and doesn't make sense outside of AWS.

Lambda + PostgreSQL is deliberately not supported. Lambda's ephemeral connections exhaust PostgreSQL's connection limit quickly at scale, and solving that requires RDS Proxy - adding cost and complexity that defeats the purpose of the low-cost Lambda option.

---

## Option 1: Local machine

Run the full backend on any machine you already have - no cloud account, no VMs to provision. A good fit for a home server, a spare machine, or a VPS where you want full control without the Lambda setup.

### Prerequisites

- Docker + docker compose
- `openssl` and `curl`
- (Optional) [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) or [ngrok](https://ngrok.com/download) to expose the backend publicly so remote agents can reach it

### Deploy

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

The script will:
- Prompt for release tag (default: `latest`)
- Prompt for `TOKEN_PEPPER` and `ADMIN_TOKEN` (or generate them)
- Start PostgreSQL, the reach backend, and nginx via Docker Compose
- Optionally start a public tunnel:
  - **cloudflared** - no account needed, URL changes on restart
  - **ngrok** - free account required, supports static domains for a stable URL across restarts
- Print your API URL, tokens, and next steps

The API URL is what you pass to `reach login --api-url` and to the agent installer.

### Tear down

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --down
```

---

## Option 2: AWS Lambda

### Prerequisites

- AWS CLI installed and configured (`aws sts get-caller-identity`)

### Deploy

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash
```

The script will:
- Prompt for AWS profile (leave blank to use environment credentials) and region (default: `us-east-1`)
- Verify credentials before proceeding
- Prompt for stack name (default: `reach-platform`) and release tag (default: `latest`)
- Prompt for `TOKEN_PEPPER` and `ADMIN_TOKEN` (or generate them)
- Deploy the CloudFormation stack and wait for it to complete
- Print your API URL, tokens, and next steps

### Upgrade

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update
```

The script lists your existing stacks, prompts for the stack name (default: `reach-platform`) and release tag (default: `latest`), and optionally rotates `ADMIN_TOKEN` (leave blank to keep the existing value). `TOKEN_PEPPER` is always kept - it cannot be changed. See [TOKEN_PEPPER is permanent](#token_pepper-is-permanent).

### Tear down

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --down
```

DynamoDB tables use `DeletionPolicy: Retain` - your data is preserved even after the stack is deleted. Remove the tables manually in the AWS console if you want to wipe everything.

---

## Option 3: Docker / FastAPI

### Prerequisites

- Docker
- A PostgreSQL database (any cloud managed DB or self-hosted)

### Deploy

**1. Pull and run:**

```bash
docker run -d \
  -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e ADMIN_TOKEN="<your-admin-token>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:latest
```

On first startup, Alembic runs `alembic upgrade head` automatically and creates all tables. Subsequent restarts apply any pending migrations from new versions. The image supports `linux/amd64` and `linux/arm64` - works on AWS Graviton, Raspberry Pi, and Apple Silicon without extra flags.

**2. Put a reverse proxy in front (nginx, Caddy, ALB, etc.) for TLS.**

---

## First-time setup

Provisioning is three steps: create a tenant, create a user under it (for the CLI), and create an agent under it (for each machine).

**1. Create a tenant:**

```bash
curl -s -X POST "$API_URL/admin/tenants" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "XYZ Corp"}' | python3 -m json.tool
```

Response:

```json
{
  "tenant_id": "tenant_xxxxx",
  "name": "XYZ Corp"
}
```

**2. Create a user under that tenant:**

```bash
curl -s -X POST "$API_URL/admin/tenants/tenant_xxxxx/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "alice"}' | python3 -m json.tool
```

Response:

```json
{
  "user_id": "user_xxxxx",
  "tenant_id": "tenant_xxxxx",
  "name": "alice",
  "token": "tok_xxx...",
  "commands": {
    "cli_login": "reach login --api-url \"...\" --token \"tok_xxx...\""
  }
}
```

The `name` field is **required** - the request returns 400 if omitted or blank. Use a human-readable identifier (e.g. the person's name or username) so it's clear who the token belongs to when listing users.

Save the `token` - it's not retrievable again. Run the `cli_login` command to set up the CLI. Repeat this step for each person who needs CLI access to this tenant - each gets their own token, so revoking one person's access never affects anyone else.

**3. Create an agent under the tenant:**

```bash
curl -s -X POST "$API_URL/admin/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "tenant_xxxxx"}' | python3 -m json.tool
```

Optional body fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | string | `"wild"` | Policy mode: `wild`, `readonly`, or `approved` |
| `grant_service_mgmt` | bool | `true` | When `false`, adds `--no-grant-service-mgmt` to the install command (skips the sudoers entry for `systemctl`/`launchctl`) |
| `grant_docker` | bool | `false` | When `true`, adds `--grant-docker` to the install command (adds `reach-agent` to the `docker` group) |

Response:

```json
{
  "agent_id": "agent_xxxxx",
  "tenant_id": "tenant_xxxxx",
  "install_token": "install_xxx...",
  "install_token_expires_at": "2026-06-17T12:00:00+00:00",
  "mode": "wild",
  "commands": {
    "cli_use": "reach agents use agent_xxxxx",
    "agent": "curl -fsSL .../install.sh | sudo bash -s -- --api-url ... --agent-id ... --install-token ... --yes"
  }
}
```

Run the `agent` install command on the target machine. The script auto-detects OS and architecture - one command works everywhere:

- **Linux** - installs as a systemd service, starts on boot, runs as a dedicated `reach-agent` user.
- **macOS (foreground)** - runs in the current terminal. Stops when the terminal closes (Ctrl+C). Good for testing.
- **macOS (background)** - add `--background` to install as a LaunchDaemon under a dedicated `reach-agent` system user. Same security model as Linux: starts on boot, minimal privileges, hidden from the login screen.

The generated `agent` command always includes `--yes`, which suppresses all optional prompts and applies their defaults (service management granted, docker not granted). See the optional fields table above to control what flags are included.

To uninstall (both platforms): `curl -fsSL .../install.sh | sudo bash -s -- --uninstall`

Any user under the tenant can run `cli_use` on their own machine to set it as the default - all users in a tenant see the same agents.

**Add another agent to the same tenant** - repeat step 3 with the same `tenant_id`.

---

## Managing users

Every user gets their own token, independently revocable. Useful when multiple people share a tenant - rotating or revoking one person's access never breaks anyone else's.

**List users in a tenant** (never exposes raw tokens - those are only shown once at creation):

```bash
curl -s "$API_URL/admin/tenants/tenant_xxxxx/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**Rotate a user's token** (e.g. they suspect it leaked) - keeps the same `user_id`, `name`, and `created_at`, just replaces the credential:

```bash
curl -s -X POST "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/rotate-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

The old token stops working immediately; the new `token` is shown once in the response. No guardrail needed here - unlike reissuing an agent's install token, rotating a user's token doesn't disconnect anything mid-flight, it just means their next CLI call needs the new token.

**Revoke a user:**

```bash
curl -s -X DELETE "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

That user's token stops working immediately. Everyone else's tokens are unaffected.

---

## Rotating the admin token

The `ADMIN_TOKEN` is just an environment variable - rotating it means setting a new value and restarting. The old token stops working immediately on the next request; there is no grace period. Update any scripts that use the admin API before rotating.

**Local machine (Option 1):**

```bash
NEW_ADMIN_TOKEN=$(openssl rand -hex 32)

# Update the env file
sed -i '' "s/^ADMIN_TOKEN=.*/ADMIN_TOKEN=${NEW_ADMIN_TOKEN}/" ~/.reach/local/env

# Restart the backend to pick it up
docker compose -f ~/.reach/local/docker-compose.yml --env-file ~/.reach/local/env up -d reach

echo "New ADMIN_TOKEN: $NEW_ADMIN_TOKEN"
```

**AWS Lambda (Option 2):**

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update
```

When prompted for a new `ADMIN_TOKEN`, enter the new value (or generate one with `openssl rand -hex 32`). Leave it blank to keep the existing token and only update the release tag.

CloudFormation updates each Lambda function's environment in-place. No cold start delay - the new token is active as soon as the update completes (usually under a minute).

**Docker / FastAPI (Option 3):**

Update the `ADMIN_TOKEN` environment variable however your deployment manages it (`.env` file, secrets manager, k8s secret), then restart the container:

```bash
docker stop reach && docker run -d \
  -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e ADMIN_TOKEN="$NEW_ADMIN_TOKEN" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:latest
```

`TOKEN_PEPPER` must stay the same across rotations - changing it invalidates every agent token, user token, and install token in the database simultaneously. See [TOKEN_PEPPER is permanent](#token_pepper-is-permanent).

---

## TOKEN_PEPPER is permanent

`TOKEN_PEPPER` is set once at deployment and must never change. It is the HMAC key used to hash every credential before storage - agent tokens, user tokens, and install tokens are all stored as `HMAC-SHA256(TOKEN_PEPPER, raw_token)`. The raw tokens are never stored anywhere.

If `TOKEN_PEPPER` changes, every hash in the database becomes invalid with no recovery path:

- Every agent loses its `agent_token` - all agents stop syncing immediately
- Every user's `tok_` token stops working - no one can use the CLI
- Any pending install tokens are invalidated

The only recovery is a full credential reset: reissue install tokens for every agent and reinstall on every machine, then rotate every user token and redistribute credentials to everyone.

**Treat `TOKEN_PEPPER` like a database encryption key: back it up securely and never rotate it.**

---

## Per-user agent access

By default every user in a tenant has `allowed_agent_ids: ["*"]` - they can see and use all agents. You can restrict a user to a specific subset of agents without touching anyone else's access.

**Check a user's current access:**

```bash
curl -s "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

```json
{"user_id": "user_xxxxx", "allowed_agent_ids": ["*"]}
```

`["*"]` means unrestricted. A list of agent IDs means restricted to exactly those agents.

**Restrict a user to specific agents** - replaces their access list entirely:

```bash
curl -s -X PUT "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_ids": ["agent_staging1", "agent_staging2"]}' | python3 -m json.tool
```

The user now sees only those agents. Every other agent returns 404 to them - they can't tell other agents exist.

**Restore full access:**

```bash
curl -s -X PUT "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_ids": ["*"]}' | python3 -m json.tool
```

**Lock a user out entirely** (no agent access, but keep the account):

```bash
curl -s -X PUT "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_ids": []}' | python3 -m json.tool
```

**Grant access to one more agent** (user must already be restricted, not `["*"]`):

```bash
curl -s -X POST "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents/agent_prod1" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Returns `409` if the user is still unrestricted - use `PUT` to set an explicit list first, then `POST`/`DELETE` to fine-tune.

**Revoke one agent:**

```bash
curl -s -X DELETE "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents/agent_prod1" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Returns `409` if the user is unrestricted, `404` if the agent isn't in their list.

**When an agent is revoked**, reach automatically removes it from every user's `allowed_agent_ids` in that tenant. No manual cleanup needed.

**Fleet access (coming soon):** a parallel `allowed_fleet_ids` field will let you grant access to all agents in a fleet with a single assignment. Individual `allowed_agent_ids` and fleet access are OR'd - either grants access.

---

## Managing agents

**List all agents for a tenant** - useful for seeing status, hostnames, and modes without needing a user token:

```bash
curl -s "$API_URL/admin/agents?tenant_id=tenant_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Response:

```json
{
  "agents": [
    {
      "agent_id": "agent_xxxxx",
      "status": "ACTIVE",
      "hostname": "prod-server-1",
      "agent_version": "0.1.0",
      "claimed_at": "2026-06-17T10:00:00+00:00",
      "token_issued_at": "2026-06-17T10:00:00+00:00",
      "mode": "readonly",
      "tags": ["env:prod", "region:us-east-1"]
    }
  ]
}
```

Returns 400 if `tenant_id` is missing, 404 if the tenant doesn't exist.

`token_issued_at` shows when the current agent token was last issued (claim or rotation). Useful for auditing which agents are approaching their 30-day rotation window.

Add `?tag=<key:value>` to filter by a specific tag:

```bash
curl -s "$API_URL/admin/agents?tenant_id=tenant_xxxxx&tag=env:prod" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

---

## Agent tags

Tags are `key:value` labels on agents for display and grouping. They are separate from access control - any user who can see an agent can see its tags. Tag keys and values must use lowercase letters, digits, hyphens, or underscores (format: `key:value`).

**Get tags for an agent:**

```bash
curl -s "$API_URL/admin/agents/agent_xxxxx/tags" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**Replace all tags (set exact list):**

```bash
curl -s -X PUT "$API_URL/admin/agents/agent_xxxxx/tags" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["env:prod", "region:us-east-1", "team:infra"]}'
```

**Add tags (merge, no duplicates):**

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/tags" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["owner:alice"]}'
```

**Remove specific tags:**

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx/tags" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["owner:alice"]}'
```

**Clear all tags:**

```bash
curl -s -X PUT "$API_URL/admin/agents/agent_xxxxx/tags" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tags": []}'
```

### Filtering agents by tag

Users can filter the agent list to only agents carrying a specific tag:

```bash
# CLI
reach agents list --tag env:prod

# API
curl -s "$API_URL/agents?tag=env:prod" \
  -H "Authorization: Bearer $USER_TOKEN" | python3 -m json.tool
```

Access control still applies - users only see agents they are allowed to access. The tag filter is an additional narrowing on top of that. If no agents match, the response is an empty `agents` list (not an error).

---

## Viewing admin job history

`GET /admin/jobs` lets you query job history across all tenants with flexible filters. At least one filter is required.

**All jobs for a tenant:**

```bash
curl -s "$API_URL/admin/jobs?tenant_id=tenant_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**All jobs run on a specific agent:**

```bash
curl -s "$API_URL/admin/jobs?agent_id=agent_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**All jobs submitted by a specific user:**

```bash
curl -s "$API_URL/admin/jobs?created_by=user_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Filters can be combined. Use `?limit=N` to cap results (default 20, max 100).

When there are more results, the response includes `next_cursor`. Pass it as `?cursor=<value>` on the next request to get the next page:

Response includes `created_by` on every job record - the `user_id` of the CLI user who submitted it. When there are more pages, `next_cursor` is included:

```json
{
  "jobs": [
    {
      "job_id": "job_xxxxx",
      "agent_id": "agent_xxxxx",
      "tenant_id": "tenant_xxxxx",
      "created_by": "user_xxxxx",
      "command": "docker ps",
      "status": "SUCCEEDED",
      "exit_code": 0,
      "created_at": "2026-06-17T10:05:00+00:00"
    }
  ],
  "next_cursor": "MjAyNi0wNi0xN1QxMDowNTowMCswMDowMA=="
}
```

Pass `?cursor=<next_cursor>` on the next request to fetch the next page. The cursor encodes the `created_at` of the last returned item - absent when you've reached the last page.


---

## Reissuing an install token

If an install token expires before the agent was set up, or the machine needs to be reimaged and re-registered, reissue a fresh install token for the same `agent_id` instead of bootstrapping a new one:

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/reissue-install-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

This resets the agent back to `CREATED` status with a new install token - the same `agent_id` is kept, so any aliases or job history pointing at it stay intact.

**This is a hard cutover, not a live rotation.** If the agent is currently running and connected, its existing `agent_token` is invalidated immediately (the machine fingerprint and claim are cleared). The agent will stop syncing on its next poll and go dormant rather than retry forever - it needs to be re-installed with the new install token to come back online. There's no in-band way to recover it remotely once this happens.

It's allowed without confirmation for `CREATED` (never claimed), `INACTIVE` (already lost contact), and `REVOKED` agents - there's no live connection to break in those states. `REVOKED` is the recommended path before reissuing on a machine you plan to re-register.

For `ACTIVE` agents the server blocks it with `409` to prevent accidental disconnects:

```json
{"error": "agent is currently ACTIVE - reissuing will disconnect it immediately with no in-band recovery. Revoke first with POST /admin/agents/{id}/revoke, or pass {\"force\": true} to proceed anyway."}
```

To force it through on an `ACTIVE` agent (e.g. a suspected compromised token), pass `force`:

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/reissue-install-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"force": true}' | python3 -m json.tool
```

Returns `409` for `DELETED` agents - remove and create a new agent instead.

---

## Decommissioning an agent

Removing an agent is a three-step sequence. Each step requires the previous one to have been completed - this prevents accidental hard-deletes.

### Step 1 - Revoke

Cuts sync access immediately. The agent's next poll returns `403` and it goes dormant. The agent is also removed from every user's `allowed_agent_ids` in that tenant at this point.

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Returns `409` if the agent is already `REVOKED` or `DELETED`. Works on `CREATED`, `ACTIVE`, and `INACTIVE` agents without any `force` flag.

A revoke can be undone - see [Reissuing an install token](#reissuing-an-install-token) below.

### Step 2 - Soft-delete

Marks the agent `DELETED`. The record stays in the database for audit purposes. Requires `REVOKED` status.

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Returns `409` if the agent is not `REVOKED` (with a message pointing to the revoke endpoint).

### Step 3 - Remove

Permanently deletes the record from the database. Job history referencing the `agent_id` is unaffected, but `GET /agents/{id}` will 404 afterward. Requires `DELETED` status.

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx/remove" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Returns `409` if the agent is not `DELETED`.

---

## Policy management

Policies are managed via the admin API, authenticated with your `ADMIN_TOKEN`.

**View policy** (returns current mode and list of effective approved commands):

```bash
curl -s "$API_URL/admin/agents/agent_xxxxx/policy" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**Set mode** (`wild` / `readonly` / `approved`):

```bash
curl -s -X PUT "$API_URL/admin/agents/agent_xxxxx/policy/mode" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "approved"}'
```

In `approved` mode, commands are not pre-configured by the admin. Instead they are approved on demand through the approval workflow - commands that get blocked by the agent create pending approval records, and the admin reviews them. See [Approvals](#approvals).

---

## Approvals

When an agent runs in `approved` mode and a write command is not yet approved, the agent blocks it and the backend creates a pending approval record. The admin reviews these records and approves or denies them.

Read commands always run in approved mode. Only write commands need approval.

**Pre-approve a command** (without waiting for a block to occur):

```bash
# Single command, permanent
curl -s -X POST "$API_URL/admin/approvals" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent_xxxxx", "command": "docker restart app"}'

# Single command, time-limited
curl -s -X POST "$API_URL/admin/approvals" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent_xxxxx", "command": "docker restart app", "duration": "8h"}'

# Bulk - provision multiple commands at once
curl -s -X POST "$API_URL/admin/approvals" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent_xxxxx",
    "commands": ["docker ps", "docker logs app", "kubectl get pods -A"],
    "duration": "permanent"
  }'
```

Single-command form: record created in `approved` state immediately; returns `409` if already active.

Bulk form (`commands: [...]`): idempotent - commands that already have an active approval are skipped, not errored. Response:

```json
{
  "created": [{ "approval_id": "appr_...", "command": "docker logs app", ... }],
  "skipped": [{ "command": "docker ps", "reason": "already_approved" }]
}
```

All pre-approved commands are included in the agent's approved list on the next sync (within 2–15 seconds). Useful for provisioning a new agent before first use.

**List pending approvals** (all agents, or filter by agent or tenant):

```bash
curl -s "$API_URL/admin/approvals?status=pending" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Filter by agent:
```bash
curl -s "$API_URL/admin/approvals?agent_id=agent_xxxxx&status=pending" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

Results are paginated (default 20, max 100). When more records exist, the response includes `next_cursor`:

```bash
# First page
curl -s "$API_URL/admin/approvals?tenant_id=tenant_xxxxx&status=pending&limit=20" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool

# Next page
curl -s "$API_URL/admin/approvals?tenant_id=tenant_xxxxx&status=pending&limit=20&cursor=<next_cursor>" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

**Approve a command** - permanently by default, or with a time limit:

```bash
# Approve permanently
curl -s -X PUT "$API_URL/admin/approvals/appr_xxxxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Approve for a limited time (1h / 8h / 24h / 7d / permanent, or custom Nh / Nd)
curl -s -X PUT "$API_URL/admin/approvals/appr_xxxxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"duration": "8h"}'
```

Supported durations:

| Value | Meaning |
|---|---|
| `permanent` (default) | Never expires |
| `1h` | 1 hour |
| `8h` | 8 hours |
| `24h` | 24 hours |
| `7d` | 7 days |
| `Nh` / `Nd` | Custom N hours or N days |

Time-limited approvals expire silently - the record stays in the database for history, but once the expiry passes the command is no longer effective. The next blocked attempt creates a new pending record.

**Deny or revoke an approval** - works on any status (`pending`, `approved`, or `denied`). Denying an already-approved record takes effect on the next agent sync:

```bash
curl -s -X PUT "$API_URL/admin/approvals/appr_xxxxx/deny" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Update the duration on an active approval** (re-approve with a new expiry):

```bash
# Shorten or extend an existing approval to 24 hours from now
curl -s -X PUT "$API_URL/admin/approvals/appr_xxxxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"duration": "24h"}'

# Make an expiring approval permanent
curl -s -X PUT "$API_URL/admin/approvals/appr_xxxxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Re-approve a denied request** (approve an already-denied record):

```bash
curl -s -X PUT "$API_URL/admin/approvals/appr_xxxxx/approve" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"duration": "8h"}'
```

The approve and deny endpoints work on records in any state - `pending`, `approved`, or `denied`. Revoke and duration changes take effect on the next agent sync (within 2–15 seconds depending on poll interval).

**Delete an approval record** (permanently removes it - use to clean up stale duplicates or erase records from history):

```bash
curl -s -X DELETE "$API_URL/admin/approvals/appr_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Returns `{"deleted": true}`. Works on records in any status. Deleting an approved record immediately removes the command from the agent's allowed list (same effect as denying, but without leaving a denied record behind).

**Users can check their own approval status** via the CLI:

```bash
reach approvals                    # effective approved commands for the default agent
reach approvals --pending          # my pending requests (awaiting review)
reach approvals --denied           # my denied requests
reach approvals --expired          # my expired approvals
reach approvals --agent prod       # any of the above for a specific agent
```

---

## How tokens work

Three token types - none stored raw, only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in the database:

| Token | Prefix | Used by | Purpose |
|---|---|---|---|
| `install_` | install token | Agent (once) | One-time claim to register the agent |
| `agent_` | agent token | Agent (ongoing) | Poll for jobs, post results, heartbeat |
| `tok_` | user token | CLI | Create jobs, read results, list agents |

User tokens belong to individuals, not the tenant as a whole - each person under a tenant gets their own, and revoking one doesn't affect anyone else's. By default all users in a tenant see all agents, but access can be restricted per user - see [Per-user agent access](#per-user-agent-access).

---

## Agent lifecycle

```
CREATED ──(claim)──► ACTIVE ──(heartbeat gap)──► INACTIVE
   ▲                    │                              │
   │               (revoke)                       (revoke)
   │                    │                              │
   │                    ▼                              │
   └──(reissue     REVOKED ◄─────────────────────────┘
      install           │
      token)       (delete)
                        │
                        ▼
                   DELETED ──(remove)──► [gone]
```

An agent starts as `CREATED`. On first run it calls `POST /agent/claim` with the install token, transitions to `ACTIVE`, and receives a permanent `agent_token`. The install token is then cleared from disk.

- **CREATED** - registered, install token valid for 24 hours. Never claimed.
- **ACTIVE** - claimed and syncing normally.
- **INACTIVE** - missed heartbeats. Auto-recovers to ACTIVE on next successful sync.
- **REVOKED** - access cut. Sync returns 403. Removed from all user access lists. Can be reset to CREATED via `POST /admin/agents/{id}/reissue-install-token`.
- **DELETED** - soft-deleted. Record stays in the database. Cannot sync or be reissued. Hidden from tenant-facing endpoints (`GET /agents`, `GET /agents/{id}` return 404); visible to the admin API so the remove step can be completed.
- **[gone]** - permanently removed via the remove action.

The heartbeat checker runs every minute (EventBridge on Lambda, APScheduler on FastAPI) and marks agents `INACTIVE` if no sync has been received in the last 45 seconds. The agent auto-reactivates on its next successful sync. The same check also sweeps PENDING jobs older than 1 hour and marks them `EXPIRED` - so if an agent goes offline after a job is submitted, the job status resolves within an hour rather than lingering indefinitely.

### Adaptive polling

The backend tells the agent how fast to poll via `next_poll_seconds`:
- `2s` - active window (job dispatched or created recently)
- `15s` - idle

### Agent token rotation

The agent automatically rotates its own `agent_token` every 30 days. On each poll iteration it checks whether the token is 30+ days old (tracked via `token_issued_at` in `config.json`). If it is, it calls `POST /agent/rotate-token` using the current still-valid token, receives a new one in the response, and atomically rewrites `config.json` before the old token is invalidated. There is no lockout window - rotation and persistence happen in one round trip while the old credential is still good.

Agents upgraded from a version without this field will skip rotation until they next re-claim (which sets `token_issued_at`).

If the config write fails after the server has issued the new token (e.g. disk full), the agent continues the current session in memory but logs a warning - a restart without fixing disk will cause a 401 and the agent will need manual reclaim.

---

## Agent sudo access

**Linux and macOS background mode** both run the agent as a dedicated `reach-agent` system user with no sudo access by default. Commands requiring elevated privileges will fail unless you explicitly grant them.

**macOS foreground mode** runs as the logged-in user. That user can do anything without a password prompt - but `sudo` commands that require a password will fail since the agent runs non-interactively with no TTY. Only `NOPASSWD` sudo entries work. Use **approved** or **readonly** policy mode to limit what the agent can do.

### What the reach-agent user can and cannot do

| | reach-agent system user (Linux / macOS background) |
|---|---|
| Read its own config (`/etc/reach-agent/`) | ✅ |
| Execute world-executable binaries | ✅ |
| Read world-readable files and directories | ✅ |
| Write to `/tmp` | ✅ |
| Make outbound network requests | ✅ |
| `systemctl status`, `is-active`, `list-units` | ✅ (read-only, no sudo needed) |
| `systemctl restart/start/stop` | ✅ if granted during install (prompted) / ❌ if skipped |
| Read another user's home directory | ❌ (protected by 700) |
| Run `docker` commands | ✅ if granted during install (prompted, default no) / ❌ if skipped |
| Write to system directories | ❌ |
| Run `sudo` | ❌ (no sudoers entry by default) |
| Log in interactively | ❌ (shell is `/usr/bin/false`) |

Commands that require access beyond this - service restarts, docker, package managers - need an explicit grant (sudoers or group membership). This is intentional: the agent can only do what you've decided it should be able to do.

**Service management** (`systemctl`/`launchctl` restart, start, stop) is prompted during interactive install with `[Y/n]` (default **yes**). The script writes `/etc/sudoers.d/reach-agent` automatically if granted.

**Interactive install - pre-answer the service management prompt** without losing the other prompts:

```bash
# Answer yes without being asked
sudo bash install.sh --grant-service-mgmt

# Answer no without being asked
sudo bash install.sh --no-grant-service-mgmt
```

**Non-interactive install** (piped through bash - no TTY, so no prompts fire). Pass `--yes` to apply the interactive defaults (service management on, docker off):

```bash
curl -fsSL .../install.sh | sudo bash -s -- \
  --api-url ... --agent-id ... --install-token ... \
  --yes
```

To skip service management in a non-interactive install, add `--no-grant-service-mgmt` alongside `--yes`:

```bash
curl -fsSL .../install.sh | sudo bash -s -- \
  --api-url ... --agent-id ... --install-token ... \
  --yes --no-grant-service-mgmt
```

The install command returned by `POST /admin/agents` always includes `--yes`. Pass `"grant_service_mgmt": false` in the request body to also include `--no-grant-service-mgmt`.

If you skipped it or want to add it after the fact:

**Service management:**

```bash
# Linux
echo 'reach-agent ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/sbin/service' \
  | sudo tee /etc/sudoers.d/reach-agent
sudo chmod 440 /etc/sudoers.d/reach-agent

# macOS (background mode)
echo 'reach-agent ALL=(ALL) NOPASSWD: /bin/launchctl' \
  | sudo tee /etc/sudoers.d/reach-agent
sudo chmod 440 /etc/sudoers.d/reach-agent
```

**Full sudo (personal machines, fully trusted environments):**

```bash
echo 'reach-agent ALL=(ALL) NOPASSWD: ALL' \
  | sudo tee /etc/sudoers.d/reach-agent
sudo chmod 440 /etc/sudoers.d/reach-agent
```

For shared multi-user environments where not all token holders should have elevated access, use **approved mode** and allowlist only the specific sudo commands needed rather than granting open sudo access.

To remove sudo access:

```bash
sudo rm /etc/sudoers.d/reach-agent
```

### Docker access

The `reach-agent` user has no docker access by default. Being in the `docker` group is equivalent to root - grant it only if you need the agent to run docker commands.

Docker access is prompted during interactive install with `[y/N]` (default **no**). The agent user is added to the `docker` group if granted.

**Interactive install - pre-answer the docker prompt** without losing the other prompts:

```bash
# Answer yes without being asked
sudo bash install.sh --grant-docker

# Answer no without being asked (redundant but explicit)
sudo bash install.sh --no-grant-docker
```

**Non-interactive install** - `--yes` applies the default (docker off). To also grant docker, add `--grant-docker`:

```bash
curl -fsSL .../install.sh | sudo bash -s -- \
  --api-url ... --agent-id ... --install-token ... \
  --yes --grant-docker
```

To include `--grant-docker` in the API-generated install command, pass `"grant_docker": true` when creating the agent:

```bash
curl -s -X POST "$API_URL/admin/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "tenant_xxxxx", "grant_docker": true}' | python3 -m json.tool
```

If you skipped it or Docker wasn't installed yet, add it manually after Docker is installed:

**Linux:**
```bash
usermod -aG docker reach-agent
systemctl restart reach-agent
```

**macOS (background mode):**
```bash
dseditgroup -o edit -a reach-agent -t user docker
launchctl unload /Library/LaunchDaemons/com.reach-agent.plist
launchctl load /Library/LaunchDaemons/com.reach-agent.plist
```

---

## API reference

### Agent endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/agent/claim` | install token | One-time agent registration |
| `POST` | `/agent/sync` | agent token | Poll for jobs + record heartbeat |
| `POST` | `/agent/jobs/{id}/result` | agent token | Post command result |
| `POST` | `/agent/rotate-token` | agent token | Self-service token rotation (called by agent, not CLI) |

### User (CLI) endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/me` | user token | Get current user identity (user_id, tenant_id, name) |
| `POST` | `/jobs` | user token | Create a job |
| `GET` | `/jobs` | user token | List your own jobs (`?agent_id=` `?limit=` `?cursor=`) |
| `GET` | `/jobs/{id}` | user token | Get job result and output |
| `GET` | `/agents` | user token | List accessible agents (`?tag=key:value` to filter) |
| `GET` | `/agents/{id}` | user token | Get agent details, policy, and tags |
| `GET` | `/agents/{id}/approved-commands` | user token | Approval records for an agent (`?status=approved\|pending\|denied\|expired`; default `approved` returns agent-wide effective list; others return your own records) |
| `GET` | `/approvals/pending` | user token | Your pending approval requests across all agents (`?agent_id=` to filter) |

### Admin endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/admin/tenants` | ADMIN_TOKEN | Create a tenant |
| `GET` | `/admin/tenants` | ADMIN_TOKEN | List tenants |
| `POST` | `/admin/tenants/{id}/users` | ADMIN_TOKEN | Create a user under a tenant (issues their token); body: `{"name": "..."}` - required |
| `GET` | `/admin/tenants/{id}/users` | ADMIN_TOKEN | List users in a tenant (no raw tokens) |
| `POST` | `/admin/tenants/{id}/users/{user_id}/rotate-token` | ADMIN_TOKEN | Rotate one user's token (keeps identity, swaps credential) |
| `DELETE` | `/admin/tenants/{id}/users/{user_id}` | ADMIN_TOKEN | Revoke one user's token |
| `GET` | `/admin/tenants/{id}/users/{user_id}/agents` | ADMIN_TOKEN | Get user's current agent access list |
| `PUT` | `/admin/tenants/{id}/users/{user_id}/agents` | ADMIN_TOKEN | Replace user's access list (`["*"]` = unrestricted, `[]` = locked out) |
| `POST` | `/admin/tenants/{id}/users/{user_id}/agents/{agent_id}` | ADMIN_TOKEN | Grant one agent to a restricted user |
| `DELETE` | `/admin/tenants/{id}/users/{user_id}/agents/{agent_id}` | ADMIN_TOKEN | Revoke one agent from a restricted user |
| `GET` | `/admin/agents` | ADMIN_TOKEN | List all agents for a tenant (`?tenant_id=` required, `?tag=` optional) |
| `POST` | `/admin/agents` | ADMIN_TOKEN | Create an agent under a tenant |
| `POST` | `/admin/agents/{id}/revoke` | ADMIN_TOKEN | Revoke agent (CREATED/ACTIVE/INACTIVE → REVOKED). Cuts sync access, removes from user access lists. Undoable via reissue. |
| `DELETE` | `/admin/agents/{id}` | ADMIN_TOKEN | Soft-delete agent (REVOKED → DELETED). Record stays in database. Requires prior revoke. |
| `DELETE` | `/admin/agents/{id}/remove` | ADMIN_TOKEN | Permanently remove agent record (DELETED only). Irreversible. |
| `POST` | `/admin/agents/{id}/reissue-install-token` | ADMIN_TOKEN | Reissue install token - resets to CREATED (allowed for CREATED/INACTIVE/REVOKED; blocked for ACTIVE without `force`, blocked for DELETED) |
| `GET` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Get agent's current tag list |
| `PUT` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Replace tag list entirely |
| `POST` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Add tags (merge, no duplicates) |
| `DELETE` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Remove specific tags |
| `GET` | `/admin/agents/{id}/policy` | ADMIN_TOKEN | Get agent policy (mode + effective approved commands) |
| `PUT` | `/admin/agents/{id}/policy/mode` | ADMIN_TOKEN | Set policy mode (`wild` / `readonly` / `approved`) |
| `GET` | `/admin/jobs` | ADMIN_TOKEN | List jobs (`?agent_id=` `?tenant_id=` `?created_by=` `?limit=` `?cursor=`) |
| `GET` | `/admin/approvals` | ADMIN_TOKEN | List approval records (`?agent_id=` `?tenant_id=` `?status=pending\|approved\|denied` `?limit=` `?cursor=`). Paginated - default 20, max 100. |
| `POST` | `/admin/approvals` | ADMIN_TOKEN | Pre-approve without a prior block. Single: `{"agent_id": "...", "command": "...", "duration": "8h"}` → returns approval object (409 if already active). Bulk: `{"agent_id": "...", "commands": [...]}` → returns `{"created": [...], "skipped": [...]}`, idempotent. |
| `PUT` | `/admin/approvals/{id}/approve` | ADMIN_TOKEN | Approve, re-approve, or update duration (body: `{"duration": "8h"}` optional; default permanent). Works on any status. |
| `PUT` | `/admin/approvals/{id}/deny` | ADMIN_TOKEN | Deny or revoke - works on any status including already-approved records |
| `DELETE` | `/admin/approvals/{id}` | ADMIN_TOKEN | Permanently delete an approval record. Works on any status; removing an approved record takes effect on the next agent sync. |

---

## Database schema

### AWS (DynamoDB)

| Table | Key | GSIs | Purpose |
|---|---|---|---|
| `reach-agents` | `agent_id` | `tenant-index` (tenant_id) | Agent records, status, token hash, fingerprint, mode |
| `reach-tenants` | `tenant_id` | - | Tenant records |
| `reach-users` | `user_id` | `token-hash-index` (token_hash), `tenant-index` (tenant_id) | User records, token hashes, per-user agent access lists |
| `reach-jobs` | `job_id` | `agent-status-index` (agent_id, status), `tenant-history-index` (tenant_id, created_at) | Job queue and results; TTL on `expires_at` auto-deletes after 7 days |
| `reach-approvals` | `approval_id` | `agent-approvals-index` (agent_id, created_at), `tenant-approvals-index` (tenant_id, created_at) | Approval records: pending, approved (with optional `expires_at`), and denied |

All tables use `DeletionPolicy: Retain` - safe to redeploy the stack without losing data.

### Docker / FastAPI (PostgreSQL)

Tables (`agents`, `tenants`, `users`, `jobs`, `approvals`) are managed via Alembic migrations. On startup the container runs `alembic upgrade head` automatically - no manual SQL or schema setup needed. Upgrades that include schema changes are applied on the next container restart.

Key columns on the `approvals` table:

| Column | Type | Description |
|---|---|---|
| `approval_id` | string | Primary key (`appr_xxx`) |
| `agent_id` | string | Which agent the command was blocked on |
| `tenant_id` | string | Tenant isolation |
| `command` | text | The exact command that was blocked |
| `requested_by` | string | `user_id` of the submitter |
| `job_id` | string | The job that triggered this record |
| `status` | string | `pending` / `approved` / `denied` |
| `expires_at` | string | ISO timestamp; null means permanent; expired approved records are filtered from the effective list but kept for history |
| `reviewed_at` | string | When the admin acted |
| `reviewed_by` | string | Who reviewed it |

---

## Policy enforcement

Commands are evaluated server-side before being queued. The agent never sees a blocked command.

### Always blocked (all modes)

These are rejected regardless of the agent's policy mode:

| Command | Reason |
|---|---|
| `rm -rf /` | Recursive root delete |
| `mkfs` | Filesystem format |
| `dd if=` | Raw disk write |
| `shutdown`, `reboot`, `poweroff` | System power control |
| `init 0`, `init 6` | SysV runlevel changes |
| Fork bomb (`: () { :|: & }`) | Process exhaustion |

### Blocked in readonly mode

In addition to the always-blocked list, readonly mode also blocks:

| Category | Examples |
|---|---|
| File writes / deletes | `rm`, `mv`, `chmod`, `chown`, `truncate`, `shred`, `tee`, `sed -i`, `>` redirect |
| Process control | `kill`, `killall`, `pkill` |
| Service management | `systemctl start/stop/restart`, `service start/stop` |
| Containers | `docker run/stop/rm/pull/build`, `docker-compose up/down`, `kubectl apply/delete/exec` |
| Package managers | `apt install`, `yum`, `dnf`, `pacman`, `apk`, `snap`, `flatpak`, `brew install`, `pip install`, `npm install`, `yarn add`, `gem install`, `cargo install` |
| File download | `wget`, `curl -o` |
| Disk / filesystem | `dd`, `mkfs`, `fdisk`, `parted`, `mount`, `umount` |
| Firewall | `iptables`, `ufw allow/deny` |
| User management | `useradd`, `userdel`, `usermod`, `passwd`, `su` |
| Scheduled jobs | `crontab` |
| Privilege escalation | `sudo` |

### Approved mode

Read commands always run - approved mode only gates write and destructive operations (anything that would be blocked in readonly mode).

Write commands are checked against the agent's approved list. If the command matches (exact match or starts-with prefix), it runs normally. If not:

- **Linux** - runs under Landlock sandbox. If the kernel blocks the write, the agent returns `blocked=true`.
- **macOS** - uses the server-supplied `is_write` flag. If the command is a write and not approved, the agent refuses it immediately without running it.

In both cases the backend creates a pending approval record. The admin reviews it via `GET /admin/approvals?status=pending`, then approves or denies via `PUT /admin/approvals/{id}/approve` (with optional duration) or `PUT /admin/approvals/{id}/deny`. Once approved, the command prefix is included in the approved list on the next sync and runs without restriction. See [Approvals](#approvals).

The match is prefix-based with a word boundary: approving `docker logs` permits `docker logs myapp --tail 100` but not `docker rm myapp`.

---

## Rate limits

Rate limiting applies to the **Docker / FastAPI** deployment only. The key function uses the Bearer token if present, falling back to the client IP - so one misbehaving agent or user cannot affect others.

**Agent endpoints**

| Endpoint | Limit |
|---|---|
| `POST /agent/claim` | 5/hour per IP (no auth token at claim time) |
| `POST /agent/sync` | 60/minute per agent token |
| `POST /agent/jobs/{id}/result` | 60/minute per agent token |
| `POST /agent/rotate-token` | 10/hour per agent token |

**User (tenant) endpoints**

| Endpoint | Limit |
|---|---|
| `POST /jobs` | 30/minute per user token |
| `GET /me`, `GET /jobs/{id}`, `GET /agents/{id}` | 120/minute per user token |
| `GET /jobs`, `GET /agents`, `GET /approvals/pending`, `GET /agents/{id}/approved-commands` | 60/minute per user token |

**Admin endpoints**

| Endpoint | Limit |
|---|---|
| All `GET /admin/*` reads | 120/minute per admin token |
| `PUT /admin/approvals/{id}/approve`, `PUT /admin/approvals/{id}/deny` | 60/minute per admin token |
| `POST /admin/approvals`, `POST/PUT/DELETE` tag/policy/access/lifecycle writes | 30/minute per admin token |
| `POST /admin/agents`, `POST /admin/tenants`, `POST /admin/tenants/{id}/users` | 20/minute per admin token |
| `POST /admin/agents/{id}/reissue-install-token`, `POST /admin/tenants/{id}/users/{id}/rotate-token` | 10/minute per admin token |

**Health**

| Endpoint | Limit |
|---|---|
| `GET /health` | 120/minute per IP |

Exceeding a limit returns `429` with `{"error": "rate limit exceeded"}`. The agent's sync loop treats 429 the same as a transient error and retries on the next poll interval.

The Lambda deployment relies on API Gateway's built-in concurrency controls rather than per-token limits.

---

## Security

- Tokens are never stored raw - only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in the database
- Install token is one-time use and expires after 24 hours
- Install token is cleared from disk after successful claim
- Agent token is bound to a machine fingerprint - replayed tokens from another machine are rejected
- Agent token automatically rotates every 30 days via self-service rotation (no lockout window)
- Config files written with `0600` permissions
- Commands are checked against a policy blocklist before execution
- Command timeout: 60 seconds
- Max output: 50 KB per command
