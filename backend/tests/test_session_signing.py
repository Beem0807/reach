"""Tenant session tokens are signed with the dedicated SESSION_SIGNING_KEY,
independent of TOKEN_PEPPER, and rotating it cleanly invalidates old sessions."""
import os
from unittest.mock import patch

from shared.tenant_auth import create_tenant_token, verify_tenant_token


def test_sign_verify_roundtrip():
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "key-A"}):
        tok = create_tenant_token("u1", "t1", "admin", "alice")
        payload = verify_tenant_token(tok)
    assert payload["sub"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert payload["role"] == "admin"


def test_rotating_session_key_invalidates_existing_sessions():
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "key-A"}):
        tok = create_tenant_token("u1", "t1", "admin", "alice")
    # New key -> the old session no longer verifies (user must log in again).
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "key-B"}):
        assert verify_tenant_token(tok) is None


def test_token_pepper_does_not_affect_sessions():
    # Sign with a fixed SESSION_SIGNING_KEY, then change TOKEN_PEPPER only.
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "sess", "TOKEN_PEPPER": "pepper-1"}):
        tok = create_tenant_token("u1", "t1", "admin", "alice")
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "sess", "TOKEN_PEPPER": "pepper-2"}):
        assert verify_tenant_token(tok)["sub"] == "u1"  # pepper change is irrelevant to sessions


def test_session_key_independent_of_token_pepper_value():
    # A token signed under SESSION_SIGNING_KEY must not verify just because
    # TOKEN_PEPPER happens to equal the old signing value.
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "the-key"}):
        tok = create_tenant_token("u1", "t1", "admin", "alice")
    with patch.dict(os.environ, {"SESSION_SIGNING_KEY": "rotated", "TOKEN_PEPPER": "the-key"}):
        assert verify_tenant_token(tok) is None
