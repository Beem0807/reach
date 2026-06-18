"""
Tests for reach/config.py — all file I/O is redirected to a tmp directory.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR and CONFIG_FILE to a temp directory for every test."""
    import reach.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path


def _write(tmp_path, data):
    f = tmp_path / "config.json"
    f.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------

class TestLoad:
    def test_no_file_returns_default(self):
        from reach.config import load
        assert load() == {"active_profile": "default", "profiles": {}}

    def test_returns_file_contents(self, tmp_path):
        _write(tmp_path, {"active_profile": "prod", "profiles": {"prod": {"api_url": "https://x"}}})
        from reach.config import load
        assert load()["active_profile"] == "prod"


class TestSave:
    def test_creates_file(self, tmp_path):
        from reach.config import save
        save({"active_profile": "default", "profiles": {}})
        assert (tmp_path / "config.json").exists()

    def test_file_is_readable_back(self, tmp_path):
        from reach.config import save, load
        save({"active_profile": "x", "profiles": {"x": {"api_url": "https://a"}}})
        assert load()["active_profile"] == "x"

    def test_file_permissions_are_restricted(self, tmp_path):
        import stat
        from reach.config import save
        save({"active_profile": "default", "profiles": {}})
        mode = (tmp_path / "config.json").stat().st_mode
        assert bool(mode & stat.S_IRUSR)
        assert bool(mode & stat.S_IWUSR)
        assert not bool(mode & stat.S_IRGRP)
        assert not bool(mode & stat.S_IROTH)


# ---------------------------------------------------------------------------
# load_profile / save_profile
# ---------------------------------------------------------------------------

class TestLoadProfile:
    def test_missing_file_returns_empty(self):
        from reach.config import load_profile
        assert load_profile() == {}

    def test_returns_active_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "prod", "profiles": {"prod": {"api_url": "https://p"}}})
        from reach.config import load_profile
        assert load_profile()["api_url"] == "https://p"

    def test_named_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {
            "default": {"api_url": "https://d"},
            "staging": {"api_url": "https://s"},
        }})
        from reach.config import load_profile
        assert load_profile("staging")["api_url"] == "https://s"

    def test_unknown_profile_returns_empty(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {}})
        from reach.config import load_profile
        assert load_profile("nonexistent") == {}


class TestSaveProfile:
    def test_saves_to_active_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {}})
        from reach.config import save_profile, load_profile
        save_profile({"api_url": "https://new"})
        assert load_profile()["api_url"] == "https://new"

    def test_saves_to_named_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {}})
        from reach.config import save_profile, load_profile
        save_profile({"api_url": "https://s"}, name="staging")
        assert load_profile("staging")["api_url"] == "https://s"


# ---------------------------------------------------------------------------
# require
# ---------------------------------------------------------------------------

class TestRequire:
    def test_returns_value_when_present(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {"api_url": "https://x"}}})
        from reach.config import require
        assert require("api_url") == "https://x"

    def test_raises_system_exit_when_missing(self):
        from reach.config import require
        with pytest.raises(SystemExit):
            require("api_url")

    def test_raises_system_exit_when_empty_string(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {"api_url": ""}}})
        from reach.config import require
        with pytest.raises(SystemExit):
            require("api_url")


# ---------------------------------------------------------------------------
# set_active_profile
# ---------------------------------------------------------------------------

class TestSetActiveProfile:
    def test_switches_active_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {
            "default": {"api_url": "https://d"},
            "prod": {"api_url": "https://p"},
        }})
        from reach.config import set_active_profile, active_profile_name
        set_active_profile("prod")
        assert active_profile_name() == "prod"

    def test_unknown_profile_raises(self):
        from reach.config import set_active_profile
        with pytest.raises(SystemExit):
            set_active_profile("nonexistent")


# ---------------------------------------------------------------------------
# delete_profile
# ---------------------------------------------------------------------------

class TestDeleteProfile:
    def test_removes_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {
            "default": {"api_url": "https://d"},
            "old": {"api_url": "https://o"},
        }})
        from reach.config import delete_profile, list_profiles
        delete_profile("old")
        assert "old" not in list_profiles()

    def test_cannot_delete_active_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {}}})
        from reach.config import delete_profile
        with pytest.raises(SystemExit):
            delete_profile("default")

    def test_unknown_profile_raises(self):
        from reach.config import delete_profile
        with pytest.raises(SystemExit):
            delete_profile("nonexistent")


# ---------------------------------------------------------------------------
# rename_profile
# ---------------------------------------------------------------------------

class TestRenameProfile:
    def test_renames_profile(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {
            "default": {"api_url": "https://d"},
            "old": {"api_url": "https://o"},
        }})
        from reach.config import rename_profile, list_profiles
        rename_profile("old", "new")
        profiles = list_profiles()
        assert "new" in profiles
        assert "old" not in profiles

    def test_updates_active_profile_if_renamed(self, tmp_path):
        _write(tmp_path, {"active_profile": "old", "profiles": {"old": {"api_url": "https://o"}}})
        from reach.config import rename_profile, active_profile_name
        rename_profile("old", "new")
        assert active_profile_name() == "new"

    def test_old_not_found_raises(self):
        from reach.config import rename_profile
        with pytest.raises(SystemExit):
            rename_profile("nonexistent", "new")

    def test_new_already_exists_raises(self, tmp_path):
        _write(tmp_path, {"active_profile": "a", "profiles": {"a": {}, "b": {}}})
        from reach.config import rename_profile
        with pytest.raises(SystemExit):
            rename_profile("a", "b")


# ---------------------------------------------------------------------------
# aliases
# ---------------------------------------------------------------------------

class TestAliases:
    def setup_method(self, tmp_path):
        pass  # autouse fixture handles isolation

    def test_set_and_get_alias(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {}}})
        from reach.config import set_alias, list_aliases
        set_alias("prod", "agent_abc")
        assert list_aliases()["prod"] == "agent_abc"

    def test_resolve_alias(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {"aliases": {"prod": "agent_abc"}}}})
        from reach.config import resolve_agent
        assert resolve_agent("prod") == "agent_abc"

    def test_resolve_unknown_returns_input(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {}}})
        from reach.config import resolve_agent
        assert resolve_agent("agent_xyz") == "agent_xyz"

    def test_remove_existing_alias(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {"aliases": {"prod": "agent_abc"}}}})
        from reach.config import remove_alias, list_aliases
        result = remove_alias("prod")
        assert result is True
        assert "prod" not in list_aliases()

    def test_remove_nonexistent_alias_returns_false(self, tmp_path):
        _write(tmp_path, {"active_profile": "default", "profiles": {"default": {}}})
        from reach.config import remove_alias
        assert remove_alias("nonexistent") is False

    def test_list_aliases_empty(self):
        from reach.config import list_aliases
        assert list_aliases() == {}

    def test_list_profiles(self, tmp_path):
        _write(tmp_path, {"active_profile": "a", "profiles": {"a": {}, "b": {}}})
        from reach.config import list_profiles
        assert sorted(list_profiles()) == ["a", "b"]
