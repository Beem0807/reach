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
