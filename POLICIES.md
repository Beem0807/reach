# Policy modes & approvals

Every agent runs in one of three **policy modes**, set in the tenant console (**Agents → [agent] → Policy**) or via the API (`PUT /tenant/agents/{id}/policy/mode`). The mode determines how each command is evaluated before it runs. Host and Kubernetes agents share the same three modes but classify and enforce them differently - see [Host vs Kubernetes enforcement](#host-vs-kubernetes-enforcement).

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

Reads are always allowed - you do not need to add read commands to any list. Write and destructive operations (anything blocked in readonly mode) are only permitted if the exact command has been pre-approved for this agent.

**How it works (host agents):**

1. When a command is submitted, the server classifies it as a write or read (`is_write: true/false`) using the same pattern list as readonly mode, and queues it to the agent.
2. The agent checks whether the command matches the approved list.
   - **Approved match** - runs the command normally (write explicitly permitted).
   - **Not approved, Linux** - runs under Landlock. If Landlock blocks the write, the agent returns a structured error and the backend creates a pending approval record.
   - **Not approved, macOS** - no Landlock available. The agent uses the server-supplied `is_write` flag: if the command is a write and not approved, it is refused immediately and a pending approval record is created. Read commands always run.
3. An operator or admin reviews pending approvals and approves or denies them in the tenant console under **Approvals**.
4. Once approved, the command prefix is included in the approved list on the next sync and runs without restriction.

On **Kubernetes** agents the flow is different - the backend gates the write at submission, before dispatch. An unapproved write is recorded as a `REJECTED` job and a pending approval is raised; the agent never receives it. See [Host vs Kubernetes enforcement](#host-vs-kubernetes-enforcement).

The match is prefix-based with a word boundary: approving `docker logs` permits `docker logs myapp --tail 100` but not `docker rm myapp`.

**Viewing approvals from the CLI:**

```bash
reach approvals list                      # effective approved commands (default agent)
reach approvals list --agent prod         # effective approved commands for a specific agent
reach approvals list --pending            # your pending requests (default agent)
reach approvals list --denied             # your denied requests (default agent)
reach approvals list --expired            # your expired approvals (default agent)
reach approvals list --agent prod --pending  # any of the above for a specific agent
```

The output adapts to the agent type: **host** agents show the command; **Kubernetes** agents show the structured rule as `verb / resource / namespace / name` columns (with `✱` for wildcard fields). `--pending`, `--denied`, and `--expired` show only your own records; expired entries are visually marked so you can see why a command stopped working.

## Host vs Kubernetes enforcement

The three modes mean the same thing on both agent types, but **how** a command is classified and **where** the decision is made differ - because host agents run a shell (gated on the agent with Landlock) while Kubernetes agents run `kubectl` with no shell (gated at the backend).

| | **Host** | **Kubernetes** |
|---|---|---|
| Write classification | regex heuristic over the command (the readonly pattern list - `rm`, `kill`, `systemctl`, package installs, …) | the `kubectl` **verb**, default-deny: `get`/`logs`/`describe`/… are reads, every other verb (incl. `exec`, `cp`, `port-forward`, and any unknown verb) is a write |
| `readonly` write | rejected at submission; Landlock on Linux is added defence-in-depth on the agent | rejected at submission |
| `approved` write, not pre-approved | **dispatched**; enforced on the **agent** (Landlock on Linux, server `is_write` flag on macOS), which raises the pending approval | **blocked at submission** - recorded as a `REJECTED` job and a pending approval is raised; never dispatched |
| `wild` | runs anything except the always-blocked set | runs any `kubectl`, still bounded by the agent's no-shell allowlist and cluster RBAC |

The net effect: on host agents the agent is the final gate for approved-mode writes; on Kubernetes the backend is, and the no-shell allowlist + RBAC bound the pod regardless of policy mode.

## Kubernetes approvals are structured rules

Host approvals are **command text** (prefix match). For Kubernetes agents a text prefix is a poor fit - `kubectl create pod nginx -n team-a` and `… redis …` are the same intent - so k8s approvals are **structured rules** instead:

```json
{ "verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*" }
```

- Any field may be `*` (matches anything). `verb` is required and must be a write verb - a single verb like `delete`/`scale`, a compound "double verb" like `rollout restart` or `auth reconcile`, or `*`. Reads are always allowed and never need approval.
- A submitted `kubectl` write is permitted when some approved rule matches **every** field (equal or `*`). So one rule - "`delete pods` in `team-a`, any name" - covers every pod delete there, without re-approving each object.
- When an unapproved write is blocked, the backend **derives** the rule from the command onto the pending request, so the operator reviews (and can widen to `*`) verb/resource/namespace/name. Operators can also author rules directly.
- Pipes and flags are handled: each `kubectl` write stage is checked; read stages and filters (`| jq`) pass; flags like `-n`, `-l`, `--from-literal=k=v` don't confuse parsing; anything unparseable stays blocked (never over-approved).
- **Double verbs** (`rollout`, `auth`, `apply`, `set`, `certificate`) and `--dry-run` are classified precisely - e.g. `rollout status` is a read, `rollout restart` a write; `--dry-run=client` makes a mutating command a read. See [ARCHITECTURE.md](ARCHITECTURE.md#kubernetes-agents).

In the console (**Approvals**), host and Kubernetes approvals are shown **separately** via a toggle - host as commands, k8s as rule chips - with the default view showing the 10 most recent and a **Search** box (case-insensitive) for filtering. See [API.md](API.md#approvals) for the `type`, `q`, `limit`, and `offset` query parameters.

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
