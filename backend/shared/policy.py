"""
Command policy enforcement.

Two-tier model:

BLOCKED_PATTERNS  - always rejected, all modes including wild.
                    Reserved for catastrophic / abuse-only operations: raw disk
                    wipes, root-filesystem deletion, privileged container/host
                    escapes, credential exfiltration, and reverse shells.
                    Legitimate admin operations are NOT here.

READONLY_BLOCKED  - rejected in readonly mode and in approved mode (unless the
                    command matches an approved list entry). Covers anything that
                    writes, deletes, installs, or mutates system state.

Wild mode is intentionally permissive. It is designed for personal machines, dev
environments, break-glass debugging, and power users who want full flexibility.
Use Approved mode for production and explicitly allowlist the write operations
the agent is permitted to perform.
"""
import re
import shlex
from typing import Optional

# Splits on shell operators: ; && || and single pipe |
# Each segment is checked independently so chained writes are caught.
_SHELL_OPERATORS = re.compile(r'&&|\|\||\|(?!\|)|;')


def _shell_segments(command: str) -> list[str]:
    return [s.strip() for s in _SHELL_OPERATORS.split(command) if s.strip()]


BLOCKED_PATTERNS = [
    # Catastrophic deletion / disk destruction
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(\s|$|\*)",   # rm -rf / or rm -rf /*
    r"rm\s+--no-preserve-root",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bwipefs\b",
    r"\bshred\s+/dev/",
    r":\(\)\{\s*:\|:\s*&\s*\}",                    # fork bomb
    # Privileged container / host escape
    r"\bdocker\s+run\b.*--privileged\b",
    r"\bdocker\s+run\b.*--(pid|network)=host\b",
    r"\bnsenter\b.*--target\s+1\b",
    r"\bchroot\s+/(\s|$)",
    r"\bkubectl\s+run\b.*--privileged\b",
    # Exfiltration
    r"\benv\b.*\|\s*\bcurl\b",
    # Reverse shells
    r"/dev/tcp/",
    r"/dev/udp/",
    r"\bnc\b.*-e\b",
    r"\bncat\b.*-e\b",
    r"\bsocat\b.*\bexec:\b",
]

READONLY_BLOCKED = [
    # File operations
    r"\brm\b", r"\bmv\b", r"\bcp\b(?=.*\s/)",
    r"\bchmod\b", r"\bchown\b", r"\bchattr\b",
    r"\btruncate\b", r"\bshred\b", r"\bwipe\b",
    r"\bln\b",
    r"\btee\b",
    r"\bsed\b.*\s-[a-zA-Z]*i",                  # sed -i in-place edit
    r">\s*\S+",                                   # output redirect (> and >>)
    # Process control
    r"\bkill\b", r"\bkillall\b", r"\bpkill\b",
    # System power / init
    r"\breboot\b", r"\bshutdown\b", r"\bpoweroff\b", r"\bhalt\b",
    r"\binit\s+[06]\b", r"\bsystemctl\s+(poweroff|reboot|halt)\b",
    # Service management
    r"\bsystemctl\s+(start|stop|restart|enable|disable|mask|unmask)\b",
    r"\bservice\s+\S+\s+(start|stop|restart|reload)\b",
    # Containers
    r"\bdocker\s+(start|stop|restart|rm|kill|exec|run|pull|build|push|rmi)\b",
    r"\bdocker-compose\s+(up|down|restart|pull|rm)\b",
    r"\bkubectl\s+(apply|delete|create|replace|patch|scale|rollout|exec|run)\b",
    # Package managers
    r"\bapt(-get)?\s+(install|remove|purge|upgrade|autoremove)\b",
    r"\byum\s+(install|remove|update|erase)\b",
    r"\bdnf\s+(install|remove|update|erase)\b",
    r"\bpacman\s+-[A-Za-z]*[SR]\b",
    r"\bapk\s+(add|del|upgrade)\b",
    r"\bsnap\s+(install|remove|refresh)\b",
    r"\bflatpak\s+(install|remove|update)\b",
    r"\bbrew\s+(install|uninstall|upgrade|remove)\b",
    r"\bpip3?\s+install\b",
    r"\bnpm\s+(install|uninstall|update)\b",
    r"\byarn\s+(add|remove|upgrade|install)\b",
    r"\bgem\s+(install|uninstall|update)\b",
    r"\bcargo\s+install\b",
    # File download / execution
    r"\bcurl\b.*\s-[a-zA-Z]*o\b", r"\bwget\b",
    # Disk / filesystem
    r"\bdd\b", r"\bmkfs\b",
    r"\bfdisk\b", r"\bparted\b", r"\bgdisk\b",
    r"\bmount\b", r"\bumount\b",
    # Networking / firewall
    r"\biptables\b", r"\bip6tables\b",
    r"\bufw\s+(allow|deny|enable|disable|delete|reject)\b",
    # User / auth management
    r"\buseradd\b", r"\buserdel\b", r"\busermod\b",
    r"\bgroupadd\b", r"\bgroupdel\b",
    r"\bpasswd\b",
    r"\bsu\b",
    # Scheduled jobs
    r"\bcrontab\b",
    # Privilege escalation
    r"\bsudo\b",
    # IaC destroy
    r"\bterraform\s+destroy\b",
    r"\bpulumi\s+destroy\b",
    r"\bcdk\s+destroy\b",
    # Cloud destructive operations
    r"\baws\s+ec2\s+terminate-instances\b",
    r"\baws\s+rds\s+delete-db-instance\b",
    r"\baws\s+s3\s+rb\b.*--force\b",
    r"\bgcloud\b.*\binstances\s+delete\b",
    r"\baz\s+vm\s+delete\b",
]


def _is_blocked(command: str) -> bool:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def _is_readonly_blocked(command: str) -> bool:
    for segment in _shell_segments(command):
        for pattern in READONLY_BLOCKED:
            if re.search(pattern, segment, re.IGNORECASE):
                return True
    return False


# Kubernetes job classification is authoritative HERE (backend), enforced at job
# submission - the k8s agent does not classify verbs; it only enforces the no-shell
# allowlist (see agent/k8s_exec.go). Default-deny: any kubectl verb that is not a
# known read (incl. exec/cp/port-forward and unrecognized verbs) is a write.
_K8S_READ_VERBS = {
    "get", "describe", "logs", "top", "explain", "api-resources", "api-versions",
    "version", "cluster-info", "events", "diff", "wait",
    # Cluster-inert utilities: they render/print locally or touch only the local
    # kubeconfig (the agent authenticates via its in-cluster ServiceAccount, not
    # kubeconfig, and runs read-only-rootfs), so they never change cluster state.
    "kustomize", "options", "completion", "plugin", "config",
}
_K8S_WRITE_VERBS = {
    "create", "apply", "delete", "edit", "patch", "replace", "scale", "autoscale",
    "expose", "run", "label", "annotate", "taint", "cordon", "uncordon",
    "drain", "exec", "attach", "cp", "port-forward", "proxy", "debug",
}
# "Double verbs": kubectl subcommands whose real operation is (base + sub). The
# operation is keyed as the compound "<base> <sub>" (e.g. "rollout restart",
# "set image", "certificate approve") - that string is what a rule's `verb`
# stores, so reads/writes are distinguished and each write is separately
# approvable (e.g. allow `certificate approve` but not `certificate deny`). An
# unrecognized sub of a known base is treated as a write (fail-closed). Keep
# _K8S_COMPOUND_WRITES in sync with the UI verb dropdown.
_K8S_COMPOUND_BASES = {"rollout", "auth", "apply", "set", "certificate"}
_K8S_COMPOUND_READS = {
    "rollout status", "rollout history",
    "auth can-i", "auth whoami",
    "apply view-last-applied",
}
_K8S_COMPOUND_WRITES = {
    "rollout restart", "rollout undo", "rollout pause", "rollout resume",
    "auth reconcile",
    "apply set-last-applied", "apply edit-last-applied",
    "set image", "set env", "set resources", "set selector",
    "set serviceaccount", "set subject",
    "certificate approve", "certificate deny",
}


def _is_read_verb(verb: str) -> bool:
    """Read operations never need approval and run even in readonly mode."""
    return verb in _K8S_READ_VERBS or verb in _K8S_COMPOUND_READS


def _kubectl_verb(tokens: list) -> str:
    """The kubectl operation: a single verb, or the compound "<base> <sub>" for
    double verbs (rollout, auth, apply, set, certificate). First recognized verb,
    skipping global flags/values. A double-verb base with no sub falls back to the
    bare base (classified a write)."""
    toks = tokens[1:]
    for i, t in enumerate(toks):
        if t == "--":
            break
        v = t.lower()
        if v in _K8S_COMPOUND_BASES:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            return f"{v} {nxt.lower()}" if nxt and not nxt.startswith("-") else v
        if v in _K8S_READ_VERBS or v in _K8S_WRITE_VERBS:
            return v
    return ""  # unrecognized -> caller treats as write


def _tokenize(segment: str) -> list:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _kubectl_stages(command: str) -> list:
    """Token lists for each kubectl stage in a (possibly piped/chained) command.
    Non-kubectl stages are read-only filters and are skipped."""
    stages = []
    for segment in _shell_segments(command):
        tokens = _tokenize(segment)
        if tokens and tokens[0].rsplit("/", 1)[-1] == "kubectl":
            stages.append(tokens)
    return stages


def _stage_is_dry_run(tokens: list) -> bool:
    """A `--dry-run=client|server` (or the deprecated bare `--dry-run`) makes an
    otherwise-mutating stage non-mutating, so it's a read. `--dry-run=none` really
    applies, so it is NOT a dry run."""
    for t in tokens[1:]:
        low = t.lower()
        if low == "--dry-run":
            return True
        if low.startswith("--dry-run="):
            return low.split("=", 1)[1] in ("client", "server")
    return False


def _stage_is_write(tokens: list) -> bool:
    """Whether one kubectl stage mutates: a non-read verb that isn't a dry run."""
    if _stage_is_dry_run(tokens):
        return False
    return not _is_read_verb(_kubectl_verb(tokens))


def is_k8s_write(command: str) -> bool:
    """Whether a k8s job mutates/execs: any kubectl stage that is a write."""
    return any(_stage_is_write(tokens) for tokens in _kubectl_stages(command))


# ---------------------------------------------------------------------------
# Structured k8s approvals
#
# Host approvals are text (prefix) matches. For k8s agents a text prefix is a
# poor fit - `kubectl create pod nginx -n team-a` and `... redis ...` are the
# same intent. Instead we parse a kubectl write into a structured rule
# {verb, resource, namespace, name} and match against approved rules, where any
# field may be "*" (wildcard). Parsing is best-effort: an unusual command that
# parses to an empty resource simply fails to match and stays blocked (safe),
# and the derived rule is shown to the operator to review before approval.
# ---------------------------------------------------------------------------

# Global flags that consume the following token as their value, so it is not a
# positional (resource/name). --namespace is handled separately.
_K8S_VALUE_FLAGS = {
    "--context", "--cluster", "--user", "--kubeconfig", "--as", "--as-group",
    "-s", "--server", "--token", "--request-timeout", "-o", "--output",
    "--field-selector", "-l", "--selector", "--field-manager", "-f", "--filename",
}

# Short/singular resource forms → canonical plural. Unknown resources pass
# through lowercased; parsing is symmetric (submitted command and derived rule
# normalize identically), so matching stays consistent either way.
_K8S_RESOURCE_ALIASES = {
    "po": "pods", "pod": "pods",
    "deploy": "deployments", "deployment": "deployments",
    "svc": "services", "service": "services",
    "ns": "namespaces", "namespace": "namespaces",
    "cm": "configmaps", "configmap": "configmaps",
    "secret": "secrets",
    "rs": "replicasets", "replicaset": "replicasets",
    "sts": "statefulsets", "statefulset": "statefulsets",
    "ds": "daemonsets", "daemonset": "daemonsets",
    "job": "jobs", "cronjob": "cronjobs", "cj": "cronjobs",
    "ing": "ingresses", "ingress": "ingresses",
    "no": "nodes", "node": "nodes",
    "pvc": "persistentvolumeclaims", "pv": "persistentvolumes",
    "sa": "serviceaccounts", "serviceaccount": "serviceaccounts",
    "ep": "endpoints", "endpoint": "endpoints",
}

def _normalize_resource(res: str) -> str:
    if not res:
        return ""
    r = res.lower().split(".", 1)[0]  # drop API group, e.g. deployments.apps
    return _K8S_RESOURCE_ALIASES.get(r, r)


def parse_kubectl(tokens: list) -> dict:
    """Parse one kubectl token list into {verb, resource, namespace, name}.

    Returns None if there is no recognizable verb. Best-effort: forms it cannot
    resolve yield an empty resource/name, which fails to match (blocked-safe)
    and surfaces to the operator for review.
    """
    verb = _kubectl_verb(tokens)
    if not verb:
        return None
    base = verb.split(" ", 1)[0]  # compound verbs (e.g. "rollout restart") skip the base token

    namespace = "default"
    positionals = []
    seen_verb = False
    i = 1
    while i < len(tokens):
        t = tokens[i]
        low = t.lower()
        if t == "--":
            break  # everything after -- is a container command (exec/run), not a resource
        if low in ("-a", "--all-namespaces"):
            namespace = "*"
            i += 1
            continue
        if low in ("-n", "--namespace"):
            if i + 1 < len(tokens):
                namespace = tokens[i + 1]
            i += 2
            continue
        if low.startswith("--namespace=") or low.startswith("-n="):
            namespace = t.split("=", 1)[1]
            i += 1
            continue
        if t.startswith("-"):
            base = low.split("=", 1)[0]
            if "=" not in t and base in _K8S_VALUE_FLAGS:
                i += 2  # skip flag and its value
            else:
                i += 1  # boolean flag or --flag=value
            continue
        # positional token
        if not seen_verb and low == base:
            seen_verb = True
            i += 1
            continue
        positionals.append(t)
        i += 1

    # Compound verbs (rollout/auth/apply/set/certificate) carry the sub-subcommand
    # in `verb`, so drop that leading positional to expose the real resource.
    if " " in verb and positionals:
        positionals = positionals[1:]

    resource, name = "", ""
    if verb == "run":
        resource, name = "pods", (positionals[0] if positionals else "")
    elif positionals:
        first = positionals[0]
        if "/" in first:
            r, _, n = first.partition("/")
            resource, name = _normalize_resource(r), n
        else:
            resource = _normalize_resource(first)
            name = positionals[1] if len(positionals) > 1 else ""

    return {"verb": verb, "resource": resource, "namespace": namespace, "name": name}


def _rule_field_matches(rule_val: str, cmd_val: str) -> bool:
    return rule_val == "*" or rule_val == cmd_val


def k8s_rule_matches(parsed: dict, rule: dict) -> bool:
    """A parsed kubectl write is permitted by a rule when every field is equal
    or wildcarded (`*`) in the rule."""
    if not parsed or not rule:
        return False
    return (
        _rule_field_matches(rule.get("verb", "*"), parsed.get("verb", ""))
        and _rule_field_matches(rule.get("resource", "*"), parsed.get("resource", ""))
        and _rule_field_matches(rule.get("namespace", "*"), parsed.get("namespace", ""))
        and _rule_field_matches(rule.get("name", "*"), parsed.get("name", ""))
    )


def derive_k8s_rule(command: str) -> dict:
    """The structured rule for a command's first write stage (for the pending
    approval an operator reviews). None if there is no parseable write."""
    for tokens in _kubectl_stages(command):
        if _stage_is_write(tokens):
            return parse_kubectl(tokens)
    return None


def is_k8s_command_approved(command: str, rules: list) -> bool:
    """Whether every write stage of a k8s command is permitted by some approved
    rule. Read stages are always allowed."""
    for tokens in _kubectl_stages(command):
        if not _stage_is_write(tokens):
            continue
        parsed = parse_kubectl(tokens)
        if not parsed or not any(k8s_rule_matches(parsed, r) for r in rules if isinstance(r, dict)):
            return False
    return True


def normalize_k8s_rule(raw: dict) -> dict:
    """Validate and normalize a structured rule for storage. The verb is
    mandatory (an explicit `*` is allowed, but an absent rule must not silently
    become allow-all); resource/namespace/name default to the `*` wildcard and
    resource is canonicalized. Returns None if the input has no verb, or the
    verb is neither `*` nor a known write verb (rules only gate writes)."""
    if not isinstance(raw, dict):
        return None
    raw_verb = raw.get("verb")
    if not raw_verb:  # verb must be explicit - may be "*", but not defaulted
        return None
    verb = " ".join(str(raw_verb).strip().lower().split())  # collapse inner spaces for compound verbs
    if verb != "*" and verb not in _K8S_WRITE_VERBS and verb not in _K8S_COMPOUND_WRITES:
        return None
    resource = str(raw.get("resource") or "*").strip()
    resource = "*" if resource == "*" else (_normalize_resource(resource) or "*")
    namespace = str(raw.get("namespace") or "*").strip() or "*"
    name = str(raw.get("name") or "*").strip() or "*"
    return {"verb": verb, "resource": resource, "namespace": namespace, "name": name}


def rule_to_command(rule: dict) -> str:
    """Human-readable one-line form of a rule - shown in lists and used as the
    dedup key alongside the structured rule."""
    verb = rule.get("verb", "*")
    resource = rule.get("resource", "*")
    name = rule.get("name", "*")
    namespace = rule.get("namespace", "*")
    target = resource if name in ("*", "") else f"{resource}/{name}"
    ns = "(all namespaces)" if namespace == "*" else f"-n {namespace}"
    return f"kubectl {verb} {target} {ns}"


def compute_access_level(
    mode: str,
    running_as_root: bool,
    **_kwargs,
) -> str:
    """Privilege label derived from policy mode and root status."""
    if mode == "wild":
        return "open" if running_as_root else "elevated"
    if mode == "approved":
        return "elevated" if running_as_root else "managed"
    # readonly
    return "managed" if running_as_root else "restricted"


# Shell control operators that let a command chain, substitute, or redirect additional
# commands. A host approval is a single command with no shell plumbing - the agent gates
# its sandbox bypass on the same check (hasShellOperators in agent/main.go), so an approval
# whose command contains any of these can never smuggle an appended pipe/chain past the gate.
_SHELL_OPERATOR_CHARS = "|;&$`()<>\n"


def has_shell_operators(command: str) -> bool:
    return any(c in command for c in _SHELL_OPERATOR_CHARS)


# Characters that mean a command relies on the shell (operators above + globbing,
# brace/tilde expansion, quoting, escaping). A command with NONE of these is a plain
# "bin arg arg" that can be structured into an argv and run with execve (no shell); a
# command with any of them keeps the freeform shell path (Landlock-gated).
_SHELL_SPECIAL_CHARS = _SHELL_OPERATOR_CHARS + "*?[]{}~'\"\\"


def needs_shell(command: str) -> bool:
    return any(c in command for c in _SHELL_SPECIAL_CHARS)


def to_argv(command: str) -> Optional[list]:
    """Convert a plain command string to an argv (whitespace split) when it uses no shell
    features; None if it needs the shell (so the caller keeps the freeform path)."""
    cmd = command.strip()
    if not cmd or needs_shell(cmd):
        return None
    return cmd.split()


# --- Structured host exec: {bin, args[]} + positional-wildcard rule matching ----
# A structured exec runs a single binary with an explicit argv and NO shell (the agent
# execve's it), so there is nothing to pipe/chain/substitute. A host approval rule is
# {bin, args} where bin is a literal and each arg is a literal or the "*" wildcard - the
# positional analog of the k8s {verb, resource, namespace, name} rule (reuses
# _rule_field_matches). Arity is fixed: a rule for 2 args does not permit 3.

def normalize_argv(raw) -> Optional[list]:
    """Validate a structured argv: a non-empty list of strings (bin + args), first
    non-empty. Returns the list, or None if invalid."""
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for tok in raw:
        if not isinstance(tok, str):
            return None
        out.append(tok)
    if not out[0].strip():
        return None
    return out


def normalize_host_rule(raw: dict) -> Optional[dict]:
    """Validate/normalize a host approval rule {bin, args}. bin is mandatory; args
    defaults to [] and each element is a literal or "*". Returns None if invalid."""
    if not isinstance(raw, dict):
        return None
    binary = str(raw.get("bin") or "").strip()
    if not binary:
        return None
    raw_args = raw.get("args")
    if raw_args is None:
        raw_args = []
    if not isinstance(raw_args, list):
        return None
    # A structured argv token never contains shell metacharacters (a command only becomes
    # an argv when it has none), so a rule with them can never match - reject it as invalid.
    # "*" is the one allowed metacharacter: a whole-arg wildcard.
    if needs_shell(binary):
        return None
    args = []
    for a in raw_args:
        if not isinstance(a, (str, int, float)):
            return None
        a = str(a)
        if a != "*" and needs_shell(a):
            return None
        args.append(a)
    return {"bin": binary, "args": args}


def host_rule_matches(argv: list, rule: dict) -> bool:
    """A structured argv [bin, *args] is permitted by a rule when the bin matches and
    every positional arg equals the rule's arg or the rule wildcards it with "*"."""
    if not argv or not rule:
        return False
    if argv[0] != rule.get("bin", ""):
        return False
    call_args, rule_args = argv[1:], rule.get("args", [])
    if len(call_args) != len(rule_args):
        return False
    return all(_rule_field_matches(str(r), c) for r, c in zip(rule_args, call_args))


def is_host_argv_approved(argv: list, rules: list) -> bool:
    """Whether a structured argv is permitted by some approved host rule."""
    return any(host_rule_matches(argv, r) for r in rules if isinstance(r, dict))


def host_rule_to_command(rule: dict) -> str:
    """Display form: {bin: systemctl, args: [restart, "*"]} -> 'systemctl restart *'."""
    if not rule:
        return ""
    return " ".join([rule.get("bin", "")] + [str(a) for a in rule.get("args", [])]).strip()


def _is_approved(command: str, approved_commands: list) -> bool:
    cmd = command.strip()
    # A command with shell operators is never "approved" - it must run sandboxed so Landlock
    # blocks any appended write (mirrors the agent's bypass gate).
    if has_shell_operators(cmd):
        return False
    for allowed in approved_commands:
        allowed = allowed.strip()
        if cmd == allowed or cmd.startswith(allowed + " "):
            return True
    return False
