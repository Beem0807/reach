"""DynamoDB AuditRepo case-insensitive search: create writes lowercased shadow
fields (actor_name_lc / resource_id_lc / ip_address_lc) that the actor/resource/ip
`contains` filters match against, so search is case-insensitive despite DynamoDB
having no ILIKE.
"""
import os

import pytest

pytest.importorskip("botocore")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from unittest.mock import MagicMock, patch  # noqa: E402

import shared.repos.dynamo as dynamo  # noqa: E402


class TestAuditCreate:
    def test_create_writes_lowercased_shadow_fields(self):
        t = MagicMock()
        with patch.object(dynamo, "_TABLE_AUDIT_LOGS", t):
            dynamo.AuditRepo().create({
                "log_id": "l1", "action": "user.login", "actor_name": "Alice",
                "resource_id": "User_ABC", "ip_address": "10.0.0.1", "created_at": "2026-06-01T00:00:00Z",
            })
        item = t.put_item.call_args.kwargs["Item"]
        # Originals preserved for display; lowercased shadows added for search.
        assert item["actor_name"] == "Alice"
        assert item["actor_name_lc"] == "alice"
        assert item["resource_id_lc"] == "user_abc"
        assert item["ip_address_lc"] == "10.0.0.1"

    def test_create_handles_missing_fields(self):
        t = MagicMock()
        with patch.object(dynamo, "_TABLE_AUDIT_LOGS", t):
            dynamo.AuditRepo().create({"log_id": "l2", "action": "x", "created_at": "2026-06-01T00:00:00Z"})
        item = t.put_item.call_args.kwargs["Item"]
        assert item["actor_name_lc"] == "" and item["resource_id_lc"] == "" and item["ip_address_lc"] == ""


class TestAuditSearchFilters:
    def _table(self, items=None):
        t = MagicMock()
        t.scan.return_value = {"Items": items or []}
        t.query.return_value = {"Items": items or []}
        return t

    def test_platform_actor_filter_builds_expr(self):
        t = self._table()
        with patch.object(dynamo, "_TABLE_AUDIT_LOGS", t):
            dynamo.AuditRepo().list_platform(actor="ALICE")
        assert "FilterExpression" in t.scan.call_args.kwargs

    def test_tenant_resource_and_ip_filters_build_expr(self):
        t = self._table()
        with patch.object(dynamo, "_TABLE_AUDIT_LOGS", t):
            dynamo.AuditRepo().list_by_tenant("t1", resource="User_ABC", ip="10.0.0.1")
        assert "FilterExpression" in t.query.call_args.kwargs
        assert t.query.call_args.kwargs["IndexName"] == "tenant-created-index"
