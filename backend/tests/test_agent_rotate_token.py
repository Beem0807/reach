import json
from unittest.mock import patch

from handlers.agent_rotate_token import handle_agent_rotate_token
from shared.auth import _hmac_token

AGENT_ID = "agent_a"
FP = "fp_abc123"

_AGENT_ACTIVE = {"agent_id": AGENT_ID, "status": "ACTIVE", "machine_fingerprint": FP}
_AGENT_INACTIVE = {"agent_id": AGENT_ID, "status": "INACTIVE", "machine_fingerprint": FP}
_AGENT_CREATED = {"agent_id": AGENT_ID, "status": "CREATED", "machine_fingerprint": FP}

_VALID_BODY = {"agent_id": AGENT_ID, "machine_fingerprint": FP}


class TestAgentRotateToken:
    def _call(self, body=None, agent=_AGENT_ACTIVE):
        with patch("handlers.agent_rotate_token._verify_agent_token", return_value=agent), \
             patch("handlers.agent_rotate_token.agents_repo") as ar:
            return handle_agent_rotate_token(body or _VALID_BODY, "tok"), ar

    def test_missing_fingerprint(self):
        with patch("handlers.agent_rotate_token._verify_agent_token", return_value=_AGENT_ACTIVE):
            r = handle_agent_rotate_token({}, "tok")
        assert r["statusCode"] == 400

    def test_unauthorized(self):
        with patch("handlers.agent_rotate_token._verify_agent_token", return_value=None):
            r = handle_agent_rotate_token(_VALID_BODY, "bad")
        assert r["statusCode"] == 401

    def test_created_agent_not_allowed(self):
        r, _ = self._call(agent=_AGENT_CREATED)
        assert r["statusCode"] == 403

    def test_fingerprint_mismatch(self):
        r, _ = self._call({**_VALID_BODY, "machine_fingerprint": "wrong"})
        assert r["statusCode"] == 403

    def test_active_agent_rotates(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["agent_token"].startswith("agent_")
        ar.update_agent_token_hash.assert_called_once()

    def test_inactive_agent_rotates(self):
        r, _ = self._call(agent=_AGENT_INACTIVE)
        assert r["statusCode"] == 200

    def test_returned_token_matches_stored_hash(self):
        with patch("handlers.agent_rotate_token._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_rotate_token.agents_repo") as ar:
            r = handle_agent_rotate_token(_VALID_BODY, "tok")
        raw_token = json.loads(r["body"])["agent_token"]
        stored_hash = ar.update_agent_token_hash.call_args[0][1]
        assert _hmac_token(raw_token) == stored_hash

    def test_new_token_differs_each_call(self):
        with patch("handlers.agent_rotate_token._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_rotate_token.agents_repo"):
            r1 = handle_agent_rotate_token(_VALID_BODY, "tok")
            r2 = handle_agent_rotate_token(_VALID_BODY, "tok")
        t1 = json.loads(r1["body"])["agent_token"]
        t2 = json.loads(r2["body"])["agent_token"]
        assert t1 != t2
