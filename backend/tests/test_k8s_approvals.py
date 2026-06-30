"""Structured k8s approval rules: parsing, matching, wildcards, normalization."""
from shared.policy import (
    derive_k8s_rule,
    is_k8s_command_approved,
    k8s_rule_matches,
    normalize_k8s_rule,
    parse_kubectl,
    rule_to_command,
)
from shared.policy import _tokenize


def _parse(cmd):
    return parse_kubectl(_tokenize(cmd))


# ---------------------------------------------------------------------------
# Parsing kubectl into {verb, resource, namespace, name}
# ---------------------------------------------------------------------------

class TestParse:
    def test_create_pod_with_namespace(self):
        assert _parse("kubectl create pod nginx -n team-a") == {
            "verb": "create", "resource": "pods", "namespace": "team-a", "name": "nginx"}

    def test_slash_form(self):
        assert _parse("kubectl -n team-a delete deploy/web") == {
            "verb": "delete", "resource": "deployments", "namespace": "team-a", "name": "web"}

    def test_namespace_defaults_to_default(self):
        assert _parse("kubectl delete pod x")["namespace"] == "default"

    def test_all_namespaces_is_wildcard(self):
        assert _parse("kubectl delete pods --all-namespaces")["namespace"] == "*"
        assert _parse("kubectl delete pods -A")["namespace"] == "*"

    def test_namespace_equals_form(self):
        assert _parse("kubectl create pod x --namespace=prod")["namespace"] == "prod"

    def test_run_implies_pods(self):
        assert _parse("kubectl run mypod --image=nginx -n dev") == {
            "verb": "run", "resource": "pods", "namespace": "dev", "name": "mypod"}

    def test_rollout_keeps_compound_verb(self):
        # Double verb: the sub-subcommand is kept in `verb` so restart (write) is
        # distinct from status (read) and separately approvable.
        assert _parse("kubectl rollout restart deploy/web -n prod") == {
            "verb": "rollout restart", "resource": "deployments", "namespace": "prod", "name": "web"}

    def test_resource_alias_normalized(self):
        assert _parse("kubectl delete svc/api -n x")["resource"] == "services"

    def test_read_verb_still_parses(self):
        # parse itself doesn't care about read/write; callers gate on verb.
        assert _parse("kubectl get pods -n x")["verb"] == "get"

    def test_non_kubectl_returns_none_via_derive(self):
        assert derive_k8s_rule("ls -la") is None


# ---------------------------------------------------------------------------
# Rule matching + wildcards
# ---------------------------------------------------------------------------

class TestMatch:
    def test_exact_match(self):
        rule = {"verb": "create", "resource": "pods", "namespace": "team-a", "name": "*"}
        assert is_k8s_command_approved("kubectl create pod redis -n team-a", [rule])

    def test_name_wildcard_covers_any_object(self):
        rule = {"verb": "create", "resource": "pods", "namespace": "team-a", "name": "*"}
        assert is_k8s_command_approved("kubectl create pod nginx -n team-a", [rule])
        assert is_k8s_command_approved("kubectl create pod redis -n team-a", [rule])

    def test_specific_name_blocks_others(self):
        rule = {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "nginx"}
        assert is_k8s_command_approved("kubectl delete pod nginx -n team-a", [rule])
        assert not is_k8s_command_approved("kubectl delete pod redis -n team-a", [rule])

    def test_namespace_must_match(self):
        rule = {"verb": "create", "resource": "pods", "namespace": "team-a", "name": "*"}
        assert not is_k8s_command_approved("kubectl create pod x -n team-b", [rule])

    def test_verb_wildcard_any_write_in_namespace(self):
        rule = {"verb": "*", "resource": "*", "namespace": "team-a", "name": "*"}
        assert is_k8s_command_approved("kubectl delete deploy/web -n team-a", [rule])
        assert is_k8s_command_approved("kubectl create pod x -n team-a", [rule])
        assert not is_k8s_command_approved("kubectl create pod x -n team-b", [rule])

    def test_namespace_wildcard_any_namespace(self):
        rule = {"verb": "delete", "resource": "pods", "namespace": "*", "name": "*"}
        assert is_k8s_command_approved("kubectl delete pod x -n anything", [rule])
        assert is_k8s_command_approved("kubectl delete pod y --all-namespaces", [rule])

    def test_resource_wildcard(self):
        rule = {"verb": "delete", "resource": "*", "namespace": "team-a", "name": "*"}
        assert is_k8s_command_approved("kubectl delete deploy/web -n team-a", [rule])
        assert is_k8s_command_approved("kubectl delete pod x -n team-a", [rule])

    def test_full_wildcard_allows_any_write(self):
        rule = {"verb": "*", "resource": "*", "namespace": "*", "name": "*"}
        assert is_k8s_command_approved("kubectl delete pod x -n whatever", [rule])

    def test_reads_always_allowed_regardless_of_rules(self):
        assert is_k8s_command_approved("kubectl get pods -n team-a", [])

    def test_pipeline_every_write_stage_must_match(self):
        rule = {"verb": "apply", "resource": "*", "namespace": "team-a", "name": "*"}
        # read | write(apply) -> only the write stage is gated
        assert is_k8s_command_approved("kubectl get x -o yaml -n team-a | kubectl apply -f - -n team-a", [rule])

    def test_unparseable_write_stays_blocked(self):
        # create -f (no positional resource) parses to empty resource; a specific
        # rule does not match it, so it stays blocked (safe default).
        rule = {"verb": "create", "resource": "pods", "namespace": "default", "name": "*"}
        assert not is_k8s_command_approved("kubectl create -f manifest.yaml", [rule])

    def test_rule_matches_direct(self):
        parsed = {"verb": "delete", "resource": "pods", "namespace": "x", "name": "a"}
        assert k8s_rule_matches(parsed, {"verb": "*", "resource": "*", "namespace": "*", "name": "*"})
        assert not k8s_rule_matches(parsed, {"verb": "create", "resource": "*", "namespace": "*", "name": "*"})


# ---------------------------------------------------------------------------
# Normalization + rendering
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_verb_is_mandatory(self):
        # An absent rule must not silently become allow-all.
        assert normalize_k8s_rule({}) is None
        assert normalize_k8s_rule({"namespace": "team-a"}) is None

    def test_other_fields_default_to_wildcard_when_verb_present(self):
        assert normalize_k8s_rule({"verb": "delete"}) == {
            "verb": "delete", "resource": "*", "namespace": "*", "name": "*"}

    def test_wildcard_verb_allowed_when_explicit(self):
        assert normalize_k8s_rule({"verb": "*", "namespace": "team-a"})["verb"] == "*"

    def test_read_verb_rejected(self):
        assert normalize_k8s_rule({"verb": "get"}) is None

    def test_resource_alias_normalized(self):
        assert normalize_k8s_rule({"verb": "delete", "resource": "deploy"})["resource"] == "deployments"

    def test_non_dict_rejected(self):
        assert normalize_k8s_rule("nope") is None

    def test_render_roundtrip_is_stable(self):
        rule = normalize_k8s_rule({"verb": "delete", "resource": "po", "namespace": "team-a", "name": "nginx"})
        assert rule == {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "nginx"}
        assert "team-a" in rule_to_command(rule)


# ---------------------------------------------------------------------------
# Deriving a rule from a blocked command (for the pending approval)
# ---------------------------------------------------------------------------

class TestDerive:
    def test_derive_uses_first_write_stage(self):
        assert derive_k8s_rule("kubectl get x -n team-a | kubectl delete pod y -n team-a") == {
            "verb": "delete", "resource": "pods", "namespace": "team-a", "name": "y"}

    def test_read_only_command_has_no_rule(self):
        assert derive_k8s_rule("kubectl get pods -n team-a") is None


# ---------------------------------------------------------------------------
# Pipes, shell chains, flags/keys, and the -- separator
# ---------------------------------------------------------------------------

class TestPipesAndFlags:
    def test_read_piped_to_filter_is_allowed(self):
        # non-kubectl filter stages (jq/grep) are ignored; only kubectl matters
        assert is_k8s_command_approved("kubectl get pods -o json -n a | jq .items", [])

    def test_write_piped_to_filter_still_gated(self):
        rule = {"verb": "delete", "resource": "pods", "namespace": "a", "name": "*"}
        assert is_k8s_command_approved("kubectl delete pod x -n a | grep foo", [rule])
        assert not is_k8s_command_approved("kubectl delete pod x -n b | grep foo", [rule])

    def test_every_kubectl_stage_in_a_chain_is_checked(self):
        # `create ns` is not covered by a pods rule, so the whole command is blocked
        pods_rule = {"verb": "create", "resource": "pods", "namespace": "a", "name": "*"}
        assert not is_k8s_command_approved("kubectl create ns a && kubectl create pod x -n a", [pods_rule])

    def test_selector_flag_does_not_pollute_resource(self):
        assert _parse("kubectl delete pods -l app=web -n a") == {
            "verb": "delete", "resource": "pods", "namespace": "a", "name": ""}

    def test_from_literal_key_value_flag_is_skipped(self):
        # --from-literal=key=value must not be read as a resource/name
        parsed = _parse("kubectl create configmap cm --from-literal=key=value -n a")
        assert parsed["verb"] == "create" and parsed["namespace"] == "a"

    def test_exec_container_command_after_dashdash_ignored(self):
        # tokens after -- are the container command, not a k8s name
        assert _parse("kubectl exec mypod -n a -- ls /")["name"] == ""

    def test_unparseable_create_f_blocks_specific_rule(self):
        rule = {"verb": "create", "resource": "pods", "namespace": "a", "name": "*"}
        assert not is_k8s_command_approved("kubectl create -f manifest.yaml -n a", [rule])
        # ...but a resource-wildcard rule the operator explicitly set does cover it
        wild = {"verb": "create", "resource": "*", "namespace": "a", "name": "*"}
        assert is_k8s_command_approved("kubectl create -f manifest.yaml -n a", [wild])
