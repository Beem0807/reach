"""Tests for client-side kubectl -> approval-rule derivation (reach/kubectl.py).

Mirrors backend/shared/policy.py; these cases pin the shapes the CLI sends to
POST /tenant/approvals for k8s agents.
"""
from reach.kubectl import command_to_k8s_approval


def _rule(cmd):
    k8s_rule, host_rule, err = command_to_k8s_approval(cmd)
    assert err is None, err
    return k8s_rule, host_rule


def test_create_with_namespace_and_name():
    k8s, host = _rule("kubectl create configmap foo --from-literal=ok=1 -n reach")
    assert host is None
    assert k8s == {"verb": "create", "resource": "configmaps",
                   "namespace": "reach", "name": "foo"}


def test_delete_short_resource_alias():
    k8s, _ = _rule("kubectl delete po mypod -n bar")
    assert k8s == {"verb": "delete", "resource": "pods", "namespace": "bar", "name": "mypod"}


def test_resource_slash_name_form():
    k8s, _ = _rule("kubectl label deploy/web tier=fe -n prod")
    assert k8s["verb"] == "label"
    assert k8s["resource"] == "deployments"
    assert k8s["name"] == "web"
    assert k8s["namespace"] == "prod"


def test_compound_verb_rollout_restart():
    k8s, _ = _rule("kubectl rollout restart deployment web -n prod")
    assert k8s["verb"] == "rollout restart"
    assert k8s["resource"] == "deployments"
    assert k8s["name"] == "web"


def test_namespace_defaults_to_default():
    k8s, _ = _rule("kubectl delete pod foo")
    assert k8s["namespace"] == "default"


def test_all_namespaces_wildcard():
    k8s, _ = _rule("kubectl delete pods --all-namespaces")
    assert k8s["namespace"] == "*"


def test_run_maps_to_pods():
    k8s, _ = _rule("kubectl run nginx --image=nginx -n web")
    assert k8s["verb"] == "run"
    assert k8s["resource"] == "pods"
    assert k8s["name"] == "nginx"


def test_value_flag_not_mistaken_for_name():
    # -f <file> takes a value; it must not be read as resource/name.
    k8s, _ = _rule("kubectl apply -f manifests/ -n reach")
    assert k8s["verb"] == "apply"
    assert k8s["namespace"] == "reach"


def test_non_kubectl_tool_becomes_host_rule():
    k8s, host = _rule("helm upgrade reach ./chart -n reach")
    assert k8s is None
    assert host == {"bin": "helm", "args": ["upgrade", "reach", "./chart", "-n", "reach"]}


def test_read_command_has_no_approvable_write():
    k8s_rule, host_rule, err = command_to_k8s_approval("kubectl get pods -n reach")
    assert k8s_rule is None and host_rule is None
    assert "no approvable write" in err


def test_piped_read_filter_still_reports_the_write_stage():
    # A write stage anywhere in a pipe is what gets approved.
    k8s, _ = _rule("kubectl delete pod foo -n reach | grep something")
    assert k8s["verb"] == "delete"
    assert k8s["name"] == "foo"
