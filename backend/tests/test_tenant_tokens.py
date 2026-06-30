"""Tests for API token create, list, revoke, and rename."""
import json
from unittest.mock import patch

from handlers.tenant_tokens import (
    handle_create_api_token,
    handle_list_api_tokens,
    handle_revoke_api_token,
    handle_revoke_all_user_tokens,
    handle_rename_api_token,
    list_tokens_handler,
    create_token_handler,
    revoke_token_handler,
    revoke_user_tokens_handler,
    rename_token_handler,
)
from shared.tenant_auth import create_tenant_token

ADMIN_TOKEN = {"sub": "user_alice", "tenant_id": "tenant_acme", "role": "admin", "username": "alice"}
USER_TOKEN  = {"sub": "user_bob",   "tenant_id": "tenant_acme", "role": "developer",  "username": "bob"}

STORED_TOKEN = {
    "token_id":    "tkid_abc",
    "user_id":     "user_alice",
    "tenant_id":   "tenant_acme",
    "name":        "My laptop",
    "status":      "ACTIVE",
    "created_at":  "2026-06-20T10:00:00",
    "last_used_at": None,
    "revoked_at":  None,
}


class TestCreateApiToken:
    def _call(self, body=None, token=ADMIN_TOKEN):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr, \
             patch("handlers.tenant_tokens.audit"):
            atr.create.return_value = None
            r = handle_create_api_token(body or {}, token)
        return r, atr

    def test_creates_token_with_name(self):
        r, atr = self._call({"name": "CI runner"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["name"] == "CI runner"
        assert body["token"].startswith("tok_")
        assert "token_id" in body

    def test_defaults_name(self):
        r, atr = self._call({})
        args = atr.create.call_args[0][0]
        assert args["name"] == "CLI token"

    def test_tenant_user_can_also_create(self):
        r, _ = self._call({}, token=USER_TOKEN)
        assert r["statusCode"] == 201

    def test_token_id_is_unique(self):
        with patch("handlers.tenant_tokens.api_tokens_repo"), \
             patch("handlers.tenant_tokens.audit"):
            r1 = handle_create_api_token({}, ADMIN_TOKEN)
            r2 = handle_create_api_token({}, ADMIN_TOKEN)
        id1 = json.loads(r1["body"])["token_id"]
        id2 = json.loads(r2["body"])["token_id"]
        assert id1 != id2

    def test_raw_tokens_are_unique(self):
        with patch("handlers.tenant_tokens.api_tokens_repo"), \
             patch("handlers.tenant_tokens.audit"):
            r1 = handle_create_api_token({}, ADMIN_TOKEN)
            r2 = handle_create_api_token({}, ADMIN_TOKEN)
        tok1 = json.loads(r1["body"])["token"]
        tok2 = json.loads(r2["body"])["token"]
        assert tok1 != tok2


class TestListApiTokens:
    def test_returns_tokens_without_raw_value(self):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = [STORED_TOKEN]
            r = handle_list_api_tokens(ADMIN_TOKEN)
        assert r["statusCode"] == 200
        tokens = json.loads(r["body"])["tokens"]
        assert len(tokens) == 1
        assert "token" not in tokens[0]  # raw token must never be returned in list

    def test_empty_list(self):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = []
            r = handle_list_api_tokens(USER_TOKEN)
        assert json.loads(r["body"])["tokens"] == []


class TestRenameApiToken:
    def _call(self, token_id="tkid_abc", body=None, token=ADMIN_TOKEN, stored=None):
        stored_tokens = [stored] if stored else [{**STORED_TOKEN, "user_id": "user_alice"}]
        if body is None:
            body = {"name": "New name"}
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr, \
             patch("handlers.tenant_tokens.audit"):
            atr.list_by_user.return_value = stored_tokens
            r = handle_rename_api_token(token_id, body, token)
        return r, atr

    def test_renames_own_token(self):
        r, atr = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["name"] == "New name"
        assert body["token_id"] == "tkid_abc"
        atr.rename.assert_called_once_with("tkid_abc", "New name")

    def test_missing_name_returns_400(self):
        r, atr = self._call(body={})
        assert r["statusCode"] == 400
        atr.rename.assert_not_called()

    def test_empty_name_returns_400(self):
        r, atr = self._call(body={"name": "  "})
        assert r["statusCode"] == 400
        atr.rename.assert_not_called()

    def test_token_not_found_returns_404(self):
        r, atr = self._call(token_id="tkid_missing")
        assert r["statusCode"] == 404
        atr.rename.assert_not_called()

    def test_cross_tenant_returns_404(self):
        other = {**STORED_TOKEN, "tenant_id": "tenant_other"}
        r, atr = self._call(stored=other)
        assert r["statusCode"] == 404
        atr.rename.assert_not_called()

    def test_tenant_user_can_rename_own_token(self):
        stored = {**STORED_TOKEN, "user_id": "user_bob", "tenant_id": "tenant_acme"}
        r, _ = self._call(token=USER_TOKEN, stored=stored)
        assert r["statusCode"] == 200

    def test_strips_whitespace_from_name(self):
        r, atr = self._call(body={"name": "  trimmed  "})
        assert r["statusCode"] == 200
        atr.rename.assert_called_once_with("tkid_abc", "trimmed")


class TestRevokeApiToken:
    def test_revokes_own_token(self):
        token = {**STORED_TOKEN, "user_id": "user_alice"}
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr, \
             patch("handlers.tenant_tokens.audit"):
            atr.list_by_user.return_value = [token]
            atr.revoke.return_value = None
            r = handle_revoke_api_token("tkid_abc", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "REVOKED"
        atr.revoke.assert_called_once_with("tkid_abc", atr.revoke.call_args[0][1])

    def test_delete_after_revoke_hard_deletes(self):
        # Two-step: DELETE on an already-REVOKED token removes the record.
        token = {**STORED_TOKEN, "user_id": "user_alice", "status": "REVOKED"}
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr, \
             patch("handlers.tenant_tokens.audit"):
            atr.list_by_user.return_value = [token]
            r = handle_revoke_api_token("tkid_abc", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "DELETED"
        atr.delete.assert_called_once_with("tkid_abc")
        atr.revoke.assert_not_called()

    def test_token_not_found_returns_404(self):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = []
            r = handle_revoke_api_token("tkid_missing", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        token = {**STORED_TOKEN, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = [token]
            r = handle_revoke_api_token("tkid_abc", ADMIN_TOKEN)
        assert r["statusCode"] == 404


class TestRevokeAllUserTokens:
    def _tokens(self):
        return [
            {**STORED_TOKEN, "token_id": "tk_1", "status": "ACTIVE"},
            {**STORED_TOKEN, "token_id": "tk_2", "status": "ACTIVE"},
            {**STORED_TOKEN, "token_id": "tk_3", "status": "REVOKED"},
        ]

    def test_admin_revokes_all_active_tokens(self):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = self._tokens()
            atr.revoke.return_value = None
            r = handle_revoke_all_user_tokens("user_alice", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        # Only the 2 ACTIVE tokens are revoked; the REVOKED one is skipped.
        assert json.loads(r["body"])["revoked"] == 2
        assert atr.revoke.call_count == 2
        revoked_ids = {c[0][0] for c in atr.revoke.call_args_list}
        assert revoked_ids == {"tk_1", "tk_2"}

    def test_skips_cross_tenant_tokens(self):
        tokens = [
            {**STORED_TOKEN, "token_id": "tk_1", "status": "ACTIVE"},
            {**STORED_TOKEN, "token_id": "tk_other", "status": "ACTIVE", "tenant_id": "tenant_other"},
        ]
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = tokens
            atr.revoke.return_value = None
            r = handle_revoke_all_user_tokens("user_alice", ADMIN_TOKEN)
        assert json.loads(r["body"])["revoked"] == 1
        atr.revoke.assert_called_once()
        assert atr.revoke.call_args[0][0] == "tk_1"

    def test_no_active_tokens_returns_zero(self):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = [{**STORED_TOKEN, "status": "REVOKED"}]
            r = handle_revoke_all_user_tokens("user_alice", ADMIN_TOKEN)
        assert json.loads(r["body"])["revoked"] == 0
        atr.revoke.assert_not_called()

    def test_empty_list_returns_zero(self):
        with patch("handlers.tenant_tokens.api_tokens_repo") as atr:
            atr.list_by_user.return_value = []
            r = handle_revoke_all_user_tokens("user_alice", ADMIN_TOKEN)
        assert json.loads(r["body"])["revoked"] == 0

    def test_non_admin_rejected(self):
        r = handle_revoke_all_user_tokens("user_alice", USER_TOKEN)
        assert r["statusCode"] == 403


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

_VALID_TOKEN = create_tenant_token(
    user_id="user_alice",
    tenant_id="tenant_acme",
    role="admin",
    username="alice",
)

_OK = {"statusCode": 200, "headers": {}, "body": "{}"}

_ACTIVE_TENANT = {"tenant_id": "tenant_acme", "name": "Acme", "status": "ACTIVE"}


def _evt(headers=None, body=None, path=None, qs=None):
    return {
        "headers": headers if headers is not None else {"authorization": f"Bearer {_VALID_TOKEN}"},
        "body": body,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
    }


class TestListTokensHandler:
    def test_missing_auth_returns_401(self):
        r = list_tokens_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        r = list_tokens_handler(_evt(headers={"authorization": "Bearer bad"}), None)
        assert r["statusCode"] == 401

    def test_delegates_to_handler(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_tokens.handle_list_api_tokens", return_value=_OK) as h:
            list_tokens_handler(_evt(), None)
        h.assert_called_once_with(ADMIN_TOKEN)


class TestCreateTokenHandler:
    def test_missing_auth_returns_401(self):
        r = create_token_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN):
            r = create_token_handler(_evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_to_handler(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_tokens.handle_create_api_token", return_value=_OK) as h:
            create_token_handler(_evt(body='{"name":"CI"}'), None)
        h.assert_called_once()
        assert h.call_args[0][0] == {"name": "CI"}


class TestRevokeTokenHandler:
    def test_missing_auth_returns_401(self):
        r = revoke_token_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        r = revoke_token_handler(_evt(headers={"authorization": "Bearer bad"}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_token_id_from_path(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_tokens.handle_revoke_api_token", return_value=_OK) as h:
            revoke_token_handler(_evt(path={"token_id": "tkid_xyz"}), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "tkid_xyz"


class TestRevokeUserTokensHandler:
    def test_missing_auth_returns_401(self):
        r = revoke_user_tokens_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_user_id_from_path(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_tokens.handle_revoke_all_user_tokens", return_value=_OK) as h:
            revoke_user_tokens_handler(_evt(path={"user_id": "user_bob"}), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"


class TestRenameTokenHandler:
    def test_missing_auth_returns_401(self):
        r = rename_token_handler(_evt(headers={}, body='{"name":"x"}'), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        r = rename_token_handler(_evt(headers={"authorization": "Bearer bad"}, body='{"name":"x"}'), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN):
            r = rename_token_handler(_evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_with_token_id_and_body(self):
        with patch("handlers.tenant_tokens._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_tokens.handle_rename_api_token", return_value=_OK) as h:
            rename_token_handler(_evt(path={"token_id": "tkid_xyz"}, body='{"name":"prod key"}'), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "tkid_xyz"
        assert h.call_args[0][1] == {"name": "prod key"}
