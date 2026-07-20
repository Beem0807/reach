# Security

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email: **pnabeem99@gmail.com**

Include:
- A description of the vulnerability and its impact
- Steps to reproduce or a proof of concept
- The component affected (CLI, agent, backend, MCP server)
- Any suggested fix if you have one

You will receive an acknowledgement within 48 hours. If the issue is confirmed, a fix will be prioritised and you will be kept updated on progress. We ask that you give us reasonable time to address the issue before any public disclosure.

---

## Supported versions

Only the latest released version receives security fixes. Older versions are not backported.

---

## Threat model

**Reach protects against:**

- **Accidental dangerous commands** - a global blocklist (fork bombs, `rm -rf /`, raw disk wipes, privileged container escapes, reverse shells) is enforced server-side before any job is queued, regardless of mode.
- **AI agent overreach** - `readonly` mode blocks writes server-side before they reach the agent. `approved` mode requires explicit admin pre-approval for each write, matched by a structured **JSON rule** (see [Unapproved production writes](#threat-model)); unapproved writes are blocked and surfaced as a pending approval record, not silently dropped or executed.
- **AI self-approval** - the MCP server (what an AI drives) is **read-only for approvals**: it exposes no tool to create, approve, or deny an approval, so an AI cannot request a command and then approve it for itself. Approval review stays a human action (console, or the CLI with an operator's own token). Multi-machine fan-outs over MCP (`fleet_exec`, `exec_by_tag`) are **confirm-gated** - a dry-run preview must be returned to the user before a `confirm=true` dispatch.
- **Exposed inbound SSH** - agents communicate outbound-only over HTTPS. No ports are opened on the remote machine. No SSH keys to distribute or rotate.
- **Unapproved production writes** - in `approved` mode, the agent cannot execute a write unless an admin has explicitly allowlisted it. A host **write is structured**: parsed into an `argv` and run with `execve` (**no shell**), and approved only if it matches a structured **JSON rule** `{bin, args[]}` (each arg a literal, the single-arg wildcard `*`, or a trailing `...` for the rest) - not a text prefix. This is enforced server-side and again on the agent (rule match, plus Landlock on Linux / the `is_write` flag on macOS). A write that uses shell operators (`| ; && $() > *`) can't be a rule, so it is **refused in approved mode**. This closes the class of bypass where an approved read/write string was extended (`approved-cmd | tee /etc/x`, `… && rm -rf /`) to smuggle an unapproved write past a prefix match.
- **Token theft across machines** - agent tokens are bound to a machine fingerprint at claim time. A token captured from one machine cannot be replayed from another.
- **Cross-tenant access** - tenant isolation is enforced in the storage layer. A user token from tenant A cannot list, access, or submit jobs to tenant B's agents.

**Reach does not protect against:**

- **Malicious or compromised machine owner** - the person who owns the machine the agent runs on can read the agent config file, extract the agent token, and use it to submit arbitrary commands (in wild mode) or approved commands (in approved mode). Reach is not a security boundary between a machine owner and their own machine.
- **Compromised `ADMIN_PASSWORD`** - whoever knows `ADMIN_PASSWORD` can create and manage tenants and users across all tenants. Treat it like a root credential for provisioning. Policy management, approvals, and agent operations are controlled by tenant admin users separately.
- **Compromised `TOKEN_PEPPER`** - `TOKEN_PEPPER` is used to hash all tokens. If it leaks alongside the database, all token hashes become forgeable. See [Backend compromise](#what-happens-if-the-backend-is-compromised).
- **Kernel-level bypasses** - the Landlock sandbox on Linux protects against unapproved writes at the kernel level, but kernel exploits or privileged container escapes are out of scope.
- **Command-obfuscation edge cases in wild mode** - wild mode runs commands through `bash -lc`. Shell aliases, `$PATH` manipulation, or creative quoting on the submitting machine may produce commands that look safe but behave differently on the remote. This applies to **wild mode and to reads** (which stay freeform). It does **not** apply to approved-mode host **writes**, which are structured `argv` run with `execve` - there is no shell to obfuscate. Use `approved` or `readonly` mode on any machine where this matters.
- **Replay within the token lifetime** - a captured agent token can be used from any machine with the correct fingerprint until the token expires or is revoked. Rotate immediately if you suspect compromise.

---

## Security design

For full architectural detail see [ARCHITECTURE.md](ARCHITECTURE.md).

**Tokens** - no token is stored raw. Only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted. If the database is compromised without `TOKEN_PEPPER`, tokens cannot be recovered or forged.

**User passwords** - console login passwords are hashed with PBKDF2-HMAC-SHA256 (200,000 iterations) using a unique 16-byte random salt per user, stored as `pbkdf2$salt$hash`. The raw password is never stored. New passwords must be at least 8 characters. First-login passwords are randomly generated, issued once, and must be changed before the account can be used.

**Console session tokens** - the web console issues short-lived (8-hour) HS256 session tokens that are distinct from API tokens and are never persisted server-side. The platform admin session is signed with `ADMIN_PASSWORD`; the tenant console session is signed with a dedicated `SESSION_SIGNING_KEY` and carries the user's tenant and role. Both signing secrets are **safe to rotate** - doing so only invalidates active sessions, so users simply log in again. This is deliberately separate from `TOKEN_PEPPER` (which hashes stored credentials and cannot be rotated without reissuing everything), so session-key rotation never touches stored tokens. The CLI and MCP server do **not** use session tokens - they authenticate with long-lived API tokens (`tok_`).

**API-token scope** - an API token authenticates as its owning user with that user's **role**, and covers the operational surface: jobs, agents, fleets, and approvals (create/approve/deny, role-permitting). It is **rejected** on the sensitive console tier - user management, API-token management, and audit logs - which require an interactive session login. So a leaked API token cannot create users, mint or revoke tokens, or read audit logs; the blast radius is bounded to that user's operational role, and the token can be revoked from the console. See [API.md → Authentication](API.md#authentication).

**Constant-time comparison** - passwords, token hashes, session signatures, and `ADMIN_PASSWORD` are all compared with `hmac.compare_digest` to avoid leaking information through timing.

**Agent token binding** - agent tokens are bound to a machine fingerprint at claim time. A token stolen from one machine cannot be replayed from another.

**Install token** - one-time use, 24-hour expiry, cleared from disk after a successful claim. It is also **bound to the agent's type**: a token minted for a `k8s` agent cannot be redeemed by the host installer, or a `host` token by the k8s image (the claim is rejected `403`). This keeps a token from being used to enroll an agent whose runtime doesn't match the RBAC / capability configuration it was created with.

**Fleet join token** - a fleet's join token is deliberately **reusable and long-lived** (it enrolls a whole autoscaling group), so it is a higher-value credential than a one-time install token: anyone who obtains it can enroll rogue **host** agents into that fleet, inheriting the fleet's mode and grants. It is host-only (a fleet claim with `type: k8s` is rejected) and is mitigated like any long-lived secret - **rotate** it (`Fleets → Rotate`, with a grace window so the old token keeps working until you update the launch template) and **revoke** the fleet if it leaks. Each enrolled instance still receives its own machine-fingerprint-bound **agent token**, so a stolen join token lets an attacker enroll *new* rogue agents but does not grant existing members' credentials. Keep it in your autoscaler's instance startup config (AWS user-data, GCP startup-script, Azure custom-data, …), protected by that platform's secret handling, not in source control.

**Tenant isolation** - user tokens can only access agents and jobs within their own tenant. The storage layer enforces this; there is no client-side filtering.

**Policy enforcement** - the global blocklist and mode-specific write blocking are enforced server-side before the job reaches the agent. On a host the agent enforces a second time locally with the **Landlock kernel sandbox** (Linux), which makes the filesystem read-only (except `/tmp`) so writes are blocked by the kernel, not just by the classifier. Two independent enforcement points must both be bypassed. **This fails closed:** if the kernel sandbox can't be applied (a Linux kernel without Landlock, or macOS, which has none), `readonly`/`approved` commands are **blocked, not run unprotected** - unless an operator has explicitly **acknowledged** running that agent unsandboxed (an audited, revocable console action; macOS agents can pre-acknowledge at create time). So the "writes are kernel-enforced on Linux" guarantee can't silently degrade to "the classifier guessed right."

**Structured host writes** - a host **write** is not a shell string: it is parsed into an `argv` and executed with `execve` (**no shell**), so there is nothing to pipe, chain, substitute, or glob. Approvals are structured **JSON rules** `{bin, args[]}` (each arg a literal, the single-arg wildcard `*`, or a trailing `...` for the rest) matched against the argv - never a string comparison - mirroring the k8s `{verb, resource, namespace, name}` model. Every host approval is such a rule: a command string submitted for approval is structured into one, or rejected if it can't be - there is no command-string (prefix-match) approval path. A write that needs shell features can't be a rule, so it is refused in `approved` mode, runs freeform only in `wild` (no approval, no sandbox), and is Landlock-blocked in `readonly`. **Reads** are unchanged: they run freeform under Landlock. This model applies to single-agent and **fleet** writes alike.

**Kubernetes execution** - a pod holds a cluster credential, so the model is stricter. Three layers compose: **RBAC** (the API server's unbypassable floor; what the agent can do is the `clusterAccess` you bind, defaulting to read-only), the **policy mode** (enforced by the backend at submission), and the **agent's no-shell + allowlist** - jobs run as `kubectl` plus a few read-only filters with **no shell**, and arguments resolving to a local file are rejected so a job can never read the mounted ServiceAccount token. The agent self-reports its effective cluster-wide RBAC for acknowledge/drift.

Write classification is **default-deny**: only `kubectl` (verb-classified) and the read-only filters count as reads; **any other binary an operator allow-lists** via `extraAllowedBinaries` (helm, flux, argocd, a custom tool) is treated as a **write**. In `approved` mode a kubectl write is gated by a `{verb, resource, namespace, name}` rule, and a non-kubectl write by a structured `{bin, args[]}` rule (the same model as host approvals, matched positionally with `*` and a trailing `...`) - so helm/flux commands are approvable, not a silent bypass. `helm`'s arbitrary-executable escapes (`--post-renderer`, `helm plugin`) are always blocked on the agent, and no approval can satisfy them. The agent also **reports its execution allowlist** to the console, which warns and blocks any attempt to approve a binary the agent won't run - approving one would be a no-op that still hard-blocks at execution, so the misconfiguration surfaces at approval time rather than silently. See [agent/README.md](agent/README.md).

**No inbound connections** - agents make outbound HTTPS requests only. No ports are opened on the remote machine (or pod). The one exception is the [optional metrics endpoint](#optional-metrics-endpoint), which is **off by default**.

**Config file permissions** - agent config files are written with `0600` permissions (owner read/write only).

**sudo access** - the agent runs as a non-privileged `reach-agent` system user with no sudoers entry by default. `sudo` commands will fail unless you explicitly grant sudo access. For production, prefer `approved` mode and allowlist only the specific `sudo` commands needed. See [Agent sudo access](SELF_HOSTING.md#agent-sudo-access).

**Secret redaction** - command output is scrubbed for recognizable secrets before it is stored or shown to an AI agent. See [Secret redaction in command output](#secret-redaction-in-command-output).

**Bounded command output** - each command runs under a wall-clock timeout, and `stdout`/`stderr` are capped as they stream (default 50 KB/stream, `REACH_MAX_OUTPUT_BYTES`), so a runaway or hostile command (`yes`, `find /`, a fork of log spew) cannot exhaust agent memory or bloat storage. The backend re-caps on ingest, keeping every result well under the datastore's item-size limit. Truncation is flagged (`stdout_truncated`/`stderr_truncated`) so callers never mistake a cut-off result for a complete one. See [Output limits](SELF_HOSTING.md#output-limits--truncation).

**Brute-force mitigation** - all login endpoints (`POST /admin/login`, `POST /tenant/login`) are rate limited to 10 requests/minute per IP, and every other endpoint is rate limited as well (see [API.md](API.md#rate-limits)). There is no per-account lockout counter; protection relies on rate limiting combined with strong, randomly generated credentials. Counters are in-memory (per-process) by default - if you run more than one backend replica, point them at a shared store via `RATE_LIMIT_STORAGE_URI` so the limit holds across replicas (see [Running multiple replicas](SELF_HOSTING.md#running-multiple-replicas)).

---

## Optional metrics endpoint

The agent can expose Prometheus metrics at `/metrics` when `REACH_METRICS_ADDR` is set. **This is off by default and is a deliberate deviation from the outbound-only model** - it is the only case in which the agent opens an inbound listening port. It is not tied to Kubernetes: the same binary serves it for host agents too (see the host note below).

What it does and does not expose:

- **Read-only counters, no secrets.** The endpoint serves operational metrics only (job counts, durations, sync success/failure, leadership, token-rotation count). It never exposes command output, tokens, RBAC contents, or configuration. It requires no authentication because it carries nothing sensitive - so it must be reached only by your monitoring stack.
- **Leader-aware.** All replicas are scraped; each reports `reach_agent_is_leader` so standby replicas are distinguishable from the active leader.

**Kubernetes (`metrics.enabled=true`) - locked down by default.** Enabling it renders a `NetworkPolicy` (Ingress-only) that admits the metrics port **only from the Prometheus namespace** (`metrics.networkPolicy.prometheusNamespace`, default `monitoring`). The policy governs ingress only, so the agent's outbound HTTPS - its real control channel - is unaffected. Keep this policy on; disabling it (`metrics.networkPolicy.enabled=false`) exposes the port to the whole cluster.

**Host agents - no automatic containment.** `install.sh` never sets `REACH_METRICS_ADDR`, so a host install exposes nothing unless you set it yourself. If you do, note that a host has **no NetworkPolicy** to scope the port and **no authentication** on the endpoint - an open `:9090` is reachable by anything that can route to the machine. Bind it to **loopback** (`REACH_METRICS_ADDR=127.0.0.1:9090`) and scrape via a collector already running on that host, or leave it unset. Never bind a host metrics port to a public or shared interface.

If your posture is "no inbound ports, ever," leave it unset (the default on both host and k8s) and instead rely on the metrics the agent already reports to the backend through its outbound sync. See [agent/README.md → Metrics](agent/README.md#metrics-opt-in).

---

## Secret redaction in command output

Command `stdout` and `stderr` are passed through a best-effort secret scrubber before they are persisted or surfaced. Redaction runs at two independent layers:

1. **Backend** - `stdout`/`stderr` are redacted before the job result is written to the database, so secrets are not stored at rest in job history.
2. **MCP server** - output is redacted again locally before it is returned to the AI agent (Claude, Cursor, etc.), so a secret never reaches the model even if it somehow reached the client.

Recognized patterns include:

- Cloud credentials - AWS access key IDs (`AKIA…`) and secret keys, Google API keys (`AIza…`) and OAuth tokens (`ya29.…`)
- SaaS / provider tokens - GitHub/GitLab/Slack (`ghp_`, `ghs_`, `glpat-`, `xoxb-`, `xoxp-`), Stripe (`sk_live_`, `rk_live_`), OpenAI (`sk-…`, `sk-proj-…`), HashiCorp Vault (`hvs.`, `hvb.`, `hvr.`), npm, and SendGrid (`SG.…`) keys
- Generic secrets - PEM private key blocks, JWTs, bearer tokens in headers, credentials embedded in URLs (`proto://user:pass@host`), common secret env-var assignments (e.g. `*_PASSWORD=`, `*_SECRET=`), and high-entropy hex values in a key-name context

This is **defense-in-depth, not a guarantee.** It catches structurally recognizable secrets, not every possible format. Do not rely on it as the only control - prefer `readonly` or `approved` mode on machines that hold sensitive data, and avoid commands that print credentials in the first place.

---

## Where tokens are stored

| Token | Stored on | Format |
|---|---|---|
| `ADMIN_PASSWORD` | Your environment / secrets manager | Raw (you set it) |
| `TOKEN_PEPPER` | Your environment / secrets manager | Raw (you set it) |
| Install token | **Host:** agent config file until claim, then cleared. **k8s:** the bootstrap Secret (or `--set`) | Raw until claim, then gone |
| Fleet join token | Autoscaler instance startup config - user-data / startup-script (reusable across instances) | Raw - **protect it** (rotatable/revocable) |
| Agent token | **Host:** agent config file (`/etc/reach-agent/config.json`). **k8s:** a Kubernetes Secret the agent manages - **never written to the pod filesystem** | Raw - **protect it** |
| API token (`tok_`) | Returned once at creation; not stored by the backend | Raw - user must store it |
| All token hashes | Database (DynamoDB or PostgreSQL) | `HMAC-SHA256(TOKEN_PEPPER, token)` only |

On a **host**, the agent config file is written with `0600` permissions and owned by the `reach-agent` system user - protect it like an SSH private key. In **Kubernetes**, the agent token lives only in a Secret the agent shares across replicas (the pod's root filesystem is read-only, so nothing is cached on a node's disk); anyone with `get secret` in the agent's namespace can read it, so scope namespace access accordingly. (The agent identifies itself to the backend by token hash - there is no `agent_id` on the agent to steal; see [ARCHITECTURE.md → Token model](ARCHITECTURE.md#token-model).)

---

## Token rotation

**Agent tokens** rotate automatically every 30 days. The agent checks token age on each poll and calls `POST /agent/rotate-token` using the current still-valid token. The new token is written to disk atomically before the old one is invalidated - no lockout window.

Tenant admins can also trigger an out-of-band rotation from the tenant console under **Agents → [agent] → Request rotation**. This sets a flag on the agent record; the agent self-rotates on its next sync and the flag is cleared. The agent stays connected throughout.

**Fleet join tokens** are rotated from the tenant console under **Fleets → [fleet] → Rotate**. Rotation issues a new join token while keeping the previous one valid for a grace window (default 24h, or immediate) so you can update the autoscaler's launch/instance template before the old token stops working. Revoking the fleet invalidates the token entirely and stops all new enrollment.

**User API tokens** do not expire automatically. Revoke individual tokens from the tenant console under **API Tokens**, or revoke all tokens for a user under **Users → [user] → Revoke all tokens**.

**`SESSION_SIGNING_KEY` is safe to rotate** - set a new value and restart. The only effect is that active console sessions stop verifying, so users log in again. There is no data impact and nothing to migrate. Rotate it on a schedule, or immediately if you suspect a session token was leaked.

**`TOKEN_PEPPER` must never be rotated** - changing it invalidates every agent token, user token, and install token simultaneously. See [SELF_HOSTING.md](SELF_HOSTING.md#token_pepper-is-permanent).

---

## How to revoke access

**Revoke an agent**: from the tenant console, go to **Agents → [agent] → Revoke**. Cuts sync access immediately and removes the agent from all user access lists. The agent goes dormant on its next poll. Reissue an install token to bring it back.

**Revoke a user's access** - two levels, depending on whether you distrust the *account* or just a *credential*:

- **Disable the account** (**Users → [user] → Disable**) - cuts **all** access immediately: existing console sessions **and** API tokens stop authenticating, not just future logins. Re-enable to restore, or **Delete** a disabled user to remove them permanently (which also purges their API tokens). Use when the person should lose access.
- **Revoke all tokens** (**Users → [user] → Revoke all tokens**) - kills only the user's API tokens while they keep console access and can mint fresh ones. Use when a *token* is compromised (leaked in CI, a commit, a laptop) but the person is fine - it rotates their programmatic credentials without locking them out.

Other users are unaffected in both cases.

**Scope a user's agent access**: from the tenant console, go to **Users → [user] → Access**. Non-admins start with **no access** and are granted specific agents/fleets as **read-only** or **read-write** (admins are always tenant-wide). Read access hides everything else (404); a read-only grant additionally blocks write commands (403) in any mode. See [SELF_HOSTING → Per-user agent access](SELF_HOSTING.md#per-user-agent-access).

---

## How audit history works

Every command submitted through Reach creates a **job record** with:
- Who submitted it (`created_by` user ID)
- Which agent it targeted
- The exact command string
- Status (`SUCCEEDED`, `FAILED`, `REJECTED`, `EXPIRED`)
- Exit code, stdout, stderr, and duration
- Timestamps for creation, start, and completion

Terminal job records are deleted by the daily cleanup after the tenant's `job_retention_days` setting (default 7).

Job history is available in the tenant console under **Jobs**, and platform-wide in the platform admin console under **Audit Logs**. The audit log covers 35+ event types including platform and tenant logins (success and failure), user management, agent lifecycle events (create, revoke, rotate, unreachable, recover), policy changes, approval requests/reviews/pre-approvals, and API-token operations. See the full action list in [API.md](API.md#audit-log-actions). A tenant's own audit entries are retained for its `audit_retention_days` setting (default 90); agent status-history records for `agent_history_retention_days` (default 30). Cross-tenant platform-admin audit entries follow the deployment-wide `AUDIT_RETENTION_DAYS` env var (default 90).

In `approved` mode, every blocked write also creates an **approval record** - a persistent log of what was attempted, who attempted it, and when. Denied and expired approval records are retained for the tenant's `approval_retention_days` setting (default 7) before cleanup. Approved records persist until manually deleted.

Per-tenant retention windows are **tenant settings**, edited under **Settings** in the tenant console (defaults: `approval_retention_days` 7, `job_retention_days` 7, `run_retention_days` 30, `audit_retention_days` 90, `agent_history_retention_days` 30) and overridable by the platform admin. Only the cross-tenant platform audit trail uses the `AUDIT_RETENTION_DAYS` env var. For compliance-grade logging that outlives these windows, forward DynamoDB Streams or PostgreSQL WAL to your preferred log sink.

---

## What happens if the backend is compromised

An attacker with read access to the **database only** (no `TOKEN_PEPPER`) sees:
- HMAC hashes - not usable as tokens (cannot reverse without the pepper)
- Agent IDs, tenant IDs, job history, approval records - metadata exposure

An attacker with the **database + `TOKEN_PEPPER`**:
- Can forge any token (agent, API token, install token)
- Can impersonate any agent or user
- **Immediate response**: rotate `ADMIN_PASSWORD`, reissue install tokens for all agents (resets all agent tokens), revoke all user API tokens, then change `TOKEN_PEPPER` **last** (changing it simultaneously invalidates everything above - do it after you've already reset)

An attacker with **`ADMIN_PASSWORD`** only (no database):
- Can create tenants and users; cannot manage agents, policies, or approvals
- Cannot read existing token hashes (they need the DB for that)
- **Immediate response**: redeploy with a new `ADMIN_PASSWORD`

An attacker with **agent config file access on the remote machine**:
- Has the raw agent token - can submit any command the agent's mode/policy allows
- **Immediate response**: revoke the agent from the tenant console, then reissue an install token and reinstall

---

## Recommended deployment for production

**Network**
- Deploy the backend behind HTTPS with a valid TLS certificate. The agent verifies TLS by default.
- Do not expose the backend admin endpoints publicly if avoidable - restrict `/admin/*` routes by IP or put them behind a private load balancer.
- The backend ships with permissive CORS (`allow_origins=["*"]`). This is low-risk because every authenticated endpoint uses a `Authorization: Bearer` token rather than cookies, so a third-party site cannot ride a logged-in user's session. If you want defense-in-depth, restrict allowed origins to your console's domain at the reverse proxy.
- Rate limiting keys off the client IP for unauthenticated endpoints. If you front the backend with a proxy or load balancer, ensure it sets `X-Forwarded-For` correctly so per-IP limits apply to the real client.

**Secrets**
- Store `TOKEN_PEPPER` and `ADMIN_PASSWORD` in a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.), not in environment files or version control.
- Rotate `ADMIN_PASSWORD` regularly. Never rotate `TOKEN_PEPPER`.
- Use a long, randomly generated `TOKEN_PEPPER` (at least 32 bytes of entropy).

**Agent policy**
- Run production agents in `approved` mode. Allowlist only the commands your automation actually needs.
- Avoid `wild` mode on shared or production machines. Reserve it for personal dev boxes where you are the only user.
- Grant each user the least access they need: non-admins start with none, so add specific agents/fleets, and prefer **read-only** where they don't need to run writes. Keep the `admin` role (tenant-wide) to as few people as possible.

**Monitoring**
- Alert on unexpected platform admin activity - `admin.login` / `admin.login_failed` events from unknown IPs, and new tenants or user changes outside a deployment window. Repeated `admin.login_failed` entries indicate a brute-force attempt against `ADMIN_PASSWORD`.
- Monitor the tenant audit log for policy changes, approval decisions, and repeated `user.login_failed` events (failed logins against tenant accounts) outside normal operations.
- Set each tenant's `approval_retention_days` setting long enough for your incident response window (14–30 days recommended for production), and its `audit_retention_days` (plus the platform-wide `AUDIT_RETENTION_DAYS` env for the cross-tenant trail) long enough for your audit/compliance window (the default is 90).

**Token hygiene**
- Share API tokens over a secure channel (not plaintext Slack/email). Tokens are shown once at creation.
- Revoke user API tokens when team members leave.
- Treat agent tokens like SSH private keys - they grant command execution on the machine they're bound to.
