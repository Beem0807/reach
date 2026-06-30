import json
from unittest.mock import patch

from shared.auth import _hmac_token
from handlers.agent_claim import handle_agent_claim

AGENT_ID = "agent_a"
RAW_INSTALL_TOKEN = "install_testtoken123"
INSTALL_TOKEN_HASH = _hmac_token(RAW_INSTALL_TOKEN)

_AGENT_CREATED = {
    "agent_id": AGENT_ID,
    "status": "CREATED",
    "mode": "wild",
    "install_token_hash": INSTALL_TOKEN_HASH,
    "install_token_expires_at": 9999999999,
}

# Credential-only: the agent never sends an agent_id; the install token identifies it.
_VALID_BODY = {
    "install_token": RAW_INSTALL_TOKEN,
    "machine_fingerprint": "fp_abc123",
    "hostname": "my-server",
    "agent_version": "0.1.0",
}


class TestAgentClaim:
    def _call(self, body=None, agent=_AGENT_CREATED):
        with patch("handlers.agent_claim.agents_repo") as ar:
            # The handler resolves the agent by install-token hash, not agent_id.
            ar.get_by_install_token_hash.return_value = agent
            return handle_agent_claim(body or _VALID_BODY)

    def test_missing_install_token(self):
        r = self._call({**_VALID_BODY, "install_token": ""})
        assert r["statusCode"] == 400

    def test_missing_machine_fingerprint(self):
        r = self._call({**_VALID_BODY, "machine_fingerprint": ""})
        assert r["statusCode"] == 400

    def test_unknown_install_token_rejected(self):
        # No agent matches the token hash -> 403 (invalid token / not found collapse).
        r = self._call(agent=None)
        assert r["statusCode"] == 403

    def test_lookup_uses_install_token_hash(self):
        with patch("handlers.agent_claim.agents_repo") as ar:
            ar.get_by_install_token_hash.return_value = _AGENT_CREATED
            handle_agent_claim(_VALID_BODY)
        ar.get_by_install_token_hash.assert_called_once_with(INSTALL_TOKEN_HASH)

    def test_already_claimed_agent_rejected(self):
        active_agent = {**_AGENT_CREATED, "status": "ACTIVE"}
        r = self._call(agent=active_agent)
        assert r["statusCode"] == 403

    def test_expired_install_token_rejected(self):
        expired_agent = {**_AGENT_CREATED, "install_token_expires_at": 1}
        r = self._call(agent=expired_agent)
        assert r["statusCode"] == 403

    def test_successful_claim_returns_agent_token(self):
        r = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["agent_token"].startswith("agent_")
        assert body["mode"] == "wild"

    def test_claim_calls_agent_repo(self):
        with patch("handlers.agent_claim.agents_repo") as ar:
            ar.get_by_install_token_hash.return_value = _AGENT_CREATED
            handle_agent_claim(_VALID_BODY)
        ar.claim.assert_called_once()
        # The agent_id comes from the looked-up record, not the request body.
        assert ar.claim.call_args[0][0] == AGENT_ID
        claim_data = ar.claim.call_args[0][1]
        assert "agent_token_hash" in claim_data
        assert claim_data["hostname"] == "my-server"
        assert claim_data["agent_version"] == "0.1.0"
        assert claim_data["machine_fingerprint"] == "fp_abc123"
        # A plain agent defaults to type=host.
        assert claim_data["type"] == "host"

    def test_k8s_claim_stores_type(self):
        # A k8s-created agent claimed by the k8s image (self-reports k8s) succeeds.
        body = {**_VALID_BODY, "type": "k8s"}
        k8s_agent = {**_AGENT_CREATED, "type": "k8s"}
        with patch("handlers.agent_claim.agents_repo") as ar:
            ar.get_by_install_token_hash.return_value = k8s_agent
            r = handle_agent_claim(body)
        assert r["statusCode"] == 200
        claim_data = ar.claim.call_args[0][1]
        assert claim_data["type"] == "k8s"

    def test_unknown_type_falls_back_to_host(self):
        # A bogus self-reported type defaults to host, which matches a host record.
        body = {**_VALID_BODY, "type": "bogus"}
        with patch("handlers.agent_claim.agents_repo") as ar:
            ar.get_by_install_token_hash.return_value = _AGENT_CREATED
            handle_agent_claim(body)
        claim_data = ar.claim.call_args[0][1]
        assert claim_data["type"] == "host"

    def test_k8s_token_rejects_host_installer(self):
        # A k8s agent's install token cannot be redeemed by the host installer.
        k8s_agent = {**_AGENT_CREATED, "type": "k8s"}
        r = self._call(body={**_VALID_BODY, "type": "host"}, agent=k8s_agent)
        assert r["statusCode"] == 403

    def test_host_token_rejects_k8s_installer(self):
        # A host agent's install token cannot be redeemed by the k8s image.
        r = self._call(body={**_VALID_BODY, "type": "k8s"}, agent=_AGENT_CREATED)
        assert r["statusCode"] == 403

    def test_returned_token_matches_stored_hash(self):
        with patch("handlers.agent_claim.agents_repo") as ar:
            ar.get_by_install_token_hash.return_value = _AGENT_CREATED
            r = handle_agent_claim(_VALID_BODY)
        raw_token = json.loads(r["body"])["agent_token"]
        stored_hash = ar.claim.call_args[0][1]["agent_token_hash"]
        assert _hmac_token(raw_token) == stored_hash
