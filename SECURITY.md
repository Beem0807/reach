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

## Scope

The following are in scope:

- Authentication bypass or token forgery
- Privilege escalation (accessing another tenant's agents or jobs)
- Command injection through policy bypass (running blocked commands)
- Agent token theft or replay across machine fingerprints
- Information disclosure (raw tokens, other tenants' data)
- Remote code execution in the backend or agent

The following are out of scope:

- Vulnerabilities that require physical access to the machine running the agent
- Issues in third-party dependencies that are not exploitable in this project
- Self-inflicted issues: a user with `ADMIN_TOKEN` misconfiguring their own deployment
- Rate limiting bypass on the Lambda deployment (API Gateway handles this separately)
- Security of the tunnel (cloudflared / ngrok) used in local deployments - those are third-party tools

---

## Security design

A brief summary of the security model. For full detail see [ARCHITECTURE.md](ARCHITECTURE.md).

**Tokens** - no token is stored raw. Only `HMAC-SHA256(TOKEN_PEPPER, token)` hashes are persisted. If the database is compromised, tokens cannot be recovered or forged without `TOKEN_PEPPER`.

**Agent token binding** - agent tokens are bound to a machine fingerprint at claim time. A token stolen from one machine cannot be replayed from another.

**Install token** - one-time use, 24-hour expiry, cleared from disk after a successful claim.

**Tenant isolation** - user tokens can only access agents and jobs within their own tenant. The storage layer enforces this; there is no client-side filtering.

**Policy enforcement** - commands are evaluated server-side before being queued. A globally blocked command (fork bombs, `rm -rf /`, raw disk writes, shutdown) is rejected regardless of the agent's policy mode. The agent never sees a blocked command.

**No inbound connections** - agents make outbound HTTPS requests only. No ports are opened on the remote machine.

**Config file permissions** - agent config files are written with `0600` permissions.

**sudo access** - the agent runs as a non-privileged `reach` system user with no sudoers entry by default. `sudo` commands will fail unless you explicitly grant the `reach` user sudo access. Granting it is fine for personal or single-user setups in wild mode. For shared multi-user environments, prefer **approved mode** and allowlist only the specific sudo commands needed. See [Agent sudo access](SELF_HOSTING.md#agent-sudo-access) for setup instructions.
