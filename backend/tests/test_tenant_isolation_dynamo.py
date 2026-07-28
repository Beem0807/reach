"""
Tenant-isolation harness - DynamoDB leg.

The SQL leg (test_tenant_isolation.py) drives the full stack (adapter -> handler -> repo) and so
covers both isolation layers end-to-end: the handler ownership check *and* the repo's
``list_by_tenant`` scoping. The handler ownership check is adapter-agnostic - literally the same
``resource.tenant_id != caller_tenant -> 404`` code path regardless of backend - so it doesn't need
re-running per adapter. What *is* adapter-specific is the scoped **listing query**: SQL's
``WHERE tenant_id = ?`` versus DynamoDB's ``tenant-index`` GSI query. This module pins that
DynamoDB query against a moto-backed table built from the *real* schema
(``shared.dynamo_schema`` via ``dynamo_bootstrap`` - the same tables/GSIs the Lambda deploys), so a
GSI mistake (wrong index, missing key, a scan that ignores tenant) can't silently leak across
tenants.

It asserts two things per resource:
  * ``list_by_tenant`` (and ``list_by_user`` for tokens) returns ONLY the caller tenant's rows.
  * ``get(id)`` is intentionally tenant-agnostic - it returns the row by primary key with its own
    ``tenant_id`` intact. That's *why* the handler layer must compare tenant_id (covered E2E in the
    SQL leg); this documents the boundary rather than duplicating it.
"""
import secrets

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from shared.response import _iso  # noqa: E402

A = "tenant_" + secrets.token_hex(6)
B = "tenant_" + secrets.token_hex(6)


@pytest.fixture(scope="module")
def repos():
    """Stand up the real DynamoDB schema in moto, then (re)bind the dynamo repos to it.

    ``shared.repos.dynamo`` binds ``boto3.resource(...)`` and its Table handles at import time, so
    it must be (re)loaded *inside* the moto context to point at the mocked backend rather than a
    real account. conftest runs the suite on STORAGE_BACKEND=postgres, so importing dynamo here is
    additive and doesn't disturb the SQL-backed tests.
    """
    import importlib
    with mock_aws():
        from shared.dynamo_bootstrap import bootstrap
        bootstrap()  # create every table + GSI from the canonical schema
        import shared.repos.dynamo as dynamo
        importlib.reload(dynamo)  # rebind _ddb / _TABLE_* to the moto-backed resource
        yield dynamo


def _seed(repos):
    now = _iso()
    ids = {}
    # agents (two under A to prove the list returns the full set, not just one)
    for t, key in ((A, "a_agent"), (A, "a_agent2"), (B, "b_agent")):
        aid = "agent_" + secrets.token_hex(6)
        repos.AgentRepo().create({"agent_id": aid, "tenant_id": t, "status": "ACTIVE",
                                  "mode": "readonly", "created_at": now})
        ids[key] = aid
    # fleets (names must be unique within a tenant)
    for t, key in ((A, "a_fleet"), (B, "b_fleet")):
        fid = "fleet_" + secrets.token_hex(6)
        repos.FleetRepo().create({"fleet_id": fid, "tenant_id": t,
                                  "name": "fleet-" + secrets.token_hex(3), "status": "ACTIVE"})
        ids[key] = fid
    # users
    for t, key in ((A, "a_user"), (B, "b_user")):
        uid = "user_" + secrets.token_hex(6)
        repos.UserRepo().create({"user_id": uid, "tenant_id": t, "username": "u" + secrets.token_hex(3),
                                 "role": "developer", "status": "ACTIVE", "created_at": now})
        ids[key] = uid
    # api tokens (list_by_user needs user_id; list_by_tenant needs tenant_id)
    for t, u_key, key in ((A, "a_user", "a_token"), (B, "b_user", "b_token")):
        tid = "tkid_" + secrets.token_hex(6)
        repos.ApiTokenRepo().create({"token_id": tid, "user_id": ids[u_key], "tenant_id": t,
                                     "token_hash": secrets.token_hex(8), "name": "tok",
                                     "status": "ACTIVE", "created_at": now})
        ids[key] = tid
    # approvals
    for t, key in ((A, "a_appr"), (B, "b_appr")):
        pid = "appr_" + secrets.token_hex(6)
        repos.ApprovalRepo().create({"approval_id": pid, "tenant_id": t, "agent_id": ids["a_agent"],
                                     "command": "systemctl restart nginx", "status": "approved",
                                     "created_at": now})
        ids[key] = pid
    # jobs
    for t, key in ((A, "a_job"), (B, "b_job")):
        jid = "job_" + secrets.token_hex(6)
        repos.JobRepo().create({"job_id": jid, "tenant_id": t, "agent_id": ids["a_agent"],
                                "status": "PENDING", "created_at": now})
        ids[key] = jid
    return ids


@pytest.fixture(scope="module")
def seeded(repos):
    return _seed(repos)


def _ids(rows, key):
    return {r[key] for r in rows if key in r}


def test_list_by_tenant_is_scoped_across_repos(repos, seeded):
    """Every tenant-scoped listing returns ONLY the caller tenant's rows - never the other's."""
    cases = [
        ("agents", lambda t: repos.AgentRepo().list_by_tenant(t), "agent_id",
         {seeded["a_agent"], seeded["a_agent2"]}, {seeded["b_agent"]}),
        ("fleets", lambda t: repos.FleetRepo().list_by_tenant(t), "fleet_id",
         {seeded["a_fleet"]}, {seeded["b_fleet"]}),
        ("users", lambda t: repos.UserRepo().list_by_tenant(t), "user_id",
         {seeded["a_user"]}, {seeded["b_user"]}),
        ("api-tokens", lambda t: repos.ApiTokenRepo().list_by_tenant(t), "token_id",
         {seeded["a_token"]}, {seeded["b_token"]}),
        ("approvals", lambda t: repos.ApprovalRepo().list_by_tenant(t), "approval_id",
         {seeded["a_appr"]}, {seeded["b_appr"]}),
        ("jobs", lambda t: repos.JobRepo().list_by_tenant(t, None, 50), "job_id",
         {seeded["a_job"]}, {seeded["b_job"]}),
    ]
    for name, list_a, key, a_expected, b_only in cases:
        a_rows = _ids(list_a(A), key)
        b_rows = _ids(list_a(B), key)
        assert a_expected <= a_rows, f"{name}: A missing its own rows ({a_expected - a_rows})"
        assert not (a_rows & b_only), f"{name}: A's list leaked B's rows"
        assert not (b_rows & a_expected), f"{name}: B's list leaked A's rows ({b_rows & a_expected})"
        assert b_only <= b_rows, f"{name}: B missing its own rows"


def test_api_tokens_list_by_user_is_scoped(repos, seeded):
    """The token list endpoint keys off list_by_user - B's user never surfaces A's tokens."""
    a_tokens = _ids(repos.ApiTokenRepo().list_by_user(seeded["a_user"]), "token_id")
    b_tokens = _ids(repos.ApiTokenRepo().list_by_user(seeded["b_user"]), "token_id")
    assert seeded["a_token"] in a_tokens
    assert seeded["b_token"] not in a_tokens
    assert seeded["a_token"] not in b_tokens


def test_get_by_id_is_tenant_agnostic_so_handler_must_check(repos, seeded):
    """A primary-key get returns the row with its own tenant_id - isolation is the handler's job.

    This is the invariant that makes the handler ownership check load-bearing: the repo will happily
    return tenant B's agent by id, so the handler's ``item.tenant_id != caller.tenant_id -> 404``
    (exercised E2E in the SQL leg) is what actually denies the cross-tenant read.
    """
    b_agent = repos.AgentRepo().get(seeded["b_agent"])
    assert b_agent is not None and b_agent["tenant_id"] == B
    a_agent = repos.AgentRepo().get(seeded["a_agent"])
    assert a_agent is not None and a_agent["tenant_id"] == A
