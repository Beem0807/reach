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

- AWS CLI configured (`aws sts get-caller-identity`)

### Deploy

**1. Generate secrets - save both, you need them for future upgrades:**

```bash
export TOKEN_PEPPER=$(openssl rand -hex 32)
export ADMIN_TOKEN=$(openssl rand -hex 32)
echo "TOKEN_PEPPER=$TOKEN_PEPPER"
echo "ADMIN_TOKEN=$ADMIN_TOKEN"
```

**2. Deploy:**

```bash
aws cloudformation create-stack \
  --stack-name reach-platform \
  --template-url https://reach-releases.s3.amazonaws.com/lambda/latest/template.yaml \
  --parameters \
    ParameterKey=TokenPepper,ParameterValue="$TOKEN_PEPPER" \
    ParameterKey=AdminToken,ParameterValue="$ADMIN_TOKEN" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND
```

**3. Get your API URL:**

```bash
export API_URL=$(aws cloudformation describe-stacks \
  --stack-name reach-platform \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)
echo $API_URL
```

### Upgrade

```bash
aws cloudformation update-stack \
  --stack-name reach-platform \
  --template-url https://reach-releases.s3.amazonaws.com/lambda/v1.1.0/template.yaml \
  --parameters \
    ParameterKey=TokenPepper,UsePreviousValue=true \
    ParameterKey=AdminToken,UsePreviousValue=true \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND
```

Replace `v1.1.0` with the target version. DynamoDB tables are retained across upgrades.

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
NEW_ADMIN_TOKEN=$(openssl rand -hex 32)

aws cloudformation update-stack \
  --stack-name reach-platform \
  --use-previous-template \
  --parameters \
    ParameterKey=TokenPepper,UsePreviousValue=true \
    ParameterKey=AdminToken,ParameterValue="$NEW_ADMIN_TOKEN" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND

echo "New ADMIN_TOKEN: $NEW_ADMIN_TOKEN"
```

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

**When an agent is deleted**, reach automatically removes it from every user's `allowed_agent_ids` in that tenant. No manual cleanup needed.

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
      "status": "COMPLETE",
      "exit_code": 0,
      "created_at": "2026-06-17T10:05:00+00:00"
    }
  ],
  "next_cursor": "MjAyNi0wNi0xN1QxMDowNTowMCswMDowMA=="
}
```

Pass `?cursor=<next_cursor>` on the next request to fetch the next page. The cursor encodes the `created_at` of the last returned item - absent when you've reached the last page.
```

---

## Reissuing an install token

If an install token expires before the agent was set up, or the machine needs to be reimaged and re-registered, reissue a fresh install token for the same `agent_id` instead of bootstrapping a new one:

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/reissue-install-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

This resets the agent back to `CREATED` status with a new install token - the same `agent_id` is kept, so any aliases or job history pointing at it stay intact.

**This is a hard cutover, not a live rotation.** If the agent is currently running and connected, its existing `agent_token` is invalidated immediately (the machine fingerprint and claim are cleared). The agent will stop syncing on its next poll and go dormant rather than retry forever - it needs to be re-installed with the new install token to come back online. There's no in-band way to recover it remotely once this happens.

To prevent accidental disconnects, the server blocks this for agents currently in `ACTIVE` status:

```json
{"error": "agent is currently ACTIVE - reissuing will disconnect it immediately with no in-band recovery. Pass {\"force\": true} to proceed anyway."}
```

It's allowed without confirmation for `CREATED` (never claimed) and `INACTIVE` (already lost contact) agents - there's no live connection to break in those states. To force it through on an `ACTIVE` agent anyway (e.g. a suspected compromised token), pass `force`:

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/reissue-install-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"force": true}' | python3 -m json.tool
```

---

## Deleting an agent

Permanently removes the agent record. Unlike reissuing, this does not keep the `agent_id` around - it's gone for good (job history referencing it is unaffected, but `GET /agents/{id}` will 404 afterward).

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Same guardrail as reissuing: blocked with `409` if the agent is currently `ACTIVE`, unless you pass `force`:

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"force": true}'
```

---

## Policy management

Policies are managed via the admin API, authenticated with your `ADMIN_TOKEN`.

**View policy:**

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

**Add approved commands:**

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/policy/commands" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"commands": ["docker ps", "git status"]}'
```

**Remove approved commands:**

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx/policy/commands" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"commands": ["git status"]}'
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
CREATED → ACTIVE → INACTIVE (heartbeat timeout) → ACTIVE (auto-reactivates on next sync)
```

An agent starts as `CREATED`. On first run it calls `POST /agent/claim` with the install token, transitions to `ACTIVE`, and receives a permanent `agent_token`. The install token is then cleared from disk.

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
| `GET` | `/jobs` | user token | List your own jobs (`?agent_id=` `?limit=` `?cursor=`) - scoped to the authenticated user |
| `GET` | `/jobs/{id}` | user token | Get job result and output |
| `GET` | `/agents` | user token | List accessible agents (`?tag=key:value` to filter by tag) |
| `GET` | `/agents/{id}` | user token | Get agent details, policy, and tags |

### Admin endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/admin/tenants` | ADMIN_TOKEN | Create a tenant |
| `GET` | `/admin/tenants` | ADMIN_TOKEN | List tenants |
| `POST` | `/admin/tenants/{id}/users` | ADMIN_TOKEN | Create a user under a tenant (issues their token) |
| `GET` | `/admin/tenants/{id}/users` | ADMIN_TOKEN | List users in a tenant (no raw tokens) |
| `POST` | `/admin/tenants/{id}/users/{user_id}/rotate-token` | ADMIN_TOKEN | Rotate one user's token (keeps identity, swaps credential) |
| `DELETE` | `/admin/tenants/{id}/users/{user_id}` | ADMIN_TOKEN | Revoke one user's token |
| `GET` | `/admin/tenants/{id}/users/{user_id}/agents` | ADMIN_TOKEN | Get user's current agent access list |
| `PUT` | `/admin/tenants/{id}/users/{user_id}/agents` | ADMIN_TOKEN | Replace user's access list (`["*"]` = unrestricted, `[]` = locked out) |
| `POST` | `/admin/tenants/{id}/users/{user_id}/agents/{agent_id}` | ADMIN_TOKEN | Grant one agent to a restricted user |
| `DELETE` | `/admin/tenants/{id}/users/{user_id}/agents/{agent_id}` | ADMIN_TOKEN | Revoke one agent from a restricted user |
| `GET` | `/admin/agents` | ADMIN_TOKEN | List all agents for a tenant (`?tenant_id=` required, `?tag=` optional filter) |
| `GET` | `/admin/jobs` | ADMIN_TOKEN | List jobs with filters (`?agent_id=` `?tenant_id=` `?created_by=` `?limit=` `?cursor=`) - at least one filter required |
| `POST` | `/admin/agents` | ADMIN_TOKEN | Create an agent under a tenant |
| `DELETE` | `/admin/agents/{id}` | ADMIN_TOKEN | Delete an agent (blocked if ACTIVE unless `force`) |
| `POST` | `/admin/agents/{id}/reissue-install-token` | ADMIN_TOKEN | Reissue install token for an existing agent (blocked if ACTIVE unless `force`) |
| `GET` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Get agent's current tag list |
| `PUT` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Replace tag list entirely |
| `POST` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Add tags (merge, no duplicates) |
| `DELETE` | `/admin/agents/{id}/tags` | ADMIN_TOKEN | Remove specific tags |
| `GET` | `/admin/agents/{id}/policy` | ADMIN_TOKEN | Get agent policy |
| `PUT` | `/admin/agents/{id}/policy/mode` | ADMIN_TOKEN | Set policy mode |
| `POST` | `/admin/agents/{id}/policy/commands` | ADMIN_TOKEN | Add approved commands |
| `DELETE` | `/admin/agents/{id}/policy/commands` | ADMIN_TOKEN | Remove approved commands |

---

## Database schema

### AWS (DynamoDB)

| Table | Key | Purpose |
|---|---|---|
| `reach-agents` | `agent_id` | Agent records, status, token hash, fingerprint |
| `reach-tenants` | `tenant_id` | Tenant records |
| `reach-users` | `user_id` | User records, token hashes, and per-user agent access lists |
| `reach-jobs` | `job_id` | Job queue and results (TTL: 7 days) |

All tables use `DeletionPolicy: Retain` - safe to redeploy the stack without losing data.

### Docker / FastAPI (PostgreSQL)

Tables (`agents`, `tenants`, `users`, `jobs`) are managed via Alembic migrations. On startup the container runs `alembic upgrade head` automatically - no manual SQL or schema setup needed. Upgrades that include schema changes are applied on the next container restart.

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

Only commands that exactly match (or start with) an entry in the agent's `approved_commands` list are allowed. Everything else is rejected.

Approved commands are managed via the admin API - the CLI can view them but not change them.

---

## Rate limits

Rate limiting applies to the **Docker / FastAPI** deployment only. Each limit is per token (per agent or per user), so one misbehaving agent or user cannot affect others.

| Endpoint | Limit |
|---|---|
| `POST /agent/claim` | 5 per hour per IP (no auth token yet at claim time) |
| `POST /agent/sync` | 60 per minute per agent token |
| `POST /agent/jobs/{id}/result` | 60 per minute per agent token |
| `POST /agent/rotate-token` | 10 per hour per agent token |
| `POST /jobs` | 30 per minute per user token |

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
