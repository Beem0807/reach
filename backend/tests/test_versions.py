"""Tests for shared.versions - version discovery, ordering and shell-safe validation."""
import json
from unittest.mock import MagicMock, patch

import shared.versions as versions


class TestValidVersion:
    def test_accepts_plain_semver(self):
        assert versions.valid_version("0.9.4") == "0.9.4"
        assert versions.valid_version("10.2.13") == "10.2.13"

    def test_accepts_prerelease(self):
        assert versions.valid_version("1.2.3-rc.1") == "1.2.3-rc.1"

    def test_latest_and_empty_are_none(self):
        assert versions.valid_version("latest") is None
        assert versions.valid_version("") is None
        assert versions.valid_version(None) is None

    def test_rejects_shell_metacharacters(self):
        # These reach a shell command, so anything non-semver must be refused.
        for bad in ("0.9.4; rm -rf /", "$(whoami)", "0.9.4 && x", "v0.9.4", "latest/../x", "0.9"):
            assert versions.valid_version(bad) is None


class TestSorting:
    def test_sorted_desc_and_deduped(self):
        assert versions._sorted_desc(["0.9.1", "0.9.4", "0.9.4", "0.10.0", "junk"]) == ["0.10.0", "0.9.4", "0.9.1"]

    def test_numeric_not_lexical_order(self):
        assert versions._sorted_desc(["0.2.0", "0.10.0", "0.9.0"]) == ["0.10.0", "0.9.0", "0.2.0"]


class TestHostDiscovery:
    def _resp(self, payload):
        r = MagicMock()
        r.read.return_value = json.dumps(payload).encode()
        r.__enter__.return_value = r
        r.__exit__.return_value = False
        return r

    def test_reads_versions_json_over_http(self):
        versions._cache.clear()
        with patch("urllib.request.urlopen", return_value=self._resp(["0.9.1", "0.9.4"])):
            assert versions.available_versions("host") == ["0.9.4", "0.9.1"]

    def test_accepts_wrapped_object(self):
        versions._cache.clear()
        with patch("urllib.request.urlopen", return_value=self._resp({"versions": ["1.0.0"]})):
            assert versions.available_versions("host") == ["1.0.0"]

    def test_empty_when_unreachable(self):
        versions._cache.clear()
        with patch("urllib.request.urlopen", side_effect=OSError("no route")):
            assert versions.available_versions("host") == []


class TestChartDiscovery:
    def _resp(self, text):
        r = MagicMock()
        r.read.return_value = text.encode()
        r.__enter__.return_value = r
        r.__exit__.return_value = False
        return r

    _INDEX = """
apiVersion: v1
entries:
  reach-agent:
  - apiVersion: v2
    appVersion: "0.1.0"
    version: 0.1.0
  - apiVersion: v2
    appVersion: "0.2.0"
    version: 0.2.0
"""

    def test_parses_index_yaml_versions(self):
        versions._cache.clear()
        with patch("urllib.request.urlopen", return_value=self._resp(self._INDEX)):
            assert versions.available_versions("k8s") == ["0.2.0", "0.1.0"]

    def test_empty_when_unreachable(self):
        versions._cache.clear()
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            assert versions.available_versions("k8s") == []
