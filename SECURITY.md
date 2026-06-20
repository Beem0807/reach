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
- **AI agent overreach** - `readonly` mode blocks writes server-side before they reach the agent. `approved` mode requires explicit admin pre-approval for each write command; unapproved writes are blocked and surfaced as a pending approval record, not silently dropped or executed.
- **Exposed inbound SSH** - agents communicate outbound-only over HTTPS. No ports are opened on the remote machine. No SSH keys to distribute or rotate.
- **Unapproved production writes** - in `approved` mode, the agent cannot execute a write command unless an admin has explicitly allowlisted it. The allowlist is enforced both server-side and locally on the agent (Landlock on Linux, `is_write` flag enforcement on macOS).
- **Token theft across machines** - agent tokens are bound to a machine fingerprint at claim time. A token captured from one machine cannot be replayed from another.
- **Cross-tenant access** - tenant isolation is enforced in the storage layer. A user token from tenant A cannot list, access, or submit jobs to tenant B's agents.

**Reach does not protect against:**

- **Malicious or compromised machine owner** - the person who owns the machine the agent runs on can read the agent config file, extract the agent token, and use it to submit arbitrary commands (in wild mode) or approved commands (in approved mode). Reach is not a security boundary between a machine owner and their own machine.
- **Compromised `ADMIN_PASSWORD`** - whoever knows `ADMIN_PASSWORD` can create and manage tenants and users across all tenants. Treat it like a root credential for provisioning. Policy management, approvals, and agent operations are controlled by tenant admin users separately.
- **Compromised `TOKEN_PEPPER`** - `TOKEN_PEPPER` is used to hash all tokens. If it leaks alongside the database, all token hashes become forgeable. See [Backend compromise](#what-happens-if-the-backend-is-compromised).
- **Kernel-level bypasses** - the Landlock sandbox on Linux protects against unapproved writes at the kernel level, but kernel exploits or privileged container escapes are out of scope.
- **Command-obfuscation edge cases in wild mode** - wild mode runs commands through `bash -lc`. Shell aliases, `$PATH` manipulation, or creative quoting on the submitting machine may produce commands that look safe but behave differently on the remote. Use `approved` or `readonly` mode on any machine where this matters.
- **Replay within the token lifetime** - a captured agent token can be used from any machine with the correct fingerprint until the token expires or is revoked. Rotate immediately if you suspect compromise.

---

## Security design

For full architectural detail see [ARCHITECTURE.md](ARCHITECTURE.md).

**Tokens** - no token is stored raw. Only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted. If the database is compromised without `TOKEN_PEPPER`, tokens cannot be recovered or forged.

**User passwords** - console login passwords are hashed with PBKDF2-HMAC-SHA256 (200,000 iterations) using a unique 16-byte random salt per user, stored as `pbkdf2$salt$hash`. The raw password is never stored. New passwords must be at least 8 characters. First-login passwords are randomly generated, issued once, and must be changed before the account can be used.

**Console session tokens** - the web console issues short-lived (8-hour) HS256 session tokens that are distinct from API tokens and are never persisted server-side. The platform admin session is signed with `ADMIN_PASSWORD`; the tenant console session is signed with a dedicated `SESSION_SIGNING_KEY` and carries the user's tenant and role. Both signing secrets are **safe to rotate** - doing so only invalidates active sessions, so users simply log in again. This is deliberately separate from `TOKEN_PEPPER` (which hashes stored credentials and cannot be rotated without reissuing everything), so session-key rotation never touches stored tokens. The CLI and MCP server do **not** use session tokens - they authenticate with long-lived API tokens (`tok_`).

**Constant-time comparison** - passwords, token hashes, session signatures, and `ADMIN_PASSWORD` are all compared with `hmac.compare_digest` to avoid leaking information through timing.

**Agent token binding** - agent tokens are bound to a machine fingerprint at claim time. A token stolen from one machine cannot be replayed from another.

**Install token** - one-time use, 24-hour expiry, cleared from disk after a successful claim.

**Tenant isolation** - user tokens can only access agents and jobs within their own tenant. The storage layer enforces this; there is no client-side filtering.

**Policy enforcement** - the global blocklist and mode-specific write blocking are enforced server-side before the job reaches the agent. The agent enforces a second time locally (Landlock on Linux, `is_write` flag on macOS). Two independent enforcement points must both be bypassed.

**No inbound connections** - agents make outbound HTTPS requests only. No ports are opened on the remote machine.

**Config file permissions** - agent config files are written with `0600` permissions (owner read/write only).

**sudo access** - the agent runs as a non-privileged `reach-agent` system user with no sudoers entry by default. `sudo` commands will fail unless you explicitly grant sudo access. For production, prefer `approved` mode and allowlist only the specific `sudo` commands needed. See [Agent sudo access](SELF_HOSTING.md#agent-sudo-access).

**Secret redaction** - command output is scrubbed for recognizable secrets before it is stored or shown to an AI agent. See [Secret redaction in command output](#secret-redaction-in-command-output).

**Brute-force mitigation** - all login endpoints (`POST /admin/login`, `POST /tenant/login`) are rate limited to 10 requests/minute per IP, and every other endpoint is rate limited as well (see [API.md](API.md#rate-limits)). There is no per-account lockout counter; protection relies on rate limiting combined with strong, randomly generated credentials. Counters are in-memory (per-process) by default - if you run more than one backend replica, point them at a shared store via `RATE_LIMIT_STORAGE_URI` so the limit holds across replicas (see [Running multiple replicas](SELF_HOSTING.md#running-multiple-replicas)).

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
| Install token | Agent config file (`/etc/reach-agent/config.json`) until claim; then cleared | Raw until claim, then gone |
| Agent token | Agent config file (`/etc/reach-agent/config.json`) | Raw - **protect this file** |
| API token (`tok_`) | Returned once at creation; not stored by the backend | Raw - user must store it |
| All token hashes | Database (DynamoDB or PostgreSQL) | `HMAC-SHA256(TOKEN_PEPPER, token)` only |

The agent config file is written with `0600` permissions and owned by the `reach-agent` system user. Only root and `reach-agent` can read it. Protect it like an SSH private key.

---

## Token rotation

**Agent tokens** rotate automatically every 30 days. The agent checks token age on each poll and calls `POST /agent/rotate-token` using the current still-valid token. The new token is written to disk atomically before the old one is invalidated - no lockout window.

Tenant admins can also trigger an out-of-band rotation from the tenant console under **Agents → [agent] → Request rotation**. This sets a flag on the agent record; the agent self-rotates on its next sync and the flag is cleared. The agent stays connected throughout.

**User API tokens** do not expire automatically. Revoke individual tokens from the tenant console under **API Tokens**, or revoke all tokens for a user under **Users → [user] → Revoke all tokens**.

**`SESSION_SIGNING_KEY` is safe to rotate** - set a new value and restart. The only effect is that active console sessions stop verifying, so users log in again. There is no data impact and nothing to migrate. Rotate it on a schedule, or immediately if you suspect a session token was leaked.

**`TOKEN_PEPPER` must never be rotated** - changing it invalidates every agent token, user token, and install token simultaneously. See [SELF_HOSTING.md](SELF_HOSTING.md#token_pepper-is-permanent).

---

## How to revoke access

**Revoke an agent**: from the tenant console, go to **Agents → [agent] → Revoke**. Cuts sync access immediately and removes the agent from all user access lists. The agent goes dormant on its next poll. Reissue an install token to bring it back.

**Revoke a user**: from the tenant console, go to **Users → [user] → Revoke all tokens**. All their API tokens stop working immediately. Other users are unaffected.

**Restrict a user to specific agents**: from the tenant console, go to **Users → [user] → Agent Access**. Set an explicit list of agents the user can see - all others return 404. Pass an empty list to lock them out of all agents without deleting the account.

---

## How audit history works

Every command submitted through Reach creates a **job record** with:
- Who submitted it (`created_by` user ID)
- Which agent it targeted
- The exact command string
- Status (`SUCCEEDED`, `FAILED`, `REJECTED`, `EXPIRED`)
- Exit code, stdout, stderr, and duration
- Timestamps for creation, start, and completion

Terminal job records are deleted by the daily cleanup after `JOB_RETENTION_DAYS` (default 7).

Job history is available in the tenant console under **Jobs**, and platform-wide in the platform admin console under **Audit Logs**. The audit log covers 35+ event types including platform and tenant logins (success and failure), user management, agent lifecycle events (create, revoke, rotate, unreachable, recover), policy changes, approval requests/reviews/pre-approvals, and API-token operations. See the full action list in [API.md](API.md#audit-log-actions). Audit entries are retained for `AUDIT_RETENTION_DAYS` (default 90) before the daily cleanup deletes them; agent status-history records for `AGENT_HISTORY_RETENTION_DAYS` (default 30).

In `approved` mode, every blocked write also creates an **approval record** - a persistent log of what was attempted, who attempted it, and when. Denied and expired approval records are retained for `APPROVAL_RETENTION_DAYS` (default 7) before cleanup. Approved records persist until manually deleted.

The four retention windows are independent environment variables: `APPROVAL_RETENTION_DAYS` (7), `JOB_RETENTION_DAYS` (7), `AUDIT_RETENTION_DAYS` (90), and `AGENT_HISTORY_RETENTION_DAYS` (30). For compliance-grade logging that outlives these windows, forward DynamoDB Streams or PostgreSQL WAL to your preferred log sink.

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
- Restrict users to specific agents rather than granting full tenant access where possible.

**Monitoring**
- Alert on unexpected platform admin activity - `admin.login` / `admin.login_failed` events from unknown IPs, and new tenants or user changes outside a deployment window. Repeated `admin.login_failed` entries indicate a brute-force attempt against `ADMIN_PASSWORD`.
- Monitor the tenant audit log for policy changes, approval decisions, and repeated `user.login_failed` events (failed logins against tenant accounts) outside normal operations.
- Set `APPROVAL_RETENTION_DAYS` long enough for your incident response window (14–30 days recommended for production), and `AUDIT_RETENTION_DAYS` long enough for your audit/compliance window (the default is 90).

**Token hygiene**
- Share API tokens over a secure channel (not plaintext Slack/email). Tokens are shown once at creation.
- Revoke user API tokens when team members leave.
- Treat agent tokens like SSH private keys - they grant command execution on the machine they're bound to.
