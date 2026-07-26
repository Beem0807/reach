"""Approval explainability (shared.explain.explain_approval)."""
from shared.explain import explain_approval


def _facts(exp):
    return {f["label"]: f["value"] for f in exp["facts"]}


class TestHostExplain:
    def test_wildcard_service_fleet_never_expires_is_high(self):
        # The spec example: restart ANY service, 48 fleet members, never expires -> High.
        appr = {"fleet_id": "fleet_1", "host_rule": {"bin": "systemctl", "args": ["restart", "*"]},
                "expires_at": None}
        exp = explain_approval(appr, member_count=48, target_label="web-prod")
        f = _facts(exp)
        assert f["Requested action"] == "Restart a service"
        assert f["Allowed service"] == "Any"          # the "also permits" line
        assert "48 fleet members" in f["Targets"]
        assert f["Reusable"] == "Yes"
        assert f["Expires"] == "Never"
        assert exp["risk"] == "high"
        assert any("wildcard" in r for r in exp["risk_factors"])
        assert any("48 targets" in r for r in exp["risk_factors"])

    def test_literal_single_agent_short_expiry_is_low(self):
        appr = {"agent_id": "a1", "host_rule": {"bin": "systemctl", "args": ["restart", "nginx"]},
                "expires_at": 10_000_000_000}   # far future epoch, but not "never"
        exp = explain_approval(appr, target_label="web-01", now=1_000)
        f = _facts(exp)
        assert f["Requested action"] == "Restart a service"
        assert "Allowed service" not in f            # fully literal -> no widening line
        assert f["Targets"] == "web-01"
        # destructive verb but single target + no wildcard -> medium at most, not high.
        assert exp["risk"] in ("low", "medium")

    def test_variadic_rest_is_a_wildcard(self):
        appr = {"agent_id": "a1", "host_rule": {"bin": "rm", "args": ["..."]}, "expires_at": None}
        exp = explain_approval(appr)
        f = _facts(exp)
        assert f["Requested action"] == "Delete files"
        assert f["Extra arguments"] == "Any"
        assert exp["risk"] == "high"                 # destructive + wildcard + never expires

    def test_sensitive_read_flagged(self):
        appr = {"agent_id": "a1", "sensitive": True,
                "host_rule": {"bin": "cat", "args": ["/etc/shadow"]}, "expires_at": None}
        exp = explain_approval(appr)
        f = _facts(exp)
        assert f["Reads secrets"] == "Yes"
        assert any("secret" in r for r in exp["risk_factors"])

    def test_sensitivity_detected_from_rule_without_flag(self):
        # No stored `sensitive` flag - it must still be detected from the rule's path.
        appr = {"agent_id": "a1", "host_rule": {"bin": "cat", "args": ["/srv/app/.env"]},
                "expires_at": None}
        exp = explain_approval(appr)
        assert _facts(exp)["Reads secrets"] == "Yes"
        assert any("secret" in r for r in exp["risk_factors"])

    def test_k8s_secret_read_detected_from_rule(self):
        appr = {"agent_id": "a1", "k8s_rule": {"verb": "get", "resource": "secrets",
                "namespace": "prod", "name": "*"}, "expires_at": None}
        exp = explain_approval(appr)
        assert _facts(exp)["Reads secrets"] == "Yes"


class TestK8sExplain:
    def test_delete_pods_any_name_in_namespace(self):
        # The spec example: delete pods, namespace payments, name any -> "all pods in payments".
        appr = {"agent_id": "a1", "k8s_rule": {"verb": "delete", "resource": "pods",
                "namespace": "payments", "name": "*"}, "expires_at": None}
        exp = explain_approval(appr, target_label="prod-cluster")
        f = _facts(exp)
        assert f["Verb / Resource"] == "delete pods"
        assert f["Namespace"] == "payments"
        assert f["Name"] == "Any"
        assert f["Blast radius"] == "delete all pods in payments"
        assert exp["risk"] == "high"

    def test_all_namespaces_widens(self):
        appr = {"agent_id": "a1", "k8s_rule": {"verb": "delete", "resource": "pods",
                "namespace": "*", "name": "*"}, "expires_at": None}
        exp = explain_approval(appr)
        assert _facts(exp)["Blast radius"] == "delete all pods across all namespaces"
        assert exp["risk"] == "high"

    def test_named_pod_is_narrower(self):
        appr = {"agent_id": "a1", "k8s_rule": {"verb": "delete", "resource": "pods",
                "namespace": "payments", "name": "nginx"}, "expires_at": 10_000_000_000}
        exp = explain_approval(appr, now=1000)
        assert _facts(exp)["Blast radius"] == "delete pod nginx in payments"


class TestExpiryFormatting:
    def test_iso_string_expiry(self):
        appr = {"agent_id": "a1", "host_rule": {"bin": "ls", "args": []},
                "expires_at": "2100-01-01T00:00:00+00:00"}
        exp = explain_approval(appr, now=1000)
        assert _facts(exp)["Expires"].startswith("in ")

    def test_expired(self):
        appr = {"agent_id": "a1", "host_rule": {"bin": "ls", "args": []}, "expires_at": 500}
        assert _facts(explain_approval(appr, now=1000))["Expires"] == "Expired"
