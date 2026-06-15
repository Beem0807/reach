# reach

Give your AI agents controlled access to every machine you own — without SSH, VPNs, or open ports.

```bash
reach exec -- hostname
reach exec --agent prod -- docker ps
```

---

## Why Reach?

AI agents can reason about your code, but they cannot safely operate your remote machines by default.

Reach gives any AI agent — Claude Code, Cursor, custom LLM workflows, or your own automation — a controlled command bridge to your machines without requiring SSH, VPNs, public IPs, or inbound firewall rules.

---

## How it works

1. You install the Reach agent on a machine.
2. The agent makes outbound HTTPS requests to the Reach API.
3. Commands are queued by the CLI or any HTTP client.
4. The agent polls for pending jobs.
5. The command runs locally on the machine.
6. stdout, stderr, exit code, and duration are sent back to the caller.

---

## Install the CLI

```bash
pip install https://reach-releases.s3.amazonaws.com/reach-0.1.0-py3-none-any.whl
```

Log in with your tenant token:

```bash
reach login --api-url <url> --token <tenant_token>
```

---

## Add a machine

**Linux:**

```bash
curl -fsSL https://reach-releases.s3.amazonaws.com/install.sh | sudo bash -s -- \
  --api-url       "<url>" \
  --agent-id      "agent_xxx" \
  --install-token "install_xxx"
```

**Mac:**

```bash
mkdir -p /tmp/reach-agent
curl -fsSL https://reach-releases.s3.amazonaws.com/reach-agent-darwin-arm64 \
  -o /tmp/reach-agent/reach-agent
chmod +x /tmp/reach-agent/reach-agent
cat > /tmp/reach-agent/config.json <<'EOF'
{"api_url":"<url>","agent_id":"agent_xxx","install_token":"install_xxx"}
EOF
REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent
```

Set it as your default:

```bash
reach use agent_xxx
```

---

## Usage

```bash
reach agents                                # list all your machines
reach status                                # show default agent status
reach exec -- <command>                     # run on default machine
reach exec --agent <id|alias> -- <command>  # run on specific machine
```

### Aliases

Give your machines friendly names:

```bash
reach alias set prod agent_xxx
reach alias set staging agent_yyy

reach exec --agent prod -- docker ps
reach exec --agent staging -- uptime
reach alias list
```

---

## Example use cases

```bash
reach exec --agent prod -- docker ps
reach exec --agent staging -- journalctl -u app --no-pager -n 100
reach exec --agent devbox -- git status
reach exec --agent k8s -- kubectl get pods -A
```

---

## AI agent integration

Run `reach agent-init` inside any project to generate context for your AI agent. It fetches your machines, prompts for a role for each, and writes a file that tells the agent to use `reach exec` automatically.

```bash
reach agent-init
```

```
Select your agent:
  1  claude        — writes CLAUDE.md
  2  cursor        — writes .cursor/rules/reach.mdc
  3  system-prompt — prints to stdout, paste anywhere
```

Or pass `--for` directly to skip the prompt:

```bash
reach agent-init --for claude        # CLAUDE.md for Claude Code
reach agent-init --for cursor        # .cursor/rules/reach.mdc for Cursor
reach agent-init --for system-prompt # paste into any agent or API call
```

---

## Policies

Policies are configured from the Reach dashboard.

Each machine can run in one of three modes:

- **Wild** — allow all commands
- **Readonly** — block write and destructive commands
- **Approved** — only approved command patterns can run

Use the CLI to view the active policy:

```bash
reach policy show
reach policy show --agent prod
```

---

## Safety

Reach is designed for controlled command execution:

- No inbound ports are opened
- No SSH server is required
- Agents only make outbound HTTPS requests
- Commands have a default timeout of 60 seconds
- Job history is recorded for 7 days
- Policies are configured from the Reach dashboard
- The CLI can view policies but cannot change them

---

## Production usage

For production machines, use the **Approved** policy mode from the Reach dashboard.

Avoid running production agents in Wild mode unless you fully trust the environment and understand the risk.

---

## Commands

| Command | Description |
|---|---|
| `reach login` | Store API URL and tenant token |
| `reach agents` | List all machines |
| `reach use <id\|alias>` | Set default machine |
| `reach status` | Show default machine status |
| `reach exec -- <cmd>` | Run command on default machine |
| `reach exec --agent <id\|alias> -- <cmd>` | Run command on specific machine |
| `reach exec --timeout <s> -- <cmd>` | Override wait timeout (default 60s) |
| `reach job <job_id>` | Re-view stdout/stderr of a past job |
| `reach history` | Show recent jobs across all machines |
| `reach history --agent <id\|alias>` | Filter history by machine |
| `reach policy show` | Show mode and approved commands for default agent |
| `reach policy show --agent <id\|alias>` | Show policy for a specific machine |
| `reach alias set <name> <id>` | Create alias |
| `reach alias list` | List aliases |
| `reach alias remove <name>` | Remove alias |
| `reach agent-init` | Interactively generate context for your AI agent |
| `reach agent-init --for claude` | Write CLAUDE.md for Claude Code |
| `reach agent-init --for cursor` | Write .cursor/rules/reach.mdc for Cursor |
| `reach agent-init --for system-prompt` | Print system prompt snippet to stdout |

---

> Running your own backend? See [SELF_HOSTING.md](SELF_HOSTING.md).

---

**License:** MIT — See [LICENSE](LICENSE).
