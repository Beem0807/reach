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
    # A fleet member whose grants mismatch the fleet can be *reconciled* (host fixed)
    # or *accepted* as an intentional exception. When accepted, this stores the fleet
    # grant signature it was accepted against, so the mismatch stops being flagged -
    # and re-flags if the fleet grants change again. Null = no accepted exception.
    grants_exception = Column(String)
    # k8s effective RBAC (self-reported via SelfSubjectRulesReview). The raw rule
    # set, its hash, and the hash the operator last acknowledged. Drift = the
    # current hash differs from the acknowledged one (computed in _enrich_agent).
    k8s_permissions = Column(JSON)
    k8s_permissions_hash = Column(String)
    k8s_permissions_acked_hash = Column(String)
    # The snapshot captured at acknowledge time, so the console can diff current vs
    # acknowledged and show *what* drifted. Null until the first acknowledge.
    k8s_permissions_acked = Column(JSON)
    # The k8s agent's effective execution allowlist (kubectl + filters + any extras), as it
    # self-reports on sync. Lets the console warn/block when someone tries to approve a
    # non-kubectl command whose binary the agent won't run. Null until first k8s sync.
    k8s_allowed_binaries = Column(JSON)
    # Host agent's filesystem-sandbox (Landlock) capability as self-reported on sync:
    # "active" | "unavailable" | "unsupported". Null until first host sync.
    landlock_status = Column(String)
    # An admin acknowledged running readonly/approved WITHOUT the Landlock sandbox on this
    # agent (only meaningful when landlock_status="unavailable"). Sent back to the agent so it
    # runs unsandboxed instead of failing closed. Revocable.
    sandbox_ack = Column(Boolean, default=False)


class _Approval(_Base):
    __tablename__ = "approvals"
    approval_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    # An approval targets exactly one of: a standalone agent, or a fleet (which
    # applies to all its members). Fleet members never carry agent-scoped approvals.
    agent_id = Column(String, index=True)
    fleet_id = Column(String, index=True)
    command = Column(Text)
    # Structured rule for k8s agents ({verb, resource, namespace, name}); None for
    # host agents, which match on the command text above. none_as_null keeps host
    # rows as SQL NULL (not JSON 'null') so the kind filter's IS NULL works.
    k8s_rule = Column(JSON(none_as_null=True))
    # Structured rule for a host agent's structured exec ({bin, args[]} with "*"
    # positional wildcards); None for a freeform-command host approval.
    host_rule = Column(JSON(none_as_null=True))
    requested_by = Column(String)
    requester_name = Column(String)
    job_id = Column(String)
    status = Column(String, nullable=False)
    expires_at = Column(String, nullable=True)
    created_at = Column(String, index=True)
    reviewed_at = Column(String)
    reviewed_by = Column(String)


class _Fleet(_Base):
    __tablename__ = "fleets"
    __table_args__ = (UniqueConstraint('tenant_id', 'name', name='ix_fleets_tenant_name'),)
    fleet_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    # Fleets are host-only: members are host agents that enroll via the join token
    # (k8s already has one-agent-per-cluster identity, so it has no fleet concept).
    mode = Column(String, nullable=False, default="readonly")    # least-privilege default
    grant_service_mgmt = Column(Boolean, default=False)
    grant_docker = Column(Boolean, default=False)
    # Fleet-level acknowledgement that members may run readonly/approved WITHOUT the Landlock
    # kernel sandbox (for hosts on an old kernel or macOS). Members churn, so this can't be
    # per-agent - every member inherits it on sync. Audited + revocable, like the per-agent one.
    sandbox_ack = Column(Boolean, default=False)
    # Tags are set at the fleet level and inherited by every member; members can't
    # set their own. Editing them propagates to all current members.
    tags = Column(JSON, default=list)
    # Reusable join token: any number of agents enroll with it (unlike the
    # per-agent one-time install token). Looked up by hash directly, like
    # install_token_hash. prev_* keeps the previous token valid during a rotation
    # grace window so an autoscaler's launch/instance template can be updated without dropping joins.
    join_token_hash = Column(String, index=True, unique=True)
    prev_join_token_hash = Column(String, index=True)
    prev_join_token_expires_at = Column(Integer)
    status = Column(String, nullable=False, default='ACTIVE', server_default='ACTIVE')  # ACTIVE | REVOKED
    # Members are ephemeral (autoscaler cattle): reaped this many seconds after their last
    # heartbeat. None falls back to the global default in the cleanup job.
    reap_after_seconds = Column(Integer)
    # Blast-radius ceiling: the max members a single fan-out may hit. Null = deployment
    # default. Operator-set in the console; a hard cap, never overridable per-call.
    max_fanout = Column(Integer)
    # Advanced: fleet-level override of the tenant's staged-rollout policy for fleet runs.
    # {"read": {mode, on_failure}, "write": {...}} - a set read/write branch wins over the
    # tenant default (see shared.waves.resolve_policy).
    wave_policy = Column(JSON)
    created_at = Column(String)
    created_by = Column(String)


class _Tenant(_Base):
    __tablename__ = "tenants"
    tenant_id = Column(String, primary_key=True)
    name = Column(String, unique=True)
    status = Column(String, default='ACTIVE', server_default='ACTIVE')
    created_at = Column(String)
    # Per-tenant overrides for retention windows + the fan-out cap (see shared/settings).
    # Only keys the tenant set; missing keys fall back to the platform default.
    settings = Column(JSON)


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
    readwrite_agent_ids = Column(JSON)
    readwrite_fleet_ids = Column(JSON)
    # Write-capability overlay: among the agents this user can access, those whose
    # agent_id (or fleet_id) appears here are read-only - the user may run read
    # commands but not writes. None = read-write on everything accessible.
    readonly_agent_ids = Column(JSON)
    readonly_fleet_ids = Column(JSON)
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
    # Jobs from one fan-out share a run_id so they can be grouped as a "run".
    run_id = Column(String, index=True)
    # The tag a tag fan-out (POST /jobs/fanout) selected on, retained so a standalone
    # "run" can show which tag it targeted. Null for fleet fan-outs and single jobs.
    run_tag = Column(String)
    # The fleet a fleet fan-out (POST /fleets/{id}/jobs) targeted, stored so a fleet
    # "run" groups durably by the fleet - not by joining jobs back to member records,
    # which vanish when autoscaler members are reaped/detached. Null for tag/single jobs.
    run_fleet_id = Column(String)
    # Staged rollout: a job's wave index within its run. Wave 0 dispatches immediately
    # (PENDING); later waves are created HELD and released one wave at a time as the
    # prior wave completes. 0 for single-wave (non-staged) fan-outs and one-off jobs.
    wave = Column(Integer, default=0)
    command = Column(Text, nullable=False)
    # Structured exec: when set, the job runs this argv with execve (no shell); `command`
    # holds the display form. Null = the job runs `command` freeform under the shell -
    # reads (always freeform) and wild-mode writes.
    argv = Column(JSON(none_as_null=True))
    # PENDING (dispatchable) | HELD (staged, not yet released) | RUNNING | SUCCEEDED |
    # FAILED | REJECTED | EXPIRED | CANCELED. Agents only ever receive PENDING.
    status = Column(String, nullable=False, default="PENDING")
    mode = Column(String)
    is_write = Column(Boolean, nullable=True)
    exit_code = Column(Integer)
    stdout = Column(Text)
    stderr = Column(Text)
    # Output was capped (agent-side and/or on ingest) - a structured signal alongside the
    # inline [TRUNCATED] marker, so callers (CLI/UI/MCP) can flag it without string-matching.
    stdout_truncated = Column(Boolean, default=False)
    stderr_truncated = Column(Boolean, default=False)
    duration_ms = Column(Integer)
    created_by = Column(String)
    created_at = Column(String, index=True)
    started_at = Column(String)
    completed_at = Column(String)
    expires_at = Column(Integer)


class _Run(_Base):
    """A fan-out (fleet or tag) as a first-class entity, so a run's identity, intent
    (dispatched / skipped / capped), and status survive independently of its member
    jobs (which are purged on retention). Counts are cached and refreshed as results
    land, so the summary is authoritative even after the jobs are gone."""
    __tablename__ = "runs"
    run_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    fleet_id = Column(String, index=True)   # null for tag (standalone) runs
    tag = Column(String)
    command = Column(Text)
    created_by = Column(String)
    created_at = Column(String, index=True)
    dispatched = Column(Integer)            # member jobs created (across all waves)
    skipped_count = Column(Integer)         # members skipped (inactive / read-only access / ...)
    skipped = Column(JSON)                  # bounded [{agent_id, hostname, reason}] - why they didn't run
    idempotency_key = Column(String)
    state = Column(String)                  # pending|running|paused|succeeded|partial|failed|empty|canceled
    counts = Column(JSON)                   # cached {ok, failed, pending, running}
    parent_run_id = Column(String)          # retry/re-run lineage (future)
    # Staged rollout: the resolved plan {"waves": [sizes...], "failure_threshold": f},
    # the wave currently in flight, and the total wave count. Null/1 = single wave.
    rollout = Column(JSON)
    current_wave = Column(Integer, default=0)
    wave_total = Column(Integer, default=1)


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

    def set_k8s_allowed_binaries(self, agent_id: str, binaries: list) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(
                    k8s_allowed_binaries=binaries
                )
            )
            db.commit()

    def set_landlock_status(self, agent_id: str, status: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(landlock_status=status)
            )
            db.commit()

    def set_sandbox_ack(self, agent_id: str, acknowledged: bool) -> None:
        with SessionLocal() as db:
            db.execute(
                update(_Agent).where(_Agent.agent_id == agent_id).values(sandbox_ack=acknowledged)
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

    def list_by_fleet(self, fleet_id: str) -> list:
        """A single fleet's members, via the fleet_id index (not a tenant-wide scan) -
        the hot path for large fleets (member list, fan-out, reaper). Returns raw rows;
        callers that need the standalone-agent enrichment use list_by_tenant instead."""
        with SessionLocal() as db:
            rows = db.execute(
                select(_Agent).where(_Agent.fleet_id == fleet_id).order_by(_Agent.agent_id)
            ).scalars().all()
            return [_to_dict(r) for r in rows]

    def fleet_member_groups(self, tenant_id: str) -> list:
        """Grouped member facts for per-fleet stats (counts + grant mismatch) WITHOUT
        loading every member: one GROUP BY whose result set is tiny (fleets x a few
        status/grant combos) even for fleets with thousands of members."""
        from sqlalchemy import func
        with SessionLocal() as db:
            rows = db.execute(
                select(_Agent.fleet_id, _Agent.status, _Agent.grant_service_mgmt,
                       _Agent.grant_docker, _Agent.grants_exception, func.count())
                .where(_Agent.tenant_id == tenant_id, _Agent.fleet_id.isnot(None))
                .group_by(_Agent.fleet_id, _Agent.status, _Agent.grant_service_mgmt,
                          _Agent.grant_docker, _Agent.grants_exception)
            ).all()
            return [{"fleet_id": f, "status": s, "grant_service_mgmt": sm,
                     "grant_docker": dk, "grants_exception": exc, "count": c}
                    for f, s, sm, dk, exc, c in rows]

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

    def get_by_fleet_and_fingerprint(self, fleet_id: str, machine_fingerprint: str) -> Optional[dict]:
        """A fleet member for idempotent re-enroll: same machine reinstalling must
        re-use its record, not create a duplicate."""
        if not fleet_id or not machine_fingerprint:
            return None
        with SessionLocal() as db:
            row = db.execute(
                select(_Agent).where(
                    _Agent.fleet_id == fleet_id,
                    _Agent.machine_fingerprint == machine_fingerprint,
                )
            ).scalars().first()
            return _enrich_agent(_to_dict(row))

    def reenroll(self, agent_id: str, fields: dict) -> None:
        """Re-issue an existing fleet member's agent token on reinstall. Unlike
        claim(), this applies regardless of current status (ACTIVE/INACTIVE)."""
        with SessionLocal() as db:
            db.execute(
                update(_Agent)
                .where(_Agent.agent_id == agent_id)
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

    def detach_fleet(self, fleet_id: str, tags: Optional[list] = None) -> int:
        """Un-fleet all of a fleet's members (they become standalone agents). When `tags`
        is given, replace each member's tags too (swap inherited fleet tags for a
        provenance tag on detach)."""
        values: dict = {"fleet_id": None}
        if tags is not None:
            values["tags"] = tags
        with SessionLocal() as db:
            result = db.execute(update(_Agent).where(_Agent.fleet_id == fleet_id).values(**values))
            db.commit()
            return result.rowcount or 0

    def delete_by_fleet(self, fleet_id: str) -> int:
        """Delete every agent record in a fleet."""
        with SessionLocal() as db:
            result = db.execute(sa_delete(_Agent).where(_Agent.fleet_id == fleet_id))
            db.commit()
            return result.rowcount or 0

    def set_mode_by_fleet(self, fleet_id: str, mode: str) -> int:
        """Propagate a fleet's mode to all its members."""
        with SessionLocal() as db:
            result = db.execute(update(_Agent).where(_Agent.fleet_id == fleet_id).values(mode=mode))
            db.commit()
            return result.rowcount or 0

    def set_tags_by_fleet(self, fleet_id: str, tags: list) -> int:
        """Propagate a fleet's tags to all its members."""
        with SessionLocal() as db:
            result = db.execute(update(_Agent).where(_Agent.fleet_id == fleet_id).values(tags=tags))
            db.commit()
            return result.rowcount or 0

    def detach_from_fleet(self, agent_id: str, tags: Optional[list] = None) -> None:
        """Remove a single agent from its fleet (becomes a standalone agent). When `tags`
        is given, replace its tags too (swap inherited fleet tags for a provenance tag)."""
        values: dict = {"fleet_id": None}
        if tags is not None:
            values["tags"] = tags
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(**values))
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

    def scan_reapable_fleet_members(self, cutoff_iso: str) -> list:
        """Fleet members whose last heartbeat is older than cutoff - candidates for
        reaping. Caller applies each member's fleet-specific reap window precisely."""
        with SessionLocal() as db:
            rows = db.execute(
                select(_Agent).where(
                    _Agent.fleet_id.isnot(None),
                    _Agent.status.in_(("ACTIVE", "INACTIVE")),
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

    def set_grants_exception(self, agent_id: str, signature: Optional[str]) -> None:
        """Record (or clear, with None) an accepted fleet-grant-mismatch exception."""
        with SessionLocal() as db:
            db.execute(update(_Agent).where(_Agent.agent_id == agent_id).values(grants_exception=signature))
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

    def release_wave(self, run_id: str, wave: int) -> list:
        """Flip a staged run's wave from HELD to PENDING so agents pick it up. Guarded by
        status==HELD (idempotent: a concurrent release only flips once). Returns the
        released job rows (agent_id/job_id) so the caller can reactivate the agents."""
        with SessionLocal() as db:
            rows = db.execute(
                select(_Job).where(_Job.run_id == run_id, _Job.wave == wave, _Job.status == "HELD")
            ).scalars().all()
            released = [_to_dict(r) for r in rows]
            if released:
                db.execute(
                    update(_Job)
                    .where(_Job.run_id == run_id, _Job.wave == wave, _Job.status == "HELD")
                    .values(status="PENDING")
                )
                db.commit()
            return released

    def cancel_staged(self, run_id: str) -> int:
        """Cancel every not-yet-released (HELD) job of a run - the remaining waves. Jobs
        already dispatched (PENDING/RUNNING) are left to finish. Returns the count."""
        with SessionLocal() as db:
            result = db.execute(
                update(_Job)
                .where(_Job.run_id == run_id, _Job.status == "HELD")
                .values(status="CANCELED")
            )
            db.commit()
            return result.rowcount

    def expire_stale(self, pending_cutoff_iso: str) -> int:
        with SessionLocal() as db:
            result = db.execute(
                update(_Job)
                .where(_Job.status == "PENDING", _Job.created_at < pending_cutoff_iso)
                .values(status="EXPIRED")
            )
            db.commit()
            return result.rowcount

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        from sqlalchemy import delete as sql_delete
        terminal = ("SUCCEEDED", "FAILED", "REJECTED", "EXPIRED")
        conds = [_Job.status.in_(terminal), _Job.created_at < before_iso]
        if tenant_id is not None:
            conds.append(_Job.tenant_id == tenant_id)
        with SessionLocal() as db:
            result = db.execute(sql_delete(_Job).where(*conds))
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

    def list_by_run(self, tenant_id: str, run_id: str) -> list:
        """Every job in one fan-out (a "run"), via the indexed run_id. Powers run
        status and idempotency-key dedupe."""
        with SessionLocal() as db:
            rows = db.execute(
                select(_Job).where(_Job.tenant_id == tenant_id, _Job.run_id == run_id)
            ).scalars().all()
            return [_to_dict(r) for r in rows]


class RunRepo:
    def create(self, run: dict) -> None:
        with SessionLocal() as db:
            db.add(_Run(**run))
            db.commit()

    def get(self, run_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Run, run_id))

    def set_counts(self, run_id: str, state: str, counts: dict, current_wave: Optional[int] = None) -> None:
        values = {"state": state, "counts": counts}
        if current_wave is not None:
            values["current_wave"] = current_wave
        with SessionLocal() as db:
            db.execute(update(_Run).where(_Run.run_id == run_id).values(**values))
            db.commit()

    def set_state(self, run_id: str, state: str) -> None:
        """Set only the run's control state (pause/cancel), leaving cached counts alone."""
        with SessionLocal() as db:
            db.execute(update(_Run).where(_Run.run_id == run_id).values(state=state))
            db.commit()

    def list_by_tenant(self, tenant_id: str, limit: int = 50, cursor: Optional[str] = None) -> list:
        with SessionLocal() as db:
            stmt = select(_Run).where(_Run.tenant_id == tenant_id)
            if cursor:
                stmt = stmt.where(_Run.created_at < cursor)   # newest-first cursor page
            rows = db.execute(stmt.order_by(_Run.created_at.desc()).limit(limit)).scalars().all()
            return [_to_dict(r) for r in rows]

    def list_by_fleet(self, fleet_id: str, limit: int = 50, cursor: Optional[str] = None) -> list:
        with SessionLocal() as db:
            stmt = select(_Run).where(_Run.fleet_id == fleet_id)
            if cursor:
                stmt = stmt.where(_Run.created_at < cursor)
            rows = db.execute(stmt.order_by(_Run.created_at.desc()).limit(limit)).scalars().all()
            return [_to_dict(r) for r in rows]

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        from sqlalchemy import delete as sql_delete
        conds = [_Run.created_at < before_iso]
        if tenant_id is not None:
            conds.append(_Run.tenant_id == tenant_id)
        with SessionLocal() as db:
            result = db.execute(sql_delete(_Run).where(*conds))
            db.commit()
            return result.rowcount


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

    def set_settings(self, tenant_id: str, settings: dict) -> None:
        with SessionLocal() as db:
            db.execute(update(_Tenant).where(_Tenant.tenant_id == tenant_id).values(settings=settings))
            db.commit()

    def delete_cascade(self, tenant_id: str) -> None:
        with SessionLocal() as db:
            db.execute(sa_delete(_Approval).where(_Approval.tenant_id == tenant_id))
            db.execute(sa_delete(_Run).where(_Run.tenant_id == tenant_id))
            db.execute(sa_delete(_Job).where(_Job.tenant_id == tenant_id))
            db.execute(sa_delete(_User).where(_User.tenant_id == tenant_id))
            db.execute(sa_delete(_Agent).where(_Agent.tenant_id == tenant_id))
            db.execute(sa_delete(_Tenant).where(_Tenant.tenant_id == tenant_id))
            db.commit()


class FleetRepo:
    def get(self, fleet_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            return _to_dict(db.get(_Fleet, fleet_id))

    def get_by_name(self, tenant_id: str, name: str) -> Optional[dict]:
        with SessionLocal() as db:
            row = db.execute(
                select(_Fleet).where(_Fleet.tenant_id == tenant_id, _Fleet.name == name)
            ).scalar_one_or_none()
            return _to_dict(row)

    def get_by_join_token_hash(self, token_hash: str, now: int) -> Optional[dict]:
        """Resolve a fleet by its current join token, or its previous token while
        still inside the rotation grace window. Empty hash short-circuits."""
        if not token_hash:
            return None
        with SessionLocal() as db:
            row = db.execute(
                select(_Fleet).where(_Fleet.join_token_hash == token_hash)
            ).scalar_one_or_none()
            if row is None:
                row = db.execute(
                    select(_Fleet).where(
                        _Fleet.prev_join_token_hash == token_hash,
                        _Fleet.prev_join_token_expires_at.isnot(None),
                        _Fleet.prev_join_token_expires_at > now,
                    )
                ).scalar_one_or_none()
            return _to_dict(row)

    def create(self, fleet: dict) -> None:
        try:
            with SessionLocal() as db:
                db.add(_Fleet(**fleet))
                db.commit()
        except IntegrityError:
            raise NameTakenError(fleet.get("name", ""))

    def list_by_tenant(self, tenant_id: str) -> list:
        with SessionLocal() as db:
            rows = db.execute(select(_Fleet).where(_Fleet.tenant_id == tenant_id)).scalars().all()
            return [_to_dict(r) for r in rows]

    def scan_all(self) -> list:
        """Every fleet across all tenants - used by the heartbeat reaper."""
        with SessionLocal() as db:
            rows = db.execute(select(_Fleet)).scalars().all()
            return [_to_dict(r) for r in rows]

    def member_counts(self, tenant_id: str) -> dict:
        """{fleet_id: member_count} across the tenant's agents (single query)."""
        from sqlalchemy import func
        with SessionLocal() as db:
            rows = db.execute(
                select(_Agent.fleet_id, func.count())
                .where(_Agent.tenant_id == tenant_id, _Agent.fleet_id.isnot(None))
                .group_by(_Agent.fleet_id)
            ).all()
            return {fid: cnt for fid, cnt in rows}

    def rotate_token(self, fleet_id: str, new_hash: str,
                     prev_hash: Optional[str], prev_expires_at: Optional[int]) -> None:
        with SessionLocal() as db:
            db.execute(update(_Fleet).where(_Fleet.fleet_id == fleet_id).values(
                join_token_hash=new_hash,
                prev_join_token_hash=prev_hash,
                prev_join_token_expires_at=prev_expires_at,
                status='ACTIVE',
            ))
            db.commit()

    def set_status(self, fleet_id: str, status: str) -> None:
        with SessionLocal() as db:
            db.execute(update(_Fleet).where(_Fleet.fleet_id == fleet_id).values(status=status))
            db.commit()

    def update_settings(self, fleet_id: str, fields: dict) -> None:
        if not fields:
            return
        try:
            with SessionLocal() as db:
                db.execute(update(_Fleet).where(_Fleet.fleet_id == fleet_id).values(**fields))
                db.commit()
        except IntegrityError:
            raise NameTakenError(fields.get("name", ""))

    def delete(self, fleet_id: str) -> None:
        with SessionLocal() as db:
            db.execute(sa_delete(_Fleet).where(_Fleet.fleet_id == fleet_id))
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
            db.execute(update(_User).where(_User.user_id == user_id).values(readwrite_agent_ids=agent_ids))
            db.commit()

    def set_agent_access(self, user_id: str, readwrite_agent_ids, readonly_agent_ids,
                         readwrite_fleet_ids=None, readonly_fleet_ids=None) -> None:
        """Set the full access scope: read-write / read-only, agents and fleets."""
        with SessionLocal() as db:
            db.execute(update(_User).where(_User.user_id == user_id).values(
                readwrite_agent_ids=readwrite_agent_ids,
                readonly_agent_ids=readonly_agent_ids,
                readwrite_fleet_ids=readwrite_fleet_ids,
                readonly_fleet_ids=readonly_fleet_ids,
            ))
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
                    (_User.readwrite_agent_ids.isnot(None)) | (_User.readonly_agent_ids.isnot(None)),
                )
            ).scalars().all()
            for row in rows:
                values = {}
                allowed = row.readwrite_agent_ids or []
                if agent_id in allowed:
                    values["readwrite_agent_ids"] = [a for a in allowed if a != agent_id]
                readonly = row.readonly_agent_ids or []
                if agent_id in readonly:
                    values["readonly_agent_ids"] = [a for a in readonly if a != agent_id]
                if values:
                    db.execute(update(_User).where(_User.user_id == row.user_id).values(**values))
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

    def list_by_fleet(self, fleet_id: str, status: Optional[str] = None, requested_by: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> list:
        from sqlalchemy import desc
        with SessionLocal() as db:
            stmt = select(_Approval).where(_Approval.fleet_id == fleet_id)
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
                         agent_ids: Optional[list] = None, fleet_id: Optional[str] = None, fleet_ids: Optional[list] = None,
                         scope: Optional[str] = None, requested_by: Optional[str] = None, kind: Optional[str] = None,
                         k8s_agent_ids: Optional[list] = None,
                         q: Optional[str] = None, limit: int = 20, offset: int = 0) -> tuple:
        """Server-side search + pagination for the tenant/developer approvals views.

        Filters run in SQL: status, agent, requester, kind (host = no rule, k8s =
        has rule), and a case-insensitive LIKE over the command and requester. For
        the `approved` status the effective-list expire/dedup is applied before
        paginating, so `total` reflects what the user actually sees. Returns
        (page_items, total).
        """
        from sqlalchemy import and_, desc, or_
        with SessionLocal() as db:
            stmt = select(_Approval).where(_Approval.tenant_id == tenant_id)
            # Scope: an approval is in view if it matches any given agent/fleet filter.
            # No filters at all → the whole tenant (unrestricted). An empty allow-list
            # matches nothing, so a fully-restricted user sees nothing.
            scope_conds = []
            if agent_id is not None:
                scope_conds.append(_Approval.agent_id == agent_id)
            if fleet_id is not None:
                scope_conds.append(_Approval.fleet_id == fleet_id)
            if agent_ids is not None:
                scope_conds.append(_Approval.agent_id.in_(agent_ids))
            if fleet_ids is not None:
                scope_conds.append(_Approval.fleet_id.in_(fleet_ids))
            if scope_conds:
                stmt = stmt.where(or_(*scope_conds))
            # scope filter: 'agent' = standalone (no fleet), 'fleet' = fleet-scoped.
            if scope == "agent":
                stmt = stmt.where(_Approval.fleet_id.is_(None))
            elif scope == "fleet":
                stmt = stmt.where(_Approval.fleet_id.isnot(None))
            if status is not None:
                stmt = stmt.where(_Approval.status == status)
            if requested_by is not None:
                stmt = stmt.where(_Approval.requested_by == requested_by)
            # host/k8s is the AGENT's type, not the rule type: a k8s agent's non-kubectl
            # approval (helm/flux) carries a host_rule but still belongs under Kubernetes.
            # k8s = has a k8s_rule (covers a since-deleted agent) OR the agent is k8s;
            # host = neither.
            k8s_ids = k8s_agent_ids or []
            if kind == "k8s":
                stmt = stmt.where(or_(_Approval.k8s_rule.isnot(None), _Approval.agent_id.in_(k8s_ids)))
            elif kind == "host":
                stmt = stmt.where(and_(
                    _Approval.k8s_rule.is_(None),
                    or_(_Approval.agent_id.is_(None), _Approval.agent_id.notin_(k8s_ids)),
                ))
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

    def exists_pending_fleet(self, fleet_id: str, command: str) -> bool:
        with SessionLocal() as db:
            row = db.execute(
                select(_Approval).where(
                    _Approval.fleet_id == fleet_id,
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

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        from sqlalchemy import delete as sql_delete, or_, and_
        conds = [
            or_(
                and_(_Approval.status == "denied", _Approval.reviewed_at < before_iso),
                and_(_Approval.status == "expired", _Approval.expires_at < before_iso),
            )
        ]
        if tenant_id is not None:
            conds.append(_Approval.tenant_id == tenant_id)
        with SessionLocal() as db:
            result = db.execute(sql_delete(_Approval).where(*conds))
            db.commit()
            return result.rowcount

    def delete(self, approval_id: str) -> None:
        from sqlalchemy import delete as sql_delete
        with SessionLocal() as db:
            db.execute(sql_delete(_Approval).where(_Approval.approval_id == approval_id))
            db.commit()

    def delete_by_agent(self, agent_id: str) -> int:
        """Purge every approval scoped to an agent - used when the agent is removed so
        no stale pre-approval could authorize a command on a future id reuse."""
        with SessionLocal() as db:
            result = db.execute(sa_delete(_Approval).where(_Approval.agent_id == agent_id))
            db.commit()
            return result.rowcount or 0

    def delete_by_fleet(self, fleet_id: str) -> int:
        """Purge every approval scoped to a fleet - used when the fleet is revoked
        (members removed) or deleted."""
        with SessionLocal() as db:
            result = db.execute(sa_delete(_Approval).where(_Approval.fleet_id == fleet_id))
            db.commit()
            return result.rowcount or 0

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

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        conds = [_AgentHistory.created_at < before_iso]
        if tenant_id is not None:
            conds.append(_AgentHistory.tenant_id == tenant_id)
        with SessionLocal() as db:
            result = db.execute(sa_delete(_AgentHistory).where(*conds))
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
                      since: Optional[str] = None, until: Optional[str] = None,
                      tenant: Optional[str] = None) -> list:
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
            if tenant:
                stmt = stmt.where(_AuditLog.tenant_id.ilike(f"%{tenant}%"))
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

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None,
                     platform_only: bool = False) -> int:
        """Purge audit rows older than the cutoff. Scope with ``tenant_id`` (a tenant's
        own trail) or ``platform_only`` (the tenant_id IS NULL platform trail). With
        neither, purges every scope (back-compat)."""
        conds = [_AuditLog.created_at < before_iso]
        if tenant_id is not None:
            conds.append(_AuditLog.tenant_id == tenant_id)
        elif platform_only:
            conds.append(_AuditLog.tenant_id.is_(None))
        with SessionLocal() as db:
            result = db.execute(sa_delete(_AuditLog).where(*conds))
            db.commit()
            return result.rowcount
