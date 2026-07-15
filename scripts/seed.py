"""
Seed the database with realistic dummy data for local development.
Run via: docker compose run --rm seed
Or directly: DATABASE_URL=... TOKEN_PEPPER=localtest python seed.py
"""
import hashlib
import hmac
import json
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



# Per-tenant settings blobs so the Settings page + staged-rollout policy have content.
# alpha is fully configured (retention + cap overrides + a wave policy for tag & fleet,
# read & write); beta sets just a fleet-run write policy; the rest inherit the platform
# defaults (settings = None). Concurrency stays <= the fan-out cap (enforced by the API).
_ALPHA_SETTINGS = {
    "job_retention_days": 14,
    "run_retention_days": 45,
    "fanout_cap": 10,
    "wave_policy": {
        # Fleet writes roll out in waves of 3, pausing after each wave (manual) and on any
        # failure; fleet reads run all at once. Tag writes auto-advance but keep going on
        # failure. Reads left unset = one-shot.
        "fleet": {"write": {"mode": "manual", "on_failure": "stop", "concurrency": 3}},
        "tag":   {"write": {"mode": "auto",   "on_failure": "continue"}},
    },
}
_BETA_SETTINGS = {
    "wave_policy": {"fleet": {"write": {"mode": "auto", "on_failure": "stop"}}},
}

TENANTS = [
    {"tenant_id": _tenant_id("alpha"), "name": "alpha", "status": "ACTIVE",   "created_at": _iso(30), "settings": _ALPHA_SETTINGS},
    {"tenant_id": _tenant_id("beta"),  "name": "beta",  "status": "ACTIVE",   "created_at": _iso(14), "settings": _BETA_SETTINGS},
    {"tenant_id": _tenant_id("gamma"), "name": "gamma", "status": "ACTIVE",   "created_at": _iso(3)},
    # DISABLED tenant - login is blocked; visible/manageable only in the platform admin console.
    {"tenant_id": _tenant_id("delta"), "name": "delta", "status": "DISABLED", "created_at": _iso(7)},
    # "scale" tenant - gets the standard rich fixtures PLUS bulk data (see make_bulk_*),
    # so every paginated console surface crosses the 20-per-page threshold.
    {"tenant_id": _tenant_id("scale"), "name": "scale", "status": "ACTIVE",   "created_at": _iso(1)},
]

# Bulk-data volumes (each comfortably above the console's 20-per-page size). Piled onto
# the "scale" tenant so you can page through agents, fleets, fleet members, jobs, and
# users; N_BULK_TENANTS adds lightweight tenants so the platform Tenants page pages too.
# All overridable via env vars for quick tuning.
SCALE_SLUG        = "scale"
BULK_AGENTS       = int(os.environ.get("SEED_BULK_AGENTS", "120"))
BULK_FLEETS       = int(os.environ.get("SEED_BULK_FLEETS", "30"))
BIG_FLEET_MEMBERS = int(os.environ.get("SEED_BIG_FLEET_MEMBERS", "55"))
BULK_USERS        = int(os.environ.get("SEED_BULK_USERS", "30"))
BULK_JOBS         = int(os.environ.get("SEED_BULK_JOBS", "60"))
N_BULK_TENANTS    = int(os.environ.get("SEED_BULK_TENANTS", "25"))

def _k8s_effective_rbac() -> dict:
    """A realistic SelfSubjectRulesReview snapshot the k8s agent would report:
    read-only cluster-wide plus write in one namespace. `hash` drives drift."""
    perms: dict = {
        "cluster_wide": [
            {"verbs": ["get", "list", "watch"], "api_groups": [""],
             "resources": ["pods", "services", "configmaps", "namespaces", "nodes"]},
            {"verbs": ["get", "list", "watch"], "api_groups": ["apps"],
             "resources": ["deployments", "statefulsets", "daemonsets"]},
        ],
        "namespaces": [
            {"namespace": "team-a", "resource_rules": [
                {"verbs": ["get", "list", "watch", "update", "patch"],
                 "api_groups": ["apps"], "resources": ["deployments"]},
            ]},
            {"namespace": "team-b", "resource_rules": [
                {"verbs": ["get", "list", "watch"],
                 "api_groups": [""], "resources": ["pods", "configmaps"]},
            ]},
            {"namespace": "monitoring", "resource_rules": [
                {"verbs": ["get", "list", "watch"],
                 "api_groups": [""], "resources": ["pods", "services"]},
            ]},
        ],
    }
    perms["hash"] = "sha256:" + hashlib.sha256(
        json.dumps(perms, sort_keys=True).encode()).hexdigest()[:32]
    return perms


def _k8s_effective_rbac_drifted() -> dict:
    """The same agent AFTER its RBAC changed since it was acknowledged, so the console
    shows a concrete drift diff vs the acknowledged baseline (`_k8s_effective_rbac`),
    covering both cluster-wide and per-namespace changes:

    - cluster-wide: apps workloads gained create/delete (verb change); new secrets read (added)
    - namespaces:   team-a gained delete (verb change); team-b removed; team-c added;
                    monitoring gained the `endpoints` resource (resource change -> old
                    rule removed + new rule added, since resources are part of a rule's
                    identity).
    """
    perms: dict = {
        "cluster_wide": [
            {"verbs": ["get", "list", "watch"], "api_groups": [""],
             "resources": ["pods", "services", "configmaps", "namespaces", "nodes"]},
            {"verbs": ["get", "list", "watch", "create", "delete"], "api_groups": ["apps"],
             "resources": ["deployments", "statefulsets", "daemonsets"]},
            {"verbs": ["get", "list"], "api_groups": [""], "resources": ["secrets"]},
        ],
        "namespaces": [
            {"namespace": "team-a", "resource_rules": [
                {"verbs": ["get", "list", "watch", "update", "patch", "delete"],
                 "api_groups": ["apps"], "resources": ["deployments"]},
            ]},
            # team-b removed
            {"namespace": "monitoring", "resource_rules": [
                # resource-level change: gained `endpoints` (same verbs)
                {"verbs": ["get", "list", "watch"],
                 "api_groups": [""], "resources": ["pods", "services", "endpoints"]},
            ]},
            {"namespace": "team-c", "resource_rules": [
                {"verbs": ["get", "list", "create"],
                 "api_groups": [""], "resources": ["secrets"]},
            ]},
        ],
    }
    perms["hash"] = "sha256:" + hashlib.sha256(
        json.dumps(perms, sort_keys=True).encode()).hexdigest()[:32]
    return perms


def make_agents(tenant_id: str) -> list[dict]:
    install_token = _raw_install_token()
    k8s_perms = _k8s_effective_rbac()
    k8s_perms_drifted = _k8s_effective_rbac_drifted()
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
            "type":                    "host",
            "fleet_id":                f"fleet_{tenant_id}_prod",
            "tags":                    ["env:prod", "role:web", "region:us-east-1"],
            "created_at":              _iso(21),
            "grant_service_mgmt":      True,     # granted + detected: fully capable
            "grant_docker":            True,
            "service_mgmt_detected":   True,
            "docker_detected":         True,
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
            "type":                    "host",
            "fleet_id":                None,
            "tags":                    ["env:dev", "role:worker"],
            "created_at":              _iso(6),
            "grant_service_mgmt":      False,    # detected but NOT granted (capability present, sudoers not added)
            "grant_docker":            False,
            "service_mgmt_detected":   True,
            "docker_detected":         True,
        },
        # 3. ACTIVE - readonly mode, staging fleet member. Has service-mgmt granted while
        #    the staging fleet grants none, so it shows GRANT DRIFT on an active host
        #    (acknowledgeable per-member).
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "staging-db-02.internal",
            "agent_version":           "0.9.3",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "readonly",
            "running_as_root":         "false",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(10),
            "last_heartbeat_at":       _iso(0),          # just now
            "active_until":            _ts(-90 / 86400), # 90s from now
            "token_issued_at":         _iso(10),
            "rotation_requested":      False,
            "type":                    "host",
            "fleet_id":                f"fleet_{tenant_id}_staging",
            "tags":                    ["env:staging", "role:db", "tier:primary"],
            "created_at":              _iso(11),
            "grant_service_mgmt":      True,     # granted here, but the staging fleet grants none -> drift
            "grant_docker":            False,
            "service_mgmt_detected":   True,
            "docker_detected":         False,
        },
        # 4. CREATED - awaiting install (no hostname/fingerprint/agent token yet).
        #    Must be a standalone host: a CREATED agent holds an install token and
        #    enrolls individually. Fleet members enroll via the fleet join token and
        #    are born ACTIVE/INACTIVE, so an agent can never be CREATED under a fleet.
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
            "type":                    "host",
            "fleet_id":                None,
            "tags":                    ["env:prod", "role:api"],
            "created_at":              _iso(0.1),
            "grant_service_mgmt":      True,     # grants chosen at creation; nothing detected yet (never synced)
            "grant_docker":            False,
            "service_mgmt_detected":   None,
            "docker_detected":         None,
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
            "type":                    "host",
            "fleet_id":                None,
            "tags":                    ["env:prod", "decommissioned", "role:bastion"],
            "created_at":              _iso(61),
            "grant_service_mgmt":      True,     # historical grants; docker never detected on the bastion
            "grant_docker":            True,
            "service_mgmt_detected":   True,
            "docker_detected":         False,
        },
        # 6. ACTIVE - Kubernetes agent, approved mode, RBAC reported + acknowledged
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "reach-agent-6d5f79c8b-x7k2p",   # pod name
            "agent_version":           "0.1.0",
            "machine_fingerprint":     "k8s:" + secrets.token_hex(16),  # cluster-derived identity
            "mode":                    "approved",
            "running_as_root":         "false",         # k8s pods run non-root (root is n/a)
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(8),
            "last_heartbeat_at":       _iso(0),
            "active_until":            _ts(-90 / 86400),
            "token_issued_at":         _iso(8),
            "rotation_requested":      False,
            "type":                    "k8s",
            "fleet_id":                None,
            "tags":                    ["env:prod", "cluster:us-east-1"],
            "created_at":              _iso(9),
            "grant_service_mgmt":      False,            # host-only grants, n/a on k8s
            "grant_docker":            False,
            "service_mgmt_detected":   None,
            "docker_detected":         None,
            "k8s_permissions":         k8s_perms,
            "k8s_permissions_hash":    k8s_perms["hash"],
            "k8s_permissions_acked_hash": k8s_perms["hash"],  # acknowledged → no drift
        },
        # 7. DELETED - soft-deleted host; hidden from user endpoints, admin-only
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "DELETED",
            "hostname":                "retired-host-09.internal",
            "agent_version":           "0.1.0",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "readonly",
            "running_as_root":         "false",
            "agent_token_hash":        None,             # cleared
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(40),
            "last_heartbeat_at":       _iso(25),
            "active_until":            _ts(25),
            "token_issued_at":         _iso(40),
            "rotation_requested":      False,
            "type":                    "host",
            "fleet_id":                None,
            "tags":                    ["env:dev", "decommissioned"],
            "created_at":              _iso(41),
            "grant_service_mgmt":      False,
            "grant_docker":            False,
            "service_mgmt_detected":   True,
            "docker_detected":         False,
        },
        # 8. ACTIVE - second k8s agent (another cluster) with RBAC DRIFT: the
        #    reported permissions no longer match what the operator acknowledged.
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "reach-agent-84c7bd5f9-mn3qz",
            "agent_version":           "0.1.0",
            "machine_fingerprint":     "k8s:" + secrets.token_hex(16),
            "mode":                    "approved",
            "running_as_root":         "false",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(12),
            "last_heartbeat_at":       _iso(0),
            "active_until":            _ts(-90 / 86400),
            "token_issued_at":         _iso(12),
            "rotation_requested":      False,
            "type":                    "k8s",
            "fleet_id":                None,
            "tags":                    ["env:staging", "cluster:eu-west-1"],
            "created_at":              _iso(13),
            "grant_service_mgmt":      False,
            "grant_docker":            False,
            "service_mgmt_detected":   None,
            "docker_detected":         None,
            # Current RBAC differs from what was acknowledged → DRIFT, and the
            # acknowledged snapshot is retained so the console shows the diff.
            "k8s_permissions":            k8s_perms_drifted,
            "k8s_permissions_hash":       k8s_perms_drifted["hash"],
            "k8s_permissions_acked_hash": k8s_perms["hash"],
            "k8s_permissions_acked":      k8s_perms,
        },
        # 9 & 10. ACTIVE standalone hosts sharing the env:prod + role:cache tags, so a
        #    tag fan-out (`exec --tag env:prod`) has a real multi-host target set and
        #    the standalone "tag runs" view has content (see make_batched_jobs).
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "prod-cache-01.internal",
            "agent_version":           "0.9.4",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "approved",
            "running_as_root":         "false",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(15),
            "last_heartbeat_at":       _iso(0),
            "active_until":            _ts(-90 / 86400),
            "token_issued_at":         _iso(15),
            "rotation_requested":      False,
            "type":                    "host",
            "fleet_id":                None,
            "tags":                    ["env:prod", "role:cache"],
            "created_at":              _iso(16),
            "grant_service_mgmt":      True,
            "grant_docker":            False,
            "service_mgmt_detected":   True,
            "docker_detected":         False,
        },
        {
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  "ACTIVE",
            "hostname":                "prod-cache-02.internal",
            "agent_version":           "0.9.4",
            "machine_fingerprint":     secrets.token_hex(16),
            "mode":                    "approved",
            "running_as_root":         "false",
            "agent_token_hash":        _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(15),
            "last_heartbeat_at":       _iso(0),
            "active_until":            _ts(-90 / 86400),
            "token_issued_at":         _iso(15),
            "rotation_requested":      False,
            "type":                    "host",
            "fleet_id":                None,
            "tags":                    ["env:prod", "role:cache"],
            "created_at":              _iso(16),
            "grant_service_mgmt":      True,
            "grant_docker":            False,
            "service_mgmt_detected":   True,
            "docker_detected":         False,
        },
    ]


def make_fleets(tenant_id: str) -> list[dict]:
    """Two real fleets so the fleet-tagged agents reference actual records and the
    Fleets page has content: an ACTIVE prod web fleet and a staging worker fleet.
    Fleet ids match the fleet_id the seeded agents already carry."""
    return [
        {
            "fleet_id":                   f"fleet_{tenant_id}_prod",
            "tenant_id":                  tenant_id,
            "name":                       "web-prod",
            "mode":                       "approved",
            "grant_service_mgmt":         True,
            "grant_docker":               True,
            "tags":                       ["env:prod", "role:web"],
            "join_token_hash":            _hmac("fleet_" + secrets.token_urlsafe(24)),
            "prev_join_token_hash":       None,
            "prev_join_token_expires_at": None,
            "status":                     "ACTIVE",
            "reap_after_seconds":         1800,   # 30 min
            "max_fanout":                 3,      # blast-radius cap: a fleets exec hits <= 3 at once
            # Advanced: fleet-level staged-rollout override of the tenant default. Writes
            # roll 2 at a time (<= max_fanout), pausing after each wave and on failure.
            "wave_policy":                {"write": {"mode": "manual", "on_failure": "stop", "concurrency": 2}},
            "created_at":                 _iso(25),
            "created_by":                 None,
        },
        {
            "fleet_id":                   f"fleet_{tenant_id}_staging",
            "tenant_id":                  tenant_id,
            "name":                       "worker-staging",
            "mode":                       "readonly",
            "grant_service_mgmt":         False,
            "grant_docker":               False,
            "tags":                       ["env:staging", "role:worker"],
            "join_token_hash":            _hmac("fleet_" + secrets.token_urlsafe(24)),
            "prev_join_token_hash":       None,
            "prev_join_token_expires_at": None,
            "status":                     "ACTIVE",
            "reap_after_seconds":         None,   # inherits the platform default
            "created_at":                 _iso(12),
            "created_by":                 None,
        },
    ]


def make_fleet_members(tenant_id: str) -> list[dict]:
    """Extra autoscaler-style host agents enrolled into the fleets, inheriting each fleet's
    mode/tags/grants, with a realistic active/inactive mix (cattle churn)."""
    def host(fleet_id, mode, tags, grant_sm, grant_dk, ip, ver, active):
        return {
            "agent_id":                 _agent_id(),
            "tenant_id":                tenant_id,
            "status":                   "ACTIVE" if active else "INACTIVE",
            "hostname":                 ip,
            "agent_version":            ver,
            "machine_fingerprint":      "i-" + secrets.token_hex(8),   # EC2-style instance id
            "mode":                     mode,
            "running_as_root":          "false",
            "agent_token_hash":         _hmac(_raw_agent_token()),
            "install_token_hash":       None,
            "install_token_expires_at": None,
            "claimed_at":               _iso(3),
            "last_heartbeat_at":        _iso(0 if active else 0.02),
            "active_until":             _ts(-90 / 86400) if active else _ts(0.02),
            "token_issued_at":          _iso(3),
            "rotation_requested":       False,
            "type":                     "host",
            "fleet_id":                 fleet_id,
            "tags":                     tags,
            "created_at":               _iso(3),
            "grant_service_mgmt":       grant_sm,
            "grant_docker":             grant_dk,
            "service_mgmt_detected":    grant_sm,
            "docker_detected":          grant_dk,
        }

    prod = f"fleet_{tenant_id}_prod"
    staging = f"fleet_{tenant_id}_staging"
    members = []
    # web-prod: 3 active + 1 inactive (a scaled-in / terminated instance not yet reaped)
    for i in range(2, 5):
        members.append(host(prod, "approved", ["env:prod", "role:web"], True, True, f"ip-10-0-1-{i}", "0.9.4", True))
    members.append(host(prod, "approved", ["env:prod", "role:web"], True, True, "ip-10-0-1-9", "0.9.4", False))
    # GRANT DRIFT: this active member enrolled before docker was granted to the fleet
    # (web-prod now wants grant_docker=True). It still runs without docker until the
    # host is re-provisioned and the operator acknowledges - the console flags it.
    members.append(host(prod, "approved", ["env:prod", "role:web"], True, False, "ip-10-0-1-12", "0.9.4", True))
    # worker-staging: 2 active
    for i in range(1, 3):
        members.append(host(staging, "readonly", ["env:staging", "role:worker"], False, False, f"ip-10-1-0-{i}", "0.9.3", True))
    return members



def make_jobs(
    tenant_id: str,
    agent_ids: list[str],
    user_ids: list[str],
    pending_job_id: str,
    running_job_id: str,
    k8s_agent_id: str = None,
) -> list[dict]:
    ag1 = agent_ids[0] if agent_ids else "agent_unknown"
    ag2 = agent_ids[1] if len(agent_ids) > 1 else ag1
    k8s_ag = k8s_agent_id or ag1
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
            "argv":         ["systemctl", "restart", "nginx"],   # structured (host write, no shell)
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
        # 8. FAILED - command not found (exit 127), stderr only
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag2,
            "command":      "helm status myrelease",
            "status":       "FAILED",
            "mode":         "wild",
            "is_write":     False,
            "exit_code":    127,
            "stdout":       "",
            "stderr":       "bash: helm: command not found\n",
            "duration_ms":  25,
            "created_by":   uid2,
            "created_at":   _iso(1.5),
            "started_at":   _iso(1.5),
            "completed_at": _iso(1.5),
            "expires_at":   None,
        },
        # 9. SUCCEEDED - exit 0 but with warnings on stderr (stdout + stderr both set)
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     ag1,
            "command":      "pip install requests",
            "status":       "SUCCEEDED",
            "mode":         "wild",
            "is_write":     True,
            "exit_code":    0,
            "stdout":       "Requirement already satisfied: requests in /usr/lib/python3/dist-packages (2.31.0)\n",
            "stderr":       "WARNING: Running pip as the 'root' user can lead to broken permissions.\n",
            "duration_ms":  1340,
            "created_by":   uid1,
            "created_at":   _iso(0.7),
            "started_at":   _iso(0.7),
            "completed_at": _iso(0.7),
            "expires_at":   None,
        },
        # 10. k8s SUCCEEDED - read (kubectl get)
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     k8s_ag,
            "command":      "kubectl get pods -A",
            "status":       "SUCCEEDED",
            "mode":         "approved",
            "is_write":     False,
            "exit_code":    0,
            "stdout":       (
                "NAMESPACE     NAME                    READY   STATUS    RESTARTS   AGE\n"
                "team-a        web-6d5f79c8b-x7k2p     1/1     Running   0          3d\n"
                "kube-system   coredns-5d78c9-abcde    1/1     Running   0          40d\n"
            ),
            "stderr":       "",
            "duration_ms":  190,
            "created_by":   uid1,
            "created_at":   _iso(0.3),
            "started_at":   _iso(0.3),
            "completed_at": _iso(0.3),
            "expires_at":   None,
        },
        # 11. k8s SUCCEEDED - approved write (kubectl scale, matches the approved rule)
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     k8s_ag,
            "command":      "kubectl scale deployment web --replicas=3 -n team-a",
            "status":       "SUCCEEDED",
            "mode":         "approved",
            "is_write":     True,
            "exit_code":    0,
            "stdout":       "deployment.apps/web scaled\n",
            "stderr":       "",
            "duration_ms":  260,
            "created_by":   uid1,
            "created_at":   _iso(0.2),
            "started_at":   _iso(0.2),
            "completed_at": _iso(0.2),
            "expires_at":   None,
        },
        # 12. k8s REJECTED - unapproved write; a pending approval was raised (delete pods)
        {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     k8s_ag,
            "command":      "kubectl delete pod web-6d5f79c8b-x7k2p -n team-a",
            "status":       "REJECTED",
            "mode":         "approved",
            "is_write":     True,
            "exit_code":    None,
            "stdout":       None,
            "stderr":       "Blocked: approval required - a request has been sent to your admin.\n",
            "duration_ms":  None,
            "created_by":   uid2,
            "created_at":   _iso(0.04),
            "started_at":   None,
            "completed_at": None,
            "expires_at":   None,
        },
    ]


def make_batched_jobs(tenant_id: str, agents: list[dict], user_ids: list[str]) -> list[dict]:
    """Fan-out **runs**: jobs that share a `run_id` so the console groups them as a
    single run. Two kinds, matching the two fan-out surfaces:

    - **Fleet runs** (`fleets exec` / `POST /fleets/{id}/jobs`): a batch across a
      fleet's members, shown under `/fleets/{id}/runs`.
    - **Tag runs** (`exec --tag` / `POST /jobs/fanout`): a batch across standalone
      agents carrying a tag, shown under `/jobs/runs`.

    Each run mixes outcomes (ok / failed / still-running) so the per-run counts are
    non-trivial in the UI."""
    uid1 = user_ids[0] if user_ids else "user_unknown"
    uid2 = user_ids[1] if len(user_ids) > 1 else uid1

    def _job(agent_id, command, run_id, outcome, mode, is_write, age, idx, run_tag=None, run_fleet_id=None):
        j = {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     agent_id,
            "run_id":     run_id,
            "run_tag":    run_tag,
            "run_fleet_id": run_fleet_id,
            "command":      command,
            "mode":         mode,
            "is_write":     is_write,
            "created_by":   uid1 if idx % 2 == 0 else uid2,
            "created_at":   _iso(age),
            "started_at":   _iso(age),
            "expires_at":   None,
        }
        if outcome == "ok":
            j.update(status="SUCCEEDED", exit_code=0, stdout="ok\n", stderr="",
                     duration_ms=140, completed_at=_iso(age))
        elif outcome == "failed":
            j.update(status="FAILED", exit_code=1, stdout="",
                     stderr="Job failed on this host\n", duration_ms=95, completed_at=_iso(age))
        else:  # running - still in progress, no result yet
            j.update(status="RUNNING", exit_code=None, stdout=None, stderr=None,
                     duration_ms=None, completed_at=None, expires_at=_ts(-10 / 1440))
        return j

    active = lambda a: a.get("status") == "ACTIVE"
    prod_fleet = f"fleet_{tenant_id}_prod"
    staging_fleet = f"fleet_{tenant_id}_staging"
    prod_members = [a["agent_id"] for a in agents if active(a) and a.get("fleet_id") == prod_fleet]
    staging_members = [a["agent_id"] for a in agents if active(a) and a.get("fleet_id") == staging_fleet]
    # Standalone host agents carrying env:prod (what an `exec --tag env:prod` selects).
    tag_hosts = [a["agent_id"] for a in agents
                 if active(a) and not a.get("fleet_id") and (a.get("type") or "host") == "host"
                 and "env:prod" in (a.get("tags") or [])]

    jobs: list = []
    # Fleet run 1 (web-prod): a write fan-out with one straggler still running and
    # one failure - the realistic "rolling restart across the fleet" shape.
    if prod_members:
        bid = "run_" + secrets.token_urlsafe(12)
        for i, aid in enumerate(prod_members):
            outcome = "running" if i == 0 and len(prod_members) > 2 else ("failed" if i == len(prod_members) - 1 else "ok")
            jobs.append(_job(aid, "systemctl restart app", bid, outcome, "approved", True, 0.2, i, run_fleet_id=prod_fleet))
    # Fleet run 2 (web-prod): an earlier, all-green read fan-out.
    if prod_members:
        bid = "run_" + secrets.token_urlsafe(12)
        for i, aid in enumerate(prod_members):
            jobs.append(_job(aid, "uptime", bid, "ok", "approved", False, 1.5, i, run_fleet_id=prod_fleet))
    # Fleet run 3 (worker-staging): a read fan-out across the staging fleet.
    if staging_members:
        bid = "run_" + secrets.token_urlsafe(12)
        for i, aid in enumerate(staging_members):
            jobs.append(_job(aid, "df -h", bid, "ok", "readonly", False, 0.6, i, run_fleet_id=staging_fleet))
    # Fleet run 4 (web-prod): a HISTORICAL run whose members have since been reaped
    # (autoscaler scaled in). The agents no longer exist, but the jobs carry run_fleet_id,
    # so the run still shows under the fleet - exercising durable grouping (a run that
    # would vanish if we re-joined jobs to live member records). Agent ids are phantom.
    bid = "run_" + secrets.token_urlsafe(12)
    for i in range(3):
        reaped_aid = "agent_reaped_" + secrets.token_hex(6)
        jobs.append(_job(reaped_aid, "systemctl restart app", bid,
                         "failed" if i == 2 else "ok", "approved", True, 4.0, i, run_fleet_id=prod_fleet))
    # Tag run (standalone env:prod hosts): a `exec --tag env:prod` fan-out.
    if tag_hosts:
        bid = "run_" + secrets.token_urlsafe(12)
        for i, aid in enumerate(tag_hosts):
            outcome = "failed" if i == len(tag_hosts) - 1 and len(tag_hosts) > 1 else "ok"
            jobs.append(_job(aid, "systemctl status nginx", bid, outcome, "approved", False, 0.4, i, run_tag="env:prod"))
    return jobs


def runs_from_jobs(tenant_id: str, batched_jobs: list[dict]) -> list[dict]:
    """A first-class `runs` row for each batch in the seeded fan-out jobs, with counts
    aggregated from the member jobs (so the runs views/status have content)."""
    by_run: dict = {}
    for j in batched_jobs:
        rid = j.get("run_id")
        if not rid:
            continue
        r = by_run.get(rid)
        if r is None:
            r = by_run[rid] = {
                "run_id": rid, "tenant_id": tenant_id,
                "fleet_id": j.get("run_fleet_id"), "tag": j.get("run_tag"),
                "command": j.get("command"), "created_by": j.get("created_by"),
                "created_at": j.get("created_at"),
                "dispatched": 0, "skipped_count": 0,
                "idempotency_key": None, "parent_run_id": None,
                "counts": {"ok": 0, "failed": 0, "pending": 0, "running": 0},
            }
        r["dispatched"] += 1
        st = j.get("status")
        if st == "RUNNING":
            r["counts"]["running"] += 1
        elif st == "SUCCEEDED" and j.get("exit_code") in (0, None):
            r["counts"]["ok"] += 1
        elif st in ("PENDING", "HELD"):   # HELD = a staged wave not yet released
            r["counts"]["pending"] += 1
        elif st == "CANCELED":
            pass  # a cancelled staged wave never ran - neither ok nor failed
        else:
            r["counts"]["failed"] += 1
    for r in by_run.values():
        c = r["counts"]
        if c["pending"] + c["running"] > 0:
            r["state"] = "running"
        elif c["failed"] == 0:
            r["state"] = "succeeded"
        elif c["ok"] == 0:
            r["state"] = "failed"
        else:
            r["state"] = "partial"
        # Every run is wave-based: these batches fit in one wave ("wave 1 of 1").
        r["wave_total"] = 1
        r["rollout"] = {"waves": [r["dispatched"]], "mode": "auto", "on_failure": "stop"}
    return list(by_run.values())


def make_staged_runs(tenant_id: str, agents: list[dict], user_ids: list[str]) -> "list[tuple[list[dict], dict]]":
    """Two staged ("waved") fleet runs on web-prod so the console's wave progress bar, the
    per-wave breakdown, and the pause/resume/cancel controls all have live content:

    - **paused**: wave 0 ran and a host failed, so on_failure=stop held the rest (manual/stop,
      2 per wave) - the interactive resume/cancel demo.
    - **running**: an auto rollout mid-flight - wave 0 done, wave 1 in progress, wave 2 HELD
      (auto/continue, 3 waves) - shows a live "current" wave next to a held one.

    Each returns (jobs, run_row); later waves are HELD."""
    active = lambda a: a.get("status") == "ACTIVE"
    prod_fleet = f"fleet_{tenant_id}_prod"
    pool = [a["agent_id"] for a in agents if active(a) and a.get("fleet_id") == prod_fleet]
    while len(pool) < 6:               # pad with phantoms if the fleet is small
        pool.append("agent_phantom_" + secrets.token_hex(6))
    uid = user_ids[0] if user_ids else "user_unknown"

    def _mk(run_id, aid, wave, status, exit_code, cmd, stderr=""):
        done = status in ("SUCCEEDED", "FAILED")
        return {
            "job_id":       "job_" + secrets.token_hex(8),
            "tenant_id":    tenant_id,
            "agent_id":     aid,
            "run_id":       run_id,
            "run_fleet_id": prod_fleet,
            "run_tag":      None,
            "wave":         wave,
            "command":      cmd,
            "mode":         "approved",
            "is_write":     True,
            "created_by":   uid,
            "created_at":   _iso(0.3),
            "started_at":   None if status in ("HELD", "PENDING") else _iso(0.3),
            "completed_at": _iso(0.3) if done else None,
            "status":       status,
            "exit_code":    exit_code,
            "stdout":       "deployed\n" if status == "SUCCEEDED" else (None if status in ("HELD", "PENDING", "RUNNING") else ""),
            "stderr":       stderr,
            "duration_ms":  210 if done else None,
            "expires_at":   None,
        }

    def _run(run_id, cmd, state, counts, rollout, current_wave, wave_total, dispatched, age=0.3):
        return {
            "run_id": run_id, "tenant_id": tenant_id, "fleet_id": prod_fleet, "tag": None,
            "command": cmd, "created_by": uid, "created_at": _iso(age),
            "dispatched": dispatched, "skipped_count": 0,
            "skipped": [], "idempotency_key": None, "parent_run_id": None,
            "state": state, "counts": counts, "rollout": rollout,
            "current_wave": current_wave, "wave_total": wave_total,
        }

    out: list = []

    # 1) Paused (manual/stop): wave 0 done with a failure -> held wave 1.
    p_id, p_cmd = "run_" + secrets.token_urlsafe(12), "deploy.sh --release v2.3.1"
    p_jobs = [
        _mk(p_id, pool[0], 0, "SUCCEEDED", 0, p_cmd),
        _mk(p_id, pool[1], 0, "FAILED", 1, p_cmd, "deploy hook failed\n"),
        _mk(p_id, pool[2], 1, "HELD", None, p_cmd),
        _mk(p_id, pool[3], 1, "HELD", None, p_cmd),
    ]
    out.append((p_jobs, _run(
        p_id, p_cmd, "paused",
        {"ok": 1, "failed": 1, "pending": 2, "running": 0},   # HELD counts as pending
        {"waves": [2, 2], "mode": "manual", "on_failure": "stop", "concurrency": 2},
        current_wave=0, wave_total=2, dispatched=4, age=0.3)))

    # 2) Running (auto/continue): wave 0 done, wave 1 in flight, wave 2 held.
    r_id, r_cmd = "run_" + secrets.token_urlsafe(12), "apt-get -y upgrade openssl"
    r_jobs = [
        _mk(r_id, pool[0], 0, "SUCCEEDED", 0, r_cmd),
        _mk(r_id, pool[1], 0, "SUCCEEDED", 0, r_cmd),
        _mk(r_id, pool[2], 1, "RUNNING", None, r_cmd),
        _mk(r_id, pool[3], 1, "SUCCEEDED", 0, r_cmd),
        _mk(r_id, pool[4], 2, "HELD", None, r_cmd),
        _mk(r_id, pool[5], 2, "HELD", None, r_cmd),
    ]
    out.append((r_jobs, _run(
        r_id, r_cmd, "running",
        {"ok": 3, "failed": 0, "pending": 2, "running": 1},   # 2 HELD -> pending
        {"waves": [2, 2, 2], "mode": "auto", "on_failure": "continue"},
        current_wave=1, wave_total=3, dispatched=6, age=0.15)))

    return out


def make_approvals(
    tenant_id: str,
    agent_ids: list[str],
    user_ids: list[str],
    pending_job_id: str,
    running_job_id: str,
    k8s_agent_id: str = None,
) -> list[dict]:
    ag1 = agent_ids[0] if agent_ids else "agent_unknown"
    ag2 = agent_ids[1] if len(agent_ids) > 1 else ag1
    uid1 = user_ids[0] if user_ids else "user_unknown"
    uid2 = user_ids[1] if len(user_ids) > 1 else uid1
    # Fleet-scoped approvals target a fleet (agent_id null); every member inherits
    # them. Ids match the fleets from make_fleets so the console can resolve names.
    prod_fleet = f"fleet_{tenant_id}_prod"
    staging_fleet = f"fleet_{tenant_id}_staging"

    return [
        # 1. PENDING - waiting for admin review, linked to the pending job
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag1,
            "command":       "systemctl restart nginx",   # host_rule_to_command(host_rule)
            "host_rule":     {"bin": "systemctl", "args": ["restart", "nginx"]},
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
            "host_rule":     {"bin": "apt-get", "args": ["upgrade", "-y"]},
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
            "command":       "docker restart api",
            "host_rule":     {"bin": "docker", "args": ["restart", "api"]},
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
            "command":       "nginx -s reload",
            "host_rule":     {"bin": "nginx", "args": ["-s", "reload"]},
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
            "command":       "rm -rf /var/log/app",
            "host_rule":     {"bin": "rm", "args": ["-rf", "/var/log/app"]},
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
            "host_rule":     {"bin": "apt", "args": ["upgrade", "-y"]},
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "expired",
            "expires_at":    _iso(1),           # expired 1 day ago
            "created_at":    _iso(2),
            "reviewed_at":   _iso(1.9),
            "reviewed_by":   "admin",
        },
        # 7. k8s APPROVED - structured rule (command is null; k8s_rule carries it)
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      k8s_agent_id or agent_ids[0],
            "command":       "kubectl scale deployments -n team-a",  # rule_to_command(k8s_rule)
            "k8s_rule":      {"verb": "scale", "resource": "deployments", "namespace": "team-a", "name": "*"},
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    None,
            "created_at":    _iso(2),
            "reviewed_at":   _iso(1.9),
            "reviewed_by":   "admin",
        },
        # 8. k8s PENDING - structured rule awaiting review
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      k8s_agent_id or agent_ids[0],
            "command":       "kubectl delete pods -n team-a",  # rule_to_command(k8s_rule)
            "k8s_rule":      {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*"},
            "requested_by":  uid2,
            "requester_name": "Bob",
            "job_id":        None,
            "status":        "pending",
            "expires_at":    None,
            "created_at":    _iso(0.03),
            "reviewed_at":   None,
            "reviewed_by":   None,
        },
        # 9. FLEET APPROVED - a command pre-approved for the whole prod fleet; every
        #    member (current and future) inherits it (agent_id is null).
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      None,
            "fleet_id":      prod_fleet,
            "command":       "docker restart web",
            "host_rule":     {"bin": "docker", "args": ["restart", "web"]},
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    None,               # permanent
            "created_at":    _iso(3),
            "reviewed_at":   _iso(2.9),
            "reviewed_by":   "admin",
        },
        # 10. FLEET APPROVED - a second, time-boxed prod-fleet command.
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      None,
            "fleet_id":      prod_fleet,
            "command":       "systemctl restart app",
            "host_rule":     {"bin": "systemctl", "args": ["restart", "app"]},
            "requested_by":  uid2,
            "requester_name": "Bob",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    _iso(-7),           # 7 days in the future
            "created_at":    _iso(2.5),
            "reviewed_at":   _iso(2.4),
            "reviewed_by":   "admin",
        },
        # 11. FLEET PENDING - a member's blocked write raised a fleet-scoped request
        #     awaiting review (staging fleet).
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      None,
            "fleet_id":      staging_fleet,
            "command":       "docker compose pull",
            "host_rule":     {"bin": "docker", "args": ["compose", "pull"]},
            "requested_by":  uid2,
            "requester_name": "Bob",
            "job_id":        None,
            "status":        "pending",
            "expires_at":    None,
            "created_at":    _iso(0.04),
            "reviewed_at":   None,
            "reviewed_by":   None,
        },
        # 12. FLEET DENIED - a fleet-scoped request that was rejected.
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      None,
            "fleet_id":      staging_fleet,
            "command":       "rm -rf /var/lib/app/cache",
            "host_rule":     {"bin": "rm", "args": ["-rf", "/var/lib/app/cache"]},
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "denied",
            "expires_at":    None,
            "created_at":    _iso(4),
            "reviewed_at":   _iso(3.9),
            "reviewed_by":   "admin",
        },
        # 13. HOST STRUCTURED APPROVED - a JSON host rule {bin, args[]} with a "*" wildcard
        #     (the structured model: host writes are argv-based and rule-approved, no strings).
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag1,
            "command":       "systemctl restart *",   # host_rule_to_command(host_rule)
            "host_rule":     {"bin": "systemctl", "args": ["restart", "*"]},
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    None,                    # permanent
            "created_at":    _iso(2),
            "reviewed_at":   _iso(1.9),
            "reviewed_by":   "admin",
        },
        # 14. HOST STRUCTURED PENDING - a structured rule awaiting review.
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      ag2,
            "command":       "docker restart *",
            "host_rule":     {"bin": "docker", "args": ["restart", "*"]},
            "requested_by":  uid2,
            "requester_name": "Bob",
            "job_id":        None,
            "status":        "pending",
            "expires_at":    None,
            "created_at":    _iso(0.05),
            "reviewed_at":   None,
            "reviewed_by":   None,
        },
        # 15. FLEET HOST RULE APPROVED - a structured rule for the whole prod fleet.
        {
            "approval_id":   "appr_" + secrets.token_hex(8),
            "tenant_id":     tenant_id,
            "agent_id":      None,
            "fleet_id":      prod_fleet,
            "command":       "systemctl restart *",
            "host_rule":     {"bin": "systemctl", "args": ["restart", "*"]},
            "requested_by":  uid1,
            "requester_name": "Alice",
            "job_id":        None,
            "status":        "approved",
            "expires_at":    None,
            "created_at":    _iso(3),
            "reviewed_at":   _iso(2.9),
            "reviewed_by":   "admin",
        },
    ]


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${h.hex()}"


def make_tenant_admin_users(tenant_id: str, tenant_slug: str, restrict_agent_id: str = None,
                            dev_agent_ids: list[str] = None, ops_agent_ids: list[str] = None) -> list[dict]:
    """Users per tenant. The login trio (localadmin/localoperator/localdeveloper)
    has password == username; three more cover edge states: agent-restricted,
    disabled, and must-reset-password. The user_adm_/user_ops_/user_dev_ ids stay
    stable so API tokens and audit logs still reference them.

    Access model (see shared/access.py):
    - Only ADMINS are tenant-wide (all four scope lists None). Everyone else -
      operators included - has NO access by default and is granted per agent/fleet,
      partitioned into read-write (`readwrite_*`) and read-only (`readonly_*`):
        * localoperator   - read-write on `ops_agent_ids` (a scoped operator)
        * localdeveloper  - read-write on `dev_agent_ids`, read-only on the prod fleet
        * localrestricted - read-only on a single agent (no write anywhere)
        * localnewbie     - no access at all (empty lists)."""
    prod_fleet = f"fleet_{tenant_id}_prod"

    def _u(uid_prefix: str, username: str, role: str, login_days: float = 1.0, **extra) -> dict:
        u = {
            "user_id":             uid_prefix + tenant_slug,
            "tenant_id":           tenant_id,
            # Clean display name (no tenant suffix). The platform console appends the
            # tenant itself; the tenant console is already scoped to one tenant.
            "name":                username,
            "username":            username,
            "password_hash":       _hash_password(username),   # password == username
            "role":                role,
            "must_reset_password": False,
            "disabled_at":         None,
            "last_login_at":       _iso(login_days),
            # Default None = tenant-wide (admins, and explicitly-unrestricted operators).
            "readwrite_agent_ids": None,
            "readonly_agent_ids":  None,
            "readwrite_fleet_ids": None,
            "readonly_fleet_ids":  None,
            "status":              "ACTIVE",
            "created_at":          _iso(20),
        }
        u.update(extra)
        return u
    return [
        _u("user_adm_", "localadmin",      "admin",     0.1, name="Alice Admin"),
        # a scoped operator: read-write on a set of standalone agents (not tenant-wide)
        _u("user_ops_", "localoperator",   "operator",  1, name="Oscar Operator",
           readwrite_agent_ids=list(ops_agent_ids) if ops_agent_ids else [],
           readonly_agent_ids=[], readwrite_fleet_ids=[], readonly_fleet_ids=[]),
        # read-write on a curated set of agents + read-only on the prod fleet
        _u("user_dev_", "localdeveloper",  "developer", 2, name="Dana Developer",
           readwrite_agent_ids=list(dev_agent_ids) if dev_agent_ids else [],
           readonly_agent_ids=[],
           readwrite_fleet_ids=[],
           readonly_fleet_ids=[prod_fleet]),
        # read-only on a single agent - can view/run reads but never write
        _u("user_res_", "localrestricted", "developer", 3, name="Riley Restricted",
           readwrite_agent_ids=[],
           readonly_agent_ids=[restrict_agent_id] if restrict_agent_id else [],
           readwrite_fleet_ids=[],
           readonly_fleet_ids=[]),
        # disabled account - login is blocked (status REVOKED + disabled_at)
        _u("user_dis_", "localdisabled",   "operator",  5, name="Dylan Disabled", status="REVOKED", disabled_at=_iso(1)),
        # forced first-login password reset; never logged in - and no access yet
        _u("user_new_", "localnewbie",     "developer", 1, name="Noah Newbie",
           must_reset_password=True, last_login_at=None,
           readwrite_agent_ids=[], readonly_agent_ids=[], readwrite_fleet_ids=[], readonly_fleet_ids=[]),
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
        ("user.login",         admin_id, "Admin",    "admin",    "user",    admin_id,  None,       _iso(0.01)),
        ("user.login",         ops_id,   "Operator", "operator", "user",    ops_id,    None,       _iso(0.05)),
        ("user.password_reset",admin_id, "Admin",    "admin",    "user",    dev_id,    None,       _iso(1)),
        ("agent.revoked",      admin_id, "Admin",    "admin",    "agent",   ag,        None,       _iso(2)),
        ("approval.approved",  ops_id,   "Operator", "operator", "approval","appr_xxx",{"command":"docker restart api","duration":"24h"}, _iso(0.5)),
        ("approval.pre_approved",ops_id, "Operator", "operator", "approval","appr_yyy",{"command":"docker restart *"},        _iso(0.8)),
        ("user.role_changed",  admin_id, "Admin",    "admin",    "user",    ops_id,    {"from":"developer","to":"operator"},  _iso(3)),
        ("api_token.revoked",  admin_id, "Admin",    "admin",    "api_token","apitok_x",None,      _iso(3)),
        ("user.login",         dev_id,   "Developer","developer","user",    dev_id,    None,       _iso(4)),
        # Single-agent job dispatch (fan-outs use run.dispatched instead).
        ("job.dispatched",     dev_id,   "Developer","developer","job",     "job_seedaa",
         {"agent_id": ag, "hostname": "web-01", "command": "systemctl status nginx", "mode": "wild", "is_write": False}, _iso(3.95)),
        # Fan-out dispatch: one audit event per run (member jobs link back via run_id).
        ("run.dispatched",     dev_id,   "Developer","developer","run",     "run_tagxx",
         {"scope":"tag","tag":"env:prod","type":"host","command":"uptime","dispatched":6,"wave_total":3,"is_write":False}, _iso(3.9)),
        ("run.dispatched",     ops_id,   "Operator", "operator", "run",     "run_fleetzz",
         {"scope":"fleet","fleet_id":"fleet_web","fleet_name":"web-tier","command":"systemctl restart nginx","dispatched":4,"wave_total":2,"is_write":True}, _iso(0.5)),
        # Per-tenant settings + staged-rollout control events (the newer action types).
        ("tenant.settings_updated", admin_id, "Admin", "admin", "tenant", tenant_id,
         {"keys":["fanout_cap","job_retention_days","wave_policy"]}, _iso(1.2)),
        ("run.paused",         admin_id, "Admin",    "admin",    "run",     "run_stagedxx", None, _iso(0.3)),
        ("run.canceled",       ops_id,   "Operator", "operator", "run",     "run_stagedyy", {"canceled":2}, _iso(0.15)),
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
    # Add an INACTIVE → ACTIVE recovery for agent[2] (staging-db-02): it missed a
    # heartbeat, then came back and is ACTIVE again.
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
        entries.append({
            "history_id":   "agenthistory_" + secrets.token_hex(6),
            "agent_id":     agent_ids[2],
            "tenant_id":    tenant_id,
            "from_status":  "INACTIVE",
            "to_status":    "ACTIVE",
            "triggered_by": "heartbeat",
            "note":         "heartbeat resumed",
            "created_at":   _iso(1),
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


# ---------------------------------------------------------------------------
# Bulk generators - only for the "scale" tenant, to exercise pagination. Rows are
# varied (status / mode / type / tags / role) so the filters have something to bite
# on, but they're intentionally shallow (no per-agent history/approvals) to keep the
# seed fast.
# ---------------------------------------------------------------------------
_ENVS    = ["env:prod", "env:staging", "env:dev"]
_ROLES   = ["role:web", "role:worker", "role:cache", "role:api", "role:db"]
_REGIONS = ["region:us-east-1", "region:us-west-2", "region:eu-west-1"]
_BULK_MODES = ["approved", "wild", "readonly"]


def make_bulk_agents(tenant_id: str, n: int) -> list[dict]:
    """n standalone hosts (with a sprinkling of k8s), a realistic status/mode/tag mix."""
    out = []
    for i in range(n):
        revoked = i % 20 == 19
        inactive = (not revoked) and i % 6 == 5
        status = "REVOKED" if revoked else ("INACTIVE" if inactive else "ACTIVE")
        active = status == "ACTIVE"
        is_k8s = i % 9 == 8
        out.append({
            "agent_id":                _agent_id(),
            "tenant_id":               tenant_id,
            "status":                  status,
            "hostname":                (f"reach-agent-{secrets.token_hex(3)}-{i:03d}" if is_k8s
                                        else f"scale-host-{i + 1:03d}.internal"),
            "agent_version":           "0.9.4",
            "machine_fingerprint":     ("k8s:" if is_k8s else "") + secrets.token_hex(16),
            "mode":                    _BULK_MODES[i % 3],
            "running_as_root":         "false",
            "agent_token_hash":        None if status == "REVOKED" else _hmac(_raw_agent_token()),
            "install_token_hash":      None,
            "install_token_expires_at": None,
            "claimed_at":              _iso(10),
            "last_heartbeat_at":       _iso(0 if active else 0.1),
            "active_until":            _ts(-90 / 86400) if active else _ts(0.1),
            "token_issued_at":         _iso(10),
            "rotation_requested":      False,
            "type":                    "k8s" if is_k8s else "host",
            "fleet_id":                None,
            "tags":                    [_ENVS[i % 3], _ROLES[i % 5], _REGIONS[i % 3]],
            "created_at":              _iso(10 + i * 0.01),
            "grant_service_mgmt":      (i % 2 == 0) and not is_k8s,
            "grant_docker":            (i % 3 == 0) and not is_k8s,
            "service_mgmt_detected":   (i % 2 == 0) and not is_k8s,
            "docker_detected":         (i % 3 == 0) and not is_k8s,
        })
    return out


def make_bulk_fleets(tenant_id: str, n: int) -> list[dict]:
    """n empty fleets (varied grants/status) so the Fleets list pages."""
    out = []
    for i in range(n):
        out.append({
            "fleet_id":                   f"fleet_{tenant_id}_bulk_{i:03d}",
            "tenant_id":                  tenant_id,
            "name":                       f"scale-asg-{i + 1:03d}",
            "mode":                       _BULK_MODES[i % 3],
            "grant_service_mgmt":         i % 2 == 0,
            "grant_docker":               i % 3 == 0,
            "tags":                       [_ENVS[i % 3], _ROLES[i % 5]],
            "join_token_hash":            _hmac("fleet_" + secrets.token_urlsafe(24)),
            "prev_join_token_hash":       None,
            "prev_join_token_expires_at": None,
            "status":                     "REVOKED" if i % 15 == 14 else "ACTIVE",
            "reap_after_seconds":         1800,
            "created_at":                 _iso(20 - i * 0.1),
            "created_by":                 None,
        })
    return out


def make_big_fleet(tenant_id: str) -> dict:
    """One fleet with many members, so the member accordion + detail modal paginate."""
    return {
        "fleet_id":                   f"fleet_{tenant_id}_mega",
        "tenant_id":                  tenant_id,
        "name":                       "mega-asg",
        "mode":                       "approved",
        "grant_service_mgmt":         True,
        "grant_docker":               True,
        "tags":                       ["env:prod", "role:web"],
        "join_token_hash":            _hmac("fleet_" + secrets.token_urlsafe(24)),
        "prev_join_token_hash":       None,
        "prev_join_token_expires_at": None,
        "status":                     "ACTIVE",
        "reap_after_seconds":         1800,
        "max_fanout":                 20,   # 55 members - a fan-out runs in waves of 20 (never all at once)
        "created_at":                 _iso(26),
        "created_by":                 None,
    }


def make_big_fleet_members(tenant_id: str, fleet_id: str, n: int) -> list[dict]:
    out = []
    for i in range(n):
        active = i % 8 != 7
        drift = i % 11 == 0   # a few members diverge from the fleet grants (docker off)
        out.append({
            "agent_id":                 _agent_id(),
            "tenant_id":                tenant_id,
            "status":                   "ACTIVE" if active else "INACTIVE",
            "hostname":                 f"ip-10-9-{i // 256}-{i % 256}",
            "agent_version":            "0.9.4",
            "machine_fingerprint":      "i-" + secrets.token_hex(8),
            "mode":                     "approved",
            "running_as_root":          "false",
            "agent_token_hash":         _hmac(_raw_agent_token()),
            "install_token_hash":       None,
            "install_token_expires_at": None,
            "claimed_at":               _iso(3),
            "last_heartbeat_at":        _iso(0 if active else 0.05),
            "active_until":             _ts(-90 / 86400) if active else _ts(0.05),
            "token_issued_at":          _iso(3),
            "rotation_requested":       False,
            "type":                     "host",
            "fleet_id":                 fleet_id,
            "tags":                     ["env:prod", "role:web"],
            "created_at":               _iso(3),
            "grant_service_mgmt":       True,
            "grant_docker":             not drift,
            "service_mgmt_detected":    True,
            "docker_detected":          not drift,
        })
    return out


def make_bulk_users(tenant_id: str, tenant_slug: str, n: int) -> list[dict]:
    """n users (roles + statuses cycled) so the Users list pages and the role/status
    filters have variety."""
    roles = ["admin", "operator", "developer"]
    out = []
    for i in range(n):
        role = roles[i % 3]
        active = i % 7 != 6
        tenant_wide = role == "admin"
        out.append({
            "user_id":             f"user_bulk_{i:03d}_" + tenant_slug,
            "tenant_id":           tenant_id,
            "name":                f"Scale User {i + 1:03d}",
            "username":            f"scaleuser{i + 1:03d}",
            "password_hash":       _hash_password("scaleuser"),
            "role":                role,
            "must_reset_password": False,
            "disabled_at":         None if active else _iso(1),
            "last_login_at":       _iso(i % 10),
            "readwrite_agent_ids": None if tenant_wide else [],
            "readonly_agent_ids":  None if tenant_wide else [],
            "readwrite_fleet_ids": None if tenant_wide else [],
            "readonly_fleet_ids":  None if tenant_wide else [],
            "status":              "ACTIVE" if active else "REVOKED",
            "created_at":          _iso(20 - i * 0.1),
        })
    return out


_BULK_COMMANDS = ["uptime", "df -h", "systemctl status nginx", "docker ps", "free -m",
                  "journalctl -u app --since '1 hour ago'", "kubectl get pods -A",
                  "systemctl restart app", "apt-get update", "cat /var/log/syslog"]


def make_bulk_jobs(tenant_id: str, agent_ids: list[str], user_ids: list[str], n: int) -> list[dict]:
    """n single jobs with strictly-decreasing created_at, so the Jobs page's created_at
    cursor pages cleanly. Commands are cycled so the command search has matches."""
    if not agent_ids:
        return []
    uid = user_ids[0] if user_ids else "user_unknown"
    statuses = ["SUCCEEDED", "SUCCEEDED", "FAILED", "RUNNING"]
    out = []
    for i in range(n):
        st = statuses[i % 4]
        age = 0.02 + i * 0.01   # distinct timestamps -> unambiguous cursor
        j = {
            "job_id":     "job_" + secrets.token_hex(8),
            "tenant_id":  tenant_id,
            "agent_id":   agent_ids[i % len(agent_ids)],
            "command":    _BULK_COMMANDS[i % len(_BULK_COMMANDS)],
            "status":     st,
            "mode":       "approved",
            "is_write":   False,
            "created_by": uid,
            "created_at": _iso(age),
            "started_at": _iso(age),
            "expires_at": None,
        }
        if st == "SUCCEEDED":
            j.update(exit_code=0, stdout="ok\n", stderr="", duration_ms=120, completed_at=_iso(age))
        elif st == "FAILED":
            j.update(exit_code=1, stdout="", stderr="error\n", duration_ms=90, completed_at=_iso(age))
        else:
            j.update(exit_code=None, stdout=None, stderr=None, duration_ms=None, completed_at=None)
        out.append(j)
    return out


def make_bulk_tenants(n: int) -> list[dict]:
    """n lightweight tenants (a record + one admin user, added in seed()) so the
    platform Tenants page pages. A few are DISABLED for variety."""
    return [{"tenant_id": _tenant_id(f"scale{i:03d}"),
             "name": f"scale-tenant-{i + 1:03d}",
             "status": "DISABLED" if i % 9 == 8 else "ACTIVE",
             "created_at": _iso(i)} for i in range(n)]


def make_bulk_tenant_admin(tenant_id: str, tenant_slug: str) -> dict:
    return {
        "user_id":             "user_adm_" + tenant_slug,
        "tenant_id":           tenant_id,
        "name":                "localadmin",
        "username":            "localadmin",
        "password_hash":       _hash_password("localadmin"),
        "role":                "admin",
        "must_reset_password": False,
        "disabled_at":         None,
        "last_login_at":       _iso(1),
        "readwrite_agent_ids": None, "readonly_agent_ids": None,
        "readwrite_fleet_ids": None, "readonly_fleet_ids": None,
        "status":              "ACTIVE",
        "created_at":          _iso(2),
    }


def seed():
    # Import models here so the module-level DATABASE_URL env var is set
    from shared.repos.sql import (
        _Tenant, _Agent, _User, _Job, _Approval, _ApiToken, _AuditLog, _AgentHistory, _Fleet, _Run,
    )

    bulk_tenants = make_bulk_tenants(N_BULK_TENANTS)

    with Session() as db:
        # Wipe existing seed data (in FK-safe order)
        known_ids = [t["tenant_id"] for t in TENANTS] + [t["tenant_id"] for t in bulk_tenants]
        db.execute(delete(_AgentHistory).where(_AgentHistory.tenant_id.in_(known_ids)))
        db.execute(delete(_AuditLog).where(_AuditLog.tenant_id.in_(known_ids)))
        db.execute(delete(_ApiToken).where(_ApiToken.tenant_id.in_(known_ids)))
        db.execute(delete(_Approval).where(_Approval.tenant_id.in_(known_ids)))
        db.execute(delete(_Run).where(_Run.tenant_id.in_(known_ids)))
        db.execute(delete(_Job).where(_Job.tenant_id.in_(known_ids)))
        db.execute(delete(_User).where(_User.tenant_id.in_(known_ids)))
        db.execute(delete(_Agent).where(_Agent.tenant_id.in_(known_ids)))
        db.execute(delete(_Fleet).where(_Fleet.tenant_id.in_(known_ids)))
        db.execute(delete(_Tenant).where(_Tenant.tenant_id.in_(known_ids)))
        db.commit()

        for tenant in TENANTS:
            tenant_slug = tenant["tenant_id"].replace("tenant_", "")
            db.add(_Tenant(**tenant))   # carries its own status (alpha/beta/gamma ACTIVE, delta DISABLED)
            db.flush()

            for fl in make_fleets(tenant["tenant_id"]):
                db.add(_Fleet(**fl))
            db.flush()

            agents = make_agents(tenant["tenant_id"]) + make_fleet_members(tenant["tenant_id"])
            for a in agents:
                db.add(_Agent(**a))
            db.flush()

            all_agent_ids = [a["agent_id"] for a in agents]
            active_agent_ids = [a["agent_id"] for a in agents if a["status"] == "ACTIVE"]
            k8s_agent_id = next((a["agent_id"] for a in agents if a.get("type") == "k8s"), None)

            # Per-user AGENT grants must reference STANDALONE agents only - fleet
            # members are ephemeral (their ids churn with the autoscaler) and are granted
            # via the fleet, not by agent id. k8s agents are never fleet members.
            standalone_host_ids = [a["agent_id"] for a in agents
                                   if a["status"] == "ACTIVE" and a.get("type") != "k8s" and not a.get("fleet_id")]
            standalone_agent_ids = [a["agent_id"] for a in agents
                                    if a["status"] == "ACTIVE" and not a.get("fleet_id")]

            # localdeveloper: read-write one standalone host + the cluster (k8s) agent.
            dev_agent_ids = ([standalone_host_ids[0]] if standalone_host_ids else []) + \
                            ([k8s_agent_id] if k8s_agent_id else [])

            pw_users = make_tenant_admin_users(
                tenant["tenant_id"], tenant_slug,
                standalone_agent_ids[0] if standalone_agent_ids else None,
                dev_agent_ids=dev_agent_ids,
                # scoped operator gets read-write on the standalone agents
                ops_agent_ids=standalone_agent_ids)
            user_ids = []
            for u in pw_users:
                db.add(_User(**u))
                user_ids.append(u["user_id"])

            db.flush()

            pending_job_id = "job_" + secrets.token_hex(8)
            running_job_id = "job_" + secrets.token_hex(8)

            for j in make_jobs(tenant["tenant_id"], active_agent_ids, user_ids, pending_job_id, running_job_id, k8s_agent_id):
                db.add(_Job(**j))

            # Fan-out runs (batched jobs): fleet runs + a standalone tag run. Each batch
            # also gets a first-class `runs` row so the runs views/status are populated.
            batched = make_batched_jobs(tenant["tenant_id"], agents, user_ids)
            for j in batched:
                db.add(_Job(**j))
            runs = runs_from_jobs(tenant["tenant_id"], batched)
            # Demonstrate the "why didn't it run" detail: an inactive member was skipped
            # (skipped = couldn't run - inactive / read-only / unapproved). There is no
            # "capping" any more - every eligible member runs, in waves of the fan-out cap.
            demo = next((r for r in runs if r.get("command") == "uptime"), runs[0] if runs else None)
            if demo:
                demo["skipped"] = [{"agent_id": "agent_skipped_demo", "hostname": "ip-10-0-1-9", "reason": "not active (INACTIVE)"}]
                demo["skipped_count"] = 1
            for run in runs:
                db.add(_Run(**run))

            # Staged (waved) fleet runs - one paused after a failing wave, one auto-rollout
            # mid-flight - so the wave progress bar, per-wave breakdown, and the
            # pause/resume/cancel controls all have something live.
            for staged_jobs, staged_run in make_staged_runs(tenant["tenant_id"], agents, user_ids):
                for j in staged_jobs:
                    db.add(_Job(**j))
                db.add(_Run(**staged_run))

            for appr in make_approvals(tenant["tenant_id"], active_agent_ids, user_ids, pending_job_id, running_job_id, k8s_agent_id):
                db.add(_Approval(**appr))

            for tok in make_api_tokens(tenant["tenant_id"], tenant_slug):
                db.add(_ApiToken(**tok))

            for log in make_audit_logs(tenant["tenant_id"], tenant_slug, active_agent_ids):
                db.add(_AuditLog(**log))

            for hist in make_agent_history(tenant["tenant_id"], all_agent_ids):
                db.add(_AgentHistory(**hist))

            # Pile bulk data onto the "scale" tenant so every paginated surface
            # (agents, fleets, fleet members, users, jobs) crosses the 20-per-page line.
            if tenant_slug == SCALE_SLUG:
                tid = tenant["tenant_id"]
                bulk_agents = make_bulk_agents(tid, BULK_AGENTS)
                for a in bulk_agents:
                    db.add(_Agent(**a))
                for fl in make_bulk_fleets(tid, BULK_FLEETS):
                    db.add(_Fleet(**fl))
                big = make_big_fleet(tid)
                db.add(_Fleet(**big))
                for m in make_big_fleet_members(tid, big["fleet_id"], BIG_FLEET_MEMBERS):
                    db.add(_Agent(**m))
                for u in make_bulk_users(tid, tenant_slug, BULK_USERS):
                    db.add(_User(**u))
                db.flush()
                bulk_active = [a["agent_id"] for a in bulk_agents if a["status"] == "ACTIVE"]
                for j in make_bulk_jobs(tid, bulk_active, user_ids, BULK_JOBS):
                    db.add(_Job(**j))

        # Lightweight bulk tenants (a record + one admin) so the platform Tenants page pages.
        for bt in bulk_tenants:
            db.add(_Tenant(**bt))
            slug = bt["tenant_id"].replace("tenant_", "")
            db.add(_User(**make_bulk_tenant_admin(bt["tenant_id"], slug)))
        db.flush()

        db.commit()

    active = [t["name"] for t in TENANTS if t.get("status") != "DISABLED"]
    disabled = [t["name"] for t in TENANTS if t.get("status") == "DISABLED"]
    print("✓ Seeded database:")
    print(f"  {len(TENANTS)} rich tenants ({'/'.join(active)} active, {'/'.join(disabled)} disabled) · "
          f"2 fleets/tenant (web-prod, worker-staging) + ~7 enrolled members (incl. a grant-drift host) · "
          f"10 standalone agents/tenant (host + k8s, incl. DELETED and RBAC-drift) · 6 users/tenant · "
          f"12 jobs + fan-out runs (3 live fleet + 1 reaped-member fleet + 1 tag, batched) · "
          f"15 approvals/tenant (10 agent + 5 fleet-scoped, incl. structured host rules) · "
          f"3 api tokens · 15 audit logs · agent history")
    print()
    print("  Pagination check - the 'scale' tenant is loaded past the 20-per-page line:")
    print(f"    {BULK_AGENTS} bulk standalone agents (+ a {BIG_FLEET_MEMBERS}-member 'mega-asg' fleet) · "
          f"{BULK_FLEETS} bulk fleets · {BULK_USERS} bulk users · {BULK_JOBS} bulk jobs.")
    print(f"    Plus {N_BULK_TENANTS} extra lightweight tenants so the platform Tenants page pages too.")
    print()
    print(f"  Tenant-console logins on {' / '.join(active)} (username == password):")
    print("    localadmin (admin, all agents)  ·  localoperator (operator, all agents)")
    print("    localdeveloper (developer, scoped to 1 host + the k8s agent)")
    print("  Also per tenant: localrestricted (developer, single agent) · localnewbie (must-reset) · localdisabled (login blocked).")
    print(f"  Platform-admin login uses ADMIN_PASSWORD. Tenant '{'/'.join(disabled)}' is DISABLED (login blocked).")


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
