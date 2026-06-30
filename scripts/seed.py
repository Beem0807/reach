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



TENANTS = [
    {"tenant_id": _tenant_id("alpha"), "name": "alpha", "status": "ACTIVE",   "created_at": _iso(30)},
    {"tenant_id": _tenant_id("beta"),  "name": "beta",  "status": "ACTIVE",   "created_at": _iso(14)},
    {"tenant_id": _tenant_id("gamma"), "name": "gamma", "status": "ACTIVE",   "created_at": _iso(3)},
    # DISABLED tenant - login is blocked; visible/manageable only in the platform admin console.
    {"tenant_id": _tenant_id("delta"), "name": "delta", "status": "DISABLED", "created_at": _iso(7)},
]

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
            "type":                    "host",
            "fleet_id":                f"fleet_{tenant_id}_staging",
            "tags":                    ["env:staging", "role:db", "tier:primary"],
            "created_at":              _iso(11),
            "grant_service_mgmt":      True,     # service mgmt granted+detected; no docker on this host
            "grant_docker":            False,
            "service_mgmt_detected":   True,
            "docker_detected":         False,
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
            "type":                    "host",
            "fleet_id":                f"fleet_{tenant_id}_prod",
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
    ]



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
    ]


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${h.hex()}"


def make_tenant_admin_users(tenant_id: str, tenant_slug: str, restrict_agent_id: str = None,
                            dev_agent_ids: list[str] = None) -> list[dict]:
    """Users per tenant. The login trio (localadmin/localoperator/localdeveloper)
    has password == username; three more cover edge states: agent-restricted,
    disabled, and must-reset-password. The user_adm_/user_ops_/user_dev_ ids stay
    stable so API tokens and audit logs still reference them.

    Developers are scoped to specific agents (allowed_agent_ids): localdeveloper
    sees only `dev_agent_ids`, and localrestricted only `restrict_agent_id`.
    Admins and operators keep tenant-wide access (allowed_agent_ids=None)."""
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
            "allowed_agent_ids":   None,
            "allowed_fleet_ids":   None,
            "status":              "ACTIVE",
            "created_at":          _iso(20),
        }
        u.update(extra)
        return u
    return [
        _u("user_adm_", "localadmin",      "admin",     0.1, name="Alice Admin"),
        _u("user_ops_", "localoperator",   "operator",  1,   name="Oscar Operator"),
        # scoped to a curated subset of agents (per-user agent access)
        _u("user_dev_", "localdeveloper",  "developer", 2, name="Dana Developer",
           allowed_agent_ids=list(dev_agent_ids) if dev_agent_ids else None),
        # restricted to a single agent (per-user agent access)
        _u("user_res_", "localrestricted", "developer", 3, name="Riley Restricted",
           allowed_agent_ids=[restrict_agent_id] if restrict_agent_id else None),
        # disabled account - login is blocked (status REVOKED + disabled_at)
        _u("user_dis_", "localdisabled",   "operator",  5, name="Dylan Disabled", status="REVOKED", disabled_at=_iso(1)),
        # forced first-login password reset; never logged in
        _u("user_new_", "localnewbie",     "developer", 1, name="Noah Newbie", must_reset_password=True, last_login_at=None),
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

        for tenant in TENANTS:
            tenant_slug = tenant["tenant_id"].replace("tenant_", "")
            db.add(_Tenant(**tenant))   # carries its own status (alpha/beta/gamma ACTIVE, delta DISABLED)
            db.flush()

            agents = make_agents(tenant["tenant_id"])
            for a in agents:
                db.add(_Agent(**a))
            db.flush()

            all_agent_ids = [a["agent_id"] for a in agents]
            active_agent_ids = [a["agent_id"] for a in agents if a["status"] == "ACTIVE"]
            active_host_ids = [a["agent_id"] for a in agents
                               if a["status"] == "ACTIVE" and a.get("type") != "k8s"]
            k8s_agent_id = next((a["agent_id"] for a in agents if a.get("type") == "k8s"), None)

            # Developers only get access to certain agents, not the whole fleet:
            # localdeveloper sees one host agent plus the cluster (k8s) agent.
            dev_agent_ids = ([active_host_ids[0]] if active_host_ids else []) + \
                            ([k8s_agent_id] if k8s_agent_id else [])

            pw_users = make_tenant_admin_users(
                tenant["tenant_id"], tenant_slug,
                active_agent_ids[0] if active_agent_ids else None,
                dev_agent_ids=dev_agent_ids)
            user_ids = []
            for u in pw_users:
                db.add(_User(**u))
                user_ids.append(u["user_id"])

            db.flush()

            pending_job_id = "job_" + secrets.token_hex(8)
            running_job_id = "job_" + secrets.token_hex(8)

            for j in make_jobs(tenant["tenant_id"], active_agent_ids, user_ids, pending_job_id, running_job_id, k8s_agent_id):
                db.add(_Job(**j))

            for appr in make_approvals(tenant["tenant_id"], active_agent_ids, user_ids, pending_job_id, running_job_id, k8s_agent_id):
                db.add(_Approval(**appr))

            for tok in make_api_tokens(tenant["tenant_id"], tenant_slug):
                db.add(_ApiToken(**tok))

            for log in make_audit_logs(tenant["tenant_id"], tenant_slug, active_agent_ids):
                db.add(_AuditLog(**log))

            for hist in make_agent_history(tenant["tenant_id"], all_agent_ids):
                db.add(_AgentHistory(**hist))

        db.commit()

    active = [t["name"] for t in TENANTS if t.get("status") != "DISABLED"]
    disabled = [t["name"] for t in TENANTS if t.get("status") == "DISABLED"]
    print("✓ Seeded database:")
    print(f"  {len(TENANTS)} tenants ({'/'.join(active)} active, {'/'.join(disabled)} disabled) · "
          f"8 agents/tenant (host + k8s, incl. DELETED and RBAC-drift) · 6 users/tenant · "
          f"12 jobs · 8 approvals · 3 api tokens · 10 audit logs · agent history")
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
