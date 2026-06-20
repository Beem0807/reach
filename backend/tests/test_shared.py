"""Tests for shared modules: admin_auth, tenant_auth, password, audit."""
import os
import time
from unittest.mock import patch, MagicMock

import pytest

import conftest


# ---------------------------------------------------------------------------
# shared/admin_auth.py
# ---------------------------------------------------------------------------

class TestAdminAuth:
    def test_valid_token_verifies(self):
        from shared.admin_auth import verify_session_token
        token = conftest.ADMIN_TOKEN
        assert verify_session_token(token) is True

    def test_empty_token_returns_false(self):
        from shared.admin_auth import verify_session_token
        assert verify_session_token("") is False

    def test_wrong_signature_returns_false(self):
        from shared.admin_auth import verify_session_token, create_session_token
        token = create_session_token()
        # Tamper with the signature
        parts = token.split(".")
        parts[2] = parts[2][:-3] + "xxx"
        assert verify_session_token(".".join(parts)) is False

    def test_expired_token_returns_false(self):
        from shared.admin_auth import verify_session_token
        import base64, json, hashlib, hmac as hmacmod
        password = os.environ.get("ADMIN_PASSWORD", "")

        def _b64url(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        def _sign(key, msg):
            return _b64url(hmacmod.new(key.encode(), msg.encode(), hashlib.sha256).digest())

        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps({"sub": "admin", "iat": 1000, "exp": 1001}).encode())
        signing = f"{header}.{payload}"
        token = f"{signing}.{_sign(password, signing)}"
        assert verify_session_token(token) is False

    def test_malformed_token_returns_false(self):
        from shared.admin_auth import verify_session_token
        assert verify_session_token("only.two") is False

    def test_non_json_payload_returns_false(self):
        from shared.admin_auth import verify_session_token
        assert verify_session_token("aaa.bbb.ccc") is False

    def test_no_admin_password_env_returns_false(self):
        from shared.admin_auth import verify_session_token
        with patch.dict(os.environ, {"ADMIN_PASSWORD": ""}):
            assert verify_session_token(conftest.ADMIN_TOKEN) is False

    def test_non_base64_payload_returns_false(self):
        # Lines 45-46: exception path in verify_session_token
        from shared.admin_auth import verify_session_token
        # Valid format but non-decodable JSON in payload
        assert verify_session_token("valid.!!!.sig") is False


# ---------------------------------------------------------------------------
# shared/tenant_auth.py
# ---------------------------------------------------------------------------

class TestTenantAuth:
    def _make_token(self, **kwargs):
        from shared.tenant_auth import create_tenant_token
        defaults = dict(user_id="user_1", tenant_id="t1", role="admin", username="alice")
        defaults.update(kwargs)
        return create_tenant_token(**defaults)

    def test_valid_token_returns_payload(self):
        from shared.tenant_auth import verify_tenant_token
        token = self._make_token()
        payload = verify_tenant_token(token)
        assert payload is not None
        assert payload["sub"] == "user_1"
        assert payload["tenant_id"] == "t1"

    def test_empty_token_returns_none(self):
        from shared.tenant_auth import verify_tenant_token
        assert verify_tenant_token("") is None

    def test_wrong_signature_returns_none(self):
        from shared.tenant_auth import verify_tenant_token
        token = self._make_token()
        parts = token.split(".")
        parts[2] = "badsig"
        assert verify_tenant_token(".".join(parts)) is None

    def test_admin_session_token_rejected_by_tenant_verifier(self):
        """Platform admin tokens (typ=JWT) must not be accepted as tenant tokens."""
        from shared.tenant_auth import verify_tenant_token
        assert verify_tenant_token(conftest.ADMIN_TOKEN) is None

    def test_expired_token_returns_none(self):
        from shared.tenant_auth import verify_tenant_token
        import base64, json, hashlib, hmac as hmacmod

        key = os.environ.get("TOKEN_PEPPER", "localtest")

        def _b64(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        def _sign(msg):
            return _b64(hmacmod.new(key.encode(), msg.encode(), hashlib.sha256).digest())

        header = _b64(json.dumps({"alg": "HS256", "typ": "tenant"}).encode())
        payload = _b64(json.dumps({"sub": "u", "tenant_id": "t", "role": "admin",
                                    "username": "u", "iat": 1000, "exp": 1001}).encode())
        signing = f"{header}.{payload}"
        token = f"{signing}.{_sign(signing)}"
        assert verify_tenant_token(token) is None

    def test_malformed_token_returns_none(self):
        from shared.tenant_auth import verify_tenant_token
        assert verify_tenant_token("bad.token") is None

    def test_garbage_base64_returns_none(self):
        from shared.tenant_auth import verify_tenant_token
        assert verify_tenant_token("aaa.!!!.ccc") is None


# ---------------------------------------------------------------------------
# shared/auth.py
# ---------------------------------------------------------------------------

class TestSharedAuth:
    def _valid_tenant_token(self):
        from shared.tenant_auth import create_tenant_token
        return create_tenant_token(
            user_id="user_1", tenant_id="t1", role="admin", username="alice"
        )

    def test_verify_tenant_payload_returns_none_when_tenant_not_found(self):
        # Line 40: tenant not found
        from shared.auth import _verify_tenant_payload
        token = self._valid_tenant_token()
        with patch("shared.store.tenants_repo") as tr:
            tr.get.return_value = None
            result = _verify_tenant_payload(token)
        assert result is None

    def test_verify_tenant_payload_returns_none_when_tenant_disabled(self):
        # Line 40: tenant disabled
        from shared.auth import _verify_tenant_payload
        token = self._valid_tenant_token()
        with patch("shared.store.tenants_repo") as tr:
            tr.get.return_value = {"tenant_id": "t1", "status": "DISABLED"}
            result = _verify_tenant_payload(token)
        assert result is None

    def test_verify_tenant_payload_returns_payload_when_tenant_active(self):
        from shared.auth import _verify_tenant_payload
        token = self._valid_tenant_token()
        with patch("shared.store.tenants_repo") as tr:
            tr.get.return_value = {"tenant_id": "t1", "status": "ACTIVE"}
            result = _verify_tenant_payload(token)
        assert result is not None
        assert result["sub"] == "user_1"


# ---------------------------------------------------------------------------
# shared/password.py
# ---------------------------------------------------------------------------

class TestPassword:
    def test_hash_password_produces_pbkdf2_format(self):
        from shared.password import hash_password
        h = hash_password("secret123")
        assert h.startswith("pbkdf2$")
        parts = h.split("$")
        assert len(parts) == 3

    def test_verify_password_correct(self):
        from shared.password import hash_password, verify_password
        h = hash_password("mypassword")
        assert verify_password("mypassword", h) is True

    def test_verify_password_wrong(self):
        from shared.password import hash_password, verify_password
        h = hash_password("mypassword")
        assert verify_password("wrongpassword", h) is False

    def test_verify_password_malformed_hash_returns_false(self):
        from shared.password import verify_password
        # Lines 17-18 in password.py - the except Exception path
        assert verify_password("password", "not_a_valid_hash") is False

    def test_generate_temp_password_returns_string(self):
        from shared.password import generate_temp_password
        pw = generate_temp_password()
        assert isinstance(pw, str)
        assert len(pw) > 0

    def test_generate_temp_password_is_unique(self):
        from shared.password import generate_temp_password
        pw1 = generate_temp_password()
        pw2 = generate_temp_password()
        assert pw1 != pw2

    def test_hash_is_unique_for_same_password(self):
        """Different salts produce different hashes."""
        from shared.password import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2


# ---------------------------------------------------------------------------
# shared/audit.py
# ---------------------------------------------------------------------------

class TestAudit:
    def test_write_calls_audit_repo_create(self):
        with patch("shared.audit.audit_repo") as ar:
            import shared.audit as audit
            audit.write("user.login", tenant_id="t1", actor_id="u1")
        ar.create.assert_called_once()
        call_arg = ar.create.call_args[0][0]
        assert call_arg["action"] == "user.login"
        assert call_arg["tenant_id"] == "t1"
        assert call_arg["actor_id"] == "u1"

    def test_write_does_not_raise_on_repo_exception(self):
        """Audit failures must never break primary operations."""
        with patch("shared.audit.audit_repo") as ar:
            ar.create.side_effect = Exception("DB error")
            import shared.audit as audit
            # Should not raise
            audit.write("some.action")

    def test_write_uses_defaults(self):
        with patch("shared.audit.audit_repo") as ar:
            import shared.audit as audit
            audit.write("platform.action")
        call_arg = ar.create.call_args[0][0]
        assert call_arg["actor_id"] == "platform_admin"
        assert call_arg["actor_role"] == "PLATFORM_ADMIN"
        assert call_arg["event_metadata"] == {}
        assert call_arg["ip_address"] is None

    def test_write_with_metadata(self):
        with patch("shared.audit.audit_repo") as ar:
            import shared.audit as audit
            audit.write("token.created", metadata={"name": "CI"})
        call_arg = ar.create.call_args[0][0]
        assert call_arg["event_metadata"] == {"name": "CI"}

    def test_write_none_metadata_becomes_empty_dict(self):
        with patch("shared.audit.audit_repo") as ar:
            import shared.audit as audit
            audit.write("x", metadata=None)
        call_arg = ar.create.call_args[0][0]
        assert call_arg["event_metadata"] == {}
