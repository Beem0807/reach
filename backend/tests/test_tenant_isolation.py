"""
Tenant-isolation harness.

Proves tenant A's credentials can never read or mutate tenant B's resources, across the
tenant-scoped API. It runs end-to-end through the FastAPI adapter - which calls the *same*
``handle_*`` functions the Lambda per-route functions call (kept in lock-step by
test_adapter_parity) - against the real in-memory SQLite repos. So it exercises the actual routing,
tenant-token auth, and handler ownership checks, not mocks.

Isolation is enforced in two places, both covered here:
  * Handler layer: fetch a resource by id, then reject if ``resource.tenant_id`` != the caller's
    tenant (-> 404). This is adapter-agnostic (identical code for SQLite and DynamoDB).
  * Repo layer: ``list_by_tenant`` only returns the caller tenant's rows (list-isolation test).

Adapter note: the ownership check is the same code on every adapter; the only adapter-specific
isolation is ``list_by_tenant`` (SQL ``WHERE tenant_id`` vs a DynamoDB tenant-index query). The
SQLite path is exercised end-to-end here; the DynamoDB ``list_by_tenant`` GSI scoping is pinned
against a moto-backed real schema in test_tenant_isolation_dynamo.py.
"""
import secrets

import pytest
from fastapi.testclient import TestClient

from adapters.fastapi.main import app
from shared.password import hash_password
from shared.response import _iso
from shared.store import tenants_repo, users_repo
from shared.tenant_auth import create_tenant_token


def _seed_tenant(name: str):
    """Create a tenant + an admin user directly in the repo and mint a tenant token for it."""
    tid = "tenant_" + secrets.token_hex(8)
    tenants_repo.create({"tenant_id": tid, "name": name, "status": "ACTIVE", "created_at": _iso()})
    uid = "user_" + secrets.token_hex(8)
    uname = "admin" + secrets.token_hex(3)
    users_repo.create({
        "user_id": uid, "tenant_id": tid, "name": uname, "username": uname,
        "password_hash": hash_password("irrelevant-passphrase"), "role": "admin",
        "must_reset_password": False, "status": "ACTIVE",
        "readwrite_agent_ids": None, "readwrite_fleet_ids": None,
        "readonly_agent_ids": None, "readonly_fleet_ids": None, "created_at": _iso(),
    })
    return tid, uid, create_tenant_token(uid, tid, "admin", uname)


def _first_id(d: dict, *keys):
    for k in keys:
        if d.get(k):
            return d[k]
    raise AssertionError(f"no id in {list(d)} (looked for {keys})")


@pytest.fixture(scope="module")
def env():
    # sqlite ":memory:" is per-connection, and TestClient runs sync handlers in a worker thread - so
    # it would otherwise get a fresh, table-less DB. Rebind the repos' session factory to a StaticPool
    # engine (one shared connection across threads) for this module, so seeding (this thread) and the
    # handlers (worker thread) share the same in-memory DB.
    import shared.repos.sql as sqlmod
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    shared_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sqlmod._Base.metadata.create_all(shared_engine)
    old_bind, sqlmod.engine = sqlmod.engine, shared_engine
    sqlmod.SessionLocal.configure(bind=shared_engine)

    client = TestClient(app, raise_server_exceptions=False)
    tid_a, _, tok_a = _seed_tenant("iso-tenant-a")
    tid_b, _, tok_b = _seed_tenant("iso-tenant-b")
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    def create(path, body):
        r = client.post(path, json=body, headers=hdr_a)
        assert r.status_code in (200, 201), f"seed {path} failed: {r.status_code} {r.text}"
        return r.json()

    # Resources owned by tenant A (created via the real API so they're valid).
    agent_id = _first_id(create("/tenant/agents", {"type": "host", "mode": "readonly"}), "agent_id", "id")
    fleet_id = _first_id(create("/tenant/fleets", {"name": "fleet-a", "mode": "readonly"}), "fleet_id", "id")
    victim_id = _first_id(create("/tenant/users", {"username": "victima", "role": "developer"}), "user_id", "id")
    token_id = _first_id(create("/tenant/api-tokens", {"name": "tok-a"}), "id", "token_id")

    yield {
        "client": client, "hdr_a": hdr_a, "hdr_b": hdr_b,
        "agent_id": agent_id, "fleet_id": fleet_id, "victim_id": victim_id, "token_id": token_id,
    }
    sqlmod.SessionLocal.configure(bind=old_bind)
    sqlmod.engine = old_bind


def _by_id_routes(e):
    a, f, u, t = e["agent_id"], e["fleet_id"], e["victim_id"], e["token_id"]
    grants = {"readwrite_agent_ids": [], "readonly_agent_ids": [],
              "readwrite_fleet_ids": [], "readonly_fleet_ids": []}
    return [
        # agents
        ("POST",   f"/tenant/agents/{a}/revoke", None),
        ("DELETE", f"/tenant/agents/{a}/remove", None),
        ("DELETE", f"/tenant/agents/{a}", None),
        ("POST",   f"/tenant/agents/{a}/reissue-install-token", {}),
        ("PUT",    f"/tenant/agents/{a}/tags", {"tags": ["x"]}),
        ("POST",   f"/tenant/agents/{a}/request-rotation", None),
        ("POST",   f"/tenant/agents/{a}/acknowledge-capability", {}),
        ("POST",   f"/tenant/agents/{a}/acknowledge-sandbox", {}),
        ("PUT",    f"/tenant/agents/{a}/policy/mode", {"mode": "readonly"}),
        ("GET",    f"/tenant/agents/{a}/history", None),
        # users
        ("POST",   f"/tenant/users/{u}/disable", None),
        ("POST",   f"/tenant/users/{u}/enable", None),
        ("DELETE", f"/tenant/users/{u}", None),
        ("POST",   f"/tenant/users/{u}/revoke-tokens", None),
        ("PUT",    f"/tenant/users/{u}/role", {"role": "developer"}),
        ("POST",   f"/tenant/users/{u}/reset-password", None),
        ("GET",    f"/tenant/users/{u}/agents", None),
        ("PUT",    f"/tenant/users/{u}/agents", grants),
        # fleets
        ("PUT",    f"/tenant/fleets/{f}", {"name": "x", "mode": "readonly"}),
        ("POST",   f"/tenant/fleets/{f}/rotate-token", None),
        ("POST",   f"/tenant/fleets/{f}/revoke", None),
        ("DELETE", f"/tenant/fleets/{f}", None),
        ("POST",   f"/tenant/fleets/{f}/resolve-grants", {"resolution": "reconcile"}),
        ("GET",    f"/tenant/fleets/{f}/history", None),
        # api tokens
        ("PATCH",  f"/tenant/api-tokens/{t}", {"name": "x"}),
        ("DELETE", f"/tenant/api-tokens/{t}", None),
    ]


def test_cross_tenant_by_id_routes_are_denied(env):
    """Tenant B, using A's resource ids, must be denied (403/404) on every by-id route."""
    c = env["client"]
    failures = []
    for method, path, body in _by_id_routes(env):
        r = c.request(method, path, json=body, headers=env["hdr_b"])
        if r.status_code not in (403, 404):
            failures.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert not failures, "cross-tenant access NOT denied:\n  " + "\n  ".join(failures)


def _ids(payload, key, id_key):
    items = payload.get(key, payload) if isinstance(payload, dict) else payload
    return {x[id_key] for x in items if isinstance(x, dict) and id_key in x}


def test_list_endpoints_are_tenant_scoped(env):
    """A sees its own resources; B's list never includes A's."""
    c = env["client"]
    for path, key, id_key, rid in [
        ("/agents", "agents", "agent_id", env["agent_id"]),
        ("/tenant/users", "users", "user_id", env["victim_id"]),
        ("/tenant/fleets", "fleets", "fleet_id", env["fleet_id"]),
        ("/tenant/api-tokens", "tokens", "token_id", env["token_id"]),
    ]:
        a = c.get(path, headers=env["hdr_a"])
        b = c.get(path, headers=env["hdr_b"])
        assert a.status_code == 200, f"{path} (A): {a.status_code} {a.text[:120]}"
        assert b.status_code == 200, f"{path} (B): {b.status_code} {b.text[:120]}"
        assert rid in _ids(a.json(), key, id_key), f"A should see its own {path} resource"
        assert rid not in _ids(b.json(), key, id_key), f"B must NOT see A's {path} resource"


def test_resources_survived_cross_tenant_attempts(env):
    """After all of B's (denied) attempts, A's resources are still intact - nothing was mutated."""
    c = env["client"]
    agents = c.get("/agents", headers=env["hdr_a"]).json()
    assert env["agent_id"] in _ids(agents, "agents", "agent_id"), "A's agent was mutated/removed by a cross-tenant call"
    fleets = c.get("/tenant/fleets", headers=env["hdr_a"]).json()
    assert env["fleet_id"] in _ids(fleets, "fleets", "fleet_id"), "A's fleet was mutated/removed by a cross-tenant call"
