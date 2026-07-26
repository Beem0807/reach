# reach

![Version: 0.1.0](https://img.shields.io/badge/Version-0.1.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.1.0](https://img.shields.io/badge/AppVersion-0.1.0-informational?style=flat-square)

Reach backend (control plane) for Kubernetes - the API, web console, and Postgres-backed store that AI agents and operators connect to. Serves the REST API and the bundled web UI from a single image; runs database migrations automatically on install/upgrade.

> This is the **backend / control plane** - the REST API and the bundled web
> console, served from a single image (`Dockerfile`, built + pushed by
> `scripts/release_backend.sh` as `nabeemdev/reach`). For the in-cluster **agent**,
> see the separate [`reach-agent`](../reach-agent) chart.

## What it deploys

- **API + console in one image.** The backend serves the REST API and the web UI
  (at `/ui`) on container port `8000`; an Ingress (or port-forward) is all you need
  to reach both.
- **Self-contained by default.** Bundled **Postgres** (StatefulSet + PVC) and
  **Redis/Valkey** (rate-limit store) are enabled out of the box, so `helm install`
  gives you a working stack with no external dependencies. Point at your own and the
  bundled ones are automatically skipped (see below).
- **Migrations run themselves, safely.** An initContainer runs the schema step before each pod
  serves; it waits for the DB and, on Postgres, serializes concurrent replicas with an advisory
  lock (see [Migrations](#migrations)).

## Install

Self-contained (bundles Postgres + Redis) - this is all you need. From the published Helm repo
(pin with `--version`; the image tag comes from the chart's `appVersion`):

```bash
helm repo add reach https://reach-releases.s3.amazonaws.com/charts/reach --force-update
helm install reach reach/reach -n reach --create-namespace \
  --set config.tokenPepper=$(openssl rand -hex 32) \
  --set config.sessionSigningKey=$(openssl rand -hex 32) \
  --set config.adminPassword=$(openssl rand -hex 16)
```

From a cloned repo, install the local chart instead: `helm install reach deploy/helm/reach …`.

The console + API are on Service port `80` → container `8000`. With no Ingress,
port-forward and open the console:

```bash
kubectl port-forward -n reach svc/reach 8080:80   # then http://localhost:8080/ui/
```

Log in to the platform-admin console (`/ui/` → admin) with `ADMIN_PASSWORD`, create
a tenant, then use the tenant console.

## Bring your own Postgres / Redis

Supplying an external URL makes the chart **skip** the matching bundled dependency -
no flag toggling needed:

```bash
# External Postgres (bundled PG is skipped):
--set config.databaseUrl='postgresql://user:pass@my-postgres:5432/reach'

# External Redis/Valkey for shared rate limiting (bundled Redis is skipped):
--set config.rateLimitStorageUri='redis://my-redis:6379'

# Turn a bundled dependency off explicitly:
--set postgresql.enabled=false   # you must then provide config.databaseUrl
--set redis.enabled=false        # rate limits fall back to in-process (per replica)
```

## How the datastore is chosen (precedence)

The **primary datastore** and the **rate-limit store** are selected independently. You never
have to toggle `postgresql.enabled`/`redis.enabled` yourself just to point at your own
infrastructure - supplying an external target automatically suppresses the matching bundled
dependency. The rules, highest precedence first:

**Primary datastore**

1. **DynamoDB** if `config.storageBackend=dynamo`.
2. else **external Postgres** if `config.databaseUrl` is set, **or** `config.existingSecret` is
   set (the chart trusts that Secret to carry `DATABASE_URL`).
3. else **bundled Postgres** if `postgresql.enabled=true` (the default).
4. else **install fails** with a clear error (see below).

So `config.storageBackend=dynamo`, `config.databaseUrl`, and `config.existingSecret` **each
suppress the bundled Postgres**, regardless of `postgresql.enabled`. The bundled StatefulSet,
Service, and Secret are simply not rendered.

**Rate-limit store** (orthogonal to the above - it is *not* the primary DB):

1. **External Redis** if `config.rateLimitStorageUri` is set.
2. else **bundled Redis** if `redis.enabled=true` (the default).
3. else **in-process, per-replica** counters (`redis.enabled=false` and no URI) - the limiter
   still works, but each replica counts independently. See [Rate-limit resilience](#rate-limit-resilience).

### What each combination does

| `storageBackend` | `postgresql.enabled` | `config.databaseUrl` / `existingSecret` | Result |
|---|---|---|---|
| `postgres` (default) | `true` (default) | neither | **Bundled Postgres** deployed; `DATABASE_URL` comes from its auto-generated Secret. |
| `postgres` | `true` | `databaseUrl` set | **External Postgres** used; **bundled PG skipped** even though `enabled=true`. |
| `postgres` | `true`/`false` | `existingSecret` set | **External** (from your Secret's `DATABASE_URL`); bundled PG skipped; chart Secret skipped. |
| `postgres` | `false` | neither | **Install fails fast** — nothing is deployed (see below). |
| `dynamo` | `true`/`false` | ignored | **DynamoDB** used; **bundled PG skipped**; no `DATABASE_URL` needed; migration runs `dynamo_bootstrap`. |

Redis is unaffected by any of the above: it stays bundled by default in every row unless you set
`config.rateLimitStorageUri` or `redis.enabled=false`.

### The one misconfiguration that is rejected

`storageBackend=postgres` **and** `postgresql.enabled=false` **and** no `config.databaseUrl` **and**
no `config.existingSecret` means "use Postgres, but there is no Postgres." The chart **fails at
render/install time** (nothing reaches the cluster) with:

```
A database is required: set config.databaseUrl, OR enable the bundled DB
(postgresql.enabled=true), OR use DynamoDB (config.storageBackend=dynamo),
OR provide config.existingSecret
```

> **Note - suppression is silent.** Setting `postgresql.enabled=true` *and* also providing
> `config.databaseUrl`/`existingSecret`/`storageBackend=dynamo` is accepted: the external/Dynamo
> choice wins and the bundled Postgres is quietly not deployed. This is deliberate (the default is
> "on", so requiring an extra `enabled=false` to bring your own DB would be a footgun) - but it
> means an unexpected external DB is not warned about. Double-check these values in CI/GitOps.

## Recommended configurations

| Goal | Set |
|---|---|
| **Dev / demo / self-contained** | defaults (bundled Postgres + bundled Redis) - nothing extra |
| **Managed Postgres (RDS/Cloud SQL)** | `config.databaseUrl` (or `config.existingSecret` with `DATABASE_URL`); bundled PG auto-off. Keep bundled Redis or set `config.rateLimitStorageUri`. |
| **Fully managed on EKS (no bundled stateful workloads)** | `config.storageBackend=dynamo` + IAM (IRSA/Pod Identity) + **`redis.enabled=false`** (or `config.rateLimitStorageUri` → ElastiCache). |
| **Single replica, no external deps** | defaults, or `redis.enabled=false` (per-process limits are correct at one replica). |

## DynamoDB backend (AWS/EKS)

Instead of Postgres, run against **DynamoDB** (AWS/EKS only). The chart then skips Postgres
entirely, drops the `DATABASE_URL` requirement, and the migration step runs `dynamo_bootstrap`
(creates the tables) instead of Alembic. The chart never holds AWS keys - grant the pod's
ServiceAccount DynamoDB access one of two ways.

The IAM role needs read/write on the `reach-*` tables (+ indexes) plus `CreateTable`/`DescribeTable`
for bootstrap - the exact policy JSON is in the project's `SELF_HOSTING.md` ("DynamoDB on AWS").

**IRSA** - annotate the ServiceAccount with the IAM role ARN (needs a cluster OIDC provider):

```bash
--set config.storageBackend=dynamo \
--set config.awsRegion=us-east-1 \
--set 'serviceAccount.annotations.eks\.amazonaws\.com/role-arn=arn:aws:iam::<acct>:role/reach-dynamo'
```

**EKS Pod Identity** - newer, no annotation. Install the *EKS Pod Identity Agent* addon, install
this chart with a stable SA name (`--set config.storageBackend=dynamo --set config.awsRegion=... --set serviceAccount.name=reach`),
then create the association out of band:

```bash
aws eks create-pod-identity-association --cluster-name <c> \
  --namespace <ns> --service-account reach \
  --role-arn arn:aws:iam::<acct>:role/reach-dynamo
```

Redis stays bundled for rate limiting (independent of the storage backend) unless you point at
an external one.

## Migrations

Schema setup runs in an **initContainer** before each pod serves, using the same storage-aware
logic as the image's entrypoint (`dynamo_bootstrap` for DynamoDB, else `alembic upgrade head`).
It waits for the DB, and on **Postgres** it holds a **session advisory lock** across
`alembic upgrade head`, so concurrent replicas *serialize* on schema DDL - not merely relying on
idempotency, which alone doesn't prevent two replicas racing on a fresh DB. The lock auto-releases
when the initContainer exits; a replica that waited then finds the schema already at head (a
no-op). DynamoDB table creation is naturally idempotent (retry loop). Set `migrations.enabled=false`
only if you migrate out of band.

## Rate-limit resilience

The rate-limit store (Redis) is **not** a hard dependency of the API. If the shared store is
unreachable, the backend transparently falls back to a **per-process in-memory** limiter (limits
still apply, but per replica rather than globally) and periodically probes the store, switching
back automatically when it recovers. If even that path errors, the limiter **fails open** (the
request proceeds) rather than returning `500`. In short: **a Redis outage degrades rate limiting
but never takes the API down.** The trade-off is availability over global exactness, which is the
right call for an anti-DoS limit backed by 256-bit tokens. With `redis.enabled=false` and no
`config.rateLimitStorageUri`, the limiter is in-memory from the start (correct for a single
replica; per-pod with several).

## Production notes

- **Secrets.** Prefer your own Secret + `config.existingSecret` over `--set` values. Required keys
  are **storage-aware**: always `TOKEN_PEPPER`, `SESSION_SIGNING_KEY`, `ADMIN_PASSWORD`; **plus
  `DATABASE_URL` only for Postgres** (`storageBackend=postgres`) - **DynamoDB needs no
  `DATABASE_URL`**; optionally `METRICS_TOKEN`. Setting `existingSecret` skips the chart Secret
  and the bundled Postgres (you bring the datastore). `TOKEN_PEPPER` must be **stable** - rotating
  it invalidates every token and login.
- **Bundled Postgres password.** With no explicit `postgresql.auth.password` and no
  `existingSecret`, the chart **auto-generates** a strong password on first install and reuses it
  on upgrades (read back from the Secret). Under GitOps tools that render with `helm template` and
  no cluster access (Argo CD), set an explicit password or use `existingSecret` to avoid churn.
- **Data durability.** The bundled Postgres is a single instance with a PVC - fine for dev/small
  installs, **not** HA. For production use a managed/replicated Postgres via `config.databaseUrl`
  (or DynamoDB on EKS). Switching the datastore **does not migrate data**: pointing at an external
  DB (or DynamoDB) leaves the bundled data on its PVC and the app starts against the new, empty
  store - dump/restore before cutting over. By design the PVC **outlives the release** - Kubernetes
  never garbage-collects a StatefulSet's `volumeClaimTemplate` PVC, so `helm uninstall` (or a
  datastore switch) **never destroys your data**, and a reinstall reattaches to `data-<release>-postgresql-0`
  and picks up where it left off. Reclaiming the disk is a deliberate, manual step: `kubectl delete
  pvc data-<release>-postgresql-0` only once you're sure the data is expendable.
- **Network isolation.** `--set networkPolicy.enabled=true` restricts the bundled Postgres/Redis
  to backend-only ingress and limits who can reach the backend (add your ingress controller /
  Prometheus via `networkPolicy.ingressFrom`). Requires a CNI that enforces NetworkPolicies.
- **Pod Security Admission.** Every workload (backend, bundled Postgres, bundled Redis, and the
  `helm test` pod) runs non-root with a seccomp profile, dropped capabilities, and no privilege
  escalation, so the chart installs cleanly in a namespace labelled
  `pod-security.kubernetes.io/enforce=restricted`.
- **Ingress.** `--set ingress.enabled=true` with your host/TLS/class. API and `/ui` share the host.
- **Value validation.** `values.schema.json` validates your values at `install`/`upgrade`/`template`/`lint`
  time - unknown keys (typos like `replicas` or `config.databseUrl`) and bad types/enums
  (`storageBackend: dynamodb`, a non-`Always/IfNotPresent/Never` `pullPolicy`) fail fast with a
  pointer to the offending path instead of being silently ignored.
- **Scaling.** Stateless backend - raise `replicaCount` or enable `autoscaling`. For correct
  multi-replica rate limiting keep Redis (bundled or external); otherwise limits are per-pod.
- **Metrics.** `/metrics` (Prometheus) on the same port; guard with `config.metricsToken` and/or a
  NetworkPolicy. Enable `metrics.serviceMonitor.enabled` with the Prometheus Operator.

## Upgrades

```bash
helm upgrade reach deploy/helm/reach -n reach --reuse-values
```

Migrations re-run automatically (idempotent, serialized on Postgres) as the new pods roll out. Pin
the image with `image.tag` to control exactly which backend version you deploy.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` |  |
| autoscaling.enabled | bool | `false` | Horizontal Pod Autoscaler (CPU-based). |
| autoscaling.maxReplicas | int | `6` |  |
| autoscaling.minReplicas | int | `2` |  |
| autoscaling.targetCPUUtilizationPercentage | int | `75` |  |
| config.adminPassword | string | `""` | Platform-admin bootstrap password (the /admin console login). REQUIRED, secret. |
| config.auditRetentionDays | int | `90` | Days to retain platform-wide audit entries (per-tenant retention is a tenant setting). |
| config.awsRegion | string | `""` | AWS region for the DynamoDB backend (only used when storageBackend=dynamo). Sets AWS_REGION/AWS_DEFAULT_REGION; credentials come from IRSA or Pod Identity, not from here. This is the DynamoDB *tables'* region, independent of the cluster's region - set it to where the tables live. Cross-region (cluster in one region, tables in another) works but adds latency and inter-region data-transfer cost per request; co-locate them when you can. |
| config.databaseUrl | string | `""` | Postgres connection string. REQUIRED unless you set existingSecret OR enable the bundled Postgres (postgresql.enabled=true, which auto-wires this). Point it at your own/managed Postgres, e.g. postgresql://reach:reach@my-postgres:5432/reach |
| config.existingSecret | string | `""` | Use an EXISTING Secret (e.g. from External Secrets / Sealed Secrets) instead of chart-managed values. Required keys are STORAGE-AWARE: always TOKEN_PEPPER, SESSION_SIGNING_KEY, ADMIN_PASSWORD; PLUS DATABASE_URL only when storageBackend=postgres (DynamoDB needs none); optionally METRICS_TOKEN. Setting this skips the chart-managed Secret AND the bundled Postgres (it implies you bring the datastore). |
| config.extraEnv | list | `[]` | Extra environment variables (list of {name, value} or valueFrom) for advanced tuning (e.g. FLEET_REAP_AFTER_SECONDS, METRICS_DOMAIN_GAUGES). |
| config.metricsToken | string | `""` | Optional bearer token to protect the /metrics endpoint. Empty = metrics are open (read-only counters, no secrets) but still guard them with a NetworkPolicy. |
| config.rateLimitStorageUri | string | `""` | Redis URI for distributed rate-limit state across replicas. Auto-wired when the bundled Redis is enabled (redis.enabled=true). Empty uses in-process limits (per replica), which is fine for small/single-replica deployments. |
| config.sessionSigningKey | string | `""` | Signing key for session cookies/tokens. REQUIRED, secret. |
| config.storageBackend | string | `"postgres"` | Storage backend: "postgres" (default) or "dynamo". With "dynamo" (AWS/EKS only) no Postgres is used or bundled, DATABASE_URL is not required, and migrations run dynamo_bootstrap. Provide config.awsRegion and grant DynamoDB access to the pod's ServiceAccount via EITHER: IRSA (set serviceAccount.annotations."eks.amazonaws.com/role-arn"), OR EKS Pod Identity (no annotation - create a Pod Identity Association for this namespace + ServiceAccount out of band with `aws eks create-pod-identity-association`). |
| config.tokenPepper | string | `""` | HMAC pepper for hashing tokens & passwords. REQUIRED, stable, secret. Rotating it invalidates all existing tokens and logins - generate once and keep it. |
| fullnameOverride | string | `""` |  |
| image.pullPolicy | string | `"IfNotPresent"` |  |
| image.repository | string | `"nabeemdev/reach"` | Backend image repository. The published image is `nabeemdev/reach` on Docker Hub (built + pushed by scripts/release_backend.sh); override for your own registry/mirror. |
| image.tag | string | `""` | Image tag. Defaults to the chart's appVersion when empty. |
| imagePullSecrets | list | `[]` | Image pull secrets for a private registry. |
| ingress.annotations | object | `{}` |  |
| ingress.className | string | `""` |  |
| ingress.enabled | bool | `false` | Expose the console + API through an Ingress. The backend serves both the API and the web UI (at /ui), so a single host/path is enough. |
| ingress.hosts[0].host | string | `"reach.example.com"` |  |
| ingress.hosts[0].paths[0].path | string | `"/"` |  |
| ingress.hosts[0].paths[0].pathType | string | `"Prefix"` |  |
| ingress.tls | list | `[]` |  |
| metrics | object | `{"serviceMonitor":{"enabled":false,"interval":"30s","labels":{}}}` | Prometheus metrics (the app exposes /metrics on the container port). |
| metrics.serviceMonitor | object | `{"enabled":false,"interval":"30s","labels":{}}` | Create a ServiceMonitor (requires the Prometheus Operator CRDs). |
| migrations.advisoryLockKey | int | `8274630119` | Postgres advisory-lock key used to serialize concurrent replica migrations. Any 64-bit int; only needs to be stable and unique to this app within the database. |
| migrations.enabled | bool | `true` | Run `alembic upgrade head` in an initContainer before each backend pod serves. The step retries (so it waits for the DB to be reachable and tolerates a concurrent replica racing on a fresh DB); `alembic upgrade head` is idempotent. Disable only if you migrate out of band. |
| migrations.retries | int | `30` | Retry attempts for the migrate/wait-for-db initContainer (spaced retryDelaySeconds apart). |
| migrations.retryDelaySeconds | int | `3` |  |
| nameOverride | string | `""` |  |
| networkPolicy.egressTo | list | `[]` | Extra egress peers allowed when restrictEgress is on (e.g. an external Postgres CIDR). |
| networkPolicy.enabled | bool | `false` | Create NetworkPolicies for the backend and the bundled Postgres/Redis. |
| networkPolicy.ingressFrom | list | `[]` | Extra ingress peers allowed to reach the backend on its HTTP port, beyond same-namespace pods (add your ingress-controller and/or Prometheus here). List of NetworkPolicyPeer. |
| networkPolicy.restrictEgress | bool | `false` | Restrict backend egress to DNS + HTTPS(443) + the bundled/known DB & cache. Off (default) leaves egress open, so an external Postgres/Redis on non-standard ports keeps working; turn on for stricter egress and add any external DB/cache peers via egressTo. |
| nodeSelector | object | `{}` |  |
| podAnnotations | object | `{}` |  |
| podDisruptionBudget | object | `{"enabled":true,"minAvailable":1}` | PodDisruptionBudget so a voluntary drain never takes the whole control plane down. Only takes effect with 2+ replicas - it is intentionally not rendered for a single replica (where minAvailable=1 would block node drains and cluster-autoscaler scale-down without adding any HA). |
| podLabels | object | `{}` |  |
| podSecurityContext | object | `{"fsGroup":10001,"runAsNonRoot":true,"runAsUser":10001,"seccompProfile":{"type":"RuntimeDefault"}}` | Pod-level security context. Runs non-root; the image is built for arbitrary uids. |
| postgresql.auth.database | string | `"reach"` |  |
| postgresql.auth.password | string | `""` | Password for the bundled Postgres. Leave EMPTY to auto-generate a strong one on first install and reuse it on upgrades (read back from the Secret via lookup). Set it explicitly only if you need a known value, or for GitOps tools that render without cluster access (Argo CD `helm template`), where an auto-generated value would churn each sync. |
| postgresql.auth.username | string | `"reach"` |  |
| postgresql.enabled | bool | `true` | Run an in-cluster Postgres and auto-wire DATABASE_URL to it. ON by default for a self-contained install; automatically skipped when you supply config.databaseUrl or config.existingSecret (your external DB takes precedence). |
| postgresql.image | string | `"postgres:16-alpine"` |  |
| postgresql.imagePullPolicy | string | `"IfNotPresent"` |  |
| postgresql.persistence.enabled | bool | `true` | Back the data dir with a PersistentVolumeClaim. Off = emptyDir (data lost on restart). |
| postgresql.persistence.size | string | `"8Gi"` |  |
| postgresql.persistence.storageClass | string | `""` | StorageClass; empty uses the cluster default. |
| postgresql.resources.limits.memory | string | `"512Mi"` |  |
| postgresql.resources.requests.cpu | string | `"100m"` |  |
| postgresql.resources.requests.memory | string | `"256Mi"` |  |
| probes.liveness.enabled | bool | `true` |  |
| probes.liveness.failureThreshold | int | `3` |  |
| probes.liveness.initialDelaySeconds | int | `15` |  |
| probes.liveness.periodSeconds | int | `20` |  |
| probes.readiness.enabled | bool | `true` |  |
| probes.readiness.failureThreshold | int | `3` |  |
| probes.readiness.initialDelaySeconds | int | `5` |  |
| probes.readiness.periodSeconds | int | `10` |  |
| probes.startup.enabled | bool | `true` |  |
| probes.startup.failureThreshold | int | `12` |  |
| probes.startup.periodSeconds | int | `5` |  |
| redis.enabled | bool | `true` | Run an in-cluster Redis-protocol cache (Valkey by default) and auto-wire RATE_LIMIT_STORAGE_URI to it (shared rate-limit state across replicas). ON by default; automatically skipped when you supply config.rateLimitStorageUri (your external cache wins). |
| redis.image | string | `"valkey/valkey:8-alpine"` | Redis-compatible image. Valkey is the license-safe default; redis:7-alpine also works. |
| redis.imagePullPolicy | string | `"IfNotPresent"` |  |
| redis.resources.limits.memory | string | `"256Mi"` |  |
| redis.resources.requests.cpu | string | `"50m"` |  |
| redis.resources.requests.memory | string | `"64Mi"` |  |
| replicaCount | int | `2` | Number of backend replicas. The app is stateless (all state is in Postgres), so it scales horizontally; each pod's initContainer runs the (idempotent) migration before serving. |
| resources | object | `{"limits":{"memory":"512Mi"},"requests":{"cpu":"100m","memory":"256Mi"}}` | Pod resource requests/limits. The backend is lightweight; tune for your load. |
| securityContext | object | `{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true}` | Container security context. Read-only root filesystem (a /tmp emptyDir is mounted for scratch); all capabilities dropped; no privilege escalation. |
| service.annotations | object | `{}` |  |
| service.port | int | `80` |  |
| service.type | string | `"ClusterIP"` |  |
| serviceAccount.annotations | object | `{}` |  |
| serviceAccount.create | bool | `true` | Create a dedicated ServiceAccount for the backend pods. |
| serviceAccount.name | string | `""` | Name to use; generated from the release when empty. |
| tests.enabled | bool | `true` | Enable the `helm test` connection hook (a Pod that GETs the Service's /health and fails if it isn't 200). Run it with `helm test <release>`. |
| tolerations | list | `[]` |  |
| topologySpreadConstraints | list | `[]` | Topology spread for backend replicas. Leave empty to get the chart's built-in best-effort spread across nodes and zones (applied automatically when there is more than one replica; ScheduleAnyway, so it never blocks scheduling). Set a value here to override that default. |

## Source Code

* <https://github.com/nabeemdev/reach>
