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
- **Compromised `ADMIN_TOKEN`** - whoever holds `ADMIN_TOKEN` can create tenants, agents, and users; change policy modes; approve any command; and revoke access. Treat it like a root credential.
- **Compromised `TOKEN_PEPPER`** - `TOKEN_PEPPER` is used to hash all tokens. If it leaks alongside the database, all token hashes become forgeable. See [Backend compromise](#what-happens-if-the-backend-is-compromised).
- **Kernel-level bypasses** - the Landlock sandbox on Linux protects against unapproved writes at the kernel level, but kernel exploits or privileged container escapes are out of scope.
- **Command-obfuscation edge cases in wild mode** - wild mode runs commands through `bash -lc`. Shell aliases, `$PATH` manipulation, or creative quoting on the submitting machine may produce commands that look safe but behave differently on the remote. Use `approved` or `readonly` mode on any machine where this matters.
- **Replay within the token lifetime** - a captured agent token can be used from any machine with the correct fingerprint until the token expires or is revoked. Rotate immediately if you suspect compromise.

---

## Security design

For full architectural detail see [ARCHITECTURE.md](ARCHITECTURE.md).

**Tokens** - no token is stored raw. Only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted. If the database is compromised without `TOKEN_PEPPER`, tokens cannot be recovered or forged.

**Agent token binding** - agent tokens are bound to a machine fingerprint at claim time. A token stolen from one machine cannot be replayed from another.

**Install token** - one-time use, 24-hour expiry, cleared from disk after a successful claim.

**Tenant isolation** - user tokens can only access agents and jobs within their own tenant. The storage layer enforces this; there is no client-side filtering.

**Policy enforcement** - the global blocklist and mode-specific write blocking are enforced server-side before the job reaches the agent. The agent enforces a second time locally (Landlock on Linux, `is_write` flag on macOS). Two independent enforcement points must both be bypassed.

**No inbound connections** - agents make outbound HTTPS requests only. No ports are opened on the remote machine.

**Config file permissions** - agent config files are written with `0600` permissions (owner read/write only).

**sudo access** - the agent runs as a non-privileged `reach-agent` system user with no sudoers entry by default. `sudo` commands will fail unless you explicitly grant sudo access. For production, prefer `approved` mode and allowlist only the specific `sudo` commands needed. See [Agent sudo access](SELF_HOSTING.md#agent-sudo-access).

---

## Where tokens are stored

| Token | Stored on | Format |
|---|---|---|
| `ADMIN_TOKEN` | Your environment / secrets manager | Raw (you set it) |
| `TOKEN_PEPPER` | Your environment / secrets manager | Raw (you set it) |
| Install token | Agent config file (`/etc/reach-agent/config.json`) until claim; then cleared | Raw until claim, then gone |
| Agent token | Agent config file (`/etc/reach-agent/config.json`) | Raw - **protect this file** |
| User token | Returned once at creation; not stored by the backend | Raw - user must store it |
| All token hashes | Database (DynamoDB or PostgreSQL) | `HMAC-SHA256(TOKEN_PEPPER, token)` only |

The agent config file is written with `0600` permissions and owned by the `reach-agent` system user. Only root and `reach-agent` can read it. Protect it like an SSH private key.

---

## Token rotation

**Agent tokens** rotate automatically every 30 days. The agent checks token age on each poll and calls `POST /agent/rotate-token` using the current still-valid token. The new token is written to disk atomically before the old one is invalidated - no lockout window.

Admins can also trigger an out-of-band rotation at any time:

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/rotate-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

This sets a flag on the agent record. On the next sync the agent self-rotates and the flag is cleared. The agent stays connected throughout.

**User tokens** do not expire automatically. Rotate manually:

```bash
curl -s -X POST "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/rotate-token" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**`TOKEN_PEPPER` must never be rotated** - changing it invalidates every agent token, user token, and install token simultaneously. See [SELF_HOSTING.md](SELF_HOSTING.md#token_pepper-is-permanent).

---

## How to revoke access

**Revoke an agent** (cuts sync access immediately, removes from all user access lists):

```bash
curl -s -X POST "$API_URL/admin/agents/agent_xxxxx/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

The agent cannot sync after revocation. It will stop polling on its next attempt and go dormant. Reissue an install token to bring it back.

**Revoke a user** (invalidates their token immediately):

```bash
curl -s -X DELETE "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Restrict a user to specific agents** (instead of full tenant access):

```bash
curl -s -X PUT "$API_URL/admin/tenants/tenant_xxxxx/users/user_xxxxx/agents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '["agent_a", "agent_b"]'
```

Pass `["*"]` to restore unrestricted access. Pass `[]` to lock the user out of all agents without deleting them.

---

## How audit history works

Every command submitted through Reach creates a **job record** with:
- Who submitted it (`created_by` user ID)
- Which agent it targeted
- The exact command string
- Status (`SUCCEEDED`, `FAILED`, `REJECTED`, `EXPIRED`)
- Exit code, stdout, stderr, and duration
- Timestamps for creation, start, and completion

Terminal job records (SUCCEEDED, FAILED, REJECTED, EXPIRED) are deleted by the daily heartbeat sweep, the same mechanism used for approval records. Retention is controlled by `JOB_RETENTION_DAYS` (default 7). This applies equally to both DynamoDB and PostgreSQL deployments.

Admin can query full history:

```bash
curl -s "$API_URL/admin/jobs?tenant_id=tenant_xxxxx" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

In `approved` mode, every blocked write also creates an **approval record** - a persistent log of what was attempted, who attempted it, and when. Approval records for denied or expired commands are retained for `APPROVAL_RETENTION_DAYS` (default 7) before the daily cleanup removes them. Approved records persist until manually deleted.

There is currently no separate audit log stream - the job and approval tables are the audit trail. For compliance-grade logging, forward DynamoDB Streams or PostgreSQL WAL to your preferred log sink.

---

## What happens if the backend is compromised

An attacker with read access to the **database only** (no `TOKEN_PEPPER`) sees:
- HMAC hashes - not usable as tokens (cannot reverse without the pepper)
- Agent IDs, tenant IDs, job history, approval records - metadata exposure

An attacker with the **database + `TOKEN_PEPPER`**:
- Can forge any token (agent, user, install)
- Can impersonate any agent or user
- **Immediate response**: rotate `ADMIN_TOKEN`, issue new install tokens for all agents (resets all agent tokens), issue new user tokens for all users, then change `TOKEN_PEPPER` **last** (changing it simultaneously invalidates everything above - do it after you've already reset)

An attacker with **`ADMIN_TOKEN`** only (no database):
- Can create agents/tenants/users, approve commands, change policy modes
- Cannot read existing token hashes (they need the DB for that)
- **Immediate response**: rotate `ADMIN_TOKEN` by redeploying with a new value

An attacker with **agent config file access on the remote machine**:
- Has the raw agent token - can submit any command the agent's mode/policy allows
- **Immediate response**: revoke the agent via `POST /admin/agents/{id}/revoke`, then reissue an install token and reinstall

---

## Recommended deployment for production

**Network**
- Deploy the backend behind HTTPS with a valid TLS certificate. The agent verifies TLS by default.
- Do not expose the backend admin endpoints publicly if avoidable - restrict `/admin/*` routes by IP or put them behind a private load balancer.

**Secrets**
- Store `TOKEN_PEPPER` and `ADMIN_TOKEN` in a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.), not in environment files or version control.
- Rotate `ADMIN_TOKEN` regularly. Never rotate `TOKEN_PEPPER`.
- Use a long, randomly generated `TOKEN_PEPPER` (at least 32 bytes of entropy).

**Agent policy**
- Run production agents in `approved` mode. Allowlist only the commands your automation actually needs.
- Avoid `wild` mode on shared or production machines. Reserve it for personal dev boxes where you are the only user.
- Restrict users to specific agents rather than granting full tenant access where possible.

**Monitoring**
- Alert on unexpected `ADMIN_TOKEN` usage (new tenants, policy changes outside a deployment window).
- Monitor job history for commands that look anomalous for the agent's role.
- Set `APPROVAL_RETENTION_DAYS` long enough for your incident response window (14–30 days recommended for production).

**Token hygiene**
- Distribute user tokens over a secure channel (not plaintext Slack/email).
- Revoke user tokens when team members leave.
- Treat agent tokens like SSH private keys - they grant command execution on the machine they're bound to.
