import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, Column, Integer, String, Text, UniqueConstraint, create_engine, delete as sa_delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from shared.exceptions import NameTakenError
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
    # Both hashes are looked up directly (credential-only auth): the agent never
    # sends its agent_id - the install token identifies it at claim, the agent
    # token on every call after. Unique so a hash maps to exactly one agent.
    agent_token_hash = Column(String, index=True, unique=True)
    install_token_hash = Column(String, index=True, unique=True)
    install_token_expires_at = Column(Integer)
    claimed_at = Column(String)
    last_heartbeat_at = Column(String)
    active_until = Column(Integer)
    token_issued_at = Column(String)
    rotation_requested = Column(Boolean, default=False)
    type = Column(String, default="host")
    fleet_id = Column(String)
    tags = Column(JSON, default=list)
    created_at = Column(String)
    grant_service_mgmt = Column(Boolean, default=False)
    grant_docker = Column(Boolean, default=False)
    service_mgmt_detected = Column(Boolean)
    docker_detected = Column(Boolean)
    # k8s effective RBAC (self-reported via SelfSubjectRulesReview). The raw rule
    # set, its hash, and the hash the operator last acknowledged. Drift = the
    # current hash differs from the acknowledged one (computed in _enrich_agent).
    k8s_permissions = Column(JSON)
    k8s_permissions_hash = Column(String)
    k8s_permissions_acked_hash = Column(String)
    # The snapshot captured at acknowledge time, so the console can diff current vs
    # acknowledged and show *what* drifted. Null until the first acknowledge.
    k8s_permissions_acked = Column(JSON)


class _Approval(_Base):
    __tablename__ = "approvals"
    approval_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    command = Column(Text)
    # Structured rule for k8s agents ({verb, resource, namespace, name}); None for
    # host agents, which match on the command text above. none_as_null keeps host
    # rows as SQL NULL (not JSON 'null') so the kind filter's IS NULL works.
    k8s_rule = Column(JSON(none_as_null=True))
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
    name = Column(String, unique=True)
    status = Column(String, default='ACTIVE', server_default='ACTIVE')
    created_at = Column(String)


class _User(_Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint('tenant_id', 'username', name='ix_users_tenant_username'),)
    user_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String)
    username = Column(String)
    password_hash = Column(String)
    role = Column(String)               # TENANT_ADMIN | TENANT_USER | None (legacy)
    must_reset_password = Column(Boolean, default=False)
    disabled_at = Column(String)
    last_login_at = Column(String)
    created_at = Column(String)
    allowed_agent_ids = Column(JSON)
    allowed_fleet_ids = Column(JSON)
    status = Column(String, default='ACTIVE', server_default='ACTIVE')


class _ApiToken(_Base):
    __tablename__ = "api_tokens"
    token_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    name = Column(String)
    status = Column(String, default='ACTIVE', server_default='ACTIVE')
    created_at = Column(String)
    last_used_at = Column(String)
    revoked_at = Column(String)


class _AgentHistory(_Base):
    __tablename__ = "agent_history"
    history_id = Column(String, primary_key=True)
    agent_id = Column(String, nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    from_status = Column(String)
    to_status = Column(String, nullable=False)
    triggered_by = Column(String)
    note = Column(String)
    created_at = Column(String, nullable=False, index=True)


class _AuditLog(_Base):
    __tablename__ = "audit_logs"
    log_id = Column(String, primary_key=True)
    tenant_id = Column(String, index=True)
    actor_id = Column(String, index=True)
    actor_name = Column(String)
    actor_role = Column(String)
    action = Column(String, nullable=False)
    resource_type = Column(String)
    resource_id = Column(String)
    event_metadata = Column(JSON)
    ip_address = Column(String)
    created_at = Column(String, nullable=False, index=True)


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


def _iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    return {c.key: getattr(row, c.key) for c in row.__mapper__.columns}


def _enrich_agent(d: Optional[dict]) -> Optional[dict]:
    if d is None:
        return None
    root = d.get("running_as_root") == "true"
    d["access_level"] = compute_access_level(
        d.get("mode", "wild"), root,
        grant_docker=bool(d.get("grant_docker")),
        grant_service_mgmt=bool(d.get("grant_service_mgmt")),
        docker_detected=bool(d.get("docker_detected")),
        service_mgmt_detected=bool(d.get("service_mgmt_detected")),
    )
    # k8s permission drift: the agent has reported permissions whose hash differs
    # from the one the operator acknowledged (or has never been acknowledged).
    cur = d.get("k8s_permissions_hash")
    d["k8s_permissions_drift"] = bool(cur) and cur != d.get("k8s_permissions_acked_hash")
    return d


class AgentRepo:
    def get(self, agent_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _enrich_agent(_to_dict(db.get(_Agent, agent_id)))

    def get_by_install_token_hash(self, install_token_hash: str) -> Optional[dict]:
        if not install_token_hash:
            return None
        with SessionLocal() as db:
            row = db.execute(
                select(_Agent).where(_Agent.install_token_hash == install_token_hash)
            ).scalar_one_or_none()
            return _enrich_agent(_to_dict(row)) if row else None

    def get_by_agent_token_hash(self, agent_token_hash: str) -> Optional[dict]:
        if not agent_token_hash:
            return None
        with SessionLocal() as db:
            row = db.execute(
                select(_Agent).where(_Agent.agent_token_hash == agent_token_hash)
            ).scalar_one_or_none()
            return _enrich_agent(_to_dict(row)) if row else None

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
                    # The agent reports its environment at claim time: "k8s" when
                    # running in a cluster, else "host". The agent record's id is
                    # the cluster's identity - no separate cluster_id needed.
                    type=fields.get("type") or "host",
                )
            )
            db.commit()

    def update_heartbeat(
        self, agent_id: str, reactivate: bool, now_iso: str,
        agent_version: Optional[str] = None,
        running_as_root: Optional[bool] = None,
        docker_detected: Optional[bool] = None,
        service_mgmt_detected: Optional[bool] = None,
    ) -> None:
        values: dict = {"last_heartbeat_at": now_iso}
        if reactivate:
            values["status"] = "ACTIVE"
        if agent_version:
            values["agent_version"] = agent_version
        if running_as_root is not None:
            values["running_as_root"] = "true" if running_as_root else "false"
        if docker_detected is not None:
            values["docker_detected"] = docker_detected
        if service_mgmt_detected is not None:
            values["service_mgmt_detected"] = service_mgmt_detected
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(**values))
            db.commit()

    def set_k8s_permissions(self, agent_id: str, permissions: dict, perm_hash: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(
                    k8s_permissions=permissions, k8s_permissions_hash=perm_hash
                )
            )
            db.commit()

    def acknowledge_k8s_permissions(self, agent_id: str, perm_hash: str, acked_permissions: Optional[dict] = None) -> None:
        # Postgres stores the acknowledged snapshot in full (JSON column, no size
        # limit) so the console can diff current vs acknowledged.
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(
                    k8s_permissions_acked_hash=perm_hash,
                    k8s_permissions_acked=acked_permissions,
                )
            )
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

    def reissue_install_token(
        self, agent_id: str, install_token_hash: str, expires_at: int,
        grant_service_mgmt: bool = False, grant_docker: bool = False,
    ) -> None:
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
                    grant_service_mgmt=grant_service_mgmt,
                    grant_docker=grant_docker,
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

    def update_grants(self, agent_id: str, grant_docker: Optional[bool] = None, grant_service_mgmt: Optional[bool] = None) -> None:
        values: dict = {}
        if grant_docker is not None:
            values["grant_docker"] = grant_docker
        if grant_service_mgmt is not None:
            values["grant_service_mgmt"] = grant_service_mgmt
        if not values:
            return
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(**values))
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

    def get_by_name(self, name: str) -> Optional[dict]:
        with SessionLocal() as db:
            row = db.execute(select(_Tenant).where(_Tenant.name == name)).scalar_one_or_none()
            return _to_dict(row)

    def create(self, tenant: dict) -> None:
        try:
            with SessionLocal() as db:
                db.add(_Tenant(**tenant))
                db.commit()
        except IntegrityError:
            raise NameTakenError(tenant.get("name", ""))

    def list_all(self) -> list:
        with SessionLocal() as db:
            rows = db.execute(select(_Tenant)).scalars().all()
            return [_to_dict(r) for r in rows]

    def set_status(self, tenant_id: str, status: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_Tenant).where(_Tenant.tenant_id == tenant_id).values(status=status))
            db.commit()

    def delete_cascade(self, tenant_id: str) -> None:
        with SessionLocal() as db:
            db.execute(sa_delete(_Approval).where(_Approval.tenant_id == tenant_id))
            db.execute(sa_delete(_Job).where(_Job.tenant_id == tenant_id))
            db.execute(sa_delete(_User).where(_User.tenant_id == tenant_id))
            db.execute(sa_delete(_Agent).where(_Agent.tenant_id == tenant_id))
            db.execute(sa_delete(_Tenant).where(_Tenant.tenant_id == tenant_id))
            db.commit()


class UserRepo:
    def get(self, user_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_User, user_id))

    def create(self, user: dict) -> None:
        try:
            with SessionLocal() as db:
                db.add(_User(**user))
                db.commit()
        except IntegrityError:
            raise NameTakenError(user.get("username", ""))

    def list_by_tenant(self, tenant_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(select(_User).where(_User.tenant_id == tenant_id)).scalars().all()
            return [_to_dict(r) for r in rows]

    def revoke(self, user_id: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(status='REVOKED'))
            db.commit()

    def delete(self, user_id: str) -> None:
        with SessionLocal() as db:
            db.execute(sa_delete(_User).where(_User.user_id == user_id))
            db.commit()

    def set_allowed_agents(self, user_id: str, agent_ids: list) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(allowed_agent_ids=agent_ids))
            db.commit()

    def get_by_username(self, tenant_id: str, username: str) -> Optional[dict]:
        with SessionLocal() as db:
            row = db.execute(
                select(_User).where(
                    _User.tenant_id == tenant_id,
                    _User.username == username,
                    _User.status != 'REVOKED',
                )
            ).scalar_one_or_none()
            return _to_dict(row)

    def update_password(self, user_id: str, password_hash: str, must_reset: bool = False) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_User).where(_User.user_id == user_id)
                .values(password_hash=password_hash, must_reset_password=must_reset)
            )
            db.commit()

    def set_last_login(self, user_id: str, now_iso: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(last_login_at=now_iso))
            db.commit()

    def disable(self, user_id: str, now_iso: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_User).where(_User.user_id == user_id)
                .values(status='REVOKED', disabled_at=now_iso)
            )
            db.commit()

    def enable(self, user_id: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_User).where(_User.user_id == user_id)
                .values(status='ACTIVE', disabled_at=None)
            )
            db.commit()

    def set_role(self, user_id: str, role: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(role=role))
            db.commit()

    def update_name(self, user_id: str, name: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(name=name))
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

    def search_by_tenant(self, tenant_id: str, *, status: Optional[str] = None, agent_id: Optional[str] = None,
                         agent_ids: Optional[list] = None,
                         requested_by: Optional[str] = None, kind: Optional[str] = None, q: Optional[str] = None,
                         limit: int = 20, offset: int = 0) -> tuple:
        """Server-side search + pagination for the tenant/developer approvals views.

        Filters run in SQL: status, agent, requester, kind (host = no rule, k8s =
        has rule), and a case-insensitive LIKE over the command and requester. For
        the `approved` status the effective-list expire/dedup is applied before
        paginating, so `total` reflects what the user actually sees. Returns
        (page_items, total).
        """
        from sqlalchemy import desc, or_
        with SessionLocal() as db:
            stmt = select(_Approval).where(_Approval.tenant_id == tenant_id)
            if agent_id is not None:
                stmt = stmt.where(_Approval.agent_id == agent_id)
            if agent_ids is not None:
                # Scope to an explicit allow-list of agents (e.g. an agent-restricted
                # operator). An empty list matches nothing, so they see nothing.
                stmt = stmt.where(_Approval.agent_id.in_(agent_ids))
            if status is not None:
                stmt = stmt.where(_Approval.status == status)
            if requested_by is not None:
                stmt = stmt.where(_Approval.requested_by == requested_by)
            if kind == "k8s":
                stmt = stmt.where(_Approval.k8s_rule.isnot(None))
            elif kind == "host":
                stmt = stmt.where(_Approval.k8s_rule.is_(None))
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(
                    _Approval.command.ilike(like),
                    _Approval.requester_name.ilike(like),
                ))
            stmt = stmt.order_by(desc(_Approval.created_at))
            rows = db.execute(stmt).scalars().all()
            results = [_to_dict(r) for r in rows]
            if status == "approved":
                results = self._lazy_expire(results, db)
                results = self._dedup_by_command(results, db)
            total = len(results)
            page = results[offset: offset + limit] if limit else results[offset:]
            return page, total

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


class ApiTokenRepo:
    def create(self, token: dict) -> None:
        with SessionLocal() as db:
            db.add(_ApiToken(**token))
            db.commit()

    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        with SessionLocal() as db:
            row = db.execute(
                select(_ApiToken).where(_ApiToken.token_hash == token_hash, _ApiToken.status == 'ACTIVE')
            ).scalar_one_or_none()
            return _to_dict(row)

    def list_by_user(self, user_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(
                select(_ApiToken).where(_ApiToken.user_id == user_id)
                .order_by(_ApiToken.created_at.desc())
            ).scalars().all()
            return [_to_dict(r) for r in rows]

    def list_by_tenant(self, tenant_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(
                select(_ApiToken).where(_ApiToken.tenant_id == tenant_id)
                .order_by(_ApiToken.created_at.desc())
            ).scalars().all()
            return [_to_dict(r) for r in rows]

    def revoke(self, token_id: str, now_iso: str) -> bool:
        with SessionLocal() as db:
            result = db.execute(
                update(_ApiToken)
                .where(_ApiToken.token_id == token_id, _ApiToken.status == 'ACTIVE')
                .values(status='REVOKED', revoked_at=now_iso)
            )
            db.commit()
            return result.rowcount > 0

    def delete(self, token_id: str) -> None:
        """Hard-delete a token row (only meaningful after it has been revoked)."""
        with SessionLocal() as db:
            db.execute(sa_delete(_ApiToken).where(_ApiToken.token_id == token_id))
            db.commit()

    def touch(self, token_id: str, now_iso: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_ApiToken).where(_ApiToken.token_id == token_id).values(last_used_at=now_iso))
            db.commit()

    def rename(self, token_id: str, name: str) -> bool:
        with SessionLocal() as db:
            result = db.execute(
                update(_ApiToken)
                .where(_ApiToken.token_id == token_id)
                .values(name=name)
            )
            db.commit()
            return result.rowcount > 0


class AgentHistoryRepo:
    def create(self, entry: dict) -> None:
        with SessionLocal() as db:
            db.add(_AgentHistory(**entry))
            db.commit()

    def list_by_agent(self, agent_id: str, limit: int = 50) -> list:
        with SessionLocal() as db:
            rows = db.execute(
                select(_AgentHistory)
                .where(_AgentHistory.agent_id == agent_id)
                .order_by(_AgentHistory.created_at.desc())
                .limit(limit)
            ).scalars().all()
            return [_to_dict(r) for r in rows]

    def delete_stale(self, before_iso: str) -> int:
        with SessionLocal() as db:
            result = db.execute(
                sa_delete(_AgentHistory).where(_AgentHistory.created_at < before_iso)
            )
            db.commit()
            return result.rowcount


def _audit_to_dict(row) -> Optional[dict]:
    d = _to_dict(row)
    if d is None:
        return None
    d["metadata"] = d.pop("event_metadata", None)
    return d


class AuditRepo:
    def create(self, entry: dict) -> None:
        with SessionLocal() as db:
            db.add(_AuditLog(**entry))
            db.commit()

    def list_platform(self, limit: int = 100, cursor: Optional[str] = None,
                      action: Optional[str] = None, actor: Optional[str] = None,
                      resource: Optional[str] = None, ip: Optional[str] = None,
                      since: Optional[str] = None, until: Optional[str] = None) -> list:
        """Platform-level audit events (tenant_id IS NULL or any)."""
        from sqlalchemy import desc
        with SessionLocal() as db:
            stmt = select(_AuditLog).order_by(desc(_AuditLog.created_at))
            if cursor:
                stmt = stmt.where(_AuditLog.created_at < cursor)
            if since:
                stmt = stmt.where(_AuditLog.created_at >= since)
            if until:
                stmt = stmt.where(_AuditLog.created_at <= until)
            if action:
                stmt = stmt.where(_AuditLog.action == action)
            if actor:
                stmt = stmt.where(_AuditLog.actor_name.ilike(f"%{actor}%"))
            if resource:
                stmt = stmt.where(_AuditLog.resource_id.ilike(f"%{resource}%"))
            if ip:
                stmt = stmt.where(_AuditLog.ip_address.ilike(f"%{ip}%"))
            stmt = stmt.limit(limit)
            return [_audit_to_dict(r) for r in db.execute(stmt).scalars().all()]

    def list_by_tenant(self, tenant_id: str, limit: int = 100, cursor: Optional[str] = None,
                       action: Optional[str] = None, actor: Optional[str] = None,
                       resource: Optional[str] = None, ip: Optional[str] = None,
                       since: Optional[str] = None, until: Optional[str] = None) -> list:
        from sqlalchemy import desc
        with SessionLocal() as db:
            stmt = (
                select(_AuditLog)
                .where(_AuditLog.tenant_id == tenant_id)
                .order_by(desc(_AuditLog.created_at))
            )
            if cursor:
                stmt = stmt.where(_AuditLog.created_at < cursor)
            if since:
                stmt = stmt.where(_AuditLog.created_at >= since)
            if until:
                stmt = stmt.where(_AuditLog.created_at <= until)
            if action:
                stmt = stmt.where(_AuditLog.action == action)
            if actor:
                stmt = stmt.where(_AuditLog.actor_name.ilike(f"%{actor}%"))
            if resource:
                stmt = stmt.where(_AuditLog.resource_id.ilike(f"%{resource}%"))
            if ip:
                stmt = stmt.where(_AuditLog.ip_address.ilike(f"%{ip}%"))
            stmt = stmt.limit(limit)
            return [_audit_to_dict(r) for r in db.execute(stmt).scalars().all()]

    def delete_stale(self, before_iso: str) -> int:
        with SessionLocal() as db:
            result = db.execute(
                sa_delete(_AuditLog).where(_AuditLog.created_at < before_iso)
            )
            db.commit()
            return result.rowcount
