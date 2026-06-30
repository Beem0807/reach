"""Discover the agent / chart versions available to install, for the create UI.

Host agents install a versioned binary (``agent/<version>/install.sh``); k8s
agents install a Helm chart pinned by ``--version``. The create dropdown offers
"Latest" (the default) plus the concrete published versions. Both sources are
plain HTTP GETs of a published index file, so discovery works on any static host
(S3, a CDN, self-hosted nginx) with no cloud SDK or IAM. Newest-first:

  - k8s  -> the Helm repo ``index.yaml`` (the authoritative published index).
  - host -> ``agent/versions.json`` (a JSON list the release script maintains).

Discovery is cached briefly. If a source is unreachable (e.g. the local dev
stack serves no release index) or empty, we return an empty list - the create
dropdown then just shows "Latest" (its always-present default).
"""
import json
import logging
import os
import re
import time
import urllib.request
from typing import List, Optional

logger = logging.getLogger()

_S3_BASE = os.environ.get("RELEASES_S3_BASE", "https://reach-releases.s3.amazonaws.com")
_CHART_REPO_URL = os.environ.get("RELEASES_CHART_REPO", f"{_S3_BASE}/charts/reach-agent")

# A version string safe to interpolate into an install command. Strict on
# purpose: these values reach a shell (`helm --version X`, `curl .../agent/X/`).
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")

_CACHE_TTL = 300  # seconds
_cache: dict = {}  # agent_type -> (expires_at, [versions])


def valid_version(v: Optional[str]) -> Optional[str]:
    """Return v if it's a concrete, shell-safe version; None for latest/invalid."""
    if not v:
        return None
    v = v.strip()
    if v.lower() == "latest":
        return None
    return v if _VERSION_RE.match(v) else None


def _semver_key(v: str):
    # Best-effort newest-first ordering; numeric segments sort before text ones.
    out = []
    for p in re.split(r"[.-]", v):
        out.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return out


def _sorted_desc(versions) -> List[str]:
    uniq = {v for v in versions if _VERSION_RE.match(v)}
    return sorted(uniq, key=_semver_key, reverse=True)


def _chart_versions() -> List[str]:
    url = f"{_CHART_REPO_URL.rstrip('/')}/index.yaml"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:  # nosec - fixed release URL
            text = resp.read().decode("utf-8", "replace")
    except Exception as e:  # unreachable repo / non-200 / timeout -> just "Latest"
        logger.info("chart version discovery failed (%s): %s", url, e)
        return []
    # index.yaml lists each release as `    version: X` under entries.reach-agent.
    found = re.findall(r'(?m)^\s+version:\s*"?([0-9][^"\s]*)"?\s*$', text)
    return _sorted_desc(found)


def _host_versions() -> List[str]:
    url = f"{_S3_BASE.rstrip('/')}/agent/versions.json"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:  # nosec - fixed release URL
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:  # unreachable / non-200 / bad JSON / offline -> just "Latest"
        logger.info("host version discovery failed (%s): %s", url, e)
        return []
    # Accept either a bare list or {"versions": [...]}.
    if isinstance(data, dict):
        data = data.get("versions", [])
    found = [str(v) for v in data] if isinstance(data, list) else []
    return _sorted_desc(found)


def available_versions(agent_type: str) -> List[str]:
    """Concrete published versions for the type, newest-first. Cached ~5 min."""
    t = "k8s" if agent_type == "k8s" else "host"
    now = time.time()
    hit = _cache.get(t)
    if hit and hit[0] > now:
        return hit[1]
    versions = _chart_versions() if t == "k8s" else _host_versions()
    _cache[t] = (now + _CACHE_TTL, versions)
    return versions
