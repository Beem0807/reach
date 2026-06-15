# Self-Hosting reach

Deploy and operate your own reach backend. The CLI and agent are separate — they just need your API URL.

---

## Deployment options

| Option | Runtime | Database | Best for |
|---|---|---|---|
| [AWS Lambda](#option-1-aws-lambda) | Lambda | DynamoDB | Small teams, low cost, AWS-native |
| [Docker / FastAPI](#option-2-docker--fastapi) | FastAPI | PostgreSQL | Any cloud, self-hosted VMs, k8s |

### Why DynamoDB with Lambda, and PostgreSQL with Docker?

Lambda functions are stateless and short-lived — each invocation starts fresh with no persistent connections. DynamoDB is a natural fit because it's serverless, has no connection to maintain, and scales to zero when idle. The combination keeps costs near zero for small teams (pay only per request, no always-on database instance).

PostgreSQL with a persistent server (FastAPI in Docker or k8s) is the right choice when you want to run anywhere — any cloud, a VPS, or on-prem — without being tied to AWS. FastAPI keeps a connection pool open for the lifetime of the process, which PostgreSQL handles well. DynamoDB would require AWS credentials and doesn't make sense outside of AWS.

Lambda + PostgreSQL is deliberately not supported. Lambda's ephemeral connections exhaust PostgreSQL's connection limit quickly at scale, and solving that requires RDS Proxy — adding cost and complexity that defeats the purpose of the low-cost Lambda option.

---

## Option 1: AWS Lambda

### Prerequisites

- AWS CLI configured (`aws sts get-caller-identity`)

### Deploy

**1. Generate a TOKEN_PEPPER — save this, you need it for future upgrades:**

```bash
export TOKEN_PEPPER=$(openssl rand -hex 32)
echo $TOKEN_PEPPER
```

**2. Deploy:**

```bash
aws cloudformation create-stack \
  --stack-name reach-platform \
  --template-url https://reach-releases.s3.amazonaws.com/lambda/latest/template.yaml \
  --parameters ParameterKey=TokenPepper,ParameterValue="$TOKEN_PEPPER" \
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
  --parameters ParameterKey=TokenPepper,UsePreviousValue=true \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND
```

Replace `v1.1.0` with the target version. DynamoDB tables are retained across upgrades.

---

## Option 2: Docker / FastAPI

### Prerequisites

- Docker
- A PostgreSQL database (any cloud managed DB or self-hosted)

### Deploy

**1. Pull and run:**

```bash
docker run -d \
  -p 8000:8000 \
  -e TOKEN_PEPPER="<your-pepper>" \
  -e DATABASE_URL="postgresql://user:pass@host:5432/reach" \
  nabeemdev/reach:latest
```

Tables are created automatically on first startup. The image supports `linux/amd64` and `linux/arm64` — works on AWS Graviton, Raspberry Pi, and Apple Silicon without extra flags.

**2. Put a reverse proxy in front (nginx, Caddy, ALB, etc.) for TLS.**

---

## Bootstrap

After deploying, call the bootstrap endpoint to create your first tenant token and agent record. It returns ready-to-paste install commands.

```bash
curl -s -X POST "$API_URL/admin/bootstrap" \
  -H "Authorization: Bearer $TOKEN_PEPPER" \
  -H "Content-Type: application/json" \
  -d '{"hostname": "my-machine"}' | python3 -m json.tool
```

Response:

```json
{
  "tenant_id": "tenant_xxxxx",
  "agent_id": "agent_xxxxx",
  "tenant_token": "tok_xxx...",
  "install_token": "install_xxx...",
  "install_token_expires_at": "2026-06-17T12:00:00+00:00",
  "mode": "wild",
  "commands": {
    "cli_login": "reach login --api-url \"...\" --token \"tok_xxx...\"",
    "cli_use": "reach use agent_xxxxx",
    "agent_linux": "curl -fsSL .../install.sh | sudo bash -s -- ...",
    "agent_mac_arm": "...",
    "agent_mac_intel": "..."
  }
}
```

**Add an agent to an existing tenant:**

```bash
curl -s -X POST "$API_URL/admin/bootstrap" \
  -H "Authorization: Bearer $TOKEN_PEPPER" \
  -H "Content-Type: application/json" \
  -d '{"hostname": "second-machine", "tenant_id": "tenant_xxxxx"}' | python3 -m json.tool
```

No new `tenant_token` is issued — the existing tenant's token remains valid.

---

## Policy management

Policies are managed via the admin API, authenticated with your `TOKEN_PEPPER`.

**View policy:**

```bash
curl -s "$API_URL/admin/agents/agent_xxxxx/policy" \
  -H "Authorization: Bearer $TOKEN_PEPPER" | python3 -m json.tool
```

**Set mode** (`wild` / `readonly` / `approved`):

```bash
curl -s -X PUT "$API_URL/admin/agents/agent_xxxxx/policy/mode" \
  -H "Authorization: Bearer $TOKEN_PEPPER" \
  -H "Content-Type: application/json" \
  -d '{"mode": "approved"}'
```

**Add approved commands:**

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/policy/commands" \
  -H "Authorization: Bearer $TOKEN_PEPPER" \
  -H "Content-Type: application/json" \
  -d '{"commands": ["docker ps", "git status"]}'
```

**Remove approved commands:**

```bash
curl -s -X DELETE "$API_URL/admin/agents/agent_xxxxx/policy/commands" \
  -H "Authorization: Bearer $TOKEN_PEPPER" \
  -H "Content-Type: application/json" \
  -d '{"commands": ["git status"]}'
```

---

## How tokens work

Three token types — none stored raw, only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in the database:

| Token | Prefix | Used by | Purpose |
|---|---|---|---|
| `install_` | install token | Agent (once) | One-time claim to register the agent |
| `agent_` | agent token | Agent (ongoing) | Poll for jobs, post results, heartbeat |
| `tok_` | tenant token | CLI | Create jobs, read results, list agents |

---

## Agent lifecycle

```
CREATED → ACTIVE → INACTIVE (heartbeat timeout) → ACTIVE (auto-reactivates on next sync)
```

An agent starts as `CREATED`. On first run it calls `POST /agent/claim` with the install token, transitions to `ACTIVE`, and receives a permanent `agent_token`. The install token is then cleared from disk.

The heartbeat checker runs every 5 minutes (EventBridge on Lambda, APScheduler on FastAPI) and marks agents `INACTIVE` if no sync has been received in the last 5 minutes. The agent auto-reactivates on its next successful sync.

### Adaptive polling

The backend tells the agent how fast to poll via `next_poll_seconds`:
- `5s` — active window (job created in last 120 seconds)
- `30s` — idle

---

## API reference

### Agent endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/agent/claim` | install token | One-time agent registration |
| `POST` | `/agent/sync` | agent token | Poll for jobs + record heartbeat |
| `POST` | `/agent/jobs/{id}/result` | agent token | Post command result |

### Tenant (CLI) endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/jobs` | tenant token | Create a job |
| `GET` | `/jobs` | tenant token | List recent jobs (`?agent_id=` `?limit=`) |
| `GET` | `/jobs/{id}` | tenant token | Get job result and output |
| `GET` | `/agents` | tenant token | List all agents for tenant |
| `GET` | `/agents/{id}` | tenant token | Get agent details and policy |

### Admin endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/admin/bootstrap` | TOKEN_PEPPER | Create tenant token + agent record |
| `GET` | `/admin/agents/{id}/policy` | TOKEN_PEPPER | Get agent policy |
| `PUT` | `/admin/agents/{id}/policy/mode` | TOKEN_PEPPER | Set policy mode |
| `POST` | `/admin/agents/{id}/policy/commands` | TOKEN_PEPPER | Add approved commands |
| `DELETE` | `/admin/agents/{id}/policy/commands` | TOKEN_PEPPER | Remove approved commands |

---

## Database schema

### AWS (DynamoDB)

| Table | Key | Purpose |
|---|---|---|
| `reach-agents` | `agent_id` | Agent records, status, token hash, fingerprint |
| `reach-tenant-tokens` | `token_hash` | Tenant credentials |
| `reach-jobs` | `job_id` | Job queue and results (TTL: 7 days) |

All tables use `DeletionPolicy: Retain` — safe to redeploy the stack without losing data.

### Docker / FastAPI (PostgreSQL)

Tables (`agents`, `tenant_tokens`, `jobs`) are created automatically on first startup via SQLAlchemy `create_all`.

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

Approved commands are managed via the admin API — the CLI can view them but not change them.

---

## Security

- Tokens are never stored raw — only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in the database
- Install token is one-time use and expires after 24 hours
- Install token is cleared from disk after successful claim
- Agent token is bound to a machine fingerprint — replayed tokens from another machine are rejected
- Config files written with `0600` permissions
- Commands are checked against a policy blocklist before execution
- Command timeout: 60 seconds
- Max output: 50 KB per command
