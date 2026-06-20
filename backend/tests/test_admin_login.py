"""Tests for platform admin login (handlers/admin_login.py)."""
import json
import os
from unittest.mock import patch

from handlers.admin_login import handle_admin_login, admin_login_handler


class TestHandleAdminLogin:
    def test_missing_admin_password_env_returns_500(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": ""}):
            r = handle_admin_login({"password": "anything"})
        assert r["statusCode"] == 500

    def test_wrong_password_returns_401(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "correct-password"}):
            r = handle_admin_login({"password": "wrong-password"})
        assert r["statusCode"] == 401

    def test_correct_password_returns_token(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "correct-password"}):
            r = handle_admin_login({"password": "correct-password"})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "token" in body
        assert body["token"]

    def test_empty_password_returns_401(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "secret"}):
            r = handle_admin_login({})
        assert r["statusCode"] == 401

    def test_none_password_returns_401(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "secret"}):
            r = handle_admin_login({"password": None})
        assert r["statusCode"] == 401

    def test_success_writes_admin_login_audit(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "correct-password"}), \
             patch("handlers.admin_login.audit") as mock_audit:
            handle_admin_login({"password": "correct-password"}, ip="203.0.113.5")
        mock_audit.write.assert_called_once()
        args, kwargs = mock_audit.write.call_args
        assert args[0] == "admin.login"
        assert kwargs["ip_address"] == "203.0.113.5"

    def test_failure_writes_admin_login_failed_audit(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "correct-password"}), \
             patch("handlers.admin_login.audit") as mock_audit:
            handle_admin_login({"password": "wrong"}, ip="203.0.113.6")
        mock_audit.write.assert_called_once()
        args, kwargs = mock_audit.write.call_args
        assert args[0] == "admin.login_failed"
        assert kwargs["ip_address"] == "203.0.113.6"

    def test_misconfigured_password_does_not_audit(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": ""}), \
             patch("handlers.admin_login.audit") as mock_audit:
            r = handle_admin_login({"password": "anything"})
        assert r["statusCode"] == 500
        mock_audit.write.assert_not_called()


class TestAdminLoginHandler:
    def _evt(self, body=None):
        return {
            "headers": {},
            "body": body,
            "pathParameters": {},
            "queryStringParameters": {},
        }

    def test_invalid_json_returns_400(self):
        r = admin_login_handler(self._evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_to_handler(self):
        with patch("handlers.admin_login.handle_admin_login", return_value={"statusCode": 200, "body": '{"token":"t"}'}) as h:
            admin_login_handler(self._evt(body='{"password":"pw"}'), None)
        h.assert_called_once_with({"password": "pw"}, "")

    def test_passes_source_ip_to_handler(self):
        evt = self._evt(body='{"password":"pw"}')
        evt["requestContext"] = {"http": {"sourceIp": "203.0.113.9"}}
        with patch("handlers.admin_login.handle_admin_login", return_value={"statusCode": 200, "body": "{}"}) as h:
            admin_login_handler(evt, None)
        h.assert_called_once_with({"password": "pw"}, "203.0.113.9")

    def test_none_body_treated_as_empty_dict(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "pw"}):
            r = admin_login_handler(self._evt(body=None), None)
        # empty dict -> no password -> 401
        assert r["statusCode"] == 401

    def test_correct_credentials_end_to_end(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "my-pw"}):
            r = admin_login_handler(self._evt(body='{"password":"my-pw"}'), None)
        assert r["statusCode"] == 200
        assert "token" in json.loads(r["body"])
