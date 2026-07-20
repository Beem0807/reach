"""Client-side derivation of a structured approval rule from a command string.

The backend accepts approvals for **k8s** agents only as a *structured* rule -
a ``k8s_rule`` ``{verb, resource, namespace, name}`` for kubectl, or a
``host_rule`` ``{bin, args[]}`` for a non-kubectl tool (helm/flux/…). Unlike
host agents, it will NOT structure a bare command string for a k8s agent. So the
CLI does that translation here, letting ``reach approvals request "<cmd>"`` work
the same for k8s agents as it does for hosts.

This mirrors the backend parser in ``backend/shared/policy.py`` so a rule derived
from a command matches that same command's job (the backend re-parses the job with
its own copy at match time). KEEP IN SYNC with backend/shared/policy.py.
"""
import re
import shlex
from typing import Optional

_SHELL_OPERATORS = re.compile(r'&&|\|\||\|(?!\|)|;')

_READ_VERBS = {
    "get", "describe", "logs", "top", "explain", "api-resources", "api-versions",
    "version", "cluster-info", "events", "diff", "wait",
    "kustomize", "options", "completion", "plugin", "config",
}
_WRITE_VERBS = {
    "create", "apply", "delete", "edit", "patch", "replace", "scale", "autoscale",
    "expose", "run", "label", "annotate", "taint", "cordon", "uncordon",
    "drain", "exec", "attach", "cp", "port-forward", "proxy", "debug",
}
_COMPOUND_BASES = {"rollout", "auth", "apply", "set", "certificate"}
_COMPOUND_READS = {
    "rollout status", "rollout history",
    "auth can-i", "auth whoami",
    "apply view-last-applied",
}
_VALUE_FLAGS = {
    "--context", "--cluster", "--user", "--kubeconfig", "--as", "--as-group",
    "-s", "--server", "--token", "--request-timeout", "-o", "--output",
    "--field-selector", "-l", "--selector", "--field-manager", "-f", "--filename",
}
_RESOURCE_ALIASES = {
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
# Pipe-filters the backend can prove are read-only; anything else (helm/…) is a write.
_READ_FILTERS = {"grep", "jq", "head", "tail", "wc", "sort", "uniq", "cut", "tr"}


def _segments(command: str) -> list:
    return [s.strip() for s in _SHELL_OPERATORS.split(command) if s.strip()]


def _tokenize(segment: str) -> list:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _stage_binary(tokens: list) -> str:
    return tokens[0].rsplit("/", 1)[-1] if tokens else ""


def _kubectl_verb(tokens: list) -> str:
    """First recognized verb, or a compound "<base> <sub>" for double verbs."""
    toks = tokens[1:]
    for i, t in enumerate(toks):
        if t == "--":
            break
        v = t.lower()
        if v in _COMPOUND_BASES:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            return f"{v} {nxt.lower()}" if nxt and not nxt.startswith("-") else v
        if v in _READ_VERBS or v in _WRITE_VERBS:
            return v
    return ""  # unrecognized -> treated as a write


def _is_read_verb(verb: str) -> bool:
    return verb in _READ_VERBS or verb in _COMPOUND_READS


def _stage_is_dry_run(tokens: list) -> bool:
    for t in tokens[1:]:
        low = t.lower()
        if low == "--dry-run":
            return True
        if low.startswith("--dry-run="):
            return low.split("=", 1)[1] in ("client", "server")
    return False


def _stage_is_write(tokens: list) -> bool:
    if _stage_is_dry_run(tokens):
        return False
    return not _is_read_verb(_kubectl_verb(tokens))


def _normalize_resource(res: str) -> str:
    if not res:
        return ""
    r = res.lower().split(".", 1)[0]  # drop API group, e.g. deployments.apps
    return _RESOURCE_ALIASES.get(r, r)


def _parse_kubectl(tokens: list) -> Optional[dict]:
    """Parse one kubectl token list into {verb, resource, namespace, name}."""
    verb = _kubectl_verb(tokens)
    if not verb:
        return None
    base = verb.split(" ", 1)[0]

    namespace = "default"
    positionals = []
    seen_verb = False
    i = 1
    while i < len(tokens):
        t = tokens[i]
        low = t.lower()
        if t == "--":
            break
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
            flag = low.split("=", 1)[0]
            if "=" not in t and flag in _VALUE_FLAGS:
                i += 2
            else:
                i += 1
            continue
        if not seen_verb and low == base:
            seen_verb = True
            i += 1
            continue
        positionals.append(t)
        i += 1

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


def _kubectl_stages(command: str) -> list:
    stages = []
    for seg in _segments(command):
        tokens = _tokenize(seg)
        if tokens and _stage_binary(tokens) == "kubectl":
            stages.append(tokens)
    return stages


def _derive_k8s_rule(command: str) -> Optional[dict]:
    """The structured rule for the command's first mutating kubectl stage."""
    for tokens in _kubectl_stages(command):
        if _stage_is_write(tokens):
            return _parse_kubectl(tokens)
    return None


def _nonkubectl_argv(command: str) -> list:
    """The argv of the first non-kubectl, non-filter stage (e.g. `helm upgrade …`)."""
    for seg in _segments(command):
        tokens = _tokenize(seg)
        binary = _stage_binary(tokens)
        if binary and binary != "kubectl" and binary not in _READ_FILTERS:
            return tokens
    return []


def command_to_k8s_approval(command: str):
    """Translate a command into the structured rule a k8s approval requires.

    Returns ``(k8s_rule, host_rule, error)``: on success exactly one of the rules
    is set and ``error`` is None; on failure both rules are None and ``error`` is a
    human-readable reason.
    """
    rule = _derive_k8s_rule(command)
    if rule:
        return rule, None, None
    argv = _nonkubectl_argv(command)
    if argv:
        return None, {"bin": argv[0], "args": argv[1:]}, None
    return None, None, (
        "no approvable write found in this command - a kubectl command needs a write "
        "verb (create/apply/delete/label/…), or use a non-kubectl tool like helm"
    )
