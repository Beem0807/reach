"""Write audit log entries. All mutating actions should call write()."""
import secrets
from typing import Optional
from shared.response import _iso
from shared.store import audit_repo


def write(
    action: str,
    *,
    tenant_id: Optional[str] = None,
    actor_id: str = "platform_admin",
    actor_name: str = "Platform Admin",
    actor_role: str = "PLATFORM_ADMIN",
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    try:
        audit_repo.create({
            "log_id":        "log_" + secrets.token_hex(10),
            "tenant_id":     tenant_id,
            "actor_id":      actor_id,
            "actor_name":    actor_name,
            "actor_role":    actor_role,
            "action":        action,
            "resource_type": resource_type,
            "resource_id":   resource_id,
            "event_metadata": metadata or {},
            "ip_address":    ip_address,
            "created_at":    _iso(),
        })
    except Exception:
        pass  # audit failures must never break primary operations
