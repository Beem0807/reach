"""Functional tests for the DynamoDB AgentRepo credential-only hash lookups.

These exercise the boto3 query wiring (GSI name + key attribute), which otherwise
would only surface against a live DynamoDB. Skipped where botocore isn't installed.
"""
import os

import pytest

pytest.importorskip("botocore")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from unittest.mock import MagicMock, patch  # noqa: E402

import shared.repos.dynamo as dynamo  # noqa: E402


def _key_attr(cond):
    """The attribute name a boto3 Key().eq() condition queries on."""
    return cond.get_expression()["values"][0].name


class TestAgentRepoHashLookups:
    def _query(self, items):
        table = MagicMock()
        table.query.return_value = {"Items": items}
        return table

    def test_get_by_install_token_hash_queries_right_gsi_and_key(self):
        table = self._query([{"agent_id": "agent_a", "mode": "wild"}])
        with patch.object(dynamo, "_TABLE_AGENTS", table):
            got = dynamo.AgentRepo().get_by_install_token_hash("hhh")
        kwargs = table.query.call_args.kwargs
        assert kwargs["IndexName"] == "install-token-hash-index"
        assert _key_attr(kwargs["KeyConditionExpression"]) == "install_token_hash"
        assert got["agent_id"] == "agent_a"
        assert "access_level" in got  # enriched

    def test_get_by_agent_token_hash_queries_right_gsi_and_key(self):
        table = self._query([{"agent_id": "agent_a", "mode": "wild"}])
        with patch.object(dynamo, "_TABLE_AGENTS", table):
            got = dynamo.AgentRepo().get_by_agent_token_hash("ttt")
        kwargs = table.query.call_args.kwargs
        assert kwargs["IndexName"] == "agent-token-hash-index"
        assert _key_attr(kwargs["KeyConditionExpression"]) == "agent_token_hash"
        assert got["agent_id"] == "agent_a"

    def test_no_match_returns_none(self):
        table = self._query([])
        with patch.object(dynamo, "_TABLE_AGENTS", table):
            assert dynamo.AgentRepo().get_by_install_token_hash("nope") is None

    def test_empty_hash_short_circuits_without_query(self):
        table = self._query([])
        with patch.object(dynamo, "_TABLE_AGENTS", table):
            assert dynamo.AgentRepo().get_by_install_token_hash("") is None
            assert dynamo.AgentRepo().get_by_agent_token_hash("") is None
        table.query.assert_not_called()


class TestK8sPermissionTruncation:
    def test_small_snapshot_untouched(self):
        perms = {"cluster_wide": [{"verbs": ["get"], "resources": ["pods"]}], "hash": "h"}
        assert dynamo._truncate_k8s_permissions(perms) is perms  # no copy, no change

    def test_none_passthrough(self):
        assert dynamo._truncate_k8s_permissions(None) is None

    def test_oversized_drops_namespaces_first_and_marks_truncated(self):
        # Over the 180KB budget from a few large per-namespace deltas.
        big_ns = [
            {"namespace": f"ns-{i}",
             "resource_rules": [{"verbs": ["get", "list", "watch"], "api_groups": ["apps"],
                                 "resources": ["r" * 4000]}]}
            for i in range(80)
        ]
        perms = {"cluster_wide": [{"verbs": ["get"], "api_groups": [""], "resources": ["pods"]}],
                 "namespaces": big_ns, "hash": "h"}
        out = dynamo._truncate_k8s_permissions(perms)
        assert out["truncated"] is True
        assert len(out["namespaces"]) < len(big_ns)          # deltas dropped
        assert out["cluster_wide"] == perms["cluster_wide"]  # cluster-wide kept
        assert out["hash"] == "h"                            # hash preserved (drift stays exact)

    def test_set_and_acknowledge_truncate_but_keep_hash(self):
        table = MagicMock()
        big = {"cluster_wide": [{"verbs": ["get"], "resources": ["x" * 200000]}], "hash": "H"}
        with patch.object(dynamo, "_TABLE_AGENTS", table):
            dynamo.AgentRepo().set_k8s_permissions("agent_a", big, "H")
            dynamo.AgentRepo().acknowledge_k8s_permissions("agent_a", "H", big)
        # Both writes pass the full hash through unchanged.
        for call in table.update_item.call_args_list:
            vals = call.kwargs["ExpressionAttributeValues"]
            assert vals[":h"] == "H"
