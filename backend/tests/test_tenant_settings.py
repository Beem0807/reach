"""Tests for per-tenant settings: shared.settings helpers + handlers/tenant_settings.py."""
import json
from unittest.mock import patch

from shared.settings import (SETTINGS_DEFAULTS, effective_settings, merge_settings,
                             validate_settings)
from handlers.tenant_settings import (
    handle_get_tenant_settings,
    handle_update_tenant_settings,
    handle_admin_get_tenant_settings,
    handle_admin_update_tenant_settings,
)

TENANT_ID = "tenant_1"
TOKEN = "tok_test"
# _verify_tenant_payload returns a JWT payload (sub/tenant_id/role/username), not a user.
_ADMIN = {"sub": "u_admin", "tenant_id": TENANT_ID, "role": "admin", "username": "alice"}
_DEV = {"sub": "u_dev", "tenant_id": TENANT_ID, "role": "developer", "username": "dev"}
_TENANT = {"tenant_id": TENANT_ID, "name": "acme", "settings": {}}


def _body(r):
    return json.loads(r["body"])


# --- shared.settings --------------------------------------------------------

class TestSettingsHelpers:
    def test_effective_falls_back_to_defaults(self):
        assert effective_settings({"settings": {}}) == SETTINGS_DEFAULTS
        assert effective_settings(None) == SETTINGS_DEFAULTS

    def test_effective_applies_overrides(self):
        eff = effective_settings({"settings": {"job_retention_days": 3}})
        assert eff["job_retention_days"] == 3
        assert eff["approval_retention_days"] == SETTINGS_DEFAULTS["approval_retention_days"]

    def test_effective_ignores_bad_types(self):
        eff = effective_settings({"settings": {"fanout_cap": "nope", "job_retention_days": True}})
        assert eff["fanout_cap"] == SETTINGS_DEFAULTS["fanout_cap"]
        assert eff["job_retention_days"] == SETTINGS_DEFAULTS["job_retention_days"]

    def test_validate_bounds_enforced_for_tenant(self):
        clean, err = validate_settings({"job_retention_days": 99999}, enforce_bounds=True)
        assert clean is None and "between" in err

    def test_validate_bounds_bypassed_for_platform(self):
        clean, err = validate_settings({"job_retention_days": 99999}, enforce_bounds=False)
        assert err is None and clean["job_retention_days"] == 99999

    def test_validate_rejects_non_positive(self):
        clean, err = validate_settings({"fanout_cap": 0}, enforce_bounds=True)
        assert clean is None and err

    def test_validate_ignores_unknown_keys(self):
        clean, err = validate_settings({"bogus": 5, "fanout_cap": 10}, enforce_bounds=True)
        assert err is None and clean == {"fanout_cap": 10}

    def test_validate_null_clears(self):
        clean, err = validate_settings({"fanout_cap": None}, enforce_bounds=True)
        assert err is None and clean == {"fanout_cap": None}

    def test_merge_sets_and_clears(self):
        merged = merge_settings({"fanout_cap": 10, "job_retention_days": 3},
                                {"fanout_cap": 20, "job_retention_days": None})
        assert merged == {"fanout_cap": 20}

    def test_run_retention_is_a_setting(self):
        assert "run_retention_days" in SETTINGS_DEFAULTS

    def test_audit_retention_is_a_setting(self):
        assert "audit_retention_days" in SETTINGS_DEFAULTS


class TestWavePolicyValidation:
    def test_valid_policy_kept(self):
        body = {"wave_policy": {"tag": {"write": {"mode": "manual", "on_failure": "stop"}},
                                "fleet": {"read": {"mode": "auto", "on_failure": "continue"}}}}
        clean, err = validate_settings(body, enforce_bounds=True)
        assert err is None
        assert clean["wave_policy"]["tag"]["write"] == {"mode": "manual", "on_failure": "stop"}
        assert clean["wave_policy"]["fleet"]["read"] == {"mode": "auto", "on_failure": "continue"}

    def test_empty_branches_dropped(self):
        clean, err = validate_settings({"wave_policy": {"tag": {"read": None, "write": {}}}}, True)
        assert err is None and clean["wave_policy"] is None

    def test_bad_mode_rejected(self):
        clean, err = validate_settings({"wave_policy": {"tag": {"write": {"mode": "x"}}}}, True)
        assert clean is None and "mode" in err

    def test_null_clears(self):
        clean, err = validate_settings({"wave_policy": None}, True)
        assert err is None and clean == {"wave_policy": None}

    def test_concurrency_over_cap_rejected(self):
        # Set-time check: concurrency 50 with fanout_cap 25 (default) is rejected.
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_ADMIN), \
             patch("handlers.tenant_settings.tenants_repo") as tr, \
             patch("handlers.tenant_settings.audit"):
            tr.get.return_value = dict(_TENANT)
            r = handle_update_tenant_settings(
                {"wave_policy": {"fleet": {"write": {"mode": "auto", "concurrency": 50}}}}, TOKEN)
        assert r["statusCode"] == 400 and "cap" in json.loads(r["body"])["error"]

    def test_concurrency_within_cap_ok(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_ADMIN), \
             patch("handlers.tenant_settings.tenants_repo") as tr, \
             patch("handlers.tenant_settings.audit"):
            tr.get.return_value = dict(_TENANT)
            r = handle_update_tenant_settings(
                {"wave_policy": {"fleet": {"write": {"mode": "auto", "concurrency": 10}}}}, TOKEN)
        assert r["statusCode"] == 200
        tr.set_settings.assert_called_once()


# --- tenant admin handlers --------------------------------------------------

class TestTenantSettingsHandlers:
    def test_get_unauthorized(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=None):
            assert handle_get_tenant_settings(TOKEN)["statusCode"] == 401

    def test_get_forbidden_for_non_admin(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_DEV):
            assert handle_get_tenant_settings(TOKEN)["statusCode"] == 403

    def test_get_returns_effective_and_defaults(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_ADMIN), \
             patch("handlers.tenant_settings.tenants_repo") as tr:
            tr.get.return_value = {**_TENANT, "settings": {"fanout_cap": 5}}
            r = handle_get_tenant_settings(TOKEN)
        assert r["statusCode"] == 200
        b = _body(r)
        assert b["settings"]["fanout_cap"] == 5
        assert b["overrides"] == {"fanout_cap": 5}
        assert b["defaults"] == SETTINGS_DEFAULTS
        assert "fanout_cap" in b["bounds"]

    def test_update_forbidden_for_non_admin(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_DEV):
            assert handle_update_tenant_settings({"fanout_cap": 5}, TOKEN)["statusCode"] == 403

    def test_update_rejects_out_of_bounds(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_ADMIN), \
             patch("handlers.tenant_settings.tenants_repo") as tr, \
             patch("handlers.tenant_settings.audit"):
            tr.get.return_value = dict(_TENANT)
            r = handle_update_tenant_settings({"job_retention_days": 999999}, TOKEN)
        assert r["statusCode"] == 400

    def test_update_persists_merged(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_ADMIN), \
             patch("handlers.tenant_settings.tenants_repo") as tr, \
             patch("handlers.tenant_settings.audit") as aud:
            tr.get.return_value = {**_TENANT, "settings": {"fanout_cap": 5}}
            r = handle_update_tenant_settings({"job_retention_days": 3}, TOKEN)
        assert r["statusCode"] == 200
        tr.set_settings.assert_called_once_with(TENANT_ID, {"fanout_cap": 5, "job_retention_days": 3})
        aud.write.assert_called_once()

    def test_update_null_clears_override(self):
        with patch("handlers.tenant_settings._verify_tenant_payload", return_value=_ADMIN), \
             patch("handlers.tenant_settings.tenants_repo") as tr, \
             patch("handlers.tenant_settings.audit"):
            tr.get.return_value = {**_TENANT, "settings": {"fanout_cap": 5, "job_retention_days": 3}}
            handle_update_tenant_settings({"fanout_cap": None}, TOKEN)
        tr.set_settings.assert_called_once_with(TENANT_ID, {"job_retention_days": 3})


# --- platform admin handlers ------------------------------------------------

class TestAdminSettingsHandlers:
    def test_get_unauthorized(self):
        with patch("handlers.tenant_settings._verify_admin", return_value=False):
            assert handle_admin_get_tenant_settings(TENANT_ID, TOKEN)["statusCode"] == 401

    def test_get_tenant_not_found(self):
        with patch("handlers.tenant_settings._verify_admin", return_value=True), \
             patch("handlers.tenant_settings.tenants_repo") as tr:
            tr.get.return_value = None
            assert handle_admin_get_tenant_settings(TENANT_ID, TOKEN)["statusCode"] == 404

    def test_admin_override_bypasses_bounds(self):
        with patch("handlers.tenant_settings._verify_admin", return_value=True), \
             patch("handlers.tenant_settings.tenants_repo") as tr, \
             patch("handlers.tenant_settings.audit") as aud:
            tr.get.return_value = dict(_TENANT)
            r = handle_admin_update_tenant_settings(TENANT_ID, {"job_retention_days": 999999}, TOKEN)
        assert r["statusCode"] == 200
        tr.set_settings.assert_called_once_with(TENANT_ID, {"job_retention_days": 999999})
        assert aud.write.call_args[0][0] == "tenant.settings_overridden"
