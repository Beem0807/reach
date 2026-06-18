import json
from unittest.mock import patch

from handlers.admin_tenants import handle_create_tenant, handle_list_tenants

ADMIN = "test-admin-token"


class TestCreateTenant:
    def _call(self, body=None):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            r = handle_create_tenant(body or {}, ADMIN)
        return r, tr

    def test_unauthorized(self):
        r = handle_create_tenant({}, "wrong")
        assert r["statusCode"] == 401

    def test_creates_with_name(self):
        r, tr = self._call({"name": "Acme Corp"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["name"] == "Acme Corp"
        assert body["tenant_id"].startswith("tenant_")
        tr.create.assert_called_once()

    def test_creates_without_name(self):
        r, tr = self._call({})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["name"] is None

    def test_tenant_id_is_unique(self):
        with patch("handlers.admin_tenants.tenants_repo"):
            r1 = handle_create_tenant({}, ADMIN)
            r2 = handle_create_tenant({}, ADMIN)
        id1 = json.loads(r1["body"])["tenant_id"]
        id2 = json.loads(r2["body"])["tenant_id"]
        assert id1 != id2


class TestListTenants:
    def test_unauthorized(self):
        r = handle_list_tenants("wrong")
        assert r["statusCode"] == 401

    def test_returns_tenants(self):
        tenants = [{"tenant_id": "t1", "name": "Acme"}, {"tenant_id": "t2", "name": None}]
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = tenants
            r = handle_list_tenants(ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["tenants"] == tenants

    def test_returns_empty_list(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = []
            r = handle_list_tenants(ADMIN)
        assert json.loads(r["body"])["tenants"] == []
