# Policy modes & approvals

Every agent runs in one of three **policy modes**, set in the tenant console (**Agents → [agent] → Policy**) or via the API (`PUT /tenant/agents/{id}/policy/mode`). The mode determines how each command is evaluated before it runs. Host and Kubernetes agents share the same three modes but classify and enforce them differently - see [Host vs Kubernetes enforcement](#host-vs-kubernetes-enforcement). (Members of a **fleet** inherit the fleet's mode instead of being set individually - changing the fleet's mode propagates to every member; see [SELF_HOSTING → Managing fleets](SELF_HOSTING.md#managing-fleets).)

For the internal enforcement design and the full `kubectl` verb classification, see [ARCHITECTURE.md](ARCHITECTURE.md#kubernetes-agents); for the always-blocked command reference, see [SELF_HOSTING.md](SELF_HOSTING.md); for the threat model, [SECURITY.md](SECURITY.md).

---

## Wild mode

Wild mode is intentionally permissive. It is designed for personal machines, dev environments, break-glass debugging, and power users who want full command flexibility.

Reach still rejects a small set of commands in wild mode - those that are catastrophic or abuse-like regardless of context: raw disk wipes (`mkfs`, `dd if=`, `wipefs`), recursive deletion of the root filesystem (`rm -rf /`), privileged container and host escapes (`docker run --privileged`, `nsenter --target 1`, `chroot /`), credential exfiltration (`env | curl`), fork bombs, and reverse shells (`/dev/tcp/`, `nc -e`, `socat exec:`). Everything else runs, including reboots, shutdowns, IaC destroys, cloud resource deletions, and package installs.

For production machines, use **Approved** mode and explicitly allowlist the write operations Reach is allowed to perform.

## Readonly mode

Readonly mode blocks any command that writes, deletes, installs, or mutates system state. This includes: file writes and deletes, process kills, service restarts, reboots and shutdowns, package managers, container mutations (`docker run/stop/rm`), firewall changes, user management, IaC destroys (`terraform destroy`, `pulumi destroy`), and cloud destructive operations (`aws ec2 terminate-instances`, `gcloud instances delete`).

Read-only commands (`ls`, `cat`, `git log`, `docker ps`, `kubectl get`, `aws describe-*`, `journalctl`) always pass.

Shell-chained commands are checked segment by segment - `ls && rm file.txt` is blocked even though the `ls` segment is safe.

On Linux, readonly mode enforcement is backed by Landlock (a kernel sandbox) on the agent. Commands run in a sandboxed subprocess that cannot write outside `/tmp`, providing defence-in-depth beyond the pattern-based check.

On macOS, Landlock is not available. Readonly mode relies entirely on the server-side blocked-command list - the server rejects blocked writes before they are queued, so the agent never receives them.

## Approved mode

Reads are always allowed - you do not need to add them to any list. Write and destructive operations (anything blocked in readonly mode) are only permitted if they match an approved **structured rule** for this agent.

**How it works (host agents):**

1. When a command is submitted, the server classifies it as a write or read (`is_write: true/false`). A **write** is parsed into an `argv` (`{bin, args}`) and will run with `execve` (**no shell**); a **read** runs as-is (freeform shell). A write that uses shell operators (`| ; && $() > *`) can't be structured, so it is **refused in approved mode** (unapprovable).
2. The agent checks whether the write's `argv` matches an approved **host rule** `{bin, args[]}` (each arg a literal or `*`).
   - **Rule match** - runs directly (write explicitly permitted).
   - **Not approved, Linux** - runs under Landlock; the write is kernel-blocked, the agent returns a structured error, and the backend creates a pending approval record.
   - **Not approved, macOS** - no Landlock; a write that matches no rule is refused immediately and a pending record is created. Reads always run.
3. An operator or admin reviews pending approvals and approves or denies them in the tenant console under **Approvals**.
4. Once approved, the rule feeds the agent's allowlist on the next sync and the write runs.

On **Kubernetes** agents the flow is different - the backend gates the write at submission, before dispatch. An unapproved write is recorded as a `REJECTED` job and a pending approval is raised; the agent never receives it. See [Host vs Kubernetes enforcement](#host-vs-kubernetes-enforcement).

For a member of a **fleet**, approvals are **fleet-scoped** rather than per-agent: pre-approve a rule once on the fleet and every member inherits it, and a member's blocked write raises a single fleet-scoped request. This makes `approved` mode practical for an autoscaling fleet where instances come and go. Manage them from the fleet (tenant console → **Fleets → [fleet]**, or `POST /tenant/approvals` with `fleet_id`).

A host rule matches the argv **positionally** - bin equal, arity equal, each arg equal or `*`: approving `{bin: systemctl, args: [restart, *]}` permits `systemctl restart nginx` and `systemctl restart web-01`, but not `systemctl stop nginx`. This mirrors the k8s rule model below. (A legacy command-string prefix path still exists for backward compatibility and rejects shell operators.)

**Approvals from the CLI:**

```bash
reach approvals list                      # effective approved commands (default agent)
reach approvals list --agent prod         # effective approved commands for a specific agent
reach approvals list --pending            # your pending requests (--denied / --expired too)
reach approvals request "<cmd>" --agent prod   # request approval (developer) / pre-approve (operator)
reach approvals approve <approval-id>     # operator+: approve a pending request (agent or fleet)
reach approvals deny <approval-id>        # operator+: deny a pending request

# fleet approvals are shared by every member and live under `reach fleets approvals`:
reach fleets approvals list <fleet>       # effective approved commands for the fleet
reach fleets approvals request <fleet> "<cmd>"  # request / pre-approve for the whole fleet
```

The output adapts to the agent type: **host** agents show the structured rule as `bin / args` columns; **Kubernetes** agents show it as `verb / resource / namespace / name` columns (with `✱` for wildcard fields). `--pending`, `--denied`, and `--expired` show only your own records; expired entries are visually marked so you can see why a command stopped working. `approve`/`deny` act on an approval **id** and work for both agent and fleet approvals.

## Host vs Kubernetes enforcement

The three modes mean the same thing on both agent types, but **how** a command is classified and **where** the decision is made differ. Host **writes** now run with no shell too (structured `argv` via `execve`, gated on the agent with Landlock); host **reads** still run as a freeform shell. Kubernetes runs `kubectl` with no shell (gated at the backend).

| | **Host** | **Kubernetes** |
|---|---|---|
| Write classification | regex heuristic over the command (the readonly pattern list - `rm`, `kill`, `systemctl`, package installs, …); a write is parsed to `argv` and run with `execve` | the `kubectl` **verb**, default-deny: `get`/`logs`/`describe`/… are reads, every other verb (incl. `exec`, `cp`, `port-forward`, and any unknown verb) is a write |
| `readonly` write | rejected at submission; Landlock on Linux is added defence-in-depth on the agent | rejected at submission |
| `approved` write, not pre-approved | **dispatched**; enforced on the **agent** (Landlock on Linux, server `is_write` flag on macOS), which raises the pending approval. A write with **shell operators** can't be structured and is **rejected at submission** | **blocked at submission** - recorded as a `REJECTED` job and a pending approval is raised; never dispatched |
| `wild` | runs anything except the always-blocked set; a shell-operator write runs freeform (no rule needed) | runs any `kubectl`, still bounded by the agent's no-shell allowlist and cluster RBAC |

The net effect: on host agents the agent is the final gate for approved-mode writes; on Kubernetes the backend is, and the no-shell allowlist + RBAC bound the pod regardless of policy mode.

## Approvals are structured rules

Both agent types use **structured rules** rather than command text. A host write is matched against a rule `{bin, args[]}` (each arg a literal or `*`); reads never need approval:

```json
{ "bin": "systemctl", "args": ["restart", "*"] }
```

For Kubernetes agents a rule is a poor fit for text prefix - `kubectl create pod nginx -n team-a` and `… redis …` are the same intent - so k8s approvals carry the parsed `kubectl` fields instead:

```json
{ "verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*" }
```

- Any field may be `*` (matches anything). `verb` is required and must be a write verb - a single verb like `delete`/`scale`, a compound "double verb" like `rollout restart` or `auth reconcile`, or `*`. Reads are always allowed and never need approval.
- A submitted `kubectl` write is permitted when some approved rule matches **every** field (equal or `*`). So one rule - "`delete pods` in `team-a`, any name" - covers every pod delete there, without re-approving each object.
- When an unapproved write is blocked, the backend **derives** the rule from the command onto the pending request, so the operator reviews (and can widen to `*`) verb/resource/namespace/name. Operators can also author rules directly.
- Pipes and flags are handled: each `kubectl` write stage is checked; read stages and filters (`| jq`) pass; flags like `-n`, `-l`, `--from-literal=k=v` don't confuse parsing; anything unparseable stays blocked (never over-approved).
- **Double verbs** (`rollout`, `auth`, `apply`, `set`, `certificate`) and `--dry-run` are classified precisely - e.g. `rollout status` is a read, `rollout restart` a write; `--dry-run=client` makes a mutating command a read. See [ARCHITECTURE.md](ARCHITECTURE.md#kubernetes-agents).

In the console (**Approvals**), host and Kubernetes approvals are shown **separately** via a toggle - both as **rule chips** (host `bin / args`, k8s `verb / resource / namespace / name`) - with the default view showing the 10 most recent and a **Search** box (case-insensitive) for filtering. See [API.md](API.md#approvals) for the `type`, `q`, `limit`, and `offset` query parameters.

## Access level

Each agent has an `access_level` label, shown in `reach agents list` and `reach status`. **It is a host-oriented descriptor** - it combines policy mode with whether the agent process runs as root.

| access_level | Mode | Running as root |
|---|---|---|
| `open` | wild | yes |
| `elevated` | wild (non-root) or approved (root) | - |
| `managed` | approved (non-root) or readonly (root) | - |
| `restricted` | readonly | no |

These are factual descriptors, not risk scores. An `open` agent in a personal dev environment is intentional.

**On Kubernetes, "running as root" does not apply.** The pod runs non-root with a read-only root filesystem, and what bounds the agent is **cluster RBAC**, not OS privilege - so the console shows root as `n/a`. An `access_level` is still computed and shown, but for k8s it only **reflects the policy mode** (always non-root, so it is never `open` - only `elevated` / `managed` / `restricted`). The meaningful privilege bound for a Kubernetes agent is its acknowledged RBAC (the chart's `clusterAccess`, surfaced and drift-tracked in the console), not its `access_level`.

## User access & agent scoping

Policy modes bound *what an agent may run*. A separate layer bounds *which users may see and drive which agents*.

**Roles** (per tenant user): `developer` submits jobs and requests/views approvals; `operator` adds reviewing and managing approvals and agents; `admin` adds managing users, tags, policy, and audit logs.

**Agent scoping.** On top of role, non-admin users are scoped to a subset of agents/fleets, granted as **read-only** or **read-write** (`readwrite_*` / `readonly_*` lists for agents and fleets). Non-admins have **no access by default**. Read access (the union of all grants) is enforced the same way everywhere - an out-of-scope agent is invisible in the agent list, and any job, approval, or job-history call for it returns "not found". A **read-only** grant additionally rejects write commands (and approval creation) with `403` in any mode - it narrows, but never bypasses, the agent's own policy mode. There is **no wildcard**: only admins are tenant-wide, and granting a non-admin "all agents" lists every id explicitly (so new agents aren't auto-included). Fleet members are granted via their **fleet**, not by individual agent id (their ids churn as the autoscaler scales).

**Admins are the trust root and are always tenant-wide** - they cannot be scoped. This guarantees at least one role can always reach every agent, so no agent can be orphaned by scoping. Because user management is admin-only, the account granting access always holds every agent. Two supporting rules keep this consistent:

- Promoting a scoped user to `admin` clears their agent/fleet scope.
- An `operator` who creates an agent is automatically granted **read-write** access to it, so a scoped operator can manage what they just created.

**Approvals follow the same scope.** In the console, an operator/admin reviews all pending/approved approvals for the agents they can access; a developer sees the pending requests **they** submitted, plus the agent-wide **approved** command set for any agent they can access.
