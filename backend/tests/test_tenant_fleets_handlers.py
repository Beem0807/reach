"""Tests for handlers/tenant_fleets.py (fleet CRUD + join token)."""
import json
from unittest.mock import patch

from shared.exceptions import NameTakenError
from handlers.tenant_fleets import (
    handle_create_fleet,
    handle_list_fleets,
    handle_update_fleet,
    handle_rotate_fleet_token,
    handle_revoke_fleet,
    handle_delete_fleet,
    handle_remove_fleet_member,
)

TENANT_ID = "tenant_1"
FLEET_ID = "fleet_abc"
TOKEN = "tok_test"
API_URL = "https://api.example.com"

_ADMIN = {"user_id": "u_admin", "tenant_id": TENANT_ID, "role": "admin", "username": "alice"}
_DEV = {"user_id": "u_dev", "tenant_id": TENANT_ID, "role": "developer", "username": "dev"}
_FLEET = {
    "fleet_id": FLEET_ID, "tenant_id": TENANT_ID, "name": "web-asg", "mode": "approved",
    "grant_service_mgmt": True, "grant_docker": False, "join_token_hash": "h", "status": "ACTIVE",
    "reap_after_seconds": 1800, "created_at": "2026-01-01T00:00:00Z",
}


def _auth(user=_ADMIN):
    return patch("handlers.tenant_fleets._verify_tenant_token", return_value=user)


class TestCreateFleet:
    def _call(self, body=None, user=_ADMIN):
        with _auth(user), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.create.return_value = None
            fr.get.return_value = {**_FLEET, **(body or {})}
            r = handle_create_fleet(body or {"name": "web-asg"}, TOKEN, API_URL)
        return r, fr

    def test_unauthorized(self):
        with patch("handlers.tenant_fleets._verify_tenant_token", return_value=None):
            assert handle_create_fleet({"name": "x"}, TOKEN, API_URL)["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_sandbox_ack_stored_when_set(self):
        r, fr = self._call(body={"name": "mac-fleet", "sandbox_ack": True})
        assert r["statusCode"] == 201
        assert fr.create.call_args[0][0]["sandbox_ack"] is True

    def test_sandbox_ack_defaults_false(self):
        r, fr = self._call(body={"name": "web-asg"})
        assert fr.create.call_args[0][0]["sandbox_ack"] is False

    def test_max_fanout_over_tenant_cap_rejected(self):
        # Tenant fanout_cap defaults to 25 here; a fleet can only lower it, never raise it.
        r, fr = self._call(body={"name": "web-asg", "max_fanout": 50})
        assert r["statusCode"] == 400
        assert "cannot exceed the tenant's fan-out cap" in json.loads(r["body"])["error"]
        fr.create.assert_not_called()

    def test_max_fanout_within_tenant_cap_ok(self):
        r, fr = self._call(body={"name": "web-asg", "max_fanout": 10})
        assert r["statusCode"] == 201
        fr.create.assert_called_once()

    def test_name_required(self):
        r, _ = self._call(body={"name": "  "})
        assert r["statusCode"] == 400

    def test_invalid_mode(self):
        r, _ = self._call(body={"name": "x", "mode": "bogus"})
        assert r["statusCode"] == 400

    def test_bad_reap(self):
        r, _ = self._call(body={"name": "x", "reap_after_seconds": "soon"})
        assert r["statusCode"] == 400

    def test_success_returns_token_and_install(self):
        r, fr = self._call(body={"name": "web-asg", "mode": "approved"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["join_token"].startswith("fleet_")           # reusable join token
        assert "--install-token" in body["install"]              # launch-template line
        assert body["type"] == "host" and body["member_count"] == 0
        # The stored hash is not the raw token.
        stored = fr.create.call_args[0][0]
        assert stored["join_token_hash"] != body["join_token"]
        assert stored["status"] == "ACTIVE"

    def test_default_mode_is_readonly(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = {**_FLEET, "mode": "readonly"}
            handle_create_fleet({"name": "x"}, TOKEN, API_URL)
        assert fr.create.call_args[0][0]["mode"] == "readonly"

    def test_duplicate_name_409(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.create.side_effect = NameTakenError("web-asg")
            r = handle_create_fleet({"name": "web-asg"}, TOKEN, API_URL)
        assert r["statusCode"] == 409


class TestListFleets:
    def test_lists_with_member_counts(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.list_by_tenant.return_value = [_FLEET]
            fr.member_counts.return_value = {FLEET_ID: 3}
            r = handle_list_fleets(TOKEN)
        body = json.loads(r["body"])
        assert body["fleets"][0]["member_count"] == 3
        assert body["fleets"][0]["writable"] is True   # admin is tenant-wide
        # The join token must never appear in a list response.
        assert "join_token" not in body["fleets"][0]

    def test_scoped_user_sees_only_accessible_fleets(self):
        other = {**_FLEET, "fleet_id": "fleet_other", "name": "other"}
        # A developer granted read-only on FLEET_ID only.
        scoped = {"user_id": "u_dev", "tenant_id": TENANT_ID, "role": "developer",
                  "readwrite_agent_ids": [], "readonly_agent_ids": [],
                  "readwrite_fleet_ids": [], "readonly_fleet_ids": [FLEET_ID]}
        with _auth(scoped), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.list_by_tenant.return_value = [_FLEET, other]
            fr.member_counts.return_value = {}
            r = handle_list_fleets(TOKEN)
        body = json.loads(r["body"])
        assert [f["fleet_id"] for f in body["fleets"]] == [FLEET_ID]   # 'other' hidden
        assert body["fleets"][0]["writable"] is False                 # read-only grant

    def test_stat_counts_from_grouped_members(self):
        # _FLEET wants grant_service_mgmt=True, grant_docker=False.
        groups = [
            {"fleet_id": FLEET_ID, "status": "ACTIVE",   "grant_service_mgmt": True,  "grant_docker": False, "grants_exception": None, "count": 3},  # matches -> ok
            {"fleet_id": FLEET_ID, "status": "ACTIVE",   "grant_service_mgmt": True,  "grant_docker": True,  "grants_exception": None, "count": 2},  # docker on -> mismatch
            {"fleet_id": FLEET_ID, "status": "INACTIVE", "grant_service_mgmt": False, "grant_docker": False, "grants_exception": None, "count": 1},  # sm off -> mismatch
            {"fleet_id": FLEET_ID, "status": "REVOKED",  "grant_service_mgmt": True,  "grant_docker": True,  "grants_exception": None, "count": 5},  # revoked -> ignored
        ]
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.list_by_tenant.return_value = [_FLEET]
            fr.member_counts.return_value = {FLEET_ID: 11}
            ar.fleet_member_groups.return_value = groups
            r = handle_list_fleets(TOKEN)
        f = json.loads(r["body"])["fleets"][0]
        assert f["member_count"] == 11
        assert f["active_count"] == 5 and f["inactive_count"] == 1
        assert f["mismatch_count"] == 3   # 2 docker + 1 svc; revoked ignored

    def test_accepted_exception_excluded_from_mismatch_count(self):
        # sm=T,dk=T vs fleet (sm=T,dk=F) mismatches, but the exception "11-10" matches
        # the (member,fleet) signature -> accepted -> not counted.
        groups = [{"fleet_id": FLEET_ID, "status": "ACTIVE", "grant_service_mgmt": True,
                   "grant_docker": True, "grants_exception": "11-10", "count": 4}]
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.list_by_tenant.return_value = [_FLEET]
            fr.member_counts.return_value = {FLEET_ID: 4}
            ar.fleet_member_groups.return_value = groups
            r = handle_list_fleets(TOKEN)
        f = json.loads(r["body"])["fleets"][0]
        assert f["mismatch_count"] == 0 and f["active_count"] == 4

    def test_scoped_user_with_no_fleet_grants_sees_none(self):
        scoped = {"user_id": "u_ops", "tenant_id": TENANT_ID, "role": "operator",
                  "readwrite_agent_ids": ["agent_x"], "readonly_agent_ids": [],
                  "readwrite_fleet_ids": [], "readonly_fleet_ids": []}
        with _auth(scoped), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.list_by_tenant.return_value = [_FLEET]
            fr.member_counts.return_value = {FLEET_ID: 3}
            r = handle_list_fleets(TOKEN)
        assert json.loads(r["body"])["fleets"] == []

    def _roster(self, n):
        return [{**_FLEET, "fleet_id": f"fleet_{i:02d}", "name": f"asg-{i:02d}"} for i in range(n)]

    def test_no_limit_returns_all_without_page_meta(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.list_by_tenant.return_value = self._roster(30)
            fr.member_counts.return_value = {}
            ar.fleet_member_groups.return_value = []
            r = handle_list_fleets(TOKEN)
        body = json.loads(r["body"])
        assert len(body["fleets"]) == 30 and "total" not in body

    def test_pagination_returns_page_and_total(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.list_by_tenant.return_value = self._roster(30)
            fr.member_counts.return_value = {}
            ar.fleet_member_groups.return_value = []
            r = handle_list_fleets(TOKEN, limit=20, offset=20)
        body = json.loads(r["body"])
        assert body["total"] == 30 and body["limit"] == 20 and body["offset"] == 20
        assert len(body["fleets"]) == 10
        # Deterministic order (by name): the second page starts at asg-20.
        assert body["fleets"][0]["name"] == "asg-20"

    def test_q_filters_by_name(self):
        fleets = [{**_FLEET, "fleet_id": "f1", "name": "web-asg"},
                  {**_FLEET, "fleet_id": "f2", "name": "db-asg"}]
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.list_by_tenant.return_value = fleets
            fr.member_counts.return_value = {}
            ar.fleet_member_groups.return_value = []
            r = handle_list_fleets(TOKEN, q="web", limit=20)
        body = json.loads(r["body"])
        assert body["total"] == 1 and [f["fleet_id"] for f in body["fleets"]] == ["f1"]


class TestUpdateFleet:
    def _call(self, body, user=_ADMIN, fleet=_FLEET):
        with _auth(user), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.side_effect = [fleet, {**fleet, **body}]
            fr.member_counts.return_value = {FLEET_ID: 0}
            r = handle_update_fleet(FLEET_ID, body, TOKEN)
        return r, fr

    def test_edit_reap_after(self):
        r, fr = self._call({"reap_after_seconds": 600})
        assert r["statusCode"] == 200
        assert fr.update_settings.call_args[0][1] == {"reap_after_seconds": 600}

    def test_edit_mode_and_name(self):
        r, fr = self._call({"mode": "wild", "name": "renamed"})
        assert fr.update_settings.call_args[0][1] == {"name": "renamed", "mode": "wild"}

    def test_edit_grants_persisted(self):
        r, fr = self._call({"grant_docker": True, "grant_service_mgmt": False})
        assert r["statusCode"] == 200
        assert fr.update_settings.call_args[0][1] == {"grant_docker": True, "grant_service_mgmt": False}

    def test_edit_sandbox_ack(self):
        r, fr = self._call({"sandbox_ack": True})
        assert r["statusCode"] == 200
        assert fr.update_settings.call_args[0][1] == {"sandbox_ack": True}

    def test_grant_edit_not_pushed_to_members(self):
        # Unlike mode/tags, grants are baked into the host install and must NOT be
        # auto-propagated - members drift until acknowledged.
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.get.side_effect = [_FLEET, {**_FLEET, "grant_docker": True}]
            fr.member_counts.return_value = {FLEET_ID: 0}
            handle_update_fleet(FLEET_ID, {"grant_docker": True}, TOKEN)
        ar.set_mode_by_fleet.assert_not_called()
        ar.update_grants.assert_not_called()

    def test_invalid_mode_rejected(self):
        r, _ = self._call({"mode": "nope"})
        assert r["statusCode"] == 400

    def test_no_fields_rejected(self):
        r, _ = self._call({})
        assert r["statusCode"] == 400

    def test_developer_forbidden(self):
        r, _ = self._call({"reap_after_seconds": 600}, user=_DEV)
        assert r["statusCode"] == 403

    def test_not_found_cross_tenant(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = {**_FLEET, "tenant_id": "other"}
            r = handle_update_fleet(FLEET_ID, {"reap_after_seconds": 600}, TOKEN)
        assert r["statusCode"] == 404


class TestReconcileFleetGrants:
    from handlers.tenant_fleets import handle_acknowledge_fleet_grants as _ack

    # sm_det/dk_det = whether the host *reports* the capability. Reconcile is verified
    # against detection, so a host must report a capability the fleet grants ON.
    def _member(self, aid, sm, dk, sm_det=True, dk_det=True, exc=None):
        return {"agent_id": aid, "tenant_id": TENANT_ID, "fleet_id": FLEET_ID,
                "status": "ACTIVE", "grant_service_mgmt": sm, "grant_docker": dk,
                "service_mgmt_detected": sm_det, "docker_detected": dk_det,
                "grants_exception": exc}

    def _call(self, members, fleet=_FLEET, user=_ADMIN, agent_id=None):
        from handlers.tenant_fleets import handle_acknowledge_fleet_grants
        with _auth(user), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar, \
             patch("handlers.tenant_fleets.audit"):
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = members
            r = handle_acknowledge_fleet_grants(FLEET_ID, TOKEN, agent_id=agent_id)
        return r, ar

    def test_reconciles_only_drifted_members(self):
        # _FLEET wants grant_service_mgmt=True, grant_docker=False.
        members = [
            self._member("a1", True, False),   # in sync
            self._member("a2", True, True),    # docker drift
            self._member("a3", False, False),  # service-mgmt drift
        ]
        r, ar = self._call(members)
        body = json.loads(r["body"])
        assert r["statusCode"] == 200 and body["reconciled"] == 2
        acked = {c.args[0] for c in ar.update_grants.call_args_list}
        assert acked == {"a2", "a3"}
        for c in ar.update_grants.call_args_list:
            assert c.kwargs == {"grant_service_mgmt": True, "grant_docker": False}

    def test_ignores_other_fleets(self):
        members = [self._member("a1", False, True),
                   {**self._member("x", False, True), "fleet_id": "other"}]
        r, ar = self._call(members)
        assert json.loads(r["body"])["reconciled"] == 1
        assert ar.update_grants.call_count == 1

    def test_no_drift_is_zero(self):
        r, ar = self._call([self._member("a1", True, False)])
        assert json.loads(r["body"])["reconciled"] == 0
        ar.update_grants.assert_not_called()

    def test_single_agent_only_reconciles_that_member(self):
        members = [self._member("a1", True, True),    # docker drift
                   self._member("a2", False, False)]  # svc drift
        r, ar = self._call(members, agent_id="a1")
        body = json.loads(r["body"])
        assert body["reconciled"] == 1 and body["agent_id"] == "a1"
        assert [c.args[0] for c in ar.update_grants.call_args_list] == ["a1"]

    def test_single_agent_not_in_fleet_404(self):
        r, _ = self._call([self._member("a1", True, True)], agent_id="ghost")
        assert r["statusCode"] == 404

    def test_single_agent_not_drifted_is_zero(self):
        r, ar = self._call([self._member("a1", True, False)], agent_id="a1")
        assert json.loads(r["body"])["reconciled"] == 0
        ar.update_grants.assert_not_called()

    # --- verified against detection -----------------------------------------
    _WANTS_DOCKER = {**_FLEET, "grant_service_mgmt": False, "grant_docker": True}

    def test_blocks_member_whose_host_lacks_the_granted_capability(self):
        # Fleet wants docker; member mismatched (docker off) and the host does NOT
        # report docker -> must be blocked, not silently reconciled.
        member = self._member("a1", False, False, dk_det=False)
        r, ar = self._call([member], fleet=self._WANTS_DOCKER)
        body = json.loads(r["body"])
        assert body["reconciled"] == 0
        assert [b["agent_id"] for b in body["blocked"]] == ["a1"]
        assert "docker" in body["blocked"][0]["reason"]
        ar.update_grants.assert_not_called()

    def test_reconciles_when_host_reports_the_capability(self):
        # Same mismatch, but the host now reports docker (re-provisioned) -> reconciled.
        member = self._member("a1", False, False, dk_det=True)
        r, ar = self._call([member], fleet=self._WANTS_DOCKER)
        body = json.loads(r["body"])
        assert body["reconciled"] == 1 and body["blocked"] == []
        ar.update_grants.assert_called_once_with("a1", grant_service_mgmt=False, grant_docker=True)

    def test_removing_a_grant_is_never_blocked(self):
        # Fleet wants docker OFF; member has it on (mismatch). Removing a grant needs no
        # detection, so it reconciles even though the host still reports docker.
        fleet_off = {**_FLEET, "grant_service_mgmt": False, "grant_docker": False}
        member = self._member("a1", False, True, dk_det=True)
        r, ar = self._call([member], fleet=fleet_off)
        body = json.loads(r["body"])
        assert body["reconciled"] == 1 and body["blocked"] == []

    def test_mixed_reconcile_some_blocked(self):
        members = [
            self._member("ok", False, False, dk_det=True),    # host reports docker -> reconciled
            self._member("no", False, False, dk_det=False),   # host lacks docker -> blocked
        ]
        r, ar = self._call(members, fleet=self._WANTS_DOCKER)
        body = json.loads(r["body"])
        assert body["reconciled"] == 1 and [b["agent_id"] for b in body["blocked"]] == ["no"]
        assert [c.args[0] for c in ar.update_grants.call_args_list] == ["ok"]

    def test_reconcile_all_leaves_accepted_members_alone(self):
        # An accepted-as-is member (exception matches the (member,fleet) signature
        # "00-01") is an intentional exception - a bulk reconcile skips it.
        members = [self._member("acc", False, False, dk_det=True, exc="00-01")]
        r, ar = self._call(members, fleet=self._WANTS_DOCKER)
        assert json.loads(r["body"])["reconciled"] == 0
        ar.update_grants.assert_not_called()


class TestAcceptFleetGrantMismatch:
    def _member(self, aid, sm, dk, exc=None):
        return {"agent_id": aid, "tenant_id": TENANT_ID, "fleet_id": FLEET_ID,
                "status": "ACTIVE", "grant_service_mgmt": sm, "grant_docker": dk,
                "grants_exception": exc}

    # Fleet wants docker only. Signature is "{member_sm}{member_dk}-{fleet_sm}{fleet_dk}".
    _WANTS_DOCKER = {**_FLEET, "grant_service_mgmt": False, "grant_docker": True}  # fleet part "01"

    def _call(self, members, fleet=_WANTS_DOCKER, user=_ADMIN, agent_id=None):
        from handlers.tenant_fleets import handle_accept_fleet_grant_mismatch
        with _auth(user), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar, \
             patch("handlers.tenant_fleets.audit"):
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = members
            r = handle_accept_fleet_grant_mismatch(FLEET_ID, TOKEN, agent_id=agent_id)
        return r, ar

    def test_accepts_flagged_members_with_current_signature(self):
        # member 'm' mismatches (docker off, fleet wants on) and isn't accepted -> flagged.
        r, ar = self._call([self._member("m", False, False)])
        body = json.loads(r["body"])
        assert body["accepted"] == 1
        ar.set_grants_exception.assert_called_once_with("m", "00-01")   # (member, fleet) signature
        # It does NOT touch the member's real grants.
        ar.update_grants.assert_not_called()

    def test_ignores_matching_and_already_accepted(self):
        members = [
            self._member("match", False, True),               # already matches fleet -> not flagged
            self._member("acc", False, False, exc="00-01"),   # accepted for current signature
        ]
        r, ar = self._call(members)
        assert json.loads(r["body"])["accepted"] == 0
        ar.set_grants_exception.assert_not_called()

    def test_stale_acceptance_reflags_after_fleet_change(self):
        # Accepted against old signature "00-01"; fleet now wants service-mgmt too, so the
        # fleet part becomes "11" and the member is flagged again -> re-accepted at "00-11".
        fleet_11 = {**_FLEET, "grant_service_mgmt": True, "grant_docker": True}
        r, ar = self._call([self._member("m", False, False, exc="00-01")], fleet=fleet_11)
        assert json.loads(r["body"])["accepted"] == 1
        ar.set_grants_exception.assert_called_once_with("m", "00-11")

    def test_member_grant_change_reflags_stale_acceptance(self):
        # Q1: accepted at "00-01"; the member's OWN grant later changed (service-mgmt on),
        # so its part is now "10" and it still differs from the fleet. The old exception no
        # longer matches -> flagged again -> re-accepted at the new signature "10-01".
        r, ar = self._call([self._member("m", True, False, exc="00-01")], fleet=self._WANTS_DOCKER)
        assert json.loads(r["body"])["accepted"] == 1
        ar.set_grants_exception.assert_called_once_with("m", "10-01")

    def test_member_matching_fleet_is_never_flagged_even_with_stale_exception(self):
        # The member's grants now EQUAL the fleet's (fleet wants docker; member has it).
        # There is no mismatch, so it's not flagged - a leftover exception ("00-00" from
        # an earlier divergence) is simply dormant and never consulted.
        r, ar = self._call([self._member("m", False, True, exc="00-00")])
        assert json.loads(r["body"])["accepted"] == 0
        ar.set_grants_exception.assert_not_called()

    def test_service_then_docker_scenario_reflags(self):
        # Fleet grants nothing. Host gained service-mgmt, accepted at "10-00". Host then
        # also gained docker (member now "11") -> current signature "11-00" != "10-00", so
        # it comes back out of the exception and is flagged/re-acceptable at "11-00".
        bare = {**_FLEET, "grant_service_mgmt": False, "grant_docker": False}
        r, ar = self._call([self._member("m", True, True, exc="10-00")], fleet=bare)
        assert json.loads(r["body"])["accepted"] == 1
        ar.set_grants_exception.assert_called_once_with("m", "11-00")

    def test_single_agent(self):
        r, ar = self._call([self._member("m", False, False)], agent_id="m")
        assert json.loads(r["body"])["accepted"] == 1 and json.loads(r["body"])["agent_id"] == "m"

    def test_single_agent_not_in_fleet_404(self):
        r, _ = self._call([self._member("m", False, False)], agent_id="ghost")
        assert r["statusCode"] == 404

    def test_developer_forbidden(self):
        r, _ = self._call([self._member("m", False, False)], user=_DEV)
        assert r["statusCode"] == 403

    def test_developer_forbidden(self):
        r, _ = self._call([], user=_DEV)
        assert r["statusCode"] == 403

    def test_not_found(self):
        from handlers.tenant_fleets import handle_acknowledge_fleet_grants
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = None
            r = handle_acknowledge_fleet_grants(FLEET_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_unauthorized(self):
        from handlers.tenant_fleets import handle_acknowledge_fleet_grants
        with patch("handlers.tenant_fleets._verify_tenant_token", return_value=None):
            assert handle_acknowledge_fleet_grants(FLEET_ID, TOKEN)["statusCode"] == 401


class TestResolveFleetGrants:
    """The unified surface dispatches to reconcile/accept by `resolution`."""
    _WANTS_DOCKER = {**_FLEET, "grant_service_mgmt": False, "grant_docker": True}

    def _member(self, aid, sm, dk, exc=None):
        return {"agent_id": aid, "tenant_id": TENANT_ID, "fleet_id": FLEET_ID,
                "status": "ACTIVE", "grant_service_mgmt": sm, "grant_docker": dk,
                "grants_exception": exc, "docker_detected": dk, "service_mgmt_detected": sm}

    def _call(self, resolution, members, fleet=_WANTS_DOCKER, agent_id=None):
        from handlers.tenant_fleets import handle_resolve_fleet_grants
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar, \
             patch("handlers.tenant_fleets.audit"):
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = members
            r = handle_resolve_fleet_grants(FLEET_ID, TOKEN, resolution, agent_id=agent_id)
        return r, ar

    def test_reconcile_routes_to_reconcile(self):
        # Mismatch (docker grant off, fleet wants on) but the host reports docker, so
        # reconcile isn't blocked -> grants get pushed to match the fleet.
        member = {"agent_id": "m", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID, "status": "ACTIVE",
                  "grant_service_mgmt": False, "grant_docker": False,
                  "docker_detected": True, "service_mgmt_detected": False, "grants_exception": None}
        r, ar = self._call("reconcile", [member])
        body = json.loads(r["body"])
        assert body["reconciled"] == 1
        ar.update_grants.assert_called_once()

    def test_accept_routes_to_accept(self):
        r, ar = self._call("accept", [self._member("m", False, False)])
        body = json.loads(r["body"])
        assert body["accepted"] == 1
        ar.set_grants_exception.assert_called_once_with("m", "00-01")
        ar.update_grants.assert_not_called()

    def test_invalid_resolution_400(self):
        r, _ = self._call("bogus", [self._member("m", False, False)])
        assert r["statusCode"] == 400
        assert "resolution" in json.loads(r["body"])["error"]

    def test_missing_resolution_400(self):
        r, _ = self._call(None, [self._member("m", False, False)])
        assert r["statusCode"] == 400

    def test_lambda_entrypoint_delegates(self):
        from handlers.tenant_fleets import resolve_fleet_grants_handler
        evt = {"headers": {"authorization": f"Bearer {TOKEN}"},
               "pathParameters": {"fleet_id": FLEET_ID},
               "body": json.dumps({"resolution": "accept", "agent_id": "m"})}
        with patch("handlers.tenant_fleets.handle_resolve_fleet_grants",
                   return_value={"statusCode": 200, "body": "{}"}) as h:
            resolve_fleet_grants_handler(evt, None)
        h.assert_called_once_with(FLEET_ID, TOKEN, "accept", agent_id="m")

    def test_lambda_entrypoint_missing_auth_401(self):
        from handlers.tenant_fleets import resolve_fleet_grants_handler
        r = resolve_fleet_grants_handler({"headers": {}, "pathParameters": {"fleet_id": FLEET_ID}}, None)
        assert r["statusCode"] == 401


class TestRotateToken:
    def test_rotate_sets_prev_and_returns_new(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = _FLEET
            r = handle_rotate_fleet_token(FLEET_ID, {}, TOKEN, API_URL)
        body = json.loads(r["body"])
        assert body["join_token"].startswith("fleet_")
        assert body["previous_token_valid_until"] is not None
        # rotate_token(fleet_id, new_hash, prev_hash, prev_expires)
        args = fr.rotate_token.call_args[0]
        assert args[2] == "h" and args[3] is not None   # previous hash carried + expiry set

    def test_zero_grace_invalidates_old_token(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = _FLEET
            r = handle_rotate_fleet_token(FLEET_ID, {"grace_seconds": 0}, TOKEN, API_URL)
        assert json.loads(r["body"])["previous_token_valid_until"] is None
        args = fr.rotate_token.call_args[0]
        assert args[2] is None and args[3] is None       # no previous token kept

    def test_custom_grace_seconds(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = _FLEET
            handle_rotate_fleet_token(FLEET_ID, {"grace_seconds": 3600}, TOKEN, API_URL)
        assert fr.rotate_token.call_args[0][2] == "h"     # previous hash kept

    def test_bad_grace_rejected(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = _FLEET
            r = handle_rotate_fleet_token(FLEET_ID, {"grace_seconds": -5}, TOKEN, API_URL)
        assert r["statusCode"] == 400


_REVOKED_FLEET = {**_FLEET, "status": "REVOKED"}


class TestRevokeAndDelete:
    def test_revoke_keep_detaches_members(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.approvals_repo") as apr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.get.return_value = _FLEET
            ar.detach_fleet.return_value = 3
            r = handle_revoke_fleet(FLEET_ID, {"members": "keep"}, TOKEN)
        assert json.loads(r["body"])["status"] == "REVOKED"
        fr.set_status.assert_called_once_with(FLEET_ID, "REVOKED")
        # Detach now also swaps the fleet's tags for a single provenance tag.
        assert ar.detach_fleet.call_args[0][0] == FLEET_ID
        prov = ar.detach_fleet.call_args[1]["tags"]
        assert len(prov) == 1 and prov[0].startswith("oldfleet:")
        ar.delete_by_fleet.assert_not_called()
        # Kept members stay standalone - their approvals are not purged here.
        apr.delete_by_fleet.assert_not_called()

    def test_revoke_remove_deletes_members_and_their_approvals(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.approvals_repo") as apr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.get.return_value = _FLEET
            ar.delete_by_fleet.return_value = 5
            r = handle_revoke_fleet(FLEET_ID, {"members": "remove"}, TOKEN)
        assert json.loads(r["body"])["affected"] == 5
        ar.delete_by_fleet.assert_called_once_with(FLEET_ID)
        ar.detach_fleet.assert_not_called()
        apr.delete_by_fleet.assert_called_once_with(FLEET_ID)

    def test_revoke_defaults_to_keep(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.get.return_value = _FLEET
            ar.detach_fleet.return_value = 0
            handle_revoke_fleet(FLEET_ID, {}, TOKEN)
        ar.detach_fleet.assert_called_once()

    def test_revoke_bad_disposition(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo"):
            fr.get.return_value = _FLEET
            r = handle_revoke_fleet(FLEET_ID, {"members": "nuke"}, TOKEN)
        assert r["statusCode"] == 400

    def test_delete_requires_revoked(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr:
            fr.get.return_value = _FLEET  # ACTIVE
            r = handle_delete_fleet(FLEET_ID, TOKEN)
        assert r["statusCode"] == 409
        fr.delete.assert_not_called()

    def test_delete_revoked_and_empty(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.approvals_repo") as apr:
            fr.get.return_value = _REVOKED_FLEET
            fr.member_counts.return_value = {}
            r = handle_delete_fleet(FLEET_ID, TOKEN)
        assert r["statusCode"] == 200
        fr.delete.assert_called_once_with(FLEET_ID)
        # Any approvals still scoped to the fleet are swept on delete.
        apr.delete_by_fleet.assert_called_once_with(FLEET_ID)


class TestRemoveFleetMember:
    _AGENT = {"agent_id": "agent_m", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID,
              "hostname": "ip-10-0-0-1", "status": "ACTIVE"}

    def test_detaches_member_and_records_history(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar, \
             patch("handlers.tenant_fleets.agent_history_repo") as hr:
            fr.get.return_value = _FLEET
            ar.get.return_value = self._AGENT
            r = handle_remove_fleet_member(FLEET_ID, "agent_m", TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["detached"] is True
        # Detach swaps the fleet's tags for a single provenance tag (fleet-id in history).
        assert ar.detach_from_fleet.call_args[0][0] == "agent_m"
        prov = ar.detach_from_fleet.call_args[1]["tags"]
        assert len(prov) == 1 and prov[0].startswith("oldfleet:")
        ar.delete.assert_not_called()             # detach keeps the agent
        hr.create.assert_called_once()            # history entry recorded

    def test_developer_forbidden(self):
        with _auth(_DEV), patch("handlers.tenant_fleets.fleets_repo"), \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            r = handle_remove_fleet_member(FLEET_ID, "agent_m", TOKEN)
        assert r["statusCode"] == 403
        ar.detach_from_fleet.assert_not_called()

    def test_agent_not_in_this_fleet(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.get.return_value = _FLEET
            ar.get.return_value = {**self._AGENT, "fleet_id": "fleet_other"}
            r = handle_remove_fleet_member(FLEET_ID, "agent_m", TOKEN)
        assert r["statusCode"] == 404
        ar.detach_from_fleet.assert_not_called()

    def test_cross_tenant_agent_rejected(self):
        with _auth(), patch("handlers.tenant_fleets.fleets_repo") as fr, \
             patch("handlers.tenant_fleets.agents_repo") as ar:
            fr.get.return_value = _FLEET
            ar.get.return_value = {**self._AGENT, "tenant_id": "other"}
            r = handle_remove_fleet_member(FLEET_ID, "agent_m", TOKEN)
        assert r["statusCode"] == 404
