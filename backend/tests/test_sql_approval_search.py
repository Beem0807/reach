"""Server-side search + pagination for ApprovalRepo against real SQLite.

Exercises the actual SQL: kind filter (host = NULL rule, k8s = non-NULL rule),
case-insensitive LIKE over command/requester, and limit/offset paging + total.
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
    return sql.ApprovalRepo()


def _mk(repo, i, *, command, k8s_rule=None, requester="alice", status="pending", agent_id="agent_1"):
    repo.create({
        "approval_id": f"appr_{i}",
        "tenant_id": "t1",
        "agent_id": agent_id,
        "command": command,
        "k8s_rule": k8s_rule,
        "requested_by": "u1",
        "requester_name": requester,
        "job_id": None,
        "status": status,
        "expires_at": None,
        "created_at": f"2026-06-01T{i:02d}:00:00Z",
        "reviewed_at": None,
        "reviewed_by": None,
    })


def _seed(repo):
    for i in range(6):
        _mk(repo, i, command=f"docker restart svc-{i}")  # host
    for i in range(6, 12):
        _mk(repo, i, command=f"kubectl delete pods -n team-{i}",
            k8s_rule={"verb": "delete", "resource": "pods", "namespace": f"team-{i}", "name": "*"})  # k8s


def test_kind_filter_host_excludes_rules(repo):
    _seed(repo)
    items, total = repo.search_by_tenant("t1", kind="host", limit=100)
    assert total == 6
    assert all(a["k8s_rule"] is None for a in items)


def test_kind_filter_k8s_only_rules(repo):
    _seed(repo)
    items, total = repo.search_by_tenant("t1", kind="k8s", limit=100)
    assert total == 6
    assert all(a["k8s_rule"] is not None for a in items)


def test_like_search_on_command(repo):
    _seed(repo)
    items, total = repo.search_by_tenant("t1", q="svc-3", limit=100)
    assert total == 1
    assert items[0]["command"] == "docker restart svc-3"


def test_like_search_is_case_insensitive(repo):
    _seed(repo)
    items, total = repo.search_by_tenant("t1", q="DOCKER", limit=100)
    assert total == 6


def test_search_matches_k8s_rule_text_via_command(repo):
    _seed(repo)
    # k8s command mirrors the rule (namespace team-9), so LIKE finds it
    items, total = repo.search_by_tenant("t1", q="team-9", kind="k8s", limit=100)
    assert total == 1
    assert items[0]["k8s_rule"]["namespace"] == "team-9"


def test_pagination_limit_offset_and_total(repo):
    _seed(repo)
    page1, total = repo.search_by_tenant("t1", kind="host", limit=4, offset=0)
    page2, total2 = repo.search_by_tenant("t1", kind="host", limit=4, offset=4)
    assert total == 6 and total2 == 6
    assert len(page1) == 4 and len(page2) == 2
    ids1 = {a["approval_id"] for a in page1}
    ids2 = {a["approval_id"] for a in page2}
    assert ids1.isdisjoint(ids2)  # no overlap across pages


def test_search_then_paginate_spans_all(repo):
    _seed(repo)
    # 6 host matches for "docker", page size 4 -> 4 + 2, total 6
    p1, total = repo.search_by_tenant("t1", q="docker", limit=4, offset=0)
    p2, _ = repo.search_by_tenant("t1", q="docker", limit=4, offset=4)
    assert total == 6 and len(p1) == 4 and len(p2) == 2


def test_agent_ids_filter_restricts_to_listed_agents(repo):
    # A scoped operator's allow-list limits results to those agents.
    _mk(repo, 0, command="docker ps", agent_id="agent_1")
    _mk(repo, 1, command="docker ps", agent_id="agent_2")
    _mk(repo, 2, command="docker ps", agent_id="agent_3")
    items, total = repo.search_by_tenant("t1", agent_ids=["agent_1", "agent_2"], limit=100)
    assert total == 2
    assert {a["agent_id"] for a in items} == {"agent_1", "agent_2"}


def test_empty_agent_ids_matches_nothing(repo):
    _mk(repo, 0, command="docker ps", agent_id="agent_1")
    items, total = repo.search_by_tenant("t1", agent_ids=[], limit=100)
    assert total == 0 and items == []


def _mk_fleet(repo, i, *, command, fleet_id="fleet_1", status="pending"):
    repo.create({
        "approval_id": f"fappr_{i}",
        "tenant_id": "t1",
        "agent_id": None,
        "fleet_id": fleet_id,
        "command": command,
        "k8s_rule": None,
        "requested_by": "u1",
        "requester_name": "alice",
        "job_id": None,
        "status": status,
        "expires_at": None,
        "created_at": f"2026-06-02T{i:02d}:00:00Z",
        "reviewed_at": None,
        "reviewed_by": None,
    })


def test_list_by_fleet_scopes_to_fleet(repo):
    _mk_fleet(repo, 0, command="docker ps", fleet_id="fleet_1")
    _mk_fleet(repo, 1, command="docker ps", fleet_id="fleet_2")
    items = repo.list_by_fleet("fleet_1")
    assert [a["approval_id"] for a in items] == ["fappr_0"]


def test_list_by_fleet_status_filter(repo):
    _mk_fleet(repo, 0, command="a", status="approved")
    _mk_fleet(repo, 1, command="b", status="pending")
    assert [a["command"] for a in repo.list_by_fleet("fleet_1", status="approved")] == ["a"]


def test_exists_pending_fleet(repo):
    _mk_fleet(repo, 0, command="docker restart web", status="pending")
    assert repo.exists_pending_fleet("fleet_1", "docker restart web") is True
    assert repo.exists_pending_fleet("fleet_1", "docker restart db") is False
    assert repo.exists_pending_fleet("fleet_2", "docker restart web") is False


def test_search_fleet_id_scopes_results(repo):
    _mk_fleet(repo, 0, command="docker ps", fleet_id="fleet_1")
    _mk_fleet(repo, 1, command="docker ps", fleet_id="fleet_2")
    _mk(repo, 5, command="docker ps", agent_id="agent_1")
    items, total = repo.search_by_tenant("t1", fleet_id="fleet_1", limit=100)
    assert total == 1 and items[0]["fleet_id"] == "fleet_1"


def test_search_fleet_ids_and_agent_ids_union(repo):
    _mk_fleet(repo, 0, command="x", fleet_id="fleet_1")
    _mk(repo, 5, command="y", agent_id="agent_1")
    _mk(repo, 6, command="z", agent_id="agent_9")  # excluded
    items, total = repo.search_by_tenant("t1", agent_ids=["agent_1"], fleet_ids=["fleet_1"], limit=100)
    assert total == 2
    assert {a["approval_id"] for a in items} == {"fappr_0", "appr_5"}


def test_search_scope_agent_excludes_fleet(repo):
    _mk(repo, 0, command="a", agent_id="agent_1")
    _mk_fleet(repo, 1, command="b", fleet_id="fleet_1")
    items, total = repo.search_by_tenant("t1", scope="agent", limit=100)
    assert total == 1 and items[0]["agent_id"] == "agent_1"


def test_search_scope_fleet_excludes_agents(repo):
    _mk(repo, 0, command="a", agent_id="agent_1")
    _mk_fleet(repo, 1, command="b", fleet_id="fleet_1")
    items, total = repo.search_by_tenant("t1", scope="fleet", limit=100)
    assert total == 1 and items[0]["fleet_id"] == "fleet_1"


def _mk_full(repo, i, *, agent_id=None, fleet_id=None):
    repo.create({
        "approval_id": f"appr_{i}", "tenant_id": "t1", "agent_id": agent_id, "fleet_id": fleet_id,
        "command": "uptime", "k8s_rule": None, "requested_by": "u1", "requester_name": "alice",
        "job_id": None, "status": "approved", "expires_at": None,
        "created_at": f"2026-06-01T{i:02d}:00:00Z", "reviewed_at": None, "reviewed_by": None,
    })


def test_delete_by_agent_removes_only_that_agents_approvals(repo):
    _mk_full(repo, 1, agent_id="agent_1")
    _mk_full(repo, 2, agent_id="agent_1")
    _mk_full(repo, 3, agent_id="agent_2")
    n = repo.delete_by_agent("agent_1")
    assert n == 2
    remaining = repo.list_by_agent("agent_2")
    assert {a["approval_id"] for a in remaining} == {"appr_3"}
    assert repo.list_by_agent("agent_1") == []


def test_delete_by_fleet_removes_only_that_fleets_approvals(repo):
    _mk_full(repo, 1, fleet_id="fleet_1")
    _mk_full(repo, 2, fleet_id="fleet_1", agent_id="agent_9")
    _mk_full(repo, 3, fleet_id="fleet_2")
    n = repo.delete_by_fleet("fleet_1")
    assert n == 2
    assert {a["approval_id"] for a in repo.list_by_fleet("fleet_2")} == {"appr_3"}
    assert repo.list_by_fleet("fleet_1") == []


def test_delete_by_agent_no_match_returns_zero(repo):
    _mk_full(repo, 1, agent_id="agent_1")
    assert repo.delete_by_agent("ghost") == 0
