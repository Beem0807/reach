# Self-Hosting reach

Deploy and operate your own reach backend on AWS.

---

## Architecture

```
reach CLI
    ↓
AWS API Gateway + Lambda
    ↓
DynamoDB (jobs queue)
    ↓
reach-agent (Go, running on any machine)
```

The agent polls the backend for jobs. No inbound ports or firewall changes needed on the remote machine.

---

## Repository structure

```
reach/
├── backend/
│   ├── app.py              # Lambda handler (API routes + heartbeat checker)
│   ├── requirements.txt
│   └── template.yaml       # SAM template (API Gateway + Lambda + DynamoDB)
├── cli/
│   ├── pyproject.toml
│   └── reach/
│       ├── main.py         # CLI commands
│       ├── config.py       # ~/.reach/config.json + alias helpers
│       └── client.py       # HTTP client
├── agent/
│   ├── main.go             # Go agent
│   ├── go.mod
│   ├── install.sh          # Linux installer (systemd)
│   └── reach-agent.service
└── scripts/
    ├── bootstrap.py        # Create tenant token + agent record in DynamoDB
    ├── release_agent.sh    # Build + upload agent binaries to S3
    └── release_cli.sh      # Build + upload CLI wheel to S3
```

---

## Prerequisites

- AWS CLI configured (`aws sts get-caller-identity`)
- AWS SAM CLI (`brew install aws-sam-cli`)
- Go 1.22+
- Python 3.9+

---

## Deploy the backend

**1. Generate a TOKEN_PEPPER and save it — you need it for every bootstrap:**

```bash
openssl rand -hex 32
```

**2. Create the S3 bucket for releases:**

```bash
aws s3 mb s3://reach-releases --region us-east-1

aws s3api put-public-access-block \
  --bucket reach-releases \
  --public-access-block-configuration \
    "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

aws s3api put-bucket-policy --bucket reach-releases --policy '{
  "Version": "2012-10-17",
  "Statement": [{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::reach-releases/*"}]
}'
```

**3. Deploy the Lambda + API Gateway + DynamoDB:**

```bash
cd backend
sam build && sam deploy --guided
```

Prompts:
```
Stack Name:              reach-platform
AWS Region:              us-east-1
Parameter TokenPepper:   <paste your pepper>
Confirm changes:         n
Allow IAM role creation: y
Disable rollback:        n
No authentication x6:    y (all six)
Save to config file:     y
```

Future deploys: `sam build && sam deploy` (no prompts).

**4. Get your API URL:**

```bash
aws cloudformation describe-stacks \
  --stack-name reach-platform \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text
```

**5. Release agent binaries and CLI:**

```bash
./scripts/release_agent.sh
./scripts/release_cli.sh
```

---

## Bootstrap

Creates a tenant token + agent record in DynamoDB and prints ready-to-paste install commands.

```bash
python scripts/bootstrap.py \
  --pepper  "<your-pepper>" \
  --api-url "https://<api-id>.execute-api.us-east-1.amazonaws.com"
```

Output:

```
============================================================
TENANT ID:     tenant_xxxxx
AGENT ID:      agent_xxxxx
Install token expires: 2024-01-02T12:00:00+00:00
============================================================

── Linux ──────────────────────────────────────────────────
curl -fsSL https://reach-releases.s3.amazonaws.com/install.sh | sudo bash -s -- \
  --api-url       "https://..." \
  --agent-id      "agent_xxx" \
  --install-token "install_xxx"

── Mac (Apple Silicon) ────────────────────────────────────
...

── CLI setup (your machine) ───────────────────────────────
pip install https://reach-releases.s3.amazonaws.com/reach-0.1.0-py3-none-any.whl
reach login --api-url "https://..." --token "tok_xxx..."
reach use agent_xxx
============================================================
```

**Add an agent to an existing tenant:**

```bash
python scripts/bootstrap.py \
  --pepper    "<pepper>" \
  --api-url   "https://..." \
  --tenant-id "tenant_xxxxx"
```

---

## How tokens work

Three token types — none stored raw, only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in DynamoDB:

| Token | Prefix | Used by | Purpose |
|---|---|---|---|
| `install_token` | `install_` | Go agent (once) | One-time claim to register the agent |
| `agent_token` | `agent_` | Go agent (ongoing) | Poll for jobs, post results, heartbeat |
| `tenant_token` | `tok_` | CLI | Create jobs, read results, list agents |

---

## Agent lifecycle

```
CREATED → ACTIVE → INACTIVE (heartbeat timeout) → ACTIVE (auto-reactivates on next sync)
                 → SUSPICIOUS / DISABLED
```

An agent starts as `CREATED`. On first run it calls `POST /agent/claim` with the install token, which transitions it to `ACTIVE` and returns a permanent `agent_token`. The install token is then cleared from disk.

A heartbeat Lambda runs every 5 minutes and marks agents `INACTIVE` if no sync has been received in the last 5 minutes. The agent auto-reactivates to `ACTIVE` on its next successful sync.

### Adaptive polling

The backend tells the agent how fast to poll via `next_poll_seconds`:
- `5s` — active window (job created in last 120 seconds)
- `30s` — idle

---

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/agent/claim` | `install_token` | One-time agent registration |
| `POST` | `/agent/sync` | `agent_token` | Poll for jobs + record heartbeat |
| `POST` | `/agent/jobs/{id}/result` | `agent_token` | Post command result |
| `POST` | `/jobs` | `tenant_token` | Create a job |
| `GET` | `/jobs` | `tenant_token` | List recent jobs (supports `?agent_id=` and `?limit=`) |
| `GET` | `/jobs/{id}` | `tenant_token` | Get job result and output |
| `GET` | `/agents` | `tenant_token` | List all agents for tenant |
| `GET` | `/agents/{id}` | `tenant_token` | Get agent details including policy |

---

## DynamoDB tables

| Table | Key | Purpose |
|---|---|---|
| `reach-agents` | `agent_id` | Agent records, status, token hash, fingerprint, heartbeat |
| `reach-tenant-tokens` | `token_hash` | Tenant credentials |
| `reach-jobs` | `job_id` | Job queue and results (TTL: 1 hour) |

All tables use `DeletionPolicy: Retain` — safe to delete and redeploy the stack without losing data.

---

## Security

- Tokens are never stored raw — only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes in DynamoDB
- Install token is one-time use and expires after 24 hours
- Install token is cleared from disk after successful claim
- Agent token is bound to a machine fingerprint — replayed tokens from another machine are rejected
- Config files written with `0600` permissions (agent and CLI)
- Commands are checked against a blocklist before execution (`rm -rf /`, `mkfs`, `shutdown`, etc.)
- Command timeout: 60 seconds
- Max output: 50 KB per command
