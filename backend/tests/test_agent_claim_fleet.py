"""Tests for fleet enrollment in the claim path (agent_claim._claim_into_fleet)."""
import json
from unittest.mock import patch

from handlers.agent_claim import handle_agent_claim

_FLEET = {
    "fleet_id": "fleet_x", "tenant_id": "tenant_1", "mode": "approved",
    "grant_service_mgmt": True, "grant_docker": False, "status": "ACTIVE",
}


def _claim(token="fleet_JOINTOKEN", fp="i-aaa", type_="host"):
    return {"install_token": token, "machine_fingerprint": fp, "hostname": "ip-10-0-0-1", "type": type_}


class TestFleetEnrollment:
    def test_new_host_enrolls_and_gets_token(self):
        with patch("handlers.agent_claim.fleets_repo") as fr, \
             patch("handlers.agent_claim.agents_repo") as ar, \
             patch("handlers.agent_claim.agent_history_repo"):
            fr.get_by_join_token_hash.return_value = _FLEET
            ar.get_by_fleet_and_fingerprint.return_value = None
            r = handle_agent_claim(_claim())
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["agent_token"].startswith("agent_")
        assert body["mode"] == "approved"
        created = ar.create.call_args[0][0]
        assert created["fleet_id"] == "fleet_x"
        assert created["status"] == "ACTIVE" and created["type"] == "host"
        assert created["grant_service_mgmt"] is True
        ar.reenroll.assert_not_called()

    def test_existing_fingerprint_reenrolls_no_duplicate(self):
        with patch("handlers.agent_claim.fleets_repo") as fr, \
             patch("handlers.agent_claim.agents_repo") as ar:
            fr.get_by_join_token_hash.return_value = _FLEET
            ar.get_by_fleet_and_fingerprint.return_value = {"agent_id": "agent_existing"}
            r = handle_agent_claim(_claim(fp="i-aaa"))
        assert r["statusCode"] == 200
        ar.reenroll.assert_called_once()
        assert ar.reenroll.call_args[0][0] == "agent_existing"
        ar.create.assert_not_called()

    def test_invalid_join_token_rejected(self):
        with patch("handlers.agent_claim.fleets_repo") as fr:
            fr.get_by_join_token_hash.return_value = None
            r = handle_agent_claim(_claim())
        assert r["statusCode"] == 403

    def test_revoked_fleet_rejected(self):
        with patch("handlers.agent_claim.fleets_repo") as fr:
            fr.get_by_join_token_hash.return_value = {**_FLEET, "status": "REVOKED"}
            r = handle_agent_claim(_claim())
        assert r["statusCode"] == 403

    def test_k8s_agent_rejected_host_only(self):
        with patch("handlers.agent_claim.fleets_repo") as fr, \
             patch("handlers.agent_claim.agents_repo"):
            fr.get_by_join_token_hash.return_value = _FLEET
            r = handle_agent_claim(_claim(type_="k8s"))
        assert r["statusCode"] == 403

    def test_non_fleet_token_uses_per_agent_path(self):
        # A normal install token must NOT hit the fleet lookup.
        with patch("handlers.agent_claim.fleets_repo") as fr, \
             patch("handlers.agent_claim.agents_repo") as ar:
            ar.get_by_install_token_hash.return_value = None
            r = handle_agent_claim(_claim(token="install_ABC"))
        fr.get_by_join_token_hash.assert_not_called()
        ar.get_by_install_token_hash.assert_called_once()
        assert r["statusCode"] == 403  # unknown install token
