# reach

**Let AI agents operate your production machines - safely.** Reads run freely; every _write_ is **blocked and queued for a human to approve** before it touches anything. No SSH, no VPN, no open ports.

The one idea: on a production agent, an AI (or any automation) can look at everything, but it cannot change anything until a person approves the exact action.

```bash
# your agent asks to do something destructive on prod...
$ reach exec --agent prod -- kubectl delete pods --all -n payments
  Status: REJECTED - approval required; a request was sent to your operator.

# ...an operator approves the pending rule once - structured, not a string:
#   { verb: delete, resource: pods, namespace: payments, name: * }
$ reach approvals approve appr_9f2c
  Approved.

# ...and now it runs - this time and next, without re-asking.
$ reach exec --agent prod -- kubectl delete pods --all -n payments
  Status: SUCCEEDED
```

You approve a **rule**, not a command string - so an approved action can't be extended (`… | tee /etc/x`, `… && rm -rf`) to smuggle something past it. That's the difference between "AI on prod" being a liability and being something you can sleep next to.

## ⚡ 2-minute Quick Start

Zero to your **AI agent running commands on a real machine**, in three steps:

**1. Start Reach.** One command runs the backend, creates your tenant and first agent, installs the CLI, and logs you in:

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
```

**2. Install the agent** on the machine you want to control. The script prints a ready-to-paste command - a `curl … | sudo bash` for a host, or `helm install …` for Kubernetes.

**3. Connect your AI tool.** `reach agent-init` writes the context file for Claude Code / Cursor and prints the MCP server config to drop in - now your agent can drive that machine through Reach:

```bash
reach agent-init
```

Now ask your AI agent to run something - or check it yourself:

```bash
reach exec -- hostname
```

That's it: your AI agent has **controlled, audited** access to the machine - no SSH, no VPN, no open ports. Setup **asks you to pick the mode** and **defaults to `readonly`** (safe: the agent can look but not touch) - a new agent never runs writes by default. For real operations, choose **`approved`** (**Agents → [agent] → Policy**) so reads run and writes stop for sign-off - that's the posture up top; `wild` (unrestricted) is opt-in for personal/dev boxes. On AWS instead? Swap step 1 for `lambda-setup.sh`. Docker, Kubernetes, and production hardening are in [SELF_HOSTING.md](SELF_HOSTING.md).

---

> ### ⚠️ What Reach is - and isn't
>
> Reach gives AI agents **controlled, audited** command execution on machines you own. It is **not a sandbox for arbitrary untrusted commands.**
>
> - **On production, run `approved` mode** - the posture above: reads run, every write needs a human-approved structured rule, everything else is blocked and queued. This is the intended way to point an AI at real infrastructure.
> - **`wild` mode is the opt-in exception** - it runs anything (reboots, deletes, package installs). Use it only on personal/dev boxes where you're the sole user.
> - Reach is **not a security boundary against the machine's own owner/root** - whoever controls the host can read the agent's token.
>
> See **[SECURITY.md](SECURITY.md)** for the full threat model, and **[POLICIES.md](POLICIES.md)** for how the modes work.

---

## What can I use this for?

- **Give an AI agent standing access to production - safely** - lock agents to `approved` mode: reads run, every write waits for a human to approve the exact rule, everything is audited. The headline use case.
- **Run agent-driven operations you can trust** - restarts, scaling, rollouts, targeted deletes - each gated by a structured rule an operator approved once, not a command string that can be extended past the check.
- **Check Kubernetes pods from an in-cluster agent** - install the agent inside the cluster, run `kubectl` through it from your laptop; non-`kubectl` tools (helm, flux) are approvable the same way.
- **Let Claude Code inspect a remote dev box** - check what's running, tail logs, or diff configs without leaving your editor (reads never need approval).
- **Debug Docker containers without SSH** - `reach exec -- docker ps`, `docker logs`, `docker inspect` from anywhere.
- **Manage an autoscaling group as one fleet** - bake a fleet join token into your autoscaler's launch/instance template (AWS ASG, GCP MIG, Azure VMSS, …); instances auto-enroll on scale-out and clean themselves up on scale-in, inheriting the fleet's approved rules.

## How it works

The agent never accepts inbound connections - it makes outbound HTTPS requests to your backend, polls for jobs, runs them, and posts results back.

1. Deploy the backend (Local, Lambda, or Docker).
2. Register agents in the console (or let the setup script do it) - you get ready-to-paste install commands.
3. Install the CLI locally and the agent on each machine.
4. Queue commands via the CLI, MCP, or the console; the agent picks them up and runs them; results come back.

The local and Lambda setup scripts do 1-3 for you. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Screenshots

<table>
  <tr>
    <td width="50%"><a href="docs/images/login.png"><img src="docs/images/login.png" alt="Sign in"></a><br><b>Sign in</b> - tenant-scoped console login (tenant name, username, password); platform admins sign in separately.</td>
    <td width="50%"><a href="docs/images/agents.png"><img src="docs/images/agents.png" alt="Agents list"></a><br><b>Agents</b> - every host and Kubernetes agent in your tenant, with status, policy mode, and cluster-RBAC drift at a glance.</td>
  </tr>
  <tr>
    <td width="50%"><a href="docs/images/new-agent.png"><img src="docs/images/new-agent.png" alt="New agent"></a><br><b>New agent</b> - enroll a host or Kubernetes agent: pick a version, execution mode, and access.</td>
    <td width="50%"><a href="docs/images/rbac-drift.png"><img src="docs/images/rbac-drift.png" alt="Cluster RBAC drift"></a><br><b>Cluster RBAC drift</b> - an agent's effective permissions diffed against the acknowledged baseline, down to the exact verbs.</td>
  </tr>
  <tr>
    <td width="50%"><a href="docs/images/fleets.png"><img src="docs/images/fleets.png" alt="Fleets"></a><br><b>Fleets</b> - reusable-join-token groups of host agents (autoscaling groups). Members inherit the fleet's mode, tags, and grants; grant drift is flagged and reconciled per member or fleet-wide.</td>
    <td width="50%"><a href="docs/images/jobs.png"><img src="docs/images/jobs.png" alt="Jobs history"></a><br><b>Jobs</b> - command history across the fleet; writes in <code>approved</code> mode are gated (note the rejected <code>kubectl delete</code>).</td>
  </tr>
</table>

---

## Deployment

Reach is self-hosted. The Quick Start above uses the Local script; three backends are available:

| Backend                             | Setup                                                                        |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| **Local** (no cloud account)        | `curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh \| bash`  |
| **AWS Lambda + DynamoDB**           | `curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh \| bash` |
| **Docker + PostgreSQL** (any cloud) | `docker run … nabeemdev/reach:0.1.0`, then finish in the console at `/ui`    |

The Local and Lambda scripts are interactive and re-runnable (`--update`, `--down`, …) and enroll your first agent for you. Full setup - environment variables, installing the CLI, enrolling **host / Kubernetes / fleet** agents, grants, and lifecycle - is in **[SELF_HOSTING.md](SELF_HOSTING.md)**.

---

## Console & CLI

The backend serves a **web console** at `/ui` (platform-admin and per-tenant logins) to manage agents, fleets, users, approvals, API tokens, and audit logs - and to **run commands** (single-agent or fleet/tag fan-out, write-gated to what you can access, with a dry-run preview + confirm). The **CLI** and **MCP server** authenticate with API tokens:

```bash
reach exec -- <command>                 # run on your default machine
reach exec --tag env:prod -- <command>  # fan out across a tag
reach fleets exec <fleet> -- <command>  # fan out across a fleet
```

`reach agent-init` wires Claude Code / Cursor in over MCP. Full command reference, profiles, aliases, and MCP setup: **[cli/README.md](cli/README.md)**; every HTTP endpoint: **[API.md](API.md)**.

---

## Policy modes

Each agent runs in one of three modes (set in the tenant console or via the API):

- **`approved`** ← the production mode - reads run; every write runs only if it matches a rule a human pre-approved for that agent, otherwise it's blocked and queued for review. This is the whole point of Reach.
- **`readonly`** - only reads run; any write/delete/restart/install is blocked. For a locked-down "look but don't touch" agent.
- **`wild`** - runs almost anything; only a catastrophic/abuse set (`rm -rf /`, `mkfs`, privileged escapes, reverse shells) is always blocked. For personal/dev boxes where you're the sole user.

Host and Kubernetes agents share these modes but enforce them differently - agent-side Landlock vs backend-side gating. Approvals are structured rules on both: host `{bin, args[]}` (positional `*`, trailing `...`), k8s `{verb, resource, namespace, name}`. Full detail (enforcement model, structured rules, `access_level`): **[POLICIES.md](POLICIES.md)**.

## Safety

Controlled execution by design: no inbound ports, outbound-HTTPS-only agents, a default command timeout, a **catastrophic-command blocklist** enforced server-side in every mode (`rm -rf /`, fork bombs, privileged escapes, exfiltration, reverse shells), and a full audit trail. Kubernetes agents add no-shell + a `kubectl` allowlist bounded by cluster RBAC. Full blocklist, threat model, and enforcement: **[SECURITY.md](SECURITY.md)** · **[POLICIES.md](POLICIES.md)**.

## Observability

A built-in **audit log** (every action - logins, agent/fleet lifecycle, policy changes, approvals - tenant- or platform-scoped, filterable, with CSV export), plus **Prometheus metrics**: the backend's `/metrics` (`reach_backend_*`, always on) and an opt-in agent `/metrics` (`reach_agent_*`). Detail: **[SELF_HOSTING.md → Backend metrics](SELF_HOSTING.md#backend-metrics-metrics)** · **[agent/README.md → Metrics](agent/README.md#metrics-opt-in)**.

---

## Documentation

| Doc                                                | What's in it                                                                                                                                    |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| [cli/README.md](cli/README.md)                     | The `reach` CLI and `reach-mcp` server - install, commands, profiles, aliases, MCP setup                                                        |
| [POLICIES.md](POLICIES.md)                         | Policy modes (approved/readonly/wild), approvals, host vs Kubernetes enforcement, structured host & k8s rules, `access_level`                   |
| [agent/README.md](agent/README.md)                 | How the agent works - host vs Kubernetes, credential-only identity, the poll loop, execution models, leader election, RBAC self-review, metrics |
| [deploy/helm/reach-agent](deploy/helm/reach-agent) | Kubernetes agent Helm chart - install, RBAC (`clusterAccess`), execution allowlist, and all values                                              |
| [SELF_HOSTING.md](SELF_HOSTING.md)                 | Deploy and operate your own backend (Local, AWS Lambda, Docker), setup, agent lifecycle, grants, blocked-command reference                      |
| [API.md](API.md)                                   | Complete HTTP endpoint reference, rate limits, pagination, audit-log actions                                                                    |
| [ARCHITECTURE.md](ARCHITECTURE.md)                 | How the pieces fit - command flow, token model, storage split, policy enforcement, approvals, fleets, multi-tenancy                             |
| [SECURITY.md](SECURITY.md)                         | Threat model, token storage and rotation, revoking access, audit history, production hardening                                                  |

---

## License

MIT - see [LICENSE](LICENSE).
