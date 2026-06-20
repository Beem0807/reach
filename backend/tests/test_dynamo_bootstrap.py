"""Behavior tests for the DynamoDB table bootstrap.

These exercise the boto3 interaction, so they're skipped where botocore isn't
installed (e.g. a Postgres-only dev environment). The boto3-free schema/parity
checks live in test_dynamo_schema.py and always run.
"""
import pytest

pytest.importorskip("botocore")
from botocore.exceptions import ClientError  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from shared.dynamo_bootstrap import bootstrap  # noqa: E402
from shared.dynamo_schema import TABLES  # noqa: E402


class TestBootstrap:
    def test_creates_all_tables_when_absent(self):
        client = MagicMock()
        client.get_waiter.return_value = MagicMock()
        result = bootstrap(client=client)
        assert client.create_table.call_count == len(TABLES)
        assert set(result["created"]) == {s["TableName"] for s in TABLES}
        assert result["existing"] == []

    def test_existing_tables_are_skipped_idempotently(self):
        client = MagicMock()
        client.get_waiter.return_value = MagicMock()
        client.create_table.side_effect = ClientError(
            {"Error": {"Code": "ResourceInUseException", "Message": "exists"}}, "CreateTable")
        result = bootstrap(client=client)
        assert result["created"] == []
        assert set(result["existing"]) == {s["TableName"] for s in TABLES}

    def test_waits_for_created_tables_to_become_active(self):
        client = MagicMock()
        waiter = MagicMock()
        client.get_waiter.return_value = waiter
        bootstrap(client=client)
        assert waiter.wait.call_count == len(TABLES)

    def test_unexpected_error_propagates(self):
        client = MagicMock()
        client.get_waiter.return_value = MagicMock()
        client.create_table.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "CreateTable")
        with pytest.raises(ClientError):
            bootstrap(client=client)
