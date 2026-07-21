# Reach Agent

The agent is the process Reach runs on a target to execute commands sent by the
backend. It is a single, dependency-light **Go binary** (standard library only -
no `client-go`), which keeps it small and easy to ship as both a host binary and
a container image.

It runs in one of two modes, auto-detected at startup:

| | **Host** (Linux / macOS) | **Kubernetes** |
|---|---|---|
| Install | `install.sh` → systemd / launchd | Helm → Deployment |
| Identity | machine fingerprint | cluster fingerprint (one per cluster) |
| Executes | reads via `bash`; approved writes as structured `argv` with `execve` (**no shell**); Landlock-sandboxed | `kubectl` (+ filters), **no shell** |
| Access bound by | policy mode + grants + Landlock | policy mode + **RBAC** + allowlist |
| Token stored | `/etc/reach-agent/config.json` | a Kubernetes Secret (nothing on disk) |

The agent is **outbound-only** - it never opens a listening port. It polls the
backend over HTTPS, pulls jobs, runs them, and posts results back. There is
nothing for an attacker to connect *to*.

---

## Identity & enrollment (credential-only)

The agent **never sends or stores an agent ID** - it is identified purely by the
credential it presents. You create an agent in the console, which mints a
one-time **install token**; the agent claims with that token and receives a
long-lived **agent token**. From then on every call carries that token (Bearer),
and the backend resolves the agent by **hashing the token** (`agent_token_hash`).

> An `agent_id` still exists **on the backend** - it's the record key, and the
> handle operators use to target the agent (`reach exec --agent <id>`, the console,
> access lists). The point is the **agent process** never knows or transmits it;
> nothing client-supplied is trusted for identity.

```
console: create agent ──► install token (one-time, 24h)
agent:   claim(install_token, machine_fingerprint, type) ──► agent token
agent:   every later call carries the agent token (Bearer); backend resolves it by hash
```

- **Machine fingerprint** binds the agent to where it runs:
  - Host: `sha256(machine-id + ":" + install_id)` - one agent per machine.
  - Kubernetes: `sha256("k8s:" + <kube-system namespace UID>)` - the kube-system
    UID is created once at cluster bootstrap and never changes, so **every replica
    computes the same fingerprint** and the cluster appears as **one** agent.
- **`type`** is reported at claim: `host` or `k8s`, and is **bound to the type the
  agent was created as**. The install token is type-specific - redeeming a `k8s`
  agent's token with the host installer, or a `host` token with the k8s image, is
  rejected at claim (`403 install token is for a '<type>' agent, not '<other>'`).
  The install command the console generates already matches the created type, so
  this only blocks using the wrong installer. The backend stores the type; the UI
  shows it and the execution model follows from it.
- The agent token **auto-rotates every 30 days** (and on admin request).

---

## The poll loop

```
loop:
  touch the liveness file            # k8s only (see Liveness)
  (k8s) if not the lease leader → stand by, reuse the shared token, sleep
  if no agent token → claim with the install token
  if token is old/expired → rotate
  sync: heartbeat + report state, receive pending jobs
  for each job: execute → post result
  sleep (adaptive: ~2s when active, ~15s idle)
```

`sync` is the heartbeat. It tells the backend the agent is alive, reports state
(version, running-as-root, detected capabilities, and in k8s its RBAC), and
returns any queued jobs. Poll cadence is server-driven and adaptive - tight while
work is flowing, relaxed when idle.

### Unrecoverable failures idle, they don't crash-loop

A **permanent** failure - a 4xx from claim, sync, or token rotation such as a
type-mismatched or expired install token, a revoked/already-claimed agent, or an
unauthorized agent token - is not retried. Exiting under `Restart=always`
(systemd) or a Deployment (Kubernetes) would just respawn the process into the
same doomed request and hammer the API forever. Instead the agent:

- logs the backend's reason once - `Permanent failure, not retrying: <reason>` -
  followed by `Fix the agent config and restart the service manually`,
- **idles** rather than exiting, and exits cleanly on `SIGTERM`.

You then correct the config and restart it yourself (`systemctl restart
reach-agent`, or delete the pod). On **Kubernetes** the idle agent keeps the
liveness file fresh, so the pod stays **Running** (not CrashLooping) with the
reason plainly visible in `kubectl logs` - rather than restarting on a loop that
buries it. Transient errors (network blips, 5xx) still retry with backoff as
normal; only permanent 4xx conditions idle.

### Fleet scale-in

When the **machine** shuts down (an Auto Scaling Group instance terminating), a
**host** agent calls `POST /agent/deregister` so the member is removed from its
fleet immediately, instead of lingering until the server-side reaper's window
elapses. It's best-effort (shutdown isn't blocked on it) and the backend no-ops
for any agent that isn't a host fleet member, so it's always safe to attempt.

A plain `systemctl restart reach-agent` does **not** deregister: both a restart
and a shutdown deliver `SIGTERM`, so the agent distinguishes them via systemd's
manager state (`systemctl is-system-running` == `stopping` only during a real
reboot/poweroff/termination). Only a confirmed OS shutdown triggers deregister;
the reaper remains the backstop if that signal is ever missed.

---

## Execution model

This is where host and Kubernetes differ the most.

### Host - shell + Landlock

**Reads** run via `/bin/bash -lc <command>` (freeform shell). A **write** is parsed
into an `argv` and run with `execve` (**no shell**), so it can be matched against
structured rules. Policy mode is enforced with the kernel:

- **wild** - runs anything (a shell-operator write runs freeform).
- **readonly** - the command runs inside a **Landlock** sandbox (Linux) that
  blocks filesystem writes; write attempts fail at the kernel.
- **approved** - same sandbox, plus a per-agent allowlist of write **rules**
  `{bin, args[]}` (each arg a literal or `*`) matched against the write's argv;
  an unapproved write is blocked and raised for admin approval. A write with shell
  operators can't be structured and is rejected in approved mode.

**Fail-closed sandbox.** The `readonly`/`approved` guarantee depends on Landlock actually
being in force, so the agent **fails closed** when it isn't. At startup it probes Landlock
and reports `landlock_status` (`active` / `unavailable` / `unsupported`) on sync. If there is
no kernel sandbox - an old/locked-down Linux kernel, or **macOS** (Landlock is Linux-only) -
`readonly`/`approved` commands are **blocked**, not run unprotected. To run anyway, an operator
**acknowledges the exception** in the console (`POST /tenant/agents/{id}/acknowledge-sandbox`,
audited and revocable); the acknowledgement rides back on each sync and the agent then runs
unsandboxed. Creating an agent as **macOS** pre-acknowledges it so it never blocks. `wild` runs
unsandboxed regardless, and explicitly-approved structured writes always run (no shell to gate).
A fail-closed block is **not** raised for admin approval the way an unapproved write is - approving
can't satisfy a missing sandbox - so the agent tags it `block_reason: sandbox_unavailable` and the
backend skips the request; the fix is to acknowledge the host or run it on a Landlock kernel.

### Kubernetes - gated, **no shell**

A pod holds a cluster credential, so arbitrary shell would let a job read the
ServiceAccount token, reach internal services (SSRF), or run any binary - none of
which RBAC bounds. So in Kubernetes the agent **never uses a shell**:

1. **Parse** the command into a pipeline itself (no `bash`). Shell operators
   (`;`, `&&`, `$(…)`, backticks, redirects) are rejected outright.
2. **Allowlist** every stage's binary. Default: `kubectl` plus read-only filters
   `grep jq head tail wc sort uniq cut tr`. `awk`/`sed` are excluded (they can
   shell out); `curl`/`cat`/`bash` are not present.
3. **Reject local-file reads** - any argument that resolves to an existing file
   (e.g. `grep '' /var/run/secrets/.../token`, `kubectl create --from-file=…`) is
   blocked. The agent has no shell or file-writing binary, so jobs only ever read
   the API or the piped stream - never pod files. (Reading cluster data via
   `kubectl` is untouched; that's RBAC-bounded, which is the point.)
4. **Wire the pipes in Go** and exec each stage directly. So
   `kubectl get pods -o json | jq …` works; arbitrary code does not.

The allowlist is tunable from the chart: `extraAllowedBinaries` adds to the
default (safe) - it's a list of dicts where each entry both allow-lists a binary
**and** provides it (an initContainer installs it from a pinned `url`+`sha256`, or you
omit the url for a binary already in a custom image), so you never allow-list a tool the
agent can't run. `allowedBinaries` replaces the set (lock-down). Any binary you add
beyond `kubectl` + the read filters (helm, flux, a custom tool) is **default-denied
as a write** - in `approved` mode it needs a structured `{bin, args[]}` approval
(the same model as host approvals, matched positionally: `*` = any one arg, a trailing
`...` = any remaining args, so `helm list ...` covers `helm list` and `helm list -n prod`
alike); in `wild` it runs bounded by RBAC. `helm`'s arbitrary-exec escapes
(`--post-renderer`, `helm plugin`) are always blocked.

The agent reports its effective allowlist on every sync, so the console knows which
binaries this agent will actually run. Approving a `{bin, args[]}` rule for a binary
that isn't allow-listed is a no-op - the command still hard-blocks at execution - so
the approval screen warns and blocks it: allow-list the binary (redeploy) first, then
approve. The allowlist is unknown until the agent's first k8s sync; enforcement kicks
in once it's reported.

**Policy mode for k8s is enforced by the backend at submission**, not the agent -
it classifies each stage (default-deny: only `kubectl` reads and read-only filters
are reads; every other verb/binary is a write) and never dispatches a blocked
command: `readonly` rejects writes, `approved` holds writes/`exec` for approval,
`wild` runs anything. The agent's no-shell + allowlist is the compromise-resistant
backstop that bounds the pod regardless.

So three independent layers compose: **RBAC** (API server, unbypassable floor) ∩
**policy mode** (backend, the day-to-day control) ∩ **allowlist/no-shell** (agent,
blast-radius bound).

---

## Kubernetes specifics

- **One agent per cluster.** All replicas share the cluster fingerprint, so the
  backend shows a single agent. This is a **Deployment**, not a StatefulSet.
- **Leader election.** A `coordination.k8s.io/Lease` elects one leader that claims,
  heartbeats, and runs jobs; the rest stand by and fail over. Always on, so even a
  rolling update (briefly two pods) can't double-act.
- **Token = the Secret is the sole store.** The leader claims once and writes the
  agent token into a Secret it manages via the API. That Secret is the only
  persistent state - shared across replicas, surviving restarts and rotation.
  **Nothing is written to the pod filesystem** (read-only root).
- **RBAC self-review + drift.** The agent discovers **every namespace** itself
  (`list namespaces`) and runs `SelfSubjectRulesReview` in each, reporting its
  **effective cluster-wide RBAC** automatically - including grants bound in
  namespaces nobody configured. The console renders it as readable capabilities
  and you **acknowledge** it; if anyone later binds the SA to more permissions
  anywhere, the next review (every few minutes) sees it, the hash changes, and it
  is flagged as **drift** to re-acknowledge. The snapshot is deduped (cluster-wide
  baseline reported once + per-namespace deltas), size-capped (so the report can
  never break the backend write), and stably hashed.
- **Liveness.** The agent has no server, so it touches `/tmp/healthy` each loop
  iteration; the chart's liveness probe restarts the pod if that goes stale (a
  wedged loop).

---

## Configuration (environment)

| Variable | Mode | Purpose |
|---|---|---|
| `REACH_API_URL` | both | Backend URL (host reads it from config.json) |
| `REACH_INSTALL_TOKEN` | both | One-time claim token (first start only) |
| `REACH_CONFIG_PATH` | host | Config file path (default `/etc/reach-agent/config.json`) |
| `REACH_K8S_TOKEN_SECRET` | k8s | Secret the agent uses to share/persist its token |
| `REACH_K8S_LEASE` | k8s | Lease name for leader election |
| `REACH_NAMESPACE` / `REACH_POD_NAME` | k8s | Injected by the chart (downward API) |
| `REACH_K8S_ALLOWED_BINARIES` | k8s | Replace the execution allowlist (lock-down) |
| `REACH_K8S_EXTRA_BINARIES` | k8s | Add to the default allowlist |
| `REACH_K8S_REVIEW_INTERVAL_SECONDS` | k8s | RBAC re-review cadence (default 300, floor 30) |
| `REACH_K8S_DEFAULT_NAMESPACE` | k8s | Namespace injected into kubectl commands that don't specify one (default `default`). Set to the agent's own namespace for a namespace-scoped install. Keeps execution aligned with the backend's approval classification instead of in-cluster kubectl's pod-namespace default. |
| `REACH_HEALTH_FILE` | k8s | Liveness freshness file (chart sets `/tmp/healthy`) |
| `REACH_METRICS_ADDR` | both | Serve Prometheus `/metrics` on this address (e.g. `:9090`). **Unset by default** - the Helm chart wires it automatically for k8s (`metrics.enabled=true`); a host install honours it too but must set it by hand (and has no NetworkPolicy, so prefer `127.0.0.1:9090` + a co-located scraper). See below. |
| `REACH_COMMAND_TIMEOUT_SECONDS` | both | Per-command timeout (default 60) |
| `REACH_MAX_OUTPUT_BYTES` | both | Max captured stdout/stderr (default 50000) |

In Kubernetes the chart injects everything; you only provide `reach.apiUrl` and
`reach.installToken` (or an existing Secret). See
[`deploy/helm/reach-agent`](../deploy/helm/reach-agent).

---

## Metrics (opt-in)

The agent is **outbound-only** and normally opens **no** listening port. Setting
`REACH_METRICS_ADDR` is the one exception: it starts a small stdlib HTTP server
(no client library) serving Prometheus text at `/metrics`. It is **off by
default**; enable it via the chart's `metrics.enabled=true`, which also renders a
`Service`, a `ServiceMonitor`, and a `NetworkPolicy` locking the port to the
Prometheus namespace.

Because a Kubernetes agent is a Deployment with **leader election**, all replicas
are scraped, so each exports `reach_agent_is_leader` (1 for the active leader, 0
for standbys) - filter or aggregate on it.

**Host agents.** The endpoint is not k8s-specific - the same binary serves it if
`REACH_METRICS_ADDR` is set. But `install.sh` never sets it, and there is no
`ServiceMonitor` for systemd/launchd. Unlike k8s, a host has no NetworkPolicy to
contain the port, so if you enable it on a host, **bind it to loopback**
(`REACH_METRICS_ADDR=127.0.0.1:9090`) and scrape via a collector already running
on that machine. On a host `reach_agent_is_leader` is always `1` (no election).

| Metric | Type | Meaning |
|---|---|---|
| `reach_agent_info{version,type}` | gauge | Build/mode info (always 1) |
| `reach_agent_up` | gauge | 1 while the process serves |
| `reach_agent_is_leader` | gauge | 1 if this replica is the active leader (or the sole agent) |
| `reach_agent_start_timestamp_seconds` | gauge | Process start time |
| `reach_agent_last_successful_sync_timestamp_seconds` | gauge | Last successful heartbeat |
| `reach_agent_jobs_total` | counter | Jobs executed |
| `reach_agent_jobs_blocked_total` | counter | Jobs blocked by policy / the k8s allowlist |
| `reach_agent_job_failures_total` | counter | Jobs that exited non-zero (excludes blocked) |
| `reach_agent_job_duration_ms_sum` | counter | Cumulative job execution time (ms) |
| `reach_agent_syncs_total` / `reach_agent_sync_errors_total` | counter | Heartbeat successes / failures |
| `reach_agent_token_rotations_total` | counter | Token rotations performed |

The endpoint is read-only (counters, no secrets), but it **is** an inbound port -
a deviation from the outbound-only model. Keep the default NetworkPolicy on. See
[SECURITY.md](../SECURITY.md#optional-metrics-endpoint).

> **Not the backend's `/metrics`.** These `reach_agent_*` metrics come from the **agent** process (opt-in via `REACH_METRICS_ADDR`). The **backend** serves a separate `/metrics` with `reach_backend_*` metrics - see [SELF_HOSTING.md → Backend metrics](../SELF_HOSTING.md#backend-metrics-metrics).

---

## Build & release

- **`go build`** produces a single static binary.
- **`scripts/release_agent.sh`** builds the host binaries (linux/macOS,
  amd64/arm64) and uploads them + `install.sh` to S3 (and refreshes
  `agent/versions.json`, the list the console's create dropdown reads), **and**
  builds and pushes the multi-arch **container image** (`agent/Dockerfile` →
  `nabeemdev/reach-agent`, tagged to the chart's `appVersion`).
- Host installs pull the binary via `install.sh`; Kubernetes installs pull the
  image via Helm. The image bundles `kubectl` + the filter set and runs non-root
  with a read-only root filesystem.

A test (`version_test.go`) fails the build if `agentVersion` and the chart's
`appVersion` drift, since the image tag is derived from one and resolved from the
other.

---

## Source map

| File | What |
|---|---|
| `main.go` | config, claim, poll loop, host execution, rotation |
| `k8s.go` | k8s detection, cluster identity, token Secret store, RBAC self-review |
| `k8s_exec.go` | gated no-shell execution (pipeline parse, allowlist, file-read block) |
| `k8s_lease.go` | leader election via Lease |
| `sandbox_linux.go` / `sandbox_other.go` | Landlock sandbox (host, Linux) |
| `install.sh` | host installer (systemd / launchd) |
| `Dockerfile` | Kubernetes image (kubectl + filters, non-root) |
