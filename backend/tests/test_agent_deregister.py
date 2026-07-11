import json
from unittest.mock import patch

from handlers.agent_deregister import handle_agent_deregister

AGENT_ID = "agent_a"
FP = "fp_abc123"

_FLEET_MEMBER = {"agent_id": AGENT_ID, "status": "ACTIVE", "machine_fingerprint": FP,
                 "type": "host", "fleet_id": "fleet_1", "tenant_id": "t1", "hostname": "ip-10-0-0-5"}
_INACTIVE_MEMBER = {**_FLEET_MEMBER, "status": "INACTIVE"}
_CREATED = {**_FLEET_MEMBER, "status": "CREATED"}
_STANDALONE = {**_FLEET_MEMBER, "fleet_id": None}
_K8S = {**_FLEET_MEMBER, "type": "k8s"}

_VALID_BODY = {"machine_fingerprint": FP}


class TestAgentDeregister:
    def _call(self, body=None, agent=_FLEET_MEMBER):
        with patch("handlers.agent_deregister._verify_agent_token", return_value=agent), \
             patch("handlers.agent_deregister.agents_repo") as ar, \
             patch("handlers.agent_deregister.agent_history_repo") as ahr, \
             patch("handlers.agent_deregister.audit") as aud:
            r = handle_agent_deregister(body or _VALID_BODY, "tok")
            return r, ar, ahr, aud

    def test_missing_fingerprint(self):
        with patch("handlers.agent_deregister._verify_agent_token", return_value=_FLEET_MEMBER):
            r = handle_agent_deregister({}, "tok")
        assert r["statusCode"] == 400

    def test_unauthorized(self):
        with patch("handlers.agent_deregister._verify_agent_token", return_value=None):
            r = handle_agent_deregister(_VALID_BODY, "bad")
        assert r["statusCode"] == 401

    def test_created_agent_not_allowed(self):
        r, _, _, _ = self._call(agent=_CREATED)
        assert r["statusCode"] == 403

    def test_fingerprint_mismatch(self):
        r, _, _, _ = self._call({"machine_fingerprint": "wrong"})
        assert r["statusCode"] == 403

    def test_k8s_agent_rejected(self):
        r, ar, _, _ = self._call(agent=_K8S)
        assert r["statusCode"] == 409
        ar.delete.assert_not_called()

    def test_non_fleet_agent_rejected(self):
        r, ar, _, _ = self._call(agent=_STANDALONE)
        assert r["statusCode"] == 409
        ar.delete.assert_not_called()

    def test_fleet_member_deregisters(self):
        r, ar, ahr, aud = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["deregistered"] is True
        ar.delete.assert_called_once_with(AGENT_ID)
        hist = ahr.create.call_args[0][0]
        assert hist["to_status"] == "DELETED"
        assert hist["triggered_by"] == "agent-deregister"
        assert aud.write.call_args[0][0] == "agent.deregistered"

    def test_inactive_fleet_member_deregisters(self):
        r, ar, _, _ = self._call(agent=_INACTIVE_MEMBER)
        assert r["statusCode"] == 200
        ar.delete.assert_called_once_with(AGENT_ID)

    def test_history_written_before_delete(self):
        # The record must still exist when we log its history, so history precedes delete.
        order = []
        with patch("handlers.agent_deregister._verify_agent_token", return_value=_FLEET_MEMBER), \
             patch("handlers.agent_deregister.agents_repo") as ar, \
             patch("handlers.agent_deregister.agent_history_repo") as ahr, \
             patch("handlers.agent_deregister.audit"):
            ahr.create.side_effect = lambda *a, **k: order.append("history")
            ar.delete.side_effect = lambda *a, **k: order.append("delete")
            handle_agent_deregister(_VALID_BODY, "tok")
        assert order == ["history", "delete"]
