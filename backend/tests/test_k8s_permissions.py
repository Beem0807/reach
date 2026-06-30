"""k8s effective-permissions reporting, drift, and acknowledgement.

Covers the real SQL AgentRepo (store/ack/drift round-trip) plus the sync and
acknowledge handlers at the unit level.
"""
import importlib
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# SQL round-trip: store -> drift -> acknowledge -> drift clears
# ---------------------------------------------------------------------------
@pytest.fixture()
def repo():
    os.environ["DATABASE_URL"] = "sqlite://"
    import shared.repos.sql as sql
    importlib.reload(sql)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    sql.engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    sql.SessionLocal = sessionmaker(bind=sql.engine)
    sql._Base.metadata.create_all(sql.engine)
    return sql.AgentRepo()


_PERMS = {
    "namespace": "team-a",
    "resource_rules": [{"verbs": ["get", "list"], "api_groups": [""], "resources": ["pods"]}],
    "incomplete": False,
    "hash": "abc123",
}


def _create(repo, agent_id):
    repo.create({"agent_id": agent_id, "tenant_id": "t1", "status": "ACTIVE", "mode": "wild"})


def test_no_permissions_means_no_drift(repo):
    _create(repo, "agent_a")
    assert repo.get("agent_a")["k8s_permissions_drift"] is False


def test_reported_permissions_drift_until_acknowledged(repo):
    _create(repo, "agent_a")
    repo.set_k8s_permissions("agent_a", _PERMS, "abc123")
    a = repo.get("agent_a")
    assert a["k8s_permissions"]["resource_rules"][0]["resources"] == ["pods"]
    assert a["k8s_permissions_drift"] is True  # never acknowledged yet

    repo.acknowledge_k8s_permissions("agent_a", "abc123")
    assert repo.get("agent_a")["k8s_permissions_drift"] is False


def test_changed_permissions_redrift_after_ack(repo):
    _create(repo, "agent_a")
    repo.set_k8s_permissions("agent_a", _PERMS, "abc123")
    repo.acknowledge_k8s_permissions("agent_a", "abc123")
    assert repo.get("agent_a")["k8s_permissions_drift"] is False

    # RBAC changes -> new hash -> drift again until re-acknowledged.
    repo.set_k8s_permissions("agent_a", {**_PERMS, "hash": "def456"}, "def456")
    assert repo.get("agent_a")["k8s_permissions_drift"] is True


# ---------------------------------------------------------------------------
# sync handler: stores reported permissions only when the hash changes
# ---------------------------------------------------------------------------
class TestSyncStoresPermissions:
    def _agent(self, **over):
        base = {"agent_id": "agent_a", "status": "ACTIVE", "machine_fingerprint": "fp"}
        base.update(over)
        return base

    def test_stores_when_hash_changes(self):
        from handlers.agent_sync import handle_agent_sync
        body = {"machine_fingerprint": "fp", "k8s_permissions": {"hash": "h1", "resource_rules": []}}
        with patch("handlers.agent_sync._verify_agent_token", return_value=self._agent()), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.approvals_repo") as apr:
            jr.get_pending_for_agent.return_value = []
            apr.list_by_agent.return_value = []
            handle_agent_sync(body, "tok")
        ar.set_k8s_permissions.assert_called_once()
        assert ar.set_k8s_permissions.call_args[0][2] == "h1"

    def test_skips_when_hash_unchanged(self):
        from handlers.agent_sync import handle_agent_sync
        body = {"machine_fingerprint": "fp", "k8s_permissions": {"hash": "h1", "resource_rules": []}}
        agent = self._agent(k8s_permissions_hash="h1")
        with patch("handlers.agent_sync._verify_agent_token", return_value=agent), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.approvals_repo") as apr:
            jr.get_pending_for_agent.return_value = []
            apr.list_by_agent.return_value = []
            handle_agent_sync(body, "tok")
        ar.set_k8s_permissions.assert_not_called()


# ---------------------------------------------------------------------------
# acknowledge handler: pins the acked hash to the current reported hash
# ---------------------------------------------------------------------------
class TestAcknowledgePermissions:
    def test_acknowledges_current_hash(self):
        from handlers.tenant_agents import handle_acknowledge_capability
        perms = {"cluster_wide": [{"verbs": ["get"], "resources": ["pods"]}], "hash": "h9"}
        agent = {"agent_id": "agent_a", "tenant_id": "t1", "k8s_permissions_hash": "h9",
                 "k8s_permissions": perms}
        user = {"user_id": "u1", "tenant_id": "t1", "role": "operator", "username": "op"}
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_agents._require_role", return_value=True), \
             patch("handlers.tenant_agents._get_agent", return_value=agent), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.audit"):
            r = handle_acknowledge_capability("agent_a", {"capability": "k8s_permissions"}, "tok")
        assert r["statusCode"] == 200
        # Pins the acked hash AND snapshots the current permissions as the baseline.
        ar.acknowledge_k8s_permissions.assert_called_once_with("agent_a", "h9", perms)

    def test_nothing_to_acknowledge(self):
        from handlers.tenant_agents import handle_acknowledge_capability
        agent = {"agent_id": "agent_a", "tenant_id": "t1"}  # no reported permissions
        user = {"user_id": "u1", "tenant_id": "t1", "role": "operator", "username": "op"}
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_agents._require_role", return_value=True), \
             patch("handlers.tenant_agents._get_agent", return_value=agent), \
             patch("handlers.tenant_agents.agents_repo"), \
             patch("handlers.tenant_agents.audit"):
            r = handle_acknowledge_capability("agent_a", {"capability": "k8s_permissions"}, "tok")
        assert r["statusCode"] == 400
