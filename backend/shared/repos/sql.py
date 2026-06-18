import os
from typing import Optional

from sqlalchemy import JSON, Column, Integer, String, Text, create_engine, delete as sa_delete, select, update
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

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
    approved_commands = Column(JSON, default=list)
    agent_token_hash = Column(String)
    install_token_hash = Column(String)
    install_token_expires_at = Column(Integer)
    claimed_at = Column(String)
    last_heartbeat_at = Column(String)
    active_until = Column(Integer)
    token_issued_at = Column(String)
    type = Column(String, default="manual")
    fleet_id = Column(String)
    tags = Column(JSON, default=list)
    created_at = Column(String)


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


class AgentRepo:
    def get(self, agent_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Agent, agent_id))

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

    def update_heartbeat(self, agent_id: str, reactivate: bool, now_iso: str, agent_version: Optional[str] = None) -> None:
        values: dict = {"last_heartbeat_at": now_iso}
        if reactivate:
            values["status"] = "ACTIVE"
        if agent_version:
            values["agent_version"] = agent_version
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
            return [_to_dict(r) for r in rows]

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

    def update_policy(self, agent_id: str, mode: str, approved_commands: list) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent)
                .where(_Agent.agent_id == agent_id)
                .values(mode=mode, approved_commands=approved_commands)
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
            return [_to_dict(r) for r in rows]

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
                )
            )
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
