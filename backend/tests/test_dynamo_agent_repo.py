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
