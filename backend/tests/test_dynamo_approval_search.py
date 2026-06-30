"""DynamoDB ApprovalRepo.search_by_tenant: query wiring + Python-side kind
filter and pagination. DynamoDB has no LIKE, so the text query is a `contains`
FilterExpression (asserted present); kind and offset/limit are applied in Python.
"""
import os

import pytest

pytest.importorskip("botocore")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from unittest.mock import MagicMock, patch  # noqa: E402

import shared.repos.dynamo as dynamo  # noqa: E402

HOST = {"approval_id": "h1", "agent_id": "a", "command": "docker restart x", "status": "pending"}
K8S = {
    "approval_id": "k1", "agent_id": "a", "command": "kubectl delete pods -n team-a",
    "status": "pending", "k8s_rule": {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*"},
}


def _table(items):
    t = MagicMock()
    t.query.return_value = {"Items": items}
    return t


class TestApprovalSearch:
    def test_kind_host_excludes_rules(self):
        t = _table([HOST, K8S])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            items, total = dynamo.ApprovalRepo().search_by_tenant("t1", kind="host", limit=100)
        assert total == 1 and items[0]["approval_id"] == "h1"

    def test_kind_k8s_only_rules(self):
        t = _table([HOST, K8S])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            items, total = dynamo.ApprovalRepo().search_by_tenant("t1", kind="k8s", limit=100)
        assert total == 1 and items[0]["approval_id"] == "k1"

    def test_query_uses_tenant_index(self):
        t = _table([HOST])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().search_by_tenant("t1", limit=100)
        assert t.query.call_args.kwargs["IndexName"] == "tenant-approvals-index"

    def test_q_builds_filter_expression(self):
        t = _table([HOST])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().search_by_tenant("t1", q="docker", limit=100)
        assert "FilterExpression" in t.query.call_args.kwargs

    def test_status_and_requested_by_build_filter(self):
        t = _table([HOST])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().search_by_tenant("t1", status="pending", requested_by="u1", limit=100)
        assert "FilterExpression" in t.query.call_args.kwargs

    def test_agent_ids_builds_filter_expression(self):
        # An agent allow-list (scoped operator) adds an OR filter over agent_id.
        t = _table([HOST])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().search_by_tenant("t1", agent_ids=["a", "b"], limit=100)
        assert "FilterExpression" in t.query.call_args.kwargs

    def test_empty_agent_ids_returns_nothing_without_query(self):
        # An empty allow-list matches no agents; short-circuit before hitting Dynamo.
        t = _table([HOST])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            items, total = dynamo.ApprovalRepo().search_by_tenant("t1", agent_ids=[], limit=100)
        assert items == [] and total == 0
        t.query.assert_not_called()

    def test_pagination_slices_and_total(self):
        items = [{"approval_id": f"h{i}", "agent_id": "a", "command": f"c{i}", "status": "pending"} for i in range(12)]
        t = _table(items)
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            page1, total = dynamo.ApprovalRepo().search_by_tenant("t1", kind="host", limit=10, offset=0)
            page2, total2 = dynamo.ApprovalRepo().search_by_tenant("t1", kind="host", limit=10, offset=10)
        assert total == 12 and total2 == 12
        assert len(page1) == 10 and len(page2) == 2


class TestCaseInsensitiveWrite:
    def test_create_writes_lowercased_shadow_fields(self):
        t = MagicMock()
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().create({
                "approval_id": "a1", "tenant_id": "t1", "agent_id": "a",
                "command": "Docker RESTART App", "requester_name": "Alice", "status": "pending",
            })
        item = t.put_item.call_args.kwargs["Item"]
        # Original preserved; lowercased shadows added for case-insensitive contains.
        assert item["command"] == "Docker RESTART App"
        assert item["command_lc"] == "docker restart app"
        assert item["requester_name_lc"] == "alice"

    def test_create_handles_missing_requester(self):
        t = MagicMock()
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().create({
                "approval_id": "a2", "tenant_id": "t1", "agent_id": "a",
                "command": "KUBECTL delete pods", "requester_name": None, "status": "pending",
            })
        item = t.put_item.call_args.kwargs["Item"]
        assert item["command_lc"] == "kubectl delete pods"
        assert item["requester_name_lc"] == ""

    def test_search_lowercases_query_into_filter(self):
        # Sanity: a mixed-case query is accepted and a FilterExpression is built;
        # matching is case-insensitive by construction (both sides lowercased).
        t = _table([HOST])
        with patch.object(dynamo, "_TABLE_APPROVALS", t):
            dynamo.ApprovalRepo().search_by_tenant("t1", q="DoCkEr", limit=100)
        assert "FilterExpression" in t.query.call_args.kwargs
