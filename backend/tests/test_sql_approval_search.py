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


def _mk(repo, i, *, command, k8s_rule=None, requester="alice", status="pending"):
    repo.create({
        "approval_id": f"appr_{i}",
        "tenant_id": "t1",
        "agent_id": "agent_1",
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
