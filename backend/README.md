# Reach Backend

The backend is Reach's **control plane**: it authenticates users and agents, stores state,
classifies every command against the policy, hands jobs to agents (which poll for them), and records
the audit trail. It's a **Python** codebase that runs in two shapes from the **same handler code**:

| | Container (Docker / Kubernetes) | Serverless (AWS Lambda) |
|---|---|---|
| Runtime | FastAPI (uvicorn) behind nginx / Ingress | API Gateway + per-route Lambda functions |
| Entry point | `adapters/fastapi/main.py` | `handlers/*.py` (wired in `deploy/lambda/template.yaml`) |
| Storage | PostgreSQL (default), or DynamoDB on AWS | DynamoDB |
| Scheduler | APScheduler (in-process) | EventBridge |

For how the backend fits the whole system see [ARCHITECTURE.md](../ARCHITECTURE.md); for the HTTP
surface see [API.md](../API.md); to deploy it see [SELF_HOSTING.md](../SELF_HOSTING.md); the security
model is in [SECURITY.md](../SECURITY.md) and policy/approvals in [POLICIES.md](../POLICIES.md).

---

## Two runtimes, one set of handlers

Every route is a plain function - `handle_*(body, token_payload, …) -> {"statusCode", "body"}` - in
`handlers/`. Both runtimes call the **same** functions, so behavior can't diverge between the Lambda
and container deployments:

- **Lambda** wires one function per route in [`deploy/lambda/template.yaml`](../deploy/lambda/template.yaml).
  A thin per-route entry point parses the API Gateway event and calls the shared `handle_*`. An
  `ANY /{proxy+}` catch-all (`handlers/not_found.py`) returns a content-negotiated 404 for unknown
  paths (a friendly HTML page for browsers, JSON for API clients).
- **FastAPI** ([`adapters/fastapi/main.py`](adapters/fastapi/main.py)) declares the same routes,
  each calling the same `handle_*`, and additionally serves the console UI (`/ui`), the
  `GET / → /ui/` redirect, rate limiting, and the same content-negotiated 404
  (`shared/error_page.py`, shared with the Lambda catch-all).

They can't silently drift: **`tests/test_adapter_parity.py`** asserts the FastAPI route set and the
Lambda template stay in lock-step. A route that is intentionally Lambda-only (the `{proxy+}`
catch-all) is tagged `LAMBDA_ONLY`; FastAPI handles that case with its 404 exception handler instead.

---

## Two storage backends, one interface

`shared/store.py` selects the repo implementation from the `STORAGE_BACKEND` env var and hands
handlers ready-bound repo objects - **handlers never import `sql` or `dynamo` directly**, so a
handler is identical on both backends.

| | `STORAGE_BACKEND=postgres` (default) | `STORAGE_BACKEND=dynamo` |
|---|---|---|
| Implementation | `shared/repos/sql.py` (SQLAlchemy) | `shared/repos/dynamo.py` (boto3) |
| Schema | Alembic migrations (`alembic/`) | `shared/dynamo_schema.py` (canonical) + `shared/dynamo_bootstrap.py` (idempotent creation) |

`shared/repos/base.py` defines the common interface both implement. Two DynamoDB-specific gotchas the
repos handle so handlers don't have to: `None` values are stripped before `put_item` (DynamoDB
rejects a NULL for a key/GSI attribute), and the k8s-permission snapshot is truncated to fit the
400 KB item cap. The *rationale* for the split (why Lambda pairs with DynamoDB and the container with
Postgres) is in [ARCHITECTURE.md → Storage backend split](../ARCHITECTURE.md#storage-backend-split);
how each backend scopes listings to a tenant (and the by-id ownership check) is the isolation
guarantee in [SECURITY.md → Tenant isolation](../SECURITY.md#security-design).

---

## Local development

The repo-root [`docker-compose.yml`](../docker-compose.yml) brings up the full container stack
(Postgres + backend + nginx) on <http://localhost:8080>:

```bash
docker compose --profile seed up -d --build   # + dummy data (tenants alpha/beta/gamma; login == username)
docker compose --profile seed down -v          # tear down, wiping the DB volume
```

Seed logins and details are in the `docker-compose.yml` header. The seed script
(`scripts/seed.py`) hashes passwords with the same scrypt scheme as
[`shared/password.py`](shared/password.py) - keep the two in sync.

### Tests

The suite runs on SQLite (in-memory) by default, so no DB or AWS is needed; DynamoDB paths use
`moto`. `conftest.py` sets the required env (`TOKEN_PEPPER`, `STORAGE_BACKEND=postgres`,
`DATABASE_URL=sqlite:///:memory:`, …). Run it in a clean interpreter:

```bash
docker run --rm -v "$PWD":/app -w /app/backend -e AWS_DEFAULT_REGION=us-east-1 python:3.12-slim \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
```

Notable suites: `test_adapter_parity.py` (runtime parity), `test_tenant_isolation*.py` (cross-tenant
denial, SQL + DynamoDB), `test_enforcement_parity.py` (backend↔agent policy parity),
`test_policy_fuzz.py` (property/adversarial policy tests), `test_cloudfront_404_parity.py`
(the CloudFront 404 stays identical to `shared/error_page.py`).

---

## Source map

| Path | What |
|---|---|
| `handlers/` | one module per API area; each exposes `handle_*` (shared) + a Lambda entry point. `not_found.py` is the `{proxy+}` catch-all |
| `adapters/fastapi/main.py` | the container runtime: routes → `handle_*`, UI serving, rate limiting, content-negotiated 404 |
| `adapters/fastapi/metrics.py` | Prometheus `reach_backend_*` metrics |
| `shared/store.py` | binds the repo implementation from `STORAGE_BACKEND` |
| `shared/repos/{base,sql,dynamo}.py` | storage interface + PostgreSQL / DynamoDB implementations |
| `shared/dynamo_schema.py` / `dynamo_bootstrap.py` | canonical DynamoDB schema + idempotent table creation |
| `shared/policy.py` | command classification, blocklist, structured host-rule + k8s-rule matching (mirrored by the agent) |
| `shared/auth.py` / `tenant_auth.py` / `admin_auth.py` | token hashing/verification, tenant session + admin session tokens |
| `shared/password.py` | scrypt password hashing |
| `shared/access.py` | per-user agent/fleet scoping (`can_access_agent` / `can_write_agent`) |
| `shared/{redact,error_page,response,exceptions,settings,mode,fanout,waves,tags,versions,explain,audit}.py` | secret redaction, the 404 page, response helpers, and other cross-cutting helpers |
| `alembic/` | PostgreSQL migrations (`alembic upgrade head`) |
| `tests/` | pytest suite (SQLite + moto); see **Tests** above |
