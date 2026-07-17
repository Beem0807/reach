import re
from typing import Optional

_TAG_RE = re.compile(r'^[a-z0-9_-]+:[a-z0-9_.-]+$')


def validate_tags(tags: list) -> Optional[str]:
    """Return an error message if any tag is invalid, else None."""
    if not isinstance(tags, list):
        return "tags must be a list"
    invalid = [t for t in tags if not isinstance(t, str) or not _TAG_RE.match(t)]
    if invalid:
        return f"invalid tag(s): {invalid} - format must be key:value using lowercase letters, digits, hyphens, or underscores"
    return None


def former_fleet_tag(name_or_id: str) -> str:
    """A single provenance tag for an agent detached from a fleet. On detach the fleet's
    operational tags are dropped (they'd otherwise wrongly match `--tag` fan-outs); this
    marks where the agent came from so it stays identifiable and groupable. Tags are
    lowercase `key:value`, and a fleet-id isn't tag-legal (it has uppercase), so the
    fleet name (unique per tenant) is slugified into the value - the exact fleet-id is
    kept in the agent's history entry on detach."""
    slug = re.sub(r"[^a-z0-9_.-]+", "-", (name_or_id or "").lower()).strip("-._")
    return f"oldfleet:{slug or 'unknown'}"
