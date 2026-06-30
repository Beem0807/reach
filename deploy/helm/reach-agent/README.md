# reach-agent

![Version: 0.1.0](https://img.shields.io/badge/Version-0.1.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.1.0](https://img.shields.io/badge/AppVersion-0.1.0-informational?style=flat-square)

Reach agent for Kubernetes - gives AI agents controlled, audited command execution against the cluster. Runs as a single logical agent (one identity per cluster) with leader election across replicas.

> The agent's Kubernetes mode (cluster identity, leader election, the shared
> token Secret, gated execution) is implemented and targeted by this chart. The
> multi-arch image (`agent/Dockerfile`) is built and pushed by
> `scripts/release_agent.sh` (tagged with the chart's `appVersion`).

## Design

- **One identity per cluster.** The agent auto-detects Kubernetes and derives a
  stable cluster id from the `kube-system` namespace UID. Every replica computes
  the same identity, so the backend shows **one** agent regardless of replica
  count, tagged `type=k8s`.
- **Fungible replicas + leader election.** A `coordination.k8s.io/Lease` elects a
  single leader that holds the identity and runs jobs; the rest stand by and take
  over on failover. This is a Deployment, **not** a StatefulSet - one shared
  identity, not one per pod.
- **Shared token, Secret is the sole store.** The agent claims once and stores
  its `agent_token` in a Secret it manages via the API. That Secret is the only
  persistent state - shared across replicas, surviving restarts and the 30-day
  rotation. Nothing is written to the pod filesystem (the root fs is read-only),
  so the token is never cached on a node's disk.

## Install

1. Create an agent in the tenant console (**Agents → New agent**) to get a
   one-time install token.
2. Install from the published Helm repo (the console prints this command,
   pre-filled and with the chart `--version` pinned):

```bash
helm repo add reach https://reach-releases.s3.amazonaws.com/charts/reach-agent --force-update
helm install reach-agent reach/reach-agent \
  --namespace reach --create-namespace \
  --set reach.apiUrl=https://reach.example.com \
  --set reach.installToken=install_xxx
```

Pin a specific chart with `--version <chartVersion>`; the agent image comes from
that chart's `appVersion`, so pinning the chart version pins the image - there is
no separate image tag to set. Omit `--version` to get the latest chart. From a
cloned repo you can install the local chart instead:
`helm install reach-agent deploy/helm/reach-agent …`.

Or reference a pre-created Secret (keys: `api-url`, `install-token`) instead of
passing the raw token - required for GitOps (see below):

```bash
helm install reach-agent reach/reach-agent \
  --namespace reach --set reach.existingSecret=my-reach-bootstrap
```

## GitOps (Argo CD / Flux)

The generated `helm install` command is the imperative quickstart. For GitOps,
**don't put the one-time install token in Git.** Create the bootstrap Secret out
of band (Sealed Secrets / External Secrets / `kubectl`) with keys `api-url` and
`install-token`, then reference it with `reach.existingSecret` and point the app
at this repo. Example Argo CD `Application`:

```yaml
spec:
  source:
    repoURL: https://reach-releases.s3.amazonaws.com/charts/reach-agent
    chart: reach-agent
    targetRevision: "*"          # track the latest chart automatically
    # or pin/range for controlled, PR-driven updates: "0.1.0" | ">=0.1.0 <0.2.0"
    helm:
      parameters:
        - {name: reach.existingSecret, value: reach-agent-bootstrap}
  destination:
    namespace: reach
    server: https://kubernetes.default.svc
```

Keep it current with no version hardcoded in the app manifest: `targetRevision: "*"`
(or a semver range) tells Argo CD to resolve the newest matching chart from the
repo index on each sync, so you never miss a release. Prefer a range + Renovate if
you want updates gated by PR. The agent image rides along automatically - each
chart version carries the matching `appVersion`, so a new chart brings its image
with it; there's no separate image tag to track.

## Chart versions - discover, pin, diff

The chart version is independent of the agent version, so you may want to see
what's available and what changed.

**List / inspect versions** (after `helm repo add reach …`):

```bash
helm repo update reach
helm search repo reach/reach-agent --versions   # all published chart versions + appVersion
helm show chart reach/reach-agent --version 0.1.0  # metadata + per-version changelog
helm list -n reach                               # which chart version is DEPLOYED (CHART column)
```

**What changed between two versions** - Helm has no built-in semantic diff, so:

```bash
# per-version changelog carried in the chart (artifacthub.io/changes annotation)
helm show chart reach/reach-agent --version 0.1.1 | sed -n '/changes/,$p'

# exact rendered-manifest delta between two chart versions
diff \
  <(helm template reach/reach-agent --version 0.1.0 --set reach.apiUrl=x --set reach.installToken=x) \
  <(helm template reach/reach-agent --version 0.1.1 --set reach.apiUrl=x --set reach.installToken=x)

# what an upgrade would change against your live release (needs the helm-diff plugin)
helm diff upgrade reach-agent reach/reach-agent --version 0.1.1 --reuse-values
```

**Pin a version** - `--version 0.1.1` (exact) or a range like `">=0.1.0 <0.2.0"`;
Argo CD `targetRevision`. Omit it to install the latest chart.

## Two permission layers

The agent's RBAC has two independent parts - don't conflate them.

**1. Operational RBAC (fixed, `rbac.create`)** - what the agent needs to function:

| Scope | Resource | Verbs | Why |
|---|---|---|---|
| Cluster (read-only) | `namespaces` | get, list | cluster id (get kube-system) + enumerate namespaces to self-review RBAC everywhere |
| Namespace | `coordination.k8s.io/leases` | get, create, update, watch | leader election |
| Namespace | `secrets` | get, create, update, patch | persist/share the agent token |

**2. What the agent can DO in the cluster (`clusterAccess`)** - the important one.
The agent can only run commands its ServiceAccount allows; this is where you grant
exactly the operations you want the AI to perform. Defaults to read-only (`view`),
cluster-wide.

```bash
# Read-only, whole cluster (default)
--set clusterAccess.roleName=view

# Read/write, but only in two namespaces (least privilege)
--set clusterAccess.roleName=edit \
--set clusterAccess.scope=namespaces \
--set "clusterAccess.namespaces={team-a,team-b}"

# Exactly pods + logs, read-only, cluster-wide (custom rule)
--set "clusterAccess.rules[0].apiGroups[0]=" \
--set "clusterAccess.rules[0].resources[0]=pods" \
--set "clusterAccess.rules[0].resources[1]=pods/log" \
--set "clusterAccess.rules[0].verbs[0]=get" \
--set "clusterAccess.rules[0].verbs[1]=list"

# No cluster access (bind your own RBAC out of band)
--set clusterAccess.enabled=false
```

**Two axes, composed.** RBAC (`clusterAccess`) is the **server-side floor** - the
API rejects anything outside it, unbypassable. The agent **discovers every
namespace itself** and self-reviews (via `SelfSubjectRulesReview`) to report its
*effective* RBAC **cluster-wide** - so access bound in a namespace nobody
configured still shows up. You **acknowledge** that snapshot in the console; if
anyone later binds the agent's ServiceAccount to more permissions anywhere, the
next review (every few minutes) picks it up, the hash changes, and it's flagged
as **drift** to re-acknowledge. The Reach **policy mode** (`readonly` / `approved` / `wild`) is the
**day-to-day control**, enforced by the backend at job submission: it classifies
each `kubectl` verb - `readonly` allows only read verbs, `approved` holds writes/
`exec` for approval, `wild` runs anything (still within RBAC). Classification is
fail-closed (unknown verb = write); "double verbs" are judged on the
sub-subcommand (`rollout status` reads, `rollout restart` writes; `auth can-i`
reads, `auth reconcile` writes). Blocked commands
never dispatch to the agent. Keep `clusterAccess` least-privilege **and** run
`approved`/`readonly` in production - `cluster-admin` + `wild` hands the AI the
cluster. (The agent additionally enforces the no-shell + allowlist below, which
bound the pod regardless.)

## Job execution

Jobs run **without a shell**. The agent parses the command into a pipeline itself,
requires every stage's binary to be allow-listed (default: `kubectl` + read-only
filters `grep jq head tail wc sort uniq cut tr`), and wires the pipes in Go. So
`kubectl get pods -o json | jq …` works, but `;`, `&&`, `$(…)`, backticks,
redirects, and other binaries (`curl`, `cat`, `awk`, `sed`) are rejected - a pod
holds a cluster credential, so arbitrary shell would let a job read the token or
reach internal services regardless of RBAC. Extend the allowlist with
`extraAllowedBinaries` (additive) or replace it with `allowedBinaries` (lock-down).

## Metrics (opt-in)

Off by default. `--set metrics.enabled=true` exposes Prometheus metrics at
`:9090/metrics` and renders a headless `Service`, a `ServiceMonitor`, and a
`NetworkPolicy` that admits the port **only from the Prometheus namespace**
(`metrics.networkPolicy.prometheusNamespace`, default `monitoring`).

This is the agent's **only** inbound port - a deliberate deviation from its
otherwise outbound-only model. It serves read-only counters (no secrets, no
auth). All replicas are scraped; each exports `reach_agent_is_leader` so you can
tell the active leader from standbys. Keep the NetworkPolicy on. See
[SECURITY.md](../../../SECURITY.md#optional-metrics-endpoint) and
[agent/README.md](../../../agent/README.md#metrics-opt-in).

```bash
helm install reach-agent deploy/helm/reach-agent \
  --namespace reach --create-namespace \
  --set reach.apiUrl=https://reach.example.com \
  --set reach.installToken=install_xxx \
  --set metrics.enabled=true \
  --set metrics.serviceMonitor.labels.release=kube-prometheus-stack
```

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Affinity rules for pod scheduling. |
| allowedBinaries | list | `[]` | REPLACE the entire allowlist (lock-down / full control). When non-empty this is the exact set - the default (`kubectl` + grep, jq, head, tail, wc, sort, uniq, cut, tr) is dropped. Use `[kubectl]` to restrict to kubectl only. Prefer `extraAllowedBinaries` unless you specifically need to remove defaults. |
| clusterAccess.enabled | bool | `true` | Grant the agent permission to operate the cluster. When false, bind your own RBAC to the ServiceAccount out of band. |
| clusterAccess.namespaces | list | `[]` | Namespaces to grant access in when `scope: namespaces`. |
| clusterAccess.roleName | string | `"view"` | Existing ClusterRole to bind: `view` (read-only, safe default), `edit`, `admin`, or `cluster-admin` (avoid - effectively root). Ignored when `rules` is set. |
| clusterAccess.rules | list | `[]` | Inline custom RBAC rules. If non-empty, the chart creates and binds a Role/ClusterRole built from these (overrides `roleName`). |
| clusterAccess.scope | string | `"cluster"` | Binding scope: `cluster` (every namespace) or `namespaces` (only those in `namespaces` below - least privilege). |
| extraAllowedBinaries | list | `[]` | Add binaries to the default allowlist (the safe way to extend it). E.g. `[helm, kustomize]` keeps `kubectl` + the default read-only filters AND adds these. Jobs run connected by pipes, with no shell. |
| extraEnv | list | `[]` | Extra environment variables for the agent container. |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy. |
| image.repository | string | `"nabeemdev/reach-agent"` | Agent image repository. |
| image.tag | string | `""` | Image tag. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Image pull secrets for private registries. |
| leaderElection.leaseName | string | `""` | Lease name used for leader election. Defaults to `<release>`. |
| livenessProbe.enabled | bool | `true` | Restart the pod if the agent's poll loop wedges. The agent touches `/tmp/healthy` each iteration; the probe fails when it goes stale. |
| livenessProbe.failureThreshold | int | `3` | Consecutive failures before the pod is restarted. |
| livenessProbe.initialDelaySeconds | int | `30` | Seconds before the first liveness check. |
| livenessProbe.periodSeconds | int | `30` | How often to run the liveness check. |
| livenessProbe.staleSeconds | int | `120` | Max age (seconds) of `/tmp/healthy` before the probe fails. Keep well above the server-driven poll interval (normally <=15s). |
| metrics.enabled | bool | `false` | Expose Prometheus metrics at :port/metrics (opens the agent's only inbound port). |
| metrics.networkPolicy.enabled | bool | `true` | Restrict ingress to the metrics port to Prometheus only (recommended). |
| metrics.networkPolicy.from | list | `[]` | Advanced: full NetworkPolicyPeer list; overrides prometheusNamespace when set. |
| metrics.networkPolicy.prometheusNamespace | string | `"monitoring"` | Namespace Prometheus runs in (matched by kubernetes.io/metadata.name). |
| metrics.port | int | `9090` | Port the agent serves /metrics on. |
| metrics.serviceMonitor.enabled | bool | `true` | Create a Prometheus Operator ServiceMonitor (only when metrics.enabled). |
| metrics.serviceMonitor.interval | string | `"30s"` | Scrape interval. |
| metrics.serviceMonitor.labels | object | `{}` | Extra labels so your Prometheus selects it, e.g. {release: kube-prometheus-stack}. |
| metrics.serviceMonitor.namespace | string | `""` | Namespace for the ServiceMonitor. Empty uses the release namespace. |
| metrics.serviceMonitor.scrapeTimeout | string | `"10s"` | Scrape timeout. |
| nodeSelector | object | `{}` | Node selector for pod scheduling. |
| permissionReviewIntervalSeconds | string | `""` | How often (seconds) the agent re-reviews its cluster-wide RBAC. Empty uses the agent default (300s / 5 min). Lower = faster drift detection; higher = less API load on very large clusters (cost scales with namespace count). Floored at 30s. |
| podAnnotations | object | `{}` | Extra annotations for the agent pods. |
| podLabels | object | `{}` | Extra labels for the agent pods. |
| podSecurityContext | object | `{"fsGroup":10001,"runAsNonRoot":true,"runAsUser":10001,"seccompProfile":{"type":"RuntimeDefault"}}` | Pod-level security context. Runs non-root by default. |
| rbac.create | bool | `true` | Create the agent's own operational RBAC (read kube-system for the cluster id, leases for election, the token Secret). |
| reach.apiUrl | string | `""` | Backend API URL, e.g. `https://reach.example.com`. |
| reach.existingSecret | string | `""` | Use a pre-existing Secret (keys: `api-url`, `install-token`) instead of the two values above. |
| reach.installToken | string | `""` | One-time install token from the tenant console (`install_...`). Consumed on first claim. |
| replicaCount | int | `2` | Number of agent replicas. Replicas are fungible - a Lease elects one active leader, the rest stand by for failover. |
| resources | object | `{"limits":{"memory":"128Mi"},"requests":{"cpu":"50m","memory":"64Mi"}}` | Pod resource requests and limits (the agent is lightweight). |
| securityContext | object | `{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true}` | Container security context. Read-only root filesystem, all capabilities dropped. |
| serviceAccount.annotations | object | `{}` | Annotations for the ServiceAccount (e.g. an IRSA/Workload Identity role). |
| serviceAccount.create | bool | `true` | Create a ServiceAccount for the agent. |
| serviceAccount.name | string | `""` | ServiceAccount name. Defaults to the chart fullname. |
| tokenSecretName | string | `""` | Name of the Secret the agent creates/updates to persist and share its claimed agent token (shared across replicas, survives restarts/rotation). Defaults to `<release>-token`. |
| tolerations | list | `[]` | Tolerations for pod scheduling. |
| topologySpreadConstraints | list | `[]` | Topology spread constraints - spread replicas across nodes/zones so the standby survives a node loss. |

## Source Code

* <https://github.com/nabeemdev/reach>
