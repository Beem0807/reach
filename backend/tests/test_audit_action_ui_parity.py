"""Guardrail: every audit action the backend emits must be registered in the UI's
AuditLogsPage ACTION_COLOR map, which drives both the row colour and the audit-log
filter dropdown. Without this, a new fleet/agent action would be un-filterable and
render as an unstyled grey chip. Keep this in sync when adding audit actions."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
AUDIT_LOGS_PAGE = REPO_ROOT / "ui" / "src" / "pages" / "AuditLogsPage.tsx"

# Actions built from a runtime value (e.g. f"approval.{new_status}") that can't be
# found as string literals - enumerate their expansions explicitly.
DYNAMIC_ACTIONS = {"approval.approved", "approval.denied", "approval.expired"}

# First string literal passed to audit.write(...) or the _audit(...) wrapper,
# tolerating a newline/comment between the paren and the string.
_LITERAL_RE = re.compile(
    r'(?:audit\.write|_audit)\(\s*(?:#[^\n]*\n\s*)?["\']([a-z_]+\.[a-z_]+)["\']'
)


def _backend_actions() -> set:
    actions = set(DYNAMIC_ACTIONS)
    for path in list((BACKEND / "handlers").rglob("*.py")) + list((BACKEND / "shared").rglob("*.py")):
        if "/tests/" in str(path):
            continue
        actions.update(_LITERAL_RE.findall(path.read_text()))
    return actions


def _ui_action_keys() -> set:
    text = AUDIT_LOGS_PAGE.read_text()
    block = re.search(r"const ACTION_COLOR[^{]*\{(.*?)\n\};", text, re.S)
    assert block, "Could not locate the ACTION_COLOR map in AuditLogsPage.tsx"
    return set(re.findall(r"['\"]([a-z_]+\.[a-z_]+)['\"]\s*:", block.group(1)))


def test_every_backend_audit_action_is_in_ui_dropdown():
    backend = _backend_actions()
    ui = _ui_action_keys()
    missing = backend - ui
    assert not missing, (
        "Audit actions emitted by the backend but missing from AuditLogsPage "
        f"ACTION_COLOR (dropdown + colour): {sorted(missing)}"
    )


def test_fleet_actions_are_registered():
    # Explicit coverage for the fleet lifecycle, since these were the gap.
    ui = _ui_action_keys()
    for action in ("fleet.created", "fleet.updated", "fleet.token_rotated",
                   "fleet.revoked", "fleet.deleted", "fleet.member_detached"):
        assert action in ui, f"{action} not registered in the audit UI"


def test_backend_action_extraction_is_nonempty():
    # Sanity: the regex actually finds the known-present actions.
    backend = _backend_actions()
    assert "agent.reaped" in backend
    assert "fleet.created" in backend
    assert "agent.deregistered" in backend
