"""
Seed the database with realistic dummy data for local development.
Run via: docker compose run --rm seed
Or directly: DATABASE_URL=... TOKEN_PEPPER=localtest python seed.py
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]
TOKEN_PEPPER = os.environ.get("TOKEN_PEPPER", "localtest")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def _iso(offset_days: float = 0) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=offset_days)
    return dt.isoformat()

def _ts(offset_days: float = 0) -> int:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=offset_days)
    return int(dt.timestamp())

def _hmac(raw: str) -> str:
    return hmac.new(TOKEN_PEPPER.encode(), raw.encode(), hashlib.sha256).hexdigest()

def _agent_id() -> str:
    return "agent_" + secrets.token_hex(8)

def _tenant_id(slug: str) -> str:
    return f"tenant_{slug}"

def _raw_agent_token() -> str:
    return "agent_" + secrets.token_urlsafe(32)

def _raw_install_token() -> str:
    return "install_" + secrets.token_urlsafe(32)

def _raw_api_token() -> str:
    return "tok_" + secrets.token_urlsafe(32)



TENANTS = [
    {"tenant_id": _tenant_id("acme"),   "name": "Acme Corp",     "created_at": _iso(30)},
    {"tenant_id": _tenant_id("globex"), "name": "Globex Systems", "created_at": _iso(14)},
    {"tenant_id": _tenant_id("initech"),"name": "Initech",        "created_at": _iso(3)},
]

def make_agents(tenant_id: str) -> list[dict]:
    install_token = _raw_install_token()
    return [
        # 1. ACTIVE - approved mode, user (not root), all fields populated, fleet tagged
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "prod-web-01.internal",
            "agent_version":           "0.9.4",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "approved",
            "running_as_root":         "false",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(20),
            "last_heartbeat_at":       _iso(0),         # just now
            "active_until":            _ts(-90 / 86400),# 90s from now
            "token_issued_at":         _iso(20),
            "rotation_requested":      False,
            "type":                    "manual",
            "fleet_id":                f"fleet_{tenant_id}_prod",
            "tags":                    ["env:prod", "role:web", "region:us-east-1"],
            "created_at":              _iso(21),
        },
        # 2. ACTIVE - wild mode, running as root, rotation requested, no fleet
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "dev-worker-03.local",
            "agent_version":           "0.9.4",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "wild",
            "running_as_root":         "true",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(5),
            "last_heartbeat_at":       _iso(1 / 24),    # 1 hour ago
            "active_until":            _ts(-90 / 86400),
            "token_issued_at":         _iso(5),
            "rotation_requested":      True,             # token rotation pending
            "type":                    "manual",
            "fleet_id":                None,
            "tags":                    ["env:dev", "role:worker"],
            "created_at":              _iso(6),
        },
        # 3. INACTIVE - readonly mode, missed heartbeat, fleet member
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "INACTIVE",
            "hostname":                "staging-db-02.internal",
            "agent_version":           "0.9.3",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "readonly",
            "running_as_root":         "false",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(10),
            "last_heartbeat_at":       _iso(2),          # 2 days ago - missed
            "active_until":            _ts(2),           # in the past
            "token_issued_at":         _iso(10),
            "rotation_requested":      False,
            "type":                    "manual",
            "fleet_id":                f"fleet_{tenant_id}_staging",
            "tags":                    ["env:staging", "role:db", "tier:primary"],
            "created_at":              _iso(11),
        },
        # 4. CREATED - awaiting install (no hostname/fingerprint/agent token yet)
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "CREATED",
            "hostname":                None,
            "agent_version":           None,
            "machine_fingerprint":     None,
            "mode":                    "approved",
            "running_as_root":         None,
            "agent_token_hash":        None,
            "install_token_hash":      _hmac(install_token),
            "install_token_expires_at": _ts(-1),        # expires 24h from now
            "claimed_at":              None,
            "last_heartbeat_at":       None,
            "active_until":            None,
            "token_issued_at":         None,
            "rotation_requested":      False,
            "type":                    "manual",
            "fleet_id":                f"fleet_{tenant_id}_prod",
            "tags":                    ["env:prod", "role:api"],
            "created_at":              _iso(0.1),
        },
        # 5. REVOKED - decommissioned, all historical timestamps populated
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "REVOKED",
            "hostname":                "old-bastion.internal",
            "agent_version":           "0.9.1",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "wild",
            "running_as_root":         "true",
            "agent_token_hash":        None,             # cleared on revoke
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(60),
            "last_heartbeat_at":       _iso(15),
            "active_until":            _ts(15),         # lapsed
            "token_issued_at":         _iso(60),
            "rotation_requested":      False,
            "type":                    "manual",
            "fleet_id":                None,
            "tags":                    ["env:prod", "decommissioned", "role:bastion"],
            "created_at":              _iso(61),
        },
    ]



def make_jobs(
    tenant_id: str,
    agent_ids: list[str],
    user_ids: list[str],
    pending_job_id: str,
    running_job_id: str,
) -> list[dict]:
    ag1 = agent_ids[0] if agent_ids else "agent_unknown"
    ag2 = agent_ids[1] if len(agent_ids) > 1 else ag1
    uid1 = user_ids[0] if user_ids else "user_unknown"
    uid2 = user_ids[1] if len(user_ids) > 1 else uid1

    return [
        # 1. SUCCEEDED - readonly, fast
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag1,
            "command":      "uptime",
            "status":       "SUCCEEDED",
            "mode":         "readonly",
            "is_write":     False,
            "exit_code":    0,
            "stdout":       " 12:03:01 up 42 days,  3:17,  0 users,  load average: 0.05, 0.03, 0.00\n",
            "stderr":       "",
            "duration_ms":  120,
            "created_by":   uid1,
            "created_at":   _iso(1),
            "started_at":   _iso(1),
            "completed_at": _iso(1),
            "expires_at":   None,
        },
        # 2. SUCCEEDED - approved write op, longer output
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag1,
            "command":      "df -h",
            "status":       "SUCCEEDED",
            "mode":         "readonly",
            "is_write":     False,
            "exit_code":    0,
            "stdout":       (
                "Filesystem      Size  Used Avail Use% Mounted on\n"
                "/dev/sda1        50G   18G   30G  38% /\n"
                "tmpfs           7.8G  1.2G  6.6G  16% /dev/shm\n"
                "/dev/sda2       200G   45G  155G  23% /data\n"
            ),
            "stderr":       "",
            "duration_ms":  85,
            "created_by":   uid2,
            "created_at":   _iso(0.5),
            "started_at":   _iso(0.5),
            "completed_at": _iso(0.5),
            "expires_at":   None,
        },
        # 3. RUNNING - approved write, currently in progress (no completed_at yet)
        {
            "job_id":       running_job_id,
            "tenant_id":    tenant_id,
            "agent_id":     ag1,
            "command":      "apt-get upgrade -y",
            "status":       "RUNNING",
            "mode":         "approved",
            "is_write":     True,
            "exit_code":    None,
            "stdout":       None,
            "stderr":       None,
            "duration_ms":  None,
            "created_by":   uid1,
            "created_at":   _iso(1 / 1440),    # 1 minute ago
            "started_at":   _iso(0.5 / 1440),  # 30 seconds ago
            "completed_at": None,
            "expires_at":   _ts(-10 / 1440),   # expires in 10 min
        },
        # 4. FAILED - permission error on second agent
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag2,
            "command":      "cat /etc/shadow",
            "status":       "FAILED",
            "mode":         "wild",
            "is_write":     False,
            "exit_code":    1,
            "stdout":       "",
            "stderr":       "cat: /etc/shadow: Permission denied\n",
            "duration_ms":  45,
            "created_by":   uid2,
            "created_at":   _iso(2),
            "started_at":   _iso(2),
            "completed_at": _iso(2),
            "expires_at":   None,
        },
        # 5. REJECTED - requires approval, approval was denied
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag1,
            "command":      "rm -rf /var/log/app/*.log",
            "status":       "REJECTED",
            "mode":         "approved",
            "is_write":     True,
            "exit_code":    None,
            "stdout":       None,
            "stderr":       None,
            "duration_ms":  None,
            "created_by":   uid1,
            "created_at":   _iso(3),
            "started_at":   None,
            "completed_at": None,
            "expires_at":   None,
        },
        # 6. PENDING - waiting for agent dispatch, requires approval first
        {
            "job_id":       pending_job_id,
            "tenant_id":    tenant_id,
            "agent_id":     ag1,
            "command":      "systemctl restart nginx",
            "status":       "PENDING",
            "mode":         "approved",
            "is_write":     True,
            "exit_code":    None,
            "stdout":       None,
            "stderr":       None,
            "duration_ms":  None,
            "created_by":   uid1,
            "created_at":   _iso(0.01),
            "started_at":   None,
            "completed_at": None,
            "expires_at":   _ts(-10 / 1440),   # expires in 10 min
        },
        # 7. EXPIRED - was pending but timed out before agent picked it up
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag2,
            "command":      "docker restart app-container",
            "status":       "EXPIRED",
            "mode":         "approved",
            "is_write":     True,
            "exit_code":    None,
            "stdout":       None,
            "stderr":       None,
            "duration_ms":  None,
            "created_by":   uid2,
            "created_at":   _iso(0.5),
            "started_at":   None,
            "completed_at": None,
            "expires_at":   _ts(0.25),          # expired 6h ago
        },
    ]


def make_approvals(
    tenant_id: str,
    agent_ids: list[str],
    user_ids: list[str],
    pending_job_id: str,
    running_job_id: str,
) -> list[dict]:
    ag1 = agent_ids[0] if agent_ids else "agent_unknown"
    ag2 = agent_ids[1] if len(agent_ids) > 1 else ag1
    uid1 = user_ids[0] if user_ids else "user_unknown"
    uid2 = user_ids[1] if len(user_ids) > 1 else uid1

    return [
        # 1. PENDING - waiting for admin review, linked to the pending job
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag1,
            "command":       "systemctl restart nginx",
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        pending_job_id,
            "status":        "pending",
            "expires_at":    None,
            "created_at":    _iso(0.01),
            "reviewed_at":   None,
            "reviewed_by":   None,
        },
        # 2. PENDING - second pending request, no linked job (pre-check)
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag2,
            "command":       "apt-get upgrade -y",
            "requested_by":  uid2,
            "requester_name": "Bob",
            "job_id":        running_job_id,
            "status":        "pending",
            "expires_at":    None,
            "created_at":    _iso(0.02),
            "reviewed_at":   None,
            "reviewed_by":   None,
        },
        # 3. APPROVED - expires 7 days from now (future, so _lazy_expire won't touch it)
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag1,
            "command":       "docker ps",
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    _iso(-7),          # 7 days in the future
            "created_at":    _iso(1),
            "reviewed_at":   _iso(0.9),
            "reviewed_by":   "admin",
        },
        # 4. APPROVED - permanent (no expiry), pre-approved by admin
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag2,
            "command":       "uptime",
            "requested_by":  uid2,
            "requester_name": "Bob",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    None,              # permanent
            "created_at":    _iso(5),
            "reviewed_at":   _iso(4.9),
            "reviewed_by":   "admin",
        },
        # 5. DENIED - rejected by admin
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag2,
            "command":       "rm -rf /var/log/app/*.log",
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "denied",
            "expires_at":    None,
            "created_at":    _iso(5),
            "reviewed_at":   _iso(4.9),
            "reviewed_by":   "admin",
        },
        # 6. EXPIRED - explicitly stored as expired (was approved but lapsed)
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag1,
            "command":       "apt upgrade -y",
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "expired",
            "expires_at":    _iso(1),           # expired 1 day ago
            "created_at":    _iso(2),
            "reviewed_at":   _iso(1.9),
            "reviewed_by":   "admin",
        },
    ]


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${h.hex()}"


def make_tenant_admin_users(tenant_id: str, tenant_slug: str) -> list[dict]:
    """Admin + operator + developer users with password-based login for testing."""
    return [
        {
            "user_id":             "user_adm_" + tenant_slug,
            "tenant_id":           tenant_id,
            "name":                f"Admin ({tenant_slug})",
            "username":            "admin",
            "password_hash":       _hash_password("admin123"),
            "role":                "admin",
            "must_reset_password": False,
            "disabled_at":         None,
            "last_login_at":       _iso(0.1),
            "allowed_agent_ids":   None,
            "allowed_fleet_ids":   None,
            "status":              "ACTIVE",
            "created_at":          _iso(20),
        },
        {
            "user_id":             "user_ops_" + tenant_slug,
            "tenant_id":           tenant_id,
            "name":                f"Operator ({tenant_slug})",
            "username":            "operator",
            "password_hash":       _hash_password("operator123"),
            "role":                "operator",
            "must_reset_password": False,
            "disabled_at":         None,
            "last_login_at":       _iso(1),
            "allowed_agent_ids":   None,
            "allowed_fleet_ids":   None,
            "status":              "ACTIVE",
            "created_at":          _iso(15),
        },
        {
            "user_id":             "user_dev_" + tenant_slug,
            "tenant_id":           tenant_id,
            "name":                f"Developer ({tenant_slug})",
            "username":            "developer",
            "password_hash":       _hash_password("changeme"),
            "role":                "developer",
            "must_reset_password": True,
            "disabled_at":         None,
            "last_login_at":       None,
            "allowed_agent_ids":   None,
            "allowed_fleet_ids":   None,
            "status":              "ACTIVE",
            "created_at":          _iso(10),
        },
    ]


def make_api_tokens(tenant_id: str, tenant_slug: str) -> list[dict]:
    """API tokens for the admin user."""
    admin_user_id = "user_adm_" + tenant_slug
    ops_user_id = "user_ops_" + tenant_slug
    raw1 = _raw_api_token()
    raw2 = _raw_api_token()
    raw3 = _raw_api_token()
    return [
        {
            "token_id":    "apitok_" + secrets.token_hex(8),
            "user_id":     admin_user_id,
            "tenant_id":   tenant_id,
            "token_hash":  _hmac(raw1),
            "name":        "CI/CD pipeline",
            "status":      "ACTIVE",
            "created_at":  _iso(14),
            "last_used_at": _iso(0.5),
            "revoked_at":  None,
        },
        {
            "token_id":    "apitok_" + secrets.token_hex(8),
            "user_id":     admin_user_id,
            "tenant_id":   tenant_id,
            "token_hash":  _hmac(raw2),
            "name":        "Monitoring bot",
            "status":      "REVOKED",
            "created_at":  _iso(30),
            "last_used_at": _iso(7),
            "revoked_at":  _iso(3),
        },
        {
            "token_id":    "apitok_" + secrets.token_hex(8),
            "user_id":     ops_user_id,
            "tenant_id":   tenant_id,
            "token_hash":  _hmac(raw3),
            "name":        "Ops automation",
            "status":      "ACTIVE",
            "created_at":  _iso(5),
            "last_used_at": _iso(0.1),
            "revoked_at":  None,
        },
    ]


def make_audit_logs(tenant_id: str, tenant_slug: str, agent_ids: list[str]) -> list[dict]:
    admin_id = "user_adm_" + tenant_slug
    ops_id = "user_ops_" + tenant_slug
    dev_id = "user_dev_" + tenant_slug
    ag = agent_ids[0] if agent_ids else "agent_unknown"

    events = [
        ("login",              admin_id, "Admin",    "admin",    "user",    admin_id,  None,       _iso(0.01)),
        ("login",              ops_id,   "Operator", "operator", "user",    ops_id,    None,       _iso(0.05)),
        ("user.password_reset",admin_id, "Admin",    "admin",    "user",    dev_id,    None,       _iso(1)),
        ("agent.revoked",      admin_id, "Admin",    "admin",    "agent",   ag,        None,       _iso(2)),
        ("approval.reviewed",  ops_id,   "Operator", "operator", "approval","appr_xxx",{"action":"approve","duration":"24h"}, _iso(0.5)),
        ("approval.pre_approved",ops_id, "Operator", "operator", "approval","appr_yyy",{"command":"docker ps"},              _iso(0.8)),
        ("user.role_changed",  admin_id, "Admin",    "admin",    "user",    ops_id,    {"from":"developer","to":"operator"},  _iso(3)),
        ("token.revoked",      admin_id, "Admin",    "admin",    "api_token","apitok_x",None,      _iso(3)),
        ("login",              dev_id,   "Developer","developer","user",    dev_id,    None,       _iso(4)),
        ("job.created",        dev_id,   "Developer","developer","job",     "job_xxx", {"command":"uptime"}, _iso(4)),
    ]

    logs = []
    for action, actor_id, actor_name, actor_role, rtype, rid, meta, ts in events:
        logs.append({
            "log_id":        "log_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "actor_id":      actor_id,
            "actor_name":    actor_name,
            "actor_role":    actor_role,
            "action":        action,
            "resource_type": rtype,
            "resource_id":   rid,
            "event_metadata": meta,
            "ip_address":    "127.0.0.1",
            "created_at":    ts,
        })
    return logs


def make_agent_history(tenant_id: str, agent_ids: list[str]) -> list[dict]:
    entries = []
    for i, aid in enumerate(agent_ids):
        # CREATED → ACTIVE (when agent first claimed)
        entries.append({
            "history_id":   "agenthistory_" + secrets.token_hex(6),
            "agent_id":     aid,
            "tenant_id":    tenant_id,
            "from_status":  "CREATED",
            "to_status":    "ACTIVE",
            "triggered_by": "agent",
            "note":         f"prod-host-0{i+1}.internal",
            "created_at":   _iso(20 - i),
        })
    # Add ACTIVE → INACTIVE for agent[2] (the inactive one)
    if len(agent_ids) >= 3:
        entries.append({
            "history_id":   "agenthistory_" + secrets.token_hex(6),
            "agent_id":     agent_ids[2],
            "tenant_id":    tenant_id,
            "from_status":  "ACTIVE",
            "to_status":    "INACTIVE",
            "triggered_by": "heartbeat",
            "note":         "missed heartbeat threshold",
            "created_at":   _iso(2),
        })
    # Add * → REVOKED for agent[4] (the revoked one)
    if len(agent_ids) >= 5:
        entries.append({
            "history_id":   "agenthistory_" + secrets.token_hex(6),
            "agent_id":     agent_ids[4],
            "tenant_id":    tenant_id,
            "from_status":  "ACTIVE",
            "to_status":    "REVOKED",
            "triggered_by": "admin",
            "note":         "decommissioned",
            "created_at":   _iso(15),
        })
    return entries


def seed():
    # Import models here so the module-level DATABASE_URL env var is set
    from shared.repos.sql import (
        _Tenant, _Agent, _User, _Job, _Approval, _ApiToken, _AuditLog, _AgentHistory,
    )

    with Session() as db:
        # Wipe existing seed data (in FK-safe order)
        known_ids = [t["tenant_id"] for t in TENANTS]
        db.execute(delete(_AgentHistory).where(_AgentHistory.tenant_id.in_(known_ids)))
        db.execute(delete(_AuditLog).where(_AuditLog.tenant_id.in_(known_ids)))
        db.execute(delete(_ApiToken).where(_ApiToken.tenant_id.in_(known_ids)))
        db.execute(delete(_Approval).where(_Approval.tenant_id.in_(known_ids)))
        db.execute(delete(_Job).where(_Job.tenant_id.in_(known_ids)))
        db.execute(delete(_User).where(_User.tenant_id.in_(known_ids)))
        db.execute(delete(_Agent).where(_Agent.tenant_id.in_(known_ids)))
        db.execute(delete(_Tenant).where(_Tenant.tenant_id.in_(known_ids)))
        db.commit()

        admin_creds: list[tuple[str, str, str, str]] = []  # (tenant_name, username, password, role)

        for tenant in TENANTS:
            tenant_slug = tenant["tenant_id"].replace("tenant_", "")
            db.add(_Tenant(**{**tenant, "status": "ACTIVE"}))
            db.flush()

            agents = make_agents(tenant["tenant_id"])
            for a in agents:
                db.add(_Agent(**a))
            db.flush()

            all_agent_ids = [a["agent_id"] for a in agents]
            active_agent_ids = [a["agent_id"] for a in agents if a["status"] == "ACTIVE"]

            pw_map = {"admin": "admin123", "operator": "operator123", "developer": "changeme"}
            pw_users = make_tenant_admin_users(tenant["tenant_id"], tenant_slug)
            user_ids = []
            for u in pw_users:
                db.add(_User(**u))
                user_ids.append(u["user_id"])
                admin_creds.append((tenant["name"], u["username"], pw_map.get(u["role"], "changeme"), u["role"]))

            db.flush()

            pending_job_id = "job_" + secrets.token_hex(8)
            running_job_id = "job_" + secrets.token_hex(8)

            for j in make_jobs(tenant["tenant_id"], active_agent_ids, user_ids, pending_job_id, running_job_id):
                db.add(_Job(**j))

            for appr in make_approvals(tenant["tenant_id"], active_agent_ids, user_ids, pending_job_id, running_job_id):
                db.add(_Approval(**appr))

            for tok in make_api_tokens(tenant["tenant_id"], tenant_slug):
                db.add(_ApiToken(**tok))

            for log in make_audit_logs(tenant["tenant_id"], tenant_slug, active_agent_ids):
                db.add(_AuditLog(**log))

            for hist in make_agent_history(tenant["tenant_id"], all_agent_ids):
                db.add(_AgentHistory(**hist))

        db.commit()

    print("✓ Seeded database:")
    print(f"  {len(TENANTS)} tenants · 5 agents · 3 users · 7 jobs · 6 approvals · 3 api tokens · 10 audit logs · agent history")
    print()
    print("  Logins (username / password  [role]):")
    for tenant_name, username, password, role in admin_creds:
        note = " [must reset]" if password == "changeme" else ""
        print(f"    [{tenant_name}] {username:<12} / {password:<14}  ({role}){note}")


if __name__ == "__main__":
    import sys
    import time
    # Backend code is at /app inside the Docker image
    sys.path.insert(0, os.environ.get("BACKEND_PATH", "/app"))

    # Wait for alembic migrations to finish before seeding.
    # The seed container starts when the backend container starts, but alembic
    # may still be running. Retry until the tables exist.
    for attempt in range(1, 16):
        try:
            seed()
            break
        except Exception as e:
            if attempt == 15:
                print(f"✗ Seed failed after 15 attempts: {e}", file=sys.stderr)
                sys.exit(1)
            print(f"  Attempt {attempt} failed ({e}), retrying in 2s…")
            time.sleep(2)
