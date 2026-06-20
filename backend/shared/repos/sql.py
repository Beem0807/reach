import os
from typing import Optional

from sqlalchemy import JSON, Boolean, Column, Integer, String, Text, create_engine, delete as sa_delete, select, update
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from shared.policy import compute_access_level

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class _Base(DeclarativeBase):
    pass


class _Agent(_Base):
    __tablename__ = "agents"
    agent_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="CREATED")
    hostname = Column(String)
    agent_version = Column(String)
    machine_fingerprint = Column(String)
    mode = Column(String, nullable=False, default="wild")
    running_as_root = Column(String)  # "true" | "false" | None until first sync
    agent_token_hash = Column(String)
    install_token_hash = Column(String)
    install_token_expires_at = Column(Integer)
    claimed_at = Column(String)
    last_heartbeat_at = Column(String)
    active_until = Column(Integer)
    token_issued_at = Column(String)
    rotation_requested = Column(Boolean, default=False)
    type = Column(String, default="manual")
    fleet_id = Column(String)
    tags = Column(JSON, default=list)
    created_at = Column(String)


class _Approval(_Base):
    __tablename__ = "approvals"
    approval_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    command = Column(Text)
    requested_by = Column(String)
    requester_name = Column(String)
    job_id = Column(String)
    status = Column(String, nullable=False)
    expires_at = Column(String, nullable=True)
    created_at = Column(String, index=True)
    reviewed_at = Column(String)
    reviewed_by = Column(String)


class _Tenant(_Base):
    __tablename__ = "tenants"
    tenant_id = Column(String, primary_key=True)
    name = Column(String)
    created_at = Column(String)


class _User(_Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    name = Column(String)
    created_at = Column(String)
    allowed_agent_ids = Column(JSON)
    allowed_fleet_ids = Column(JSON)


class _Job(_Base):
    __tablename__ = "jobs"
    job_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    command = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    mode = Column(String)
    is_write = Column(Boolean, nullable=True)
    exit_code = Column(Integer)
    stdout = Column(Text)
    stderr = Column(Text)
    duration_ms = Column(Integer)
    created_by = Column(String)
    created_at = Column(String, index=True)
    started_at = Column(String)
    completed_at = Column(String)
    expires_at = Column(Integer)


def _to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    return {c.key: getattr(row, c.key) for c in row.__mapper__.columns}


def _enrich_agent(d: Optional[dict]) -> Optional[dict]:
    if d is None:
        return None
    root = d.get("running_as_root") == "true"
    d["access_level"] = compute_access_level(d.get("mode", "wild"), root)
    return d


class AgentRepo:
    def get(self, agent_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _enrich_agent(_to_dict(db.get(_Agent, agent_id)))

    def claim(self, agent_id: str, fields: dict) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent)
                .where(_Agent.agent_id == agent_id, _Agent.status == "CREATED")
                .values(
                    status="ACTIVE",
                    agent_token_hash=fields["agent_token_hash"],
                    machine_fingerprint=fields["machine_fingerprint"],
                    hostname=fields["hostname"],
                    agent_version=fields["agent_version"],
                    claimed_at=fields["claimed_at"],
                    active_until=fields["active_until"],
                    last_heartbeat_at=fields["claimed_at"],
                    token_issued_at=fields["token_issued_at"],
                )
            )
            db.commit()

    def update_heartbeat(self, agent_id: str, reactivate: bool, now_iso: str, agent_version: Optional[str] = None, running_as_root: Optional[bool] = None) -> None:
        values: dict = {"last_heartbeat_at": now_iso}
        if reactivate:
            values["status"] = "ACTIVE"
        if agent_version:
            values["agent_version"] = agent_version
        if running_as_root is not None:
            values["running_as_root"] = "true" if running_as_root else "false"
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(**values))
            db.commit()

    def set_active_until(self, agent_id: str, active_until: int) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(active_until=active_until)
            )
            db.commit()

    def list_by_tenant(self, tenant_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(select(_Agent).where(_Agent.tenant_id == tenant_id)).scalars().all()
            return [_enrich_agent(_to_dict(r)) for r in rows]

    def mark_inactive(self, agent_id: str) -> bool:
        with SessionLocal() as db:
            result = db.execute(
                update(_Agent)
                .where(_Agent.agent_id == agent_id, _Agent.status == "ACTIVE")
                .values(status="INACTIVE")
            )
            db.commit()
            return result.rowcount > 0

    def create(self, agent: dict) -> None:
        with SessionLocal() as db:
            db.add(_Agent(**agent))
            db.commit()

    def update_policy(self, agent_id: str, mode: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent)
                .where(_Agent.agent_id == agent_id)
                .values(mode=mode)
            )
            db.commit()

    def scan_stale_active(self, cutoff_iso: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(
                select(_Agent).where(
                    _Agent.status == "ACTIVE",
                    _Agent.last_heartbeat_at.isnot(None),
                    _Agent.last_heartbeat_at < cutoff_iso,
                )
            ).scalars().all()
            return [_enrich_agent(_to_dict(r)) for r in rows]

    def reissue_install_token(self, agent_id: str, install_token_hash: str, expires_at: int) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent)
                .where(_Agent.agent_id == agent_id)
                .values(
                    status="CREATED",
                    install_token_hash=install_token_hash,
                    install_token_expires_at=expires_at,
                    agent_token_hash=None,
                    machine_fingerprint=None,
                    claimed_at=None,
                )
            )
            db.commit()

    def update_agent_token_hash(self, agent_id: str, token_hash: str, token_issued_at: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(
                    agent_token_hash=token_hash,
                    token_issued_at=token_issued_at,
                    rotation_requested=False,
                )
            )
            db.commit()

    def request_rotation(self, agent_id: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(rotation_requested=True))
            db.commit()

    def set_status(self, agent_id: str, status: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(status=status))
            db.commit()

    def delete(self, agent_id: str) -> None:
        with SessionLocal() as db:
            db.execute(sa_delete(_Agent).where(_Agent.agent_id == agent_id))
            db.commit()

    def set_tags(self, agent_id: str, tags: list) -> None:
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(tags=tags))
            db.commit()


class JobRepo:
    def create(self, job: dict) -> None:
        with SessionLocal() as db:
            db.add(_Job(**job))
            db.commit()

    def get(self, job_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Job, job_id))

    def set_running(self, job_id: str, started_at: str) -> bool:
        with SessionLocal() as db:
            result = db.execute(
                update(_Job)
                .where(_Job.job_id == job_id, _Job.status == "PENDING")
                .values(status="RUNNING", started_at=started_at)
            )
            db.commit()
            return result.rowcount > 0

    def set_result(self, job_id: str, fields: dict) -> None:
        with SessionLocal() as db:
            db.execute(update(_Job).where(_Job.job_id == job_id).values(**fields))
            db.commit()

    def mark_expired(self, job_id: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_Job).where(_Job.job_id == job_id).values(status="EXPIRED"))
            db.commit()

    def expire_stale(self, pending_cutoff_iso: str) -> int:
        with SessionLocal() as db:
            result = db.execute(
                update(_Job)
                .where(_Job.status == "PENDING", _Job.created_at < pending_cutoff_iso)
                .values(status="EXPIRED")
            )
            db.commit()
            return result.rowcount

    def delete_stale(self, before_iso: str) -> int:
        from sqlalchemy import delete as sql_delete
        terminal = ("SUCCEEDED", "FAILED", "REJECTED", "EXPIRED")
        with SessionLocal() as db:
            result = db.execute(
                sql_delete(_Job).where(
                    _Job.status.in_(terminal),
                    _Job.created_at < before_iso,
                )
            )
            db.commit()
            return result.rowcount

    def get_pending_for_agent(self, agent_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(
                select(_Job)
                .where(_Job.agent_id == agent_id, _Job.status == "PENDING")
                .order_by(_Job.created_at)
                .limit(1)
            ).scalars().all()
            return [_to_dict(r) for r in rows]

    def list_admin(self, agent_id: Optional[str], tenant_id: Optional[str], created_by: Optional[str], limit: int, cursor: Optional[str] = None) -> list:
        with SessionLocal() as db:
            stmt = select(_Job).order_by(_Job.created_at.desc()).limit(limit)
            if tenant_id:
                stmt = stmt.where(_Job.tenant_id == tenant_id)
            if agent_id:
                stmt = stmt.where(_Job.agent_id == agent_id)
            if created_by:
                stmt = stmt.where(_Job.created_by == created_by)
            if cursor:
                stmt = stmt.where(_Job.created_at < cursor)
            rows = db.execute(stmt).scalars().all()
            return [_to_dict(r) for r in rows]

    def list_by_tenant(self, tenant_id: str, agent_id: Optional[str], limit: int, created_by: Optional[str] = None, cursor: Optional[str] = None) -> list:
        with SessionLocal() as db:
            stmt = (
                select(_Job)
                .where(_Job.tenant_id == tenant_id)
                .order_by(_Job.created_at.desc())
                .limit(limit)
            )
            if agent_id:
                stmt = stmt.where(_Job.agent_id == agent_id)
            if created_by:
                stmt = stmt.where(_Job.created_by == created_by)
            if cursor:
                stmt = stmt.where(_Job.created_at < cursor)
            rows = db.execute(stmt).scalars().all()
            return [_to_dict(r) for r in rows]


class TenantRepo:
    def get(self, tenant_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Tenant, tenant_id))

    def create(self, tenant: dict) -> None:
        with SessionLocal() as db:
            db.add(_Tenant(**tenant))
            db.commit()

    def list_all(self) -> list:
        with SessionLocal() as db:
            rows = db.execute(select(_Tenant)).scalars().all()
            return [_to_dict(r) for r in rows]


class UserRepo:
    def get(self, user_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_User, user_id))

    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        with SessionLocal() as db:
            row = db.execute(select(_User).where(_User.token_hash == token_hash)).scalar_one_or_none()
            return _to_dict(row)

    def create(self, user: dict) -> None:
        with SessionLocal() as db:
            db.add(_User(**user))
            db.commit()

    def list_by_tenant(self, tenant_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(select(_User).where(_User.tenant_id == tenant_id)).scalars().all()
            return [_to_dict(r) for r in rows]

    def update_token_hash(self, user_id: str, token_hash: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(token_hash=token_hash))
            db.commit()

    def delete(self, user_id: str) -> None:
        with SessionLocal() as db:
            db.execute(sa_delete(_User).where(_User.user_id == user_id))
            db.commit()

    def set_allowed_agents(self, user_id: str, agent_ids: list) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(allowed_agent_ids=agent_ids))
            db.commit()

    def remove_agent_from_all_users(self, agent_id: str, tenant_id: str) -> None:
        with SessionLocal() as db:
            rows = db.execute(
                select(_User).where(
                    _User.tenant_id == tenant_id,
                    _User.allowed_agent_ids.isnot(None),
                )
            ).scalars().all()
            for row in rows:
                current = row.allowed_agent_ids or []
                if agent_id in current:
                    db.execute(
                        update(_User)
                        .where(_User.user_id == row.user_id)
                        .values(allowed_agent_ids=[a for a in current if a != agent_id])
                    )
            db.commit()


class ApprovalRepo:
    def create(self, approval: dict) -> None:
        with SessionLocal() as db:
            db.add(_Approval(**approval))
            db.commit()

    def get(self, approval_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Approval, approval_id))

    def list_by_agent(self, agent_id: str, status: Optional[str] = None, requested_by: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> list:
        from sqlalchemy import desc
        with SessionLocal() as db:
            stmt = select(_Approval).where(_Approval.agent_id == agent_id)
            if status is not None:
                stmt = stmt.where(_Approval.status == status)
            if requested_by is not None:
                stmt = stmt.where(_Approval.requested_by == requested_by)
            stmt = stmt.order_by(desc(_Approval.created_at))
            if cursor is not None:
                stmt = stmt.where(_Approval.created_at < cursor)
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = db.execute(stmt).scalars().all()
            results = [_to_dict(r) for r in rows]
            if status == "approved":
                results = self._lazy_expire(results, db)
            return results

    def list_by_tenant(self, tenant_id: str, agent_id: Optional[str] = None, status: Optional[str] = None, requested_by: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> list:
        from sqlalchemy import desc
        with SessionLocal() as db:
            stmt = select(_Approval).where(_Approval.tenant_id == tenant_id)
            if agent_id is not None:
                stmt = stmt.where(_Approval.agent_id == agent_id)
            if status is not None:
                stmt = stmt.where(_Approval.status == status)
            if requested_by is not None:
                stmt = stmt.where(_Approval.requested_by == requested_by)
            stmt = stmt.order_by(desc(_Approval.created_at))
            if cursor is not None:
                stmt = stmt.where(_Approval.created_at < cursor)
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = db.execute(stmt).scalars().all()
            results = [_to_dict(r) for r in rows]
            if status == "approved":
                results = self._lazy_expire(results, db)
                results = self._dedup_by_command(results, db)
            return results

    @staticmethod
    def _lazy_expire(records: list, db) -> list:
        now = _iso()
        stale_ids = [r["approval_id"] for r in records if r.get("expires_at") and r["expires_at"] <= now]
        if stale_ids:
            db.execute(
                update(_Approval)
                .where(_Approval.approval_id.in_(stale_ids))
                .where(_Approval.status == "approved")
                .values(status="expired")
            )
            db.commit()
        return [r for r in records if r["approval_id"] not in set(stale_ids)]

    @staticmethod
    def _dedup_by_command(records: list, db) -> list:
        from collections import defaultdict
        from sqlalchemy import delete as sql_delete
        by_command: dict = defaultdict(list)
        for r in records:
            by_command[r["command"]].append(r)
        kept = []
        to_delete_ids = []
        for recs in by_command.values():
            if len(recs) == 1:
                kept.append(recs[0])
                continue
            permanent = [r for r in recs if not r.get("expires_at")]
            timed = sorted(
                [r for r in recs if r.get("expires_at")],
                key=lambda r: r["expires_at"],
                reverse=True,
            )
            if permanent:
                keeper = permanent[0]
                to_delete_ids.extend(r["approval_id"] for r in permanent[1:])
                to_delete_ids.extend(r["approval_id"] for r in timed)
            else:
                keeper = timed[0]
                to_delete_ids.extend(r["approval_id"] for r in timed[1:])
            kept.append(keeper)
        if to_delete_ids:
            db.execute(sql_delete(_Approval).where(_Approval.approval_id.in_(to_delete_ids)))
            db.commit()
        return kept

    def exists_pending(self, agent_id: str, command: str) -> bool:
        with SessionLocal() as db:
            row = db.execute(
                select(_Approval).where(
                    _Approval.agent_id == agent_id,
                    _Approval.command == command,
                    _Approval.status == "pending",
                ).limit(1)
            ).scalar_one_or_none()
            return row is not None

    def update_status(self, approval_id: str, status: str, reviewed_at: str, reviewed_by: str, expires_at: Optional[str] = None) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Approval)
                .where(_Approval.approval_id == approval_id)
                .values(status=status, reviewed_at=reviewed_at, reviewed_by=reviewed_by, expires_at=expires_at)
            )
            db.commit()

    def mark_expired(self, now_iso: str) -> int:
        with SessionLocal() as db:
            result = db.execute(
                update(_Approval)
                .where(_Approval.status == "approved")
                .where(_Approval.expires_at.isnot(None))
                .where(_Approval.expires_at < now_iso)
                .values(status="expired")
            )
            db.commit()
            return result.rowcount

    def delete_stale(self, before_iso: str) -> int:
        from sqlalchemy import delete as sql_delete, or_, and_
        with SessionLocal() as db:
            result = db.execute(
                sql_delete(_Approval).where(
                    or_(
                        and_(_Approval.status == "denied", _Approval.reviewed_at < before_iso),
                        and_(_Approval.status == "expired", _Approval.expires_at < before_iso),
                    )
                )
            )
            db.commit()
            return result.rowcount

    def delete(self, approval_id: str) -> None:
        from sqlalchemy import delete as sql_delete
        with SessionLocal() as db:
            db.execute(sql_delete(_Approval).where(_Approval.approval_id == approval_id))
            db.commit()
