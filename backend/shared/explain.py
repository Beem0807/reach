"""Approval explainability.

Turns a stored approval rule into the human-readable *consequences* an operator needs before
granting it - not just the command that triggered it, but everything the rule will ALSO permit
(because an approved rule is reusable and its wildcards widen scope), its blast radius across
targets, and a transparent risk verdict that names its own reasons.

Pure/derivational - no I/O. Callers pass the approval plus context (fleet member count, a display
label) and attach the returned `explanation` dict to the approval payload. See POLICIES.md.
"""
from datetime import datetime, timezone
from typing import Optional

from shared.policy import host_rule_to_command, is_sensitive_read

HOST_WILDCARD = "*"
HOST_REST = "..."

# Bins whose FIRST arg is an action verb; the value is the noun for the object they act on
# (used to phrase "Allowed <noun>: Any" when that positional is wildcarded).
_MULTIPLEXERS = {
    "systemctl": "service", "service": "service",
    "docker": "container", "podman": "container", "nerdctl": "container",
    "apt": "package", "apt-get": "package", "yum": "package", "dnf": "package",
    "apk": "package", "brew": "package", "snap": "package",
    "npm": "package", "pip": "package", "pip3": "package", "yarn": "package", "pnpm": "package",
    "git": "argument", "kubectl": "resource", "helm": "release", "flux": "resource",
}

# (bin, verb) -> action phrase. Falls back to a generic phrase when unmapped.
_ACTIONS = {
    ("systemctl", "restart"): "Restart a service", ("systemctl", "start"): "Start a service",
    ("systemctl", "stop"): "Stop a service", ("systemctl", "reload"): "Reload a service",
    ("systemctl", "enable"): "Enable a service", ("systemctl", "disable"): "Disable a service",
    ("service", "restart"): "Restart a service", ("service", "start"): "Start a service",
    ("service", "stop"): "Stop a service",
    ("apt", "install"): "Install packages", ("apt-get", "install"): "Install packages",
    ("yum", "install"): "Install packages", ("dnf", "install"): "Install packages",
    ("apk", "add"): "Install packages", ("brew", "install"): "Install packages",
    ("apt", "remove"): "Remove packages", ("apt-get", "remove"): "Remove packages",
    ("apt", "purge"): "Remove packages", ("yum", "remove"): "Remove packages",
    ("npm", "install"): "Install packages", ("pip", "install"): "Install packages",
    ("docker", "restart"): "Restart a container", ("docker", "stop"): "Stop a container",
    ("docker", "rm"): "Remove a container", ("docker", "run"): "Run a container",
    ("helm", "upgrade"): "Upgrade a release", ("helm", "install"): "Install a release",
    ("helm", "uninstall"): "Uninstall a release", ("helm", "rollback"): "Roll back a release",
}

# Standalone bins (no action verb) -> phrase.
_BIN_ACTIONS = {
    "rm": "Delete files", "unlink": "Delete a file", "shred": "Destroy files",
    "reboot": "Reboot the host", "shutdown": "Shut down the host",
    "halt": "Halt the host", "poweroff": "Power off the host",
    "kill": "Kill a process", "pkill": "Kill processes", "killall": "Kill processes",
    "mount": "Mount a filesystem", "umount": "Unmount a filesystem",
    "useradd": "Create a user", "userdel": "Delete a user", "passwd": "Change a password",
    "cat": "Read a file", "less": "Read a file", "head": "Read a file", "tail": "Read a file",
    "cp": "Copy a file", "chmod": "Change file permissions", "chown": "Change file ownership",
}

# Verbs/bins that CHANGE or destroy state (drive the risk floor).
_DESTRUCTIVE_VERBS = {
    "restart", "stop", "reload", "delete", "remove", "rm", "purge", "uninstall", "destroy",
    "down", "drop", "kill", "reboot", "shutdown", "halt", "poweroff", "scale", "drain",
    "cordon", "evict", "replace", "rollout", "rollback", "patch", "apply", "edit", "wipe",
}
_K8S_WRITE_VERBS = {
    "delete", "apply", "patch", "replace", "create", "scale", "edit", "annotate", "label",
    "set", "rollout", "cordon", "drain", "taint", "exec", "cp", "port-forward",
}


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

def _to_epoch(expires_at) -> Optional[int]:
    """expires_at may be None (never), an epoch int, or an ISO-8601 string."""
    if expires_at in (None, "", 0):
        return None
    if isinstance(expires_at, (int, float)):
        return int(expires_at)
    try:
        return int(datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def _format_expiry(expires_at, now: int) -> str:
    exp = _to_epoch(expires_at)
    if exp is None:
        return "Never"
    rem = exp - now
    if rem <= 0:
        return "Expired"
    if rem < 3600:
        return f"in {max(1, rem // 60)}m"
    if rem < 86400:
        return f"in {rem // 3600}h"
    return f"in {rem // 86400}d"


# ---------------------------------------------------------------------------
# Host rules
# ---------------------------------------------------------------------------

def _host_action(rule: dict) -> tuple:
    """(action_phrase, verb_or_None). Uses the bin's action map, or the multiplexer verb."""
    binary = rule.get("bin", "")
    args = rule.get("args", [])
    if binary in _MULTIPLEXERS and args and args[0] not in (HOST_WILDCARD, HOST_REST):
        verb = args[0]
        return _ACTIONS.get((binary, verb), f"Run {binary} {verb}"), verb
    if binary in _BIN_ACTIONS:
        return _BIN_ACTIONS[binary], None
    return f"Run {binary}", None


def _host_scope_lines(rule: dict) -> list:
    """The 'Allowed <noun>: Any' lines - the scope a wildcard widens the rule to. Empty when
    the rule is fully literal (it only permits that exact argv)."""
    binary = rule.get("bin", "")
    args = list(rule.get("args", []))
    noun = _MULTIPLEXERS.get(binary, "argument")
    lines = []
    # For a multiplexer the first arg is the verb; the OBJECT is the next positional.
    obj_index = 1 if (binary in _MULTIPLEXERS and args and args[0] not in (HOST_WILDCARD, HOST_REST)) else 0
    for i, a in enumerate(args):
        if a == HOST_REST:
            lines.append({"label": "Extra arguments", "value": "Any", "wide": True})
        elif a == HOST_WILDCARD:
            label = f"Allowed {noun}" if i == obj_index else f"Argument {i + 1}"
            lines.append({"label": label, "value": "Any", "wide": True})
    return lines


# ---------------------------------------------------------------------------
# k8s rules
# ---------------------------------------------------------------------------

def _k8s_blast_radius(rule: dict) -> str:
    verb = rule.get("verb", "*")
    resource = rule.get("resource", "*")
    namespace = rule.get("namespace", "*")
    name = rule.get("name", "*")
    res = "resources" if resource in ("*", "") else resource
    ns = "all namespaces" if namespace in ("*", "") else namespace
    if name in ("*", ""):
        scope = f"all {res}"
    else:
        singular = res[:-1] if res.endswith("s") and res != "resources" else res
        scope = f"{singular} {name}"
    where = "across all namespaces" if ns == "all namespaces" else f"in {ns}"
    v = "any verb on" if verb in ("*", "") else verb
    return f"{v} {scope} {where}".strip()


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

def _risk(*, destructive: bool, wildcards: int, targets: int, never_expires: bool,
          sensitive: bool) -> tuple:
    """(level, factors[]). Transparent: the factors are the reasons, shown alongside the label.
    Conservative - anything unusual biases upward. Tune the thresholds here."""
    score = 0
    factors = []
    if destructive:
        score += 2; factors.append("changes or destroys state")
    if wildcards:
        score += 2; factors.append("wildcard scope (permits more than the triggering command)")
    if sensitive:
        score += 2; factors.append("exposes secrets")
    if targets >= 10:
        score += 2; factors.append(f"{targets} targets")
    elif targets > 1:
        score += 1; factors.append(f"{targets} targets")
    if never_expires:
        score += 1; factors.append("never expires")
    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    return level, factors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def explain_approval(approval: dict, *, member_count: Optional[int] = None,
                     target_label: Optional[str] = None, now: Optional[int] = None) -> dict:
    """Human-readable consequences of granting `approval`. `member_count` is the fleet's ACTIVE
    member count for a fleet-scoped approval (the blast radius); `target_label` is a display name
    (hostname or fleet name). Returns {summary, facts:[{label,value,wide?}], risk, risk_factors}.
    """
    now = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    host_rule = approval.get("host_rule")
    k8s_rule = approval.get("k8s_rule")
    is_fleet = bool(approval.get("fleet_id")) or approval.get("scope") == "fleet"
    # Detect a secret/credential read from the rule itself - never under-report because a
    # stored `sensitive` flag was missing (older/seeded approvals may lack it).
    sensitive = bool(approval.get("sensitive"))
    if not sensitive:
        if host_rule:
            sensitive = is_sensitive_read(host_rule_to_command(host_rule))
        elif k8s_rule:
            sensitive = str(k8s_rule.get("resource", "")).rstrip("s") == "secret"
    expires = _format_expiry(approval.get("expires_at"), now)
    never = expires == "Never"

    if is_fleet:
        n = member_count if member_count is not None else 0
        targets = n
        target_value = f"{n} fleet member{'' if n == 1 else 's'}"
        if target_label:
            target_value += f" ({target_label})"
    else:
        targets = 1
        target_value = target_label or "1 agent"

    facts = []
    if k8s_rule:
        verb = k8s_rule.get("verb", "*")
        resource = k8s_rule.get("resource", "*")
        namespace = k8s_rule.get("namespace", "*")
        name = k8s_rule.get("name", "*")
        wildcards = sum(1 for v in (resource, namespace, name) if v in ("*", ""))
        blast = _k8s_blast_radius(k8s_rule)
        summary = blast[0].upper() + blast[1:] if blast else "Kubernetes action"
        facts = [
            {"label": "Verb / Resource", "value": f"{verb} {'(any)' if resource in ('*','') else resource}"},
            {"label": "Namespace", "value": "All namespaces" if namespace in ("*", "") else namespace,
             "wide": namespace in ("*", "")},
            {"label": "Name", "value": "Any" if name in ("*", "") else name, "wide": name in ("*", "")},
            {"label": "Blast radius", "value": blast, "wide": True},
        ]
        destructive = verb in _K8S_WRITE_VERBS or verb in ("*", "")
    else:
        rule = host_rule or {"bin": (approval.get("command") or "").split(" ")[0], "args": []}
        action, verb = _host_action(rule)
        scope_lines = _host_scope_lines(rule)
        wildcards = len(scope_lines)
        summary = action + (" (any)" if scope_lines else "")
        facts = [{"label": "Requested action", "value": action}]
        facts += scope_lines
        # Destructive when the action verb OR a standalone bin (rm/reboot/kill/…) mutates state.
        destructive = (verb in _DESTRUCTIVE_VERBS) or (rule.get("bin") in _DESTRUCTIVE_VERBS)

    facts += [
        {"label": "Targets", "value": target_value, "wide": targets >= 10},
        {"label": "Reusable", "value": "Yes"},   # an approved rule auto-matches future commands
        {"label": "Expires", "value": expires, "wide": never},
    ]
    if sensitive:
        facts.insert(1, {"label": "Reads secrets", "value": "Yes", "wide": True})

    level, factors = _risk(destructive=bool(destructive), wildcards=wildcards, targets=targets,
                           never_expires=never, sensitive=sensitive)
    return {"summary": summary, "facts": facts, "risk": level, "risk_factors": factors}
