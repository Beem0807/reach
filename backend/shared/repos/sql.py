import os
from typing import Optional

from sqlalchemy import JSON, Column, Integer, String, Text, create_engine, select, update
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
    created_at = Column(String)


class _Token(_Base):
    __tablename__ = "tenant_tokens"
    token_hash = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False)
    created_at = Column(String)


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
    created_at = Column(String, index=True)
    started_at = Column(String)
    completed_at = Column(String)
    expires_at = Column(Integer)


# Creates tables on first import — use Alembic for production migrations
_Base.metadata.create_all(engine)


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
                )
            )
            db.commit()

    def update_heartbeat(self, agent_id: str, reactivate: bool, now_iso: str) -> None:
        values: dict = {"last_heartbeat_at": now_iso}
        if reactivate:
            values["status"] = "ACTIVE"
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

    def get_pending_for_agent(self, agent_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(
                select(_Job)
                .where(_Job.agent_id == agent_id, _Job.status == "PENDING")
                .order_by(_Job.created_at)
                .limit(1)
            ).scalars().all()
            return [_to_dict(r) for r in rows]

    def list_by_tenant(self, tenant_id: str, agent_id: Optional[str], limit: int) -> list:
        with SessionLocal() as db:
            stmt = (
                select(_Job)
                .where(_Job.tenant_id == tenant_id)
                .order_by(_Job.created_at.desc())
                .limit(limit)
            )
            if agent_id:
                stmt = stmt.where(_Job.agent_id == agent_id)
            rows = db.execute(stmt).scalars().all()
            return [_to_dict(r) for r in rows]


class TokenRepo:
    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Token, token_hash))

    def create(self, token: dict) -> None:
        with SessionLocal() as db:
            db.add(_Token(**token))
            db.commit()
