# Self-Hosting reach

Deploy and operate your own reach backend. The CLI and agent are separate - they just need your API URL.

---

## Deployment options

| Option | Runtime | Database | Best for |
|---|---|---|---|
| [Local machine](#option-1-local-machine) | FastAPI | PostgreSQL | Home server, spare machine, no cloud account needed |
| [AWS Lambda](#option-2-aws-lambda) | Lambda | DynamoDB | Small teams, low cost, AWS-native |
| [Docker / FastAPI](#option-3-docker--fastapi) | FastAPI | PostgreSQL (or [DynamoDB on AWS](#dynamodb-on-aws)) | Any cloud, self-hosted VMs, k8s |

### Why DynamoDB with Lambda, and PostgreSQL with Docker?

Lambda functions are stateless and short-lived - each invocation starts fresh with no persistent connections. DynamoDB is a natural fit because it's serverless, has no connection to maintain, and scales to zero when idle. The combination keeps costs near zero for small teams (pay only per request, no always-on database instance).

PostgreSQL with a persistent server (FastAPI in Docker or k8s) is the right choice when you want to run anywhere - any cloud, a VPS, or on-prem - without being tied to AWS. FastAPI keeps a connection pool open for the lifetime of the process, which PostgreSQL handles well. DynamoDB would require AWS credentials and doesn't make sense outside of AWS.

Lambda + PostgreSQL is deliberately not supported. Lambda's ephemeral connections exhaust PostgreSQL's connection limit quickly at scale, and solving that requires RDS Proxy - adding cost and complexity that defeats the purpose of the low-cost Lambda option.

---

## Option 1: Local machine

Run the full backend on any machine you already have - no cloud account, no VMs to provision. A good fit for a home server, a spare machine, or a VPS where you want full control without the Lambda setup.

### Prerequisites

- Docker + docker compose
- `curl`, `openssl`, and `python3`
- (Optional) [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) or [ngrok](https://ngrok.com/download) to expose the backend publicly so remote agents can reach it

### Deploy

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

The script handles everything in one run:

1. Prompts for release tag (default: `latest`)
2. Prompts for `ADMIN_PASSWORD` and `TOKEN_PEPPER` (or generates them)
3. Prompts for workspace name, admin username, and admin password (your day-to-day login)
4. Optionally prompts for data retention settings (defaults: approvals 7d, jobs 7d, audit 90d, agent history 30d)
5. Starts PostgreSQL, the Reach backend, and nginx via Docker Compose
6. Optionally starts a public tunnel (if cloudflared or ngrok is installed):
   - **cloudflared** - no account needed, URL changes on restart
   - **ngrok** - static domains, requires free account
7. **Bootstraps your workspace automatically**: creates the tenant, admin user, and API key
8. Optionally creates an agent (prompts for mode and capability grants)
9. Optionally installs the CLI and logs you in - you can run `reach exec -- hostname` immediately

No need to open the console for initial setup - the script handles everything.

### Managing the local stack

After deploying, re-run the script with a subcommand to manage it. All subcommands operate on the stack in `~/.reach/local` - you can run them via `curl … | bash -s -- <flag>` or, from a checkout, `./scripts/local-setup.sh <flag>`.

| Command | What it does | Data |
|---|---|---|
| `--status` | Check container, backend health, API-key auth, and agent state | - |
| `--update` | Pull a newer backend image and restart. Keeps `TOKEN_PEPPER`, `ADMIN_PASSWORD`, the database, tenants, users, API keys, and agents | Kept |
| `--rotate-password` | Set a new platform admin password and restart the backend | Kept |
| `--rotate-session-key` | Generate a new `SESSION_SIGNING_KEY` and restart - invalidates active console sessions (users log in again), no data impact | Kept |
| `--down` | Stop the containers but keep the Postgres data volume - restart later with no data loss | **Kept** |
| `--reset` | Remove containers, network, the Postgres data volume, and the local env file | **Deleted** |
| `--purge` | Everything `--reset` does, plus delete `~/.reach/local`, and prompt to uninstall the Reach CLI | **Deleted** |

```bash
# Stop the backend but keep your data (resume later by re-running the script)
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --down

# Permanently remove the stack and its data
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --reset

# Remove everything, including ~/.reach/local, and optionally the CLI
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --purge
```

> `--down` stops the stack but **keeps the database** - it is not a full teardown. Use `--reset` to delete the data, or `--purge` to remove the local setup directory and (optionally) the CLI as well.

---

## Option 2: AWS Lambda

### Prerequisites

- AWS CLI installed and configured (`aws sts get-caller-identity`)

### Deploy

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash
```

The script handles everything in one run:

1. Prompts for AWS profile (leave blank to use environment credentials) and region (default: `us-east-1`)
2. Verifies credentials before proceeding
3. Prompts for stack name (default: `reach-platform`) and release tag (default: `latest`)
4. Prompts for `ADMIN_PASSWORD` and `TOKEN_PEPPER` (or generates them)
5. Prompts for workspace name, admin username, and admin password
6. Prompts for grant options (systemctl/docker access, default off)
7. Optionally prompts for data retention settings (defaults: approvals 7d, jobs 7d, audit 90d, agent history 30d)
8. Deploys the CloudFormation stack and waits for completion
9. **Bootstraps your workspace automatically**: creates the tenant, admin user, API key, and an agent
10. Optionally installs the CLI and logs you in - you can run `reach exec -- hostname` immediately

No need to open the console for initial setup - the script handles everything.

### Upgrade

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update
```

The script lists your existing stacks, prompts for the stack name and release tag (leave blank to keep), and optionally rotates `ADMIN_PASSWORD` or changes any retention setting. `TOKEN_PEPPER` is always kept - it cannot be changed. See [TOKEN_PEPPER is permanent](#token_pepper-is-permanent).

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
  -e SESSION_SIGNING_KEY="<your-session-key>" \
  -e ADMIN_PASSWORD="<your-admin-password>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  -e APPROVAL_RETENTION_DAYS="7" \
  -e JOB_RETENTION_DAYS="7" \
  nabeemdev/reach:latest
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOKEN_PEPPER` | Yes | - | HMAC pepper for token hashing. Permanent - changing it invalidates all existing tokens. |
| `SESSION_SIGNING_KEY` | Yes | - | HMAC key for signing tenant session (login) tokens. **Safe to rotate** - only forces re-login. Use the same value across replicas if you run more than one backend instance. |
| `ADMIN_PASSWORD` | Yes | - | Password for the platform admin console and admin API. Rotate by restarting with a new value. |
| `DATABASE_URL` | Postgres only | - | PostgreSQL connection string. Required for the default Postgres backend; **not used** when `STORAGE_BACKEND=dynamo` (see [DynamoDB on AWS](#dynamodb-on-aws)). |
| `APPROVAL_RETENTION_DAYS` | No | `7` | Days to retain terminal approval records (`denied`, `expired`) before deletion. |
| `JOB_RETENTION_DAYS` | No | `7` | Days to retain terminal job records (`SUCCEEDED`, `FAILED`, `REJECTED`, `EXPIRED`) before deletion. |
| `AUDIT_RETENTION_DAYS` | No | `90` | Days to retain audit log entries before deletion. |
| `AGENT_HISTORY_RETENTION_DAYS` | No | `30` | Days to retain agent status history entries before deletion. |

**Advanced (rarely changed).** These are baked into the image with working defaults - only override them if you self-host the agent binaries or pin a specific agent version:

| Variable | Default | Description |
|---|---|---|
| `STORAGE_BACKEND` | `postgres` (set in the image) | Storage driver - `postgres` for this deployment, `dynamo` on Lambda. Don't change it for the Docker image. |
| `RELEASES_S3_BASE` | `https://reach-releases.s3.amazonaws.com` | Base URL for agent binary and install-script downloads (host agents) and the default Helm chart repo (k8s agents). Point this at your own mirror if you host the artifacts yourself. |
| `AGENT_VERSION` | `latest` | **Host agents only.** The agent release the generated host install command references - it sets the binary/install-script S3 path. Set to a specific version to pin host agents instead of tracking `latest`. (The k8s agent image is not set here; it rides the chart's `appVersion` - pin it via `CHART_VERSION` below.) |
| `RELEASES_CHART_REPO` | `<RELEASES_S3_BASE>/charts/reach-agent` | Helm chart repo URL used in the generated k8s `helm install` command (`helm repo add reach <this>`). Override if you host the chart repo elsewhere (e.g. gh-pages or an OCI registry base). |
| `CHART_VERSION` | _(empty)_ | Pin the chart version in the generated k8s command (`--version <this>`). Empty installs the latest chart in the repo. Independent of `AGENT_VERSION` - the chart and agent version move separately. |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Where API rate-limit counters live. In-memory is per-process - correct for a single instance. Set to a shared store (e.g. `redis://host:6379`) when running more than one backend replica. See [Running multiple replicas](#running-multiple-replicas). |

On the **Docker** image you can set these directly as environment variables. The setup scripts also prompt for them interactively (the prompts read from your terminal even over `curl … | bash`): both the local and Lambda installers ask whether to **pin agent / chart versions** at create time and let you change them on `--update` - the local one writes them into the generated compose file, the Lambda one into CloudFormation parameters (`AGENT_VERSION`, `CHART_VERSION`, `RELEASES_CHART_REPO`). For a non-interactive run, export the variable **for the piped shell**, e.g. `curl -fsSL …/local-setup.sh | CHART_VERSION=0.1.3 bash` (a prefix before `curl` would set it for curl, not the shell). Empty `RELEASES_CHART_REPO` derives from `RELEASES_S3_BASE`.

On first startup, Alembic runs `alembic upgrade head` automatically and creates all tables. Subsequent restarts apply any pending migrations from new versions. The image supports `linux/amd64` and `linux/arm64` - works on AWS Graviton, Raspberry Pi, and Apple Silicon without extra flags.

### Running multiple replicas

The backend is stateless except for **rate-limit counters**, which default to in-memory (`memory://`) - per-process. Behind a load balancer with N replicas, each replica keeps its own counters, so a client effectively gets up to N× the configured limit depending on which replica it hits.

To enforce limits correctly across replicas, give them a **shared counter store** by setting `RATE_LIMIT_STORAGE_URI` to a Redis URL (the same value on every replica):

```bash
docker run -d -p 8000:8000 \
  -e RATE_LIMIT_STORAGE_URI="redis://your-redis-host:6379" \
  -e TOKEN_PEPPER="..." -e SESSION_SIGNING_KEY="..." -e ADMIN_PASSWORD="..." \
  -e DATABASE_URL="..." \
  nabeemdev/reach:latest
```

Notes:
- The bundled image already includes the Redis client; you only need a reachable Redis (managed ElastiCache/MemoryDB, or your own).
- `limits` (the rate-limit backend) also supports `redis+sentinel://`, `memcached://`, and `mongodb://` URIs.
- **Alternative:** rate limit at a single ingress instead of in the app - nginx `limit_req`, an ALB/API Gateway, or Cloudflare. That moves the shared-state problem to one chokepoint and removes the Redis dependency, at the cost of not keying off the API token the way the app does.
- This applies only to the Docker/FastAPI deployment. On Lambda, throttling is handled by API Gateway, not by the app.

**2. Put a reverse proxy in front (nginx, Caddy, ALB, etc.) for TLS.**

### DynamoDB on AWS

If you run the container **on AWS** (ECS/Fargate, EKS, or EC2), you can use DynamoDB instead of PostgreSQL - no RDS instance to manage, and no connection pool to tune. A long-lived FastAPI process talking to DynamoDB is fine; the connection-limit problem that rules out Lambda + PostgreSQL does not apply here.

This path is **AWS-only**: the agent's boto3 client uses the standard AWS credential and region chain (task role, IRSA, instance profile, or env vars). It is not intended for off-AWS or local DynamoDB.

**1. Run the container with the DynamoDB backend:**

```bash
docker run -d \
  -p 8000:8000 \
  -e STORAGE_BACKEND=dynamo \
  -e AWS_REGION=us-east-1 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e SESSION_SIGNING_KEY="<your-session-key>" \
  -e ADMIN_PASSWORD="<your-admin-password>" \
  nabeemdev/reach:latest
```

- No `DATABASE_URL` is needed.
- Provide AWS credentials the boto3 way - an ECS task role / EKS IRSA / EC2 instance profile is recommended over static keys. For local testing you can pass `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.
- On startup the container runs an idempotent bootstrap that creates the eight `reach-*` tables (on-demand billing) if they don't already exist - the DynamoDB equivalent of `alembic upgrade head`. Existing tables and their data are left untouched. You can also run it standalone with `python -m shared.dynamo_bootstrap`.
- The retention env vars (`APPROVAL_RETENTION_DAYS`, etc.) work the same as the PostgreSQL path.

**2. IAM policy.** The container's role needs table and index access on the `reach-*` tables. To let the bootstrap create them, include the create/describe actions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReachData",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
        "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
        "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:*:*:table/reach-*",
        "arn:aws:dynamodb:*:*:table/reach-*/index/*"
      ]
    },
    {
      "Sid": "ReachBootstrap",
      "Effect": "Allow",
      "Action": ["dynamodb:CreateTable", "dynamodb:DescribeTable"],
      "Resource": "arn:aws:dynamodb:*:*:table/reach-*"
    }
  ]
}
```

Once the tables exist you can drop the `ReachBootstrap` statement if you prefer a least-privilege runtime role (the bootstrap treats already-existing tables as a no-op).

DynamoDB tables created this way use no `DeletionPolicy`, so deleting them deletes their data - unlike the Lambda stack, which retains them. Back up with point-in-time recovery if needed.

---

## First-time setup

**Options 1 and 2 (setup scripts)** handle provisioning automatically - tenant, admin user, API key, and agent are all created by the script. You're ready to use the CLI immediately. Skip this section.

**Option 3 (Docker run directly)** requires manual setup. Open `http://<your-api-url>/ui` in a browser.

### Using the admin console

**1. Platform admin - create a tenant:**

Choose **Platform Admin** at the login screen and sign in with your `ADMIN_PASSWORD`. Go to **Tenants → New tenant**, enter a name, and click Create. The tenant appears in the list immediately.

**2. Platform admin - create the first user in the tenant:**

In the tenant row, click the user count or open the **Users** page, filter to your tenant, and click **Add user**. Enter a name, username, and role. The console shows the temporary password once - give it to the user and have them log in to the tenant console to set their own password.

**3. User - log in to the tenant console and create an agent:**

Choose **Tenant Console** at the login screen, enter the tenant name, username, and temporary password. You will be prompted to set a permanent password. Then go to **Agents → New agent**, choose a policy mode, and click Create. The console shows the install command - run it on the target machine.

**4. User - create an API token for the CLI:**

Go to **API Tokens → New token**, give it a name, and copy the token value (shown once). Run:

```bash
reach login --api-url "<your-api-url>" --api-key "<your-api-token>"
```

For automation, see [API.md](API.md) - specifically `POST /admin/login`, `POST /admin/tenants`, and `POST /admin/tenants/{id}/admin-users`.

---

## Managing users

From the **platform admin console** go to **Users**, filter by tenant, and use the table actions to add, disable, or change roles. From the **tenant console**, admin-role users can manage users under **Users**.

Rotating a user's API token keeps their identity and access list - only the credential changes. The new token is shown once. Revoking a user cuts access immediately; everyone else's tokens are unaffected.

For automation, see the user-management endpoints in [API.md](API.md). Platform admins use `POST /admin/tenants/{id}/admin-users` (create), `POST .../users/{user_id}/reset-password`, `POST .../users/{user_id}/disable`, and `PATCH .../users/{user_id}/role`; these require a session token from `POST /admin/login`. Tenant admins manage users within their own tenant through the `/tenant/users` endpoints using an API token.

---

## Rotating the admin password

`ADMIN_PASSWORD` is an environment variable - rotating it means setting a new value and restarting. Any active session tokens (8-hour JWTs signed with the old password) stop working immediately on the next request. Update any scripts that call `POST /admin/login` before rotating.

**Local machine (Option 1):**

Use the built-in subcommand - it generates (or prompts for) a new password, updates the env file, and restarts the backend:

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --rotate-password
```

**AWS Lambda (Option 2):**

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update
```

When prompted for a new `ADMIN_PASSWORD`, enter the new value (or generate one with `openssl rand -hex 32`). Leave it blank to keep the existing value.

**Docker / FastAPI (Option 3):**

Update the `ADMIN_PASSWORD` environment variable however your deployment manages it (`.env` file, secrets manager, k8s secret), then restart the container:

```bash
docker stop reach && docker run -d \
  -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e SESSION_SIGNING_KEY="<your-session-key>" \
  -e ADMIN_PASSWORD="$NEW_ADMIN_PASSWORD" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:latest
```

`TOKEN_PEPPER` must stay the same across rotations - changing it invalidates every agent token, user token, and install token in the database simultaneously. See [TOKEN_PEPPER is permanent](#token_pepper-is-permanent).

---

## Rotating the session signing key

`SESSION_SIGNING_KEY` signs the short-lived (8-hour) console session tokens. Unlike `TOKEN_PEPPER`, it is **safe to rotate** - the only effect is that active console sessions stop verifying, so users log in again. There is no data impact and nothing to migrate. Rotate it on a schedule, or immediately if you suspect a session token leaked.

**Local machine (Option 1):**

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --rotate-session-key
```

**AWS Lambda (Option 2):** run the update flow and answer **yes** when it asks `Rotate SESSION_SIGNING_KEY?`:

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update
```

**Docker / FastAPI (Option 3):** restart the container with a new `SESSION_SIGNING_KEY` value:

```bash
docker stop reach && docker run -d \
  -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e SESSION_SIGNING_KEY="$(openssl rand -hex 32)" \
  -e ADMIN_PASSWORD="<your-admin-password>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:latest
```

If you run multiple backend replicas, give them all the **same** new value so a session created on one verifies on the others.

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

By default every user in a tenant sees all agents. From the **tenant console**, go to **Users → [user] → Agent Access** to restrict a user to specific machines.

`allowed_agent_ids: ["*"]` means unrestricted access. A list of agent IDs means the user can only see and use those agents - every other agent returns 404 to them. An empty list locks them out of all agents without deleting their account.

When an agent is revoked, reach automatically removes it from every user's access list in that tenant - no manual cleanup needed.

For automation see `GET /tenant/users/{user_id}/agents` and `PUT /tenant/users/{user_id}/agents` in [API.md](API.md). The `PUT` replaces the whole access list - `{"agent_ids": ["*"]}` is unrestricted, `[]` locks the user out, and a list restricts them to those agents.

---

## Kubernetes agents

Agents come in two **types**, chosen when you create the agent (**Agents → New agent → Host / Kubernetes**):

- **Host** - a machine or VM. Installed with the `curl … install.sh …` command (see [README → Add a machine](README.md#add-a-machine)).
- **Kubernetes** - a cluster. Installed with the `helm install …` command the console generates.

A Kubernetes agent is **one logical agent per cluster**: it derives a stable identity from the `kube-system` namespace UID, so any number of replicas appear as a single agent, with a `Lease` electing one active leader. Install it from the published Helm repo (the console generates this, pre-filled and with the chart `--version` pinned):

```bash
helm repo add reach https://reach-releases.s3.amazonaws.com/charts/reach-agent --force-update
helm install reach-agent reach/reach-agent \
  --namespace reach --create-namespace \
  --set reach.apiUrl=https://reach.example.com \
  --set reach.installToken=install_xxx
```

Pin the chart with `--version <chartVersion>`; the agent image comes from that chart's `appVersion`, so pinning the chart pins the image (no separate `image.tag`). For Argo CD / Flux, use `reach.existingSecret` instead of the raw token - see [the chart's GitOps section](deploy/helm/reach-agent#gitops-argo-cd--flux).

Key differences from a host agent:

- **Access is Kubernetes RBAC.** What the agent can do in the cluster is the `clusterAccess` you bind in the chart (defaults to read-only `view`); the host-only Docker / service-management grants don't apply. The agent **auto-discovers and reports its effective cluster-wide RBAC**, which you **acknowledge** in the console - and any permission added later (in any namespace) shows up as **drift** to re-acknowledge.
- **Execution is gated, no shell.** Jobs run as `kubectl` (plus a few read-only filters) connected by pipes - no arbitrary shell. Tune the allowlist with `extraAllowedBinaries` / `allowedBinaries`.
- **Policy mode still applies** (`readonly` / `approved` / `wild`), enforced by the backend per `kubectl` verb.

The chart's image (`nabeemdev/reach-agent`) and all values are documented in [deploy/helm/reach-agent](deploy/helm/reach-agent); how the agent itself works is in [agent/README.md](agent/README.md).

---

## Managing agents

From the **tenant console**, go to **Agents** to see all agents with their status, hostname, type, policy mode, access level, tags, and detected capabilities. Filter by type or tag using the toolbar.

`token_issued_at` in the agent record shows when the current agent token was last issued - useful for auditing which agents are approaching their 30-day auto-rotation window.

For automation, tenant users list their own agents with `GET /agents` (filter with `?tag=key:value`); platform admins get a read-only cross-tenant view with `GET /admin/agents?tenant_id=...`. See [API.md](API.md).

---

## Agent tags

Tags are `key:value` labels on agents for display and grouping (format: `key:value`, lowercase letters/digits/hyphens/underscores). They are separate from access control - any user who can see an agent can see its tags.

Manage tags from the **tenant console** under **Agents → [agent] → Tags**. Users can filter the agent list by tag:

```bash
reach agents list --tag env:prod
```

Access control still applies - users only see agents they are allowed to access. The tag filter narrows on top of that. If no agents match, the result is an empty list (not an error).

For automation see `PUT /tenant/agents/{id}/tags` in [API.md](API.md). It replaces the full tag list in one call - pass `{"tags": []}` to clear all tags.

---

## Viewing job history and audit logs

Reach keeps two separate records:

- **Jobs** - every command that was submitted, with its stdout, stderr, exit code, and status. Browse them in the **tenant console** under **Jobs**. Each record includes `created_by` (the user ID of whoever submitted it).
- **Audit log** - a structured event log of who did what: logins, user and agent management, policy changes, approvals, and token operations. View it tenant-scoped in the **tenant console** under **Audit Logs**, or platform-wide in the **platform admin console** under **Audit Logs**.

Both are paginated (jobs default 20 / max 100; audit logs default 100 / max 200); subsequent pages use the `next_cursor` from the previous response.

For automation, users list their own jobs with `GET /jobs` (filters: `?agent_id=`, `?limit=`, `?cursor=`); tenant admins read the audit log with `GET /tenant/audit-logs` and platform admins with `GET /admin/audit-logs`. See [API.md](API.md).


---

## Reissuing an install token

If an install token expires before the agent was set up, or a machine needs to be reimaged, reissue a fresh install token from the **tenant console** under **Agents → [agent] → Reissue install token**. This resets the agent back to `CREATED` with a new install token - the same `agent_id` is kept, so aliases and job history stay intact.

**This is a hard cutover, not a live rotation.** If the agent is currently `ACTIVE`, its existing token is invalidated immediately and the agent goes dormant on its next poll. There is no in-band recovery - the agent must be reinstalled.

The console blocks reissue for `ACTIVE` agents unless you confirm. For `CREATED`, `INACTIVE`, and `REVOKED` agents it proceeds without a prompt - there is no live connection to break. `REVOKED` is the recommended path before reissuing on a machine you plan to re-register.

`DELETED` agents cannot have their install token reissued - create a new agent instead.

---

## Decommissioning an agent

Removing an agent is a three-step sequence from the **tenant console** under **Agents → [agent] → Decommission**. Each step requires the previous one to complete - this prevents accidental hard-deletes.

**Step 1 - Revoke**: cuts sync access immediately. The agent's next poll returns `403` and it goes dormant. The agent is also removed from every user's access list in the tenant. Works on `CREATED`, `ACTIVE`, and `INACTIVE` agents. A revoke can be undone - see [Reissuing an install token](#reissuing-an-install-token).

**Step 2 - Soft-delete**: marks the agent `DELETED`. The record stays in the database for audit. Requires `REVOKED` status.

**Step 3 - Remove**: permanently deletes the record. Job history referencing the agent ID is unaffected. Requires `DELETED` status.

---

## Policy management

Set an agent's policy mode from the **tenant console** under **Agents → [agent] → Policy**. Choose `wild`, `readonly`, or `approved`.

In `approved` mode, write commands are not pre-configured - they're approved on demand. When a write is blocked it creates a pending approval record; admins and operators review these in the console under **Approvals**. See [Approvals](#approvals).

---

## Approvals

When an agent runs in `approved` mode and a write command is not yet approved, the agent blocks it and the backend creates a pending approval record.

Read commands always run. Only write commands need approval.

**Host vs Kubernetes.** Host approvals are **command text** (prefix match). Kubernetes approvals are **structured rules** - `{verb, resource, namespace, name}`, any field wildcardable with `*` - so one rule (e.g. `delete pods` in `team-a`) covers every matching object without re-approving each one. The console shows the two kinds **separately** (a Host/Kubernetes toggle), defaults to the 10 most recent, and has a case-insensitive **Search**. See [POLICIES → Kubernetes approvals are structured rules](POLICIES.md#kubernetes-approvals-are-structured-rules).

**Reviewing approvals**: admins and operators use the **tenant console → Approvals** to see pending requests and approve or deny them. You can approve permanently or with a time limit.

**Pre-approving**: before first use, go to **Agents → [agent] → Approvals → Add** (or the **Approvals** page) to approve a command (host) or author a rule (k8s) without waiting for a block. Useful for provisioning a new agent.

**Approval durations**: `permanent`, `1h`, `8h`, `24h`, `7d`, `30d`, `90d`, or custom `Nh`/`Nd`. Set `now` to instantly expire an approved record.

**Users** check their own approval status via the CLI:

```bash
reach approvals list                    # effective approved commands/rules for the default agent
reach approvals list --pending          # my pending requests
reach approvals list --denied           # my denied requests
reach approvals list --expired          # my expired approvals
reach approvals list --agent prod       # any of the above for a specific agent
```

The output adapts to the agent type: **host** agents show the command, **Kubernetes** agents show the structured rule (`verb / resource / namespace / name`, `✱` = any).

### Approval lifecycle

| Current status | approve | deny |
|---|---|---|
| `pending` | initial approval (any duration except `now`) | → `denied` |
| `approved` | updates `expires_at`; `duration=now` → immediately `expired` | 409 |
| `denied` | 409 | 409 - terminal |
| `expired` | 409 | 409 - terminal |

`denied` is terminal - delete the record and let the next block create a fresh `pending` record if re-approval is needed.

### Automatic expiry and cleanup

**Lazy expiry on read** - when the approved list is fetched (agent sync, `reach approvals list`), any record with `expires_at ≤ now` is immediately marked `expired`.

**Hourly sweep** - the scheduler marks all `approved` records with `expires_at < now` as `expired` in bulk.

**Daily cleanup** - at midnight UTC the scheduler deletes `denied` and `expired` approval records older than `APPROVAL_RETENTION_DAYS` (default 7), and terminal job records older than `JOB_RETENTION_DAYS` (default 7).

Deleting an approved record immediately removes the command from the agent's allowed list on the next sync.

For automation see the approvals endpoints in [API.md](API.md).

---

## How tokens work

Three token types - none stored raw, only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in the database:

| Token | Prefix | Used by | Purpose |
|---|---|---|---|
| `install_` | install token | Agent (once) | One-time claim to register the agent |
| `agent_` | agent token | Agent (ongoing) | Poll for jobs, post results, heartbeat |
| `tok_` | API token | CLI / MCP | Create jobs, read results, list agents |

API tokens are named, per-user credentials created in the tenant console under **API Tokens → New token**. Each person gets their own, and revoking one doesn't affect anyone else. By default all users in a tenant see all agents, but access can be restricted per user - see [Per-user agent access](#per-user-agent-access).

Users authenticate to the tenant console with a username and password (separate from API tokens). API tokens are only for CLI and MCP server use.

---

## Agent lifecycle

```
   CREATED
      │  claim
      ▼
   ACTIVE  ◄──── sync resumes ────┐
      │                           │
      │  heartbeat gap            │
      ▼                           │
   INACTIVE ──────────────────────┘
      │
      │  revoke   (or directly from ACTIVE)
      ▼
   REVOKED ──── reissue install token ────►  back to CREATED
      │
      │  delete
      ▼
   DELETED ──── remove ────►  [gone]
```

An agent starts as `CREATED`. On first run it calls `POST /agent/claim` with the install token, transitions to `ACTIVE`, and receives a permanent `agent_token`. The install token is then cleared from disk.

- **CREATED** - registered, install token valid for 24 hours. Never claimed.
- **ACTIVE** - claimed and syncing normally.
- **INACTIVE** - missed heartbeats. Auto-recovers to ACTIVE on next successful sync.
- **REVOKED** - access cut. Sync returns 403. Removed from all user access lists. Can be reset to CREATED via `POST /tenant/agents/{id}/reissue-install-token`.
- **DELETED** - soft-deleted. Record stays in the database. Cannot sync or be reissued. Hidden from the user-facing endpoints (`GET /agents`, `GET /agents/{id}` return 404); still actionable by tenant admins so the remove step can be completed.
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

#### Tenant-initiated rotation

To rotate an agent token out-of-band (e.g. suspected credential exposure) without disconnecting the agent, go to the **tenant console → Agents → [agent] → Request rotation**.

This sets a `rotation_requested` flag on the agent record. On its next sync the agent self-rotates and the flag is cleared. The agent remains connected throughout - no lockout window.

Only ACTIVE and INACTIVE agents can be flagged for rotation. To confirm completion, check that `token_issued_at` has advanced in the agent detail view.

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
  --api-url ... --install-token ... \
  --yes
```

To skip service management in a non-interactive install, add `--no-grant-service-mgmt` alongside `--yes`:

```bash
curl -fsSL .../install.sh | sudo bash -s -- \
  --api-url ... --install-token ... \
  --yes --no-grant-service-mgmt
```

The install command returned by `POST /tenant/agents` always includes `--yes`. Pass `"grant_service_mgmt": false` in the request body to also include `--no-grant-service-mgmt` in the generated command.

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
  --api-url ... --install-token ... \
  --yes --grant-docker
```

To include `--grant-docker` in the install command from the console, enable **Grant Docker access** when creating the agent.

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

## Agent configuration (environment variables)

The agent reads a few optional environment variables. The installer sets the timeout and output cap to their defaults in the service definition, so you only touch these if you want to override them.

| Variable | Default | Description |
|---|---|---|
| `REACH_COMMAND_TIMEOUT_SECONDS` | `60` | Max wall-clock time for a single command before the agent kills it and returns a timeout result. |
| `REACH_MAX_OUTPUT_BYTES` | `50000` | Max bytes of `stdout`/`stderr` captured per command. Output beyond this is truncated. |
| `REACH_CONFIG_PATH` | `/etc/reach-agent/config.json` | Path to the agent config file. Mainly for local development; the installer manages this path for you. |
| `REACH_METRICS_ADDR` | _(unset)_ | Opt-in: serve Prometheus `/metrics` on this address. **Unset by default** - the agent otherwise opens no inbound port. On a host there is no NetworkPolicy to contain it, so bind to loopback (`127.0.0.1:9090`) and scrape with a co-located collector. On Kubernetes the Helm chart wires this for you (`metrics.enabled=true`) with a Service, ServiceMonitor, and NetworkPolicy. See [SECURITY.md → Optional metrics endpoint](SECURITY.md#optional-metrics-endpoint). |

To change the timeout or output cap on an installed agent, edit the service definition and restart:

**Linux (systemd):** edit the `Environment=` lines in `/etc/systemd/system/reach-agent.service`, then:
```bash
sudo systemctl daemon-reload
sudo systemctl restart reach-agent
```

**macOS (background mode):** edit the `EnvironmentVariables` dict in `/Library/LaunchDaemons/com.reach-agent.plist`, then:
```bash
sudo launchctl unload /Library/LaunchDaemons/com.reach-agent.plist
sudo launchctl load /Library/LaunchDaemons/com.reach-agent.plist
```

These are agent-side limits enforced on the remote machine, independent of the backend.

---

## Database schema

### AWS (DynamoDB)

| Table | Key | GSIs | Purpose |
|---|---|---|---|
| `reach-agents` | `agent_id` | `tenant-index` (tenant_id) | Agent records, status, token hash, fingerprint, mode |
| `reach-tenants` | `tenant_id` | - | Tenant records |
| `reach-users` | `user_id` | `token-hash-index` (token_hash), `tenant-index` (tenant_id) | User records, token hashes, per-user agent access lists |
| `reach-jobs` | `job_id` | `agent-status-index` (agent_id, status), `tenant-history-index` (tenant_id, created_at) | Job queue and results; terminal records deleted by the daily heartbeat sweep after `JOB_RETENTION_DAYS` (default 7) |
| `reach-approvals` | `approval_id` | `agent-approvals-index` (agent_id, created_at), `tenant-approvals-index` (tenant_id, created_at) | Approval records: `pending`, `approved` (with optional `expires_at`), `denied`, `expired` |

All tables use `DeletionPolicy: Retain` - safe to redeploy the stack without losing data.

### Docker / FastAPI (PostgreSQL)

Tables (`agents`, `tenants`, `users`, `jobs`, `approvals`) are managed via Alembic migrations. On startup the container runs `alembic upgrade head` automatically - no manual SQL or schema setup needed. Upgrades that include schema changes are applied on the next container restart.

Notable columns on the `agents` table:

| Column | Type | Description |
|---|---|---|
| `agent_token_hash` | string | HMAC-SHA256 of the current agent token |
| `token_issued_at` | string | ISO timestamp of when the current agent token was issued (claim or rotation) |
| `rotation_requested` | boolean | Set by `POST /tenant/agents/{id}/request-rotation`; cleared automatically when the agent self-rotates |
| `machine_fingerprint` | string | SHA-256 of `machine-id + install_id`; token replay from a different machine is rejected |
| `active_until` | integer | Unix timestamp until which the agent is considered active (extended by `reach exec`) |

Key columns on the `approvals` table:

| Column | Type | Description |
|---|---|---|
| `approval_id` | string | Primary key (`appr_xxx`) |
| `agent_id` | string | Which agent the command was blocked on |
| `tenant_id` | string | Tenant isolation |
| `command` | text | The exact command that was blocked |
| `requested_by` | string | `user_id` of the submitter |
| `job_id` | string | The job that triggered this record |
| `status` | string | `pending` / `approved` / `denied` / `expired` |
| `expires_at` | string | ISO timestamp; null means permanent; set when approval is time-limited or instantly expired via `duration=now` |
| `reviewed_at` | string | When the admin acted |
| `reviewed_by` | string | Who reviewed it |

---

## Policy enforcement

Commands are evaluated server-side before being queued. The agent never sees a blocked command.

### Always blocked (all modes)

These are rejected regardless of the agent's policy mode:

| Command | Reason |
|---|---|
| `rm -rf /`, `rm --no-preserve-root` | Recursive root filesystem delete |
| `mkfs`, `wipefs` | Filesystem format / wipe |
| `dd if=` | Raw disk write |
| `shred /dev/` | Raw device destruction |
| Fork bomb (`: () { :|: & }`) | Process exhaustion |
| `docker run --privileged`, `docker run --pid=host`, `docker run --network=host` | Container host escape |
| `nsenter --target 1` | Namespace / host escape |
| `chroot /` | Chroot host escape |
| `kubectl run --privileged` | Privileged pod escape |
| `env \| curl` | Credential exfiltration |
| `/dev/tcp/`, `/dev/udp/`, `nc -e`, `ncat -e`, `socat exec:` | Reverse shell |

### Blocked in readonly mode

In addition to the always-blocked list, readonly mode also blocks:

| Category | Examples |
|---|---|
| File writes / deletes | `rm`, `mv`, `chmod`, `chown`, `truncate`, `shred`, `tee`, `sed -i`, `>` redirect |
| Process control | `kill`, `killall`, `pkill` |
| System power / init | `reboot`, `shutdown`, `poweroff`, `halt`, `init 0`, `init 6`, `systemctl reboot/poweroff/halt` |
| Service management | `systemctl start/stop/restart`, `service start/stop` |
| Containers | `docker run/stop/rm/pull/build`, `docker-compose up/down`, `kubectl apply/delete/exec` |
| Package managers | `apt install`, `yum`, `dnf`, `pacman`, `apk`, `snap`, `flatpak`, `brew install`, `pip install`, `npm install`, `yarn add`, `gem install`, `cargo install` |
| File download | `wget`, `curl -o` |
| Disk / filesystem | `dd`, `mkfs`, `fdisk`, `parted`, `gdisk`, `mount`, `umount` |
| Firewall | `iptables`, `ip6tables`, `ufw allow/deny/enable/disable` |
| User management | `useradd`, `userdel`, `usermod`, `groupadd`, `groupdel`, `passwd`, `su` |
| Scheduled jobs | `crontab` |
| Privilege escalation | `sudo` |
| IaC destroy | `terraform destroy`, `pulumi destroy`, `cdk destroy` |
| Cloud destructive | `aws ec2 terminate-instances`, `aws rds delete-db-instance`, `aws s3 rb --force`, `gcloud instances delete`, `az vm delete` |

### Approved mode

Read commands always run - approved mode only gates write and destructive operations (anything that would be blocked in readonly mode).

Write commands are checked against the agent's approved list. If the command matches (exact match or starts-with prefix), it runs normally. If not:

- **Linux** - runs under Landlock sandbox. If the kernel blocks the write, the agent returns `blocked=true`.
- **macOS** - uses the server-supplied `is_write` flag. If the command is a write and not approved, the agent refuses it immediately without running it.

In both cases the backend creates a pending approval record. Admins and operators review it in the **tenant console → Approvals**, then approve or deny. Once approved, the command prefix is included in the approved list on the next sync and runs without restriction. See [Approvals](#approvals).

The match is prefix-based with a word boundary: approving `docker logs` permits `docker logs myapp --tail 100` but not `docker rm myapp`.

> **Kubernetes agents work differently.** The blocklist tables above are the regex classifier used for **host** agents. A `type=k8s` agent has no shell, so write-ness is classified from the `kubectl` **verb** (default-deny: anything that isn't a known read verb - including `exec`/`cp`/`port-forward` and unknown verbs - is a write), and the policy decision is made **at the backend on submission**, not on the agent. "Double verbs" whose sub-subcommand changes read/write are classified on the pair - e.g. `rollout status`/`history` and `auth can-i` are reads, while `rollout restart`/`undo` and `auth reconcile` are writes - and cluster-inert utilities (`kustomize`, `options`, `plugin`, `config`, …) count as reads. In approved mode an unapproved k8s write never dispatches: it is recorded as a `REJECTED` job and a pending approval is raised. There is no Landlock or `is_write`-flag step on a k8s agent. See [ARCHITECTURE.md → How `kubectl` commands are classified](ARCHITECTURE.md#kubernetes-agents) for the full model.

---

## API reference

See [API.md](API.md) for the complete endpoint reference, rate limits, and automation examples.

---

## Security

- Tokens are never stored raw - only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in the database
- Install token is one-time use and expires after 24 hours
- Install token is cleared from disk after successful claim
- Agent token is bound to a machine fingerprint - replayed tokens from another machine are rejected
- Agent token automatically rotates every 30 days via self-service rotation (no lockout window)
- Config files written with `0600` permissions
- Commands are checked against a policy blocklist before execution
- Command timeout: 60 seconds (default; configurable via `REACH_COMMAND_TIMEOUT_SECONDS` - see [Agent configuration](#agent-configuration-environment-variables))
- Max output: 50 KB per command (default; configurable via `REACH_MAX_OUTPUT_BYTES`)
