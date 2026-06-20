import time
from unittest.mock import patch

from shared.admin_auth import create_session_token, verify_session_token


class TestVerifySessionToken:
    def test_valid_token_accepted(self):
        token = create_session_token()
        assert verify_session_token(token) is True

    def test_wrong_password_rejected(self):
        token = create_session_token()
        with patch.dict("os.environ", {"ADMIN_PASSWORD": "different-password"}):
            assert verify_session_token(token) is False

    def test_expired_token_rejected(self):
        with patch("shared.admin_auth.time") as mock_time:
            mock_time.time.return_value = 1000.0
            token = create_session_token()
        # verify well past expiry
        with patch("shared.admin_auth.time") as mock_time:
            mock_time.time.return_value = 1000.0 + 8 * 3600 + 1
            assert verify_session_token(token) is False

    def test_garbage_rejected(self):
        assert verify_session_token("not-a-token") is False
        assert verify_session_token("") is False
        assert verify_session_token("a.b") is False

    def test_tampered_payload_rejected(self):
        token = create_session_token()
        parts = token.split(".")
        # flip a char in the payload
        tampered_payload = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        assert verify_session_token(f"{parts[0]}.{tampered_payload}.{parts[2]}") is False

    def test_no_password_configured_rejected(self):
        token = create_session_token()
        with patch.dict("os.environ", {"ADMIN_PASSWORD": ""}):
            assert verify_session_token(token) is False


class TestAdminLogin:
    def _client(self):
        from fastapi.testclient import TestClient
        from adapters.fastapi.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_correct_credentials_returns_token(self):
        r = self._client().post("/admin/login", json={"password": "test-password"})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert verify_session_token(data["token"])

    def test_wrong_password_returns_401(self):
        r = self._client().post("/admin/login", json={"password": "wrong"})
        assert r.status_code == 401

    def test_missing_password_env_returns_500(self):
        with patch.dict("os.environ", {"ADMIN_PASSWORD": ""}):
            r = self._client().post("/admin/login", json={"password": ""})
        assert r.status_code == 500

    def test_token_gates_admin_endpoint(self):
        client = self._client()
        login = client.post("/admin/login", json={"password": "test-password"})
        token = login.json()["token"]

        r = client.get("/admin/tenants", headers={"Authorization": f"Bearer {token}"})
        # 401 = auth rejected; anything else means the token was accepted
        assert r.status_code != 401

    def test_invalid_token_blocked(self):
        r = self._client().get("/admin/tenants", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401

    def test_no_token_blocked(self):
        r = self._client().get("/admin/tenants")
        assert r.status_code == 401
