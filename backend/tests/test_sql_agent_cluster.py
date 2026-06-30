"""Round-trip tests for type-at-claim behaviour.

Runs the real SQL AgentRepo against an in-memory SQLite database so we exercise
the actual claim() update statement and _to_dict serialization. k8s agents are
distinguished by type alone - the agent record's id is the cluster's identity,
so there is no separate cluster_id column.
"""
import importlib
import os

import pytest


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


def _create(repo, agent_id):
    repo.create({
        "agent_id": agent_id,
        "tenant_id": "t1",
        "status": "CREATED",
        "mode": "wild",
    })


def _claim_fields(**over):
    base = {
        "agent_token_hash": "h",
        "machine_fingerprint": "fp",
        "hostname": "host",
        "agent_version": "0.1.0",
        "claimed_at": "2026-06-24T00:00:00Z",
        "active_until": 0,
        "token_issued_at": "2026-06-24T00:00:00Z",
        "type": "host",
    }
    base.update(over)
    return base


def test_host_claim_sets_type_host(repo):
    _create(repo, "agent_m")
    repo.claim("agent_m", _claim_fields())
    assert repo.get("agent_m")["type"] == "host"


def test_k8s_claim_sets_type_k8s(repo):
    _create(repo, "agent_k")
    repo.claim("agent_k", _claim_fields(type="k8s"))
    assert repo.get("agent_k")["type"] == "k8s"


def test_claim_defaults_type_when_missing(repo):
    _create(repo, "agent_d")
    fields = _claim_fields()
    del fields["type"]
    repo.claim("agent_d", fields)
    assert repo.get("agent_d")["type"] == "host"


# ---------------------------------------------------------------------------
# k8s permissions: acknowledged snapshot round-trip + drift
# ---------------------------------------------------------------------------

def test_acknowledge_stores_full_snapshot_and_clears_drift(repo):
    _create(repo, "agent_k")
    perms = {"cluster_wide": [{"verbs": ["get", "list"], "api_groups": ["apps"],
                               "resources": ["deployments"]}], "hash": "h1"}
    repo.set_k8s_permissions("agent_k", perms, "h1")

    # Before acknowledge: reported, drift True (no acked hash yet).
    a = repo.get("agent_k")
    assert a["k8s_permissions"] == perms
    assert a["k8s_permissions_drift"] is True
    assert a["k8s_permissions_acked"] is None

    # Acknowledge stores the snapshot (Postgres/SQLite: in full) and clears drift.
    repo.acknowledge_k8s_permissions("agent_k", "h1", perms)
    a = repo.get("agent_k")
    assert a["k8s_permissions_acked"] == perms
    assert a["k8s_permissions_drift"] is False

    # A changed permission set (new hash) drifts again; acked baseline is retained
    # so the console can diff current vs acknowledged.
    changed = {"cluster_wide": perms["cluster_wide"] + [
        {"verbs": ["create"], "api_groups": ["apps"], "resources": ["deployments"]}], "hash": "h2"}
    repo.set_k8s_permissions("agent_k", changed, "h2")
    a = repo.get("agent_k")
    assert a["k8s_permissions_drift"] is True
    assert a["k8s_permissions"] == changed          # current
    assert a["k8s_permissions_acked"] == perms      # baseline unchanged
