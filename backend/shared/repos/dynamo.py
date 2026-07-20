import json
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key as DKey
from botocore.exceptions import ClientError

from shared.policy import compute_access_level
from shared.response import _iso

_ddb = boto3.resource("dynamodb")

# DynamoDB items are capped at 400KB. The agent hashes and (mostly) sends the full
# RBAC snapshot; here - and only here, for the DynamoDB backend - we truncate for
# storage so `current` + `acknowledged` snapshots fit in one item. The hash is left
# untouched (it's over the full snapshot), so drift detection stays exact; only the
# stored snapshot / diff loses fidelity, which is flagged via `truncated`. Postgres
# stores both snapshots in full and never calls this.
_K8S_PERM_MAX_BYTES = 180 * 1024


def _truncate_k8s_permissions(perms: Optional[dict]) -> Optional[dict]:
    """Drop per-namespace deltas (then cluster-wide rules) until the snapshot fits,
    marking it `truncated`. Mirrors the agent's capPermissions ordering."""
    if not perms:
        return perms

    def size(p: dict) -> int:
        return len(json.dumps(p, separators=(",", ":"), default=str).encode())

    if size(perms) <= _K8S_PERM_MAX_BYTES:
        return perms
    p = dict(perms)
    p["truncated"] = True
    # Drop from the end, ~1/8 at a time, so a huge snapshot converges in O(log n)
    # re-serializations instead of O(n). Deterministic (same input, same output);
    # over-dropping slightly is fine - it's already a lossy, flagged fallback.
    for field in ("namespaces", "cluster_wide"):
        rules = list(p.get(field) or [])
        p[field] = rules
        while p[field] and size(p) > _K8S_PERM_MAX_BYTES:
            drop = max(1, len(p[field]) // 8)
            p[field] = p[field][:-drop]
    return p
_TABLE_AGENTS = _ddb.Table("reach-agents")
_TABLE_TENANTS = _ddb.Table("reach-tenants")
_TABLE_USERS = _ddb.Table("reach-users")
_TABLE_JOBS = _ddb.Table("reach-jobs")
_TABLE_APPROVALS = _ddb.Table("reach-approvals")
_TABLE_API_TOKENS = _ddb.Table("reach-api-tokens")
_TABLE_AUDIT_LOGS = _ddb.Table("reach-audit-logs")
_TABLE_FLEETS = _ddb.Table("reach-fleets")
_TABLE_RUNS = _ddb.Table("reach-runs")


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
    cur = d.get("k8s_permissions_hash")
    d["k8s_permissions_drift"] = bool(cur) and cur != d.get("k8s_permissions_acked_hash")
    return d


class AgentRepo:
    def get(self, agent_id: str) -> Optional[dict]:
        return _enrich_agent(_TABLE_AGENTS.get_item(Key={"agent_id": agent_id}).get("Item"))

    def get_by_install_token_hash(self, install_token_hash: str) -> Optional[dict]:
        if not install_token_hash:
            return None
        items = _TABLE_AGENTS.query(
            IndexName="install-token-hash-index",
            KeyConditionExpression=DKey("install_token_hash").eq(install_token_hash),
            Limit=1,
        ).get("Items", [])
        return _enrich_agent(items[0]) if items else None

    def get_by_agent_token_hash(self, agent_token_hash: str) -> Optional[dict]:
        if not agent_token_hash:
            return None
        items = _TABLE_AGENTS.query(
            IndexName="agent-token-hash-index",
            KeyConditionExpression=DKey("agent_token_hash").eq(agent_token_hash),
            Limit=1,
        ).get("Items", [])
        return _enrich_agent(items[0]) if items else None

    def claim(self, agent_id: str, fields: dict) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression=(
                "SET #st = :s, agent_token_hash = :h, machine_fingerprint = :fp,"
                " hostname = :hn, agent_version = :av, claimed_at = :ca,"
                " active_until = :au, last_heartbeat_at = :hb, token_issued_at = :ti,"
                " #ty = :ty"
            ),
            # "type" and "status" are DynamoDB reserved words.
            ExpressionAttributeNames={"#st": "status", "#ty": "type"},
            ExpressionAttributeValues={
                ":s": "ACTIVE",
                ":h": fields["agent_token_hash"],
                ":fp": fields["machine_fingerprint"],
                ":hn": fields["hostname"],
                ":av": fields["agent_version"],
                ":ca": fields["claimed_at"],
                ":au": fields["active_until"],
                ":hb": fields["claimed_at"],
                ":ti": fields["token_issued_at"],
                # k8s agents report "k8s", everything else "host".
                ":ty": fields.get("type") or "host",
            },
        )

    def update_heartbeat(
        self, agent_id: str, reactivate: bool, now_iso: str,
        agent_version: Optional[str] = None,
        running_as_root: Optional[bool] = None,
        docker_detected: Optional[bool] = None,
        service_mgmt_detected: Optional[bool] = None,
    ) -> None:
        sets = ["last_heartbeat_at = :hb"]
        names: dict = {}
        values: dict = {":hb": now_iso}
        if reactivate:
            sets.append("#st = :active")
            names["#st"] = "status"
            values[":active"] = "ACTIVE"
        if agent_version:
            sets.append("agent_version = :av")
            values[":av"] = agent_version
        if running_as_root is not None:
            sets.append("running_as_root = :rar")
            values[":rar"] = "true" if running_as_root else "false"
        if docker_detected is not None:
            sets.append("docker_detected = :dd")
            values[":dd"] = docker_detected
        if service_mgmt_detected is not None:
            sets.append("service_mgmt_detected = :smd")
            values[":smd"] = service_mgmt_detected
        kwargs: dict = {
            "Key": {"agent_id": agent_id},
            "UpdateExpression": "SET " + ", ".join(sets),
            "ExpressionAttributeValues": values,
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        _TABLE_AGENTS.update_item(**kwargs)

    def set_k8s_permissions(self, agent_id: str, permissions: dict, perm_hash: str) -> None:
        # Truncate for storage only (the hash is over the full snapshot).
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET k8s_permissions = :p, k8s_permissions_hash = :h",
            ExpressionAttributeValues={":p": _truncate_k8s_permissions(permissions), ":h": perm_hash},
        )

    def set_k8s_allowed_binaries(self, agent_id: str, binaries: list) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET k8s_allowed_binaries = :b",
            ExpressionAttributeValues={":b": binaries},
        )

    def set_landlock_status(self, agent_id: str, status: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET landlock_status = :s",
            ExpressionAttributeValues={":s": status},
        )

    def set_sandbox_ack(self, agent_id: str, acknowledged: bool) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET sandbox_ack = :a",
            ExpressionAttributeValues={":a": acknowledged},
        )

    def acknowledge_k8s_permissions(self, agent_id: str, perm_hash: str, acked_permissions: Optional[dict] = None) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET k8s_permissions_acked_hash = :h, k8s_permissions_acked = :a",
            ExpressionAttributeValues={":h": perm_hash, ":a": _truncate_k8s_permissions(acked_permissions)},
        )

    def set_active_until(self, agent_id: str, active_until: int) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET active_until = :au",
            ExpressionAttributeValues={":au": active_until},
        )

    def list_by_tenant(self, tenant_id: str) -> list:
        items = _TABLE_AGENTS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
        ).get("Items", [])
        return [_enrich_agent(item) for item in items]

    def list_by_fleet(self, fleet_id: str) -> list:
        """A single fleet's members via the fleet-index GSI (not a tenant-wide read) -
        the hot path for large fleets. Raw items; standalone-agent enrichment is only
        needed by list_by_tenant."""
        results: list = []
        kwargs = {"IndexName": "fleet-index",
                  "KeyConditionExpression": DKey("fleet_id").eq(fleet_id)}
        while True:
            resp = _TABLE_AGENTS.query(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return results

    def fleet_member_groups(self, tenant_id: str) -> list:
        """Grouped member facts for per-fleet stats. DynamoDB has no GROUP BY, so we read
        a **projection** (5 small attrs, no enrichment/full rows) of the tenant's fleet
        members and aggregate in Python - the same read shape as member_counts."""
        counts: dict = {}
        kwargs = {"IndexName": "tenant-index",
                  "KeyConditionExpression": DKey("tenant_id").eq(tenant_id),
                  "FilterExpression": Attr("fleet_id").exists(),
                  "ProjectionExpression": "fleet_id, #st, grant_service_mgmt, grant_docker, grants_exception",
                  "ExpressionAttributeNames": {"#st": "status"}}
        while True:
            resp = _TABLE_AGENTS.query(**kwargs)
            for it in resp.get("Items", []):
                if not it.get("fleet_id"):
                    continue
                key = (it["fleet_id"], it.get("status"), bool(it.get("grant_service_mgmt")),
                       bool(it.get("grant_docker")), it.get("grants_exception"))
                counts[key] = counts.get(key, 0) + 1
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return [{"fleet_id": k[0], "status": k[1], "grant_service_mgmt": k[2],
                 "grant_docker": k[3], "grants_exception": k[4], "count": v}
                for k, v in counts.items()]

    def mark_inactive(self, agent_id: str) -> bool:
        try:
            _TABLE_AGENTS.update_item(
                Key={"agent_id": agent_id},
                UpdateExpression="SET #st = :inactive",
                ConditionExpression="#st = :active",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":inactive": "INACTIVE", ":active": "ACTIVE"},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def create(self, agent: dict) -> None:
        _TABLE_AGENTS.put_item(Item=agent)

    def get_by_fleet_and_fingerprint(self, fleet_id: str, machine_fingerprint: str) -> Optional[dict]:
        if not fleet_id or not machine_fingerprint:
            return None
        items = _TABLE_AGENTS.query(
            IndexName="fleet-index",
            KeyConditionExpression=DKey("fleet_id").eq(fleet_id)
            & DKey("machine_fingerprint").eq(machine_fingerprint),
            Limit=1,
        ).get("Items", [])
        return _enrich_agent(items[0]) if items else None

    def _fleet_member_ids(self, fleet_id: str) -> list:
        ids: list = []
        kwargs = {"IndexName": "fleet-index",
                  "KeyConditionExpression": DKey("fleet_id").eq(fleet_id),
                  "ProjectionExpression": "agent_id"}
        while True:
            resp = _TABLE_AGENTS.query(**kwargs)
            ids.extend(i["agent_id"] for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return ids

    def detach_fleet(self, fleet_id: str, tags: Optional[list] = None) -> int:
        ids = self._fleet_member_ids(fleet_id)
        for aid in ids:
            if tags is None:
                _TABLE_AGENTS.update_item(Key={"agent_id": aid}, UpdateExpression="REMOVE fleet_id")
            else:
                _TABLE_AGENTS.update_item(
                    Key={"agent_id": aid},
                    UpdateExpression="SET tags = :t REMOVE fleet_id",
                    ExpressionAttributeValues={":t": tags},
                )
        return len(ids)

    def delete_by_fleet(self, fleet_id: str) -> int:
        ids = self._fleet_member_ids(fleet_id)
        for aid in ids:
            _TABLE_AGENTS.delete_item(Key={"agent_id": aid})
        return len(ids)

    def set_mode_by_fleet(self, fleet_id: str, mode: str) -> int:
        ids = self._fleet_member_ids(fleet_id)
        for aid in ids:
            _TABLE_AGENTS.update_item(
                Key={"agent_id": aid},
                UpdateExpression="SET #m = :v",
                ExpressionAttributeNames={"#m": "mode"},
                ExpressionAttributeValues={":v": mode},
            )
        return len(ids)

    def set_tags_by_fleet(self, fleet_id: str, tags: list) -> int:
        ids = self._fleet_member_ids(fleet_id)
        for aid in ids:
            _TABLE_AGENTS.update_item(
                Key={"agent_id": aid},
                UpdateExpression="SET tags = :v",
                ExpressionAttributeValues={":v": tags},
            )
        return len(ids)

    def detach_from_fleet(self, agent_id: str, tags: Optional[list] = None) -> None:
        if tags is None:
            _TABLE_AGENTS.update_item(Key={"agent_id": agent_id}, UpdateExpression="REMOVE fleet_id")
        else:
            _TABLE_AGENTS.update_item(
                Key={"agent_id": agent_id},
                UpdateExpression="SET tags = :t REMOVE fleet_id",
                ExpressionAttributeValues={":t": tags},
            )

    def reenroll(self, agent_id: str, fields: dict) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression=(
                "SET #st = :a, agent_token_hash = :t, machine_fingerprint = :m, "
                "hostname = :h, agent_version = :v, claimed_at = :c, "
                "active_until = :u, last_heartbeat_at = :c, token_issued_at = :ti"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":a": "ACTIVE",
                ":t": fields["agent_token_hash"],
                ":m": fields["machine_fingerprint"],
                ":h": fields["hostname"],
                ":v": fields["agent_version"],
                ":c": fields["claimed_at"],
                ":u": fields["active_until"],
                ":ti": fields["token_issued_at"],
            },
        )

    def update_policy(self, agent_id: str, mode: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET #m = :m",
            ExpressionAttributeNames={"#m": "mode"},
            ExpressionAttributeValues={":m": mode},
        )

    def reissue_install_token(
        self, agent_id: str, install_token_hash: str, expires_at: int,
        grant_service_mgmt: bool = False, grant_docker: bool = False,
    ) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression=(
                "SET #st = :created, install_token_hash = :ith, install_token_expires_at = :exp"
                ", grant_service_mgmt = :gsm, grant_docker = :gd"
                " REMOVE agent_token_hash, machine_fingerprint, claimed_at"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":created": "CREATED",
                ":ith": install_token_hash,
                ":exp": expires_at,
                ":gsm": grant_service_mgmt,
                ":gd": grant_docker,
            },
        )

    def update_agent_token_hash(self, agent_id: str, token_hash: str, token_issued_at: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET agent_token_hash = :th, token_issued_at = :ti REMOVE rotation_requested",
            ExpressionAttributeValues={":th": token_hash, ":ti": token_issued_at},
        )

    def request_rotation(self, agent_id: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET rotation_requested = :t",
            ExpressionAttributeValues={":t": True},
        )

    def set_status(self, agent_id: str, status: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": status},
        )

    def delete(self, agent_id: str) -> None:
        _TABLE_AGENTS.delete_item(Key={"agent_id": agent_id})

    def set_tags(self, agent_id: str, tags: list) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET tags = :t",
            ExpressionAttributeValues={":t": tags},
        )

    def update_grants(self, agent_id: str, grant_docker=None, grant_service_mgmt=None) -> None:
        exprs, names, vals = [], {}, {}
        if grant_docker is not None:
            exprs.append("grant_docker = :gd")
            vals[":gd"] = grant_docker
        if grant_service_mgmt is not None:
            exprs.append("grant_service_mgmt = :gsm")
            vals[":gsm"] = grant_service_mgmt
        if not exprs:
            return
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET " + ", ".join(exprs),
            ExpressionAttributeValues=vals,
        )

    def set_grants_exception(self, agent_id: str, signature) -> None:
        """Record (or clear, with None) an accepted fleet-grant-mismatch exception."""
        if signature is None:
            _TABLE_AGENTS.update_item(Key={"agent_id": agent_id},
                                      UpdateExpression="REMOVE grants_exception")
        else:
            _TABLE_AGENTS.update_item(
                Key={"agent_id": agent_id},
                UpdateExpression="SET grants_exception = :s",
                ExpressionAttributeValues={":s": signature})

    def scan_stale_active(self, cutoff_iso: str) -> list:
        results = []
        kwargs: dict = {
            "FilterExpression": (
                Attr("status").eq("ACTIVE")
                & Attr("last_heartbeat_at").exists()
                & Attr("last_heartbeat_at").lt(cutoff_iso)
            ),
        }
        while True:
            resp = _TABLE_AGENTS.scan(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return [_enrich_agent(item) for item in results]

    def scan_reapable_fleet_members(self, cutoff_iso: str) -> list:
        """Fleet members whose last heartbeat is older than cutoff - candidates for
        reaping. Caller applies each member's fleet-specific reap window precisely."""
        results = []
        kwargs: dict = {
            "FilterExpression": (
                Attr("fleet_id").exists()
                & (Attr("status").eq("ACTIVE") | Attr("status").eq("INACTIVE"))
                & Attr("last_heartbeat_at").exists()
                & Attr("last_heartbeat_at").lt(cutoff_iso)
            ),
        }
        while True:
            resp = _TABLE_AGENTS.scan(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        # fleet_id may be present-but-null for detached agents; keep only real members.
        return [_enrich_agent(item) for item in results if item.get("fleet_id")]


class JobRepo:
    def create(self, job: dict) -> None:
        _TABLE_JOBS.put_item(Item=job)

    def get(self, job_id: str) -> Optional[dict]:
        return _TABLE_JOBS.get_item(Key={"job_id": job_id}).get("Item")

    def set_running(self, job_id: str, started_at: str) -> bool:
        try:
            _TABLE_JOBS.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #st = :r, started_at = :sa",
                ConditionExpression="#st = :p",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":r": "RUNNING", ":p": "PENDING", ":sa": started_at},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def set_result(self, job_id: str, fields: dict) -> None:
        _TABLE_JOBS.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #st = :s, exit_code = :ec, stdout = :out, stderr = :err,"
                " duration_ms = :dur, completed_at = :ca, is_write = :iw,"
                " stdout_truncated = :ot, stderr_truncated = :et"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":s": fields["status"],
                ":ec": fields["exit_code"],
                ":out": fields["stdout"],
                ":err": fields["stderr"],
                ":dur": fields["duration_ms"],
                ":ca": fields["completed_at"],
                ":iw": fields.get("is_write"),
                ":ot": bool(fields.get("stdout_truncated", False)),
                ":et": bool(fields.get("stderr_truncated", False)),
            },
        )

    def mark_expired(self, job_id: str) -> None:
        _TABLE_JOBS.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "EXPIRED"},
        )

    def _run_jobs_by_status(self, run_id: str, status: str) -> list:
        items: list = []
        kwargs: dict = {
            "IndexName": "run-index",
            "KeyConditionExpression": DKey("run_id").eq(run_id),
            "FilterExpression": Attr("status").eq(status),
        }
        while True:
            resp = _TABLE_JOBS.query(**kwargs)
            items.extend(resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return items

    def release_wave(self, run_id: str, wave: int) -> list:
        """Flip a staged run's wave from HELD to PENDING. Each flip is guarded by
        status==HELD (idempotent under a concurrent release). Returns the released rows."""
        released: list = []
        for item in self._run_jobs_by_status(run_id, "HELD"):
            if (item.get("wave") or 0) != wave:
                continue
            try:
                _TABLE_JOBS.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression="SET #st = :p",
                    ConditionExpression="#st = :h",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":p": "PENDING", ":h": "HELD"},
                )
                released.append(item)
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        return released

    def cancel_staged(self, run_id: str) -> int:
        """Cancel every not-yet-released (HELD) job of a run - the remaining waves."""
        count = 0
        for item in self._run_jobs_by_status(run_id, "HELD"):
            try:
                _TABLE_JOBS.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression="SET #st = :c",
                    ConditionExpression="#st = :h",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":c": "CANCELED", ":h": "HELD"},
                )
                count += 1
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        return count

    def expire_stale(self, pending_cutoff_iso: str) -> int:
        items = _TABLE_JOBS.scan(
            FilterExpression=Attr("status").eq("PENDING") & Attr("created_at").lt(pending_cutoff_iso)
        ).get("Items", [])
        count = 0
        for item in items:
            try:
                _TABLE_JOBS.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression="SET #st = :e",
                    ConditionExpression="#st = :p",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":e": "EXPIRED", ":p": "PENDING"},
                )
                count += 1
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        return count

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        fe = Attr("created_at").lt(before_iso) & (
            Attr("status").eq("SUCCEEDED") |
            Attr("status").eq("FAILED") |
            Attr("status").eq("REJECTED") |
            Attr("status").eq("EXPIRED")
        )
        if tenant_id is not None:
            fe = fe & Attr("tenant_id").eq(tenant_id)
        items = _TABLE_JOBS.scan(
            FilterExpression=fe,
            ProjectionExpression="job_id",
        ).get("Items", [])
        with _TABLE_JOBS.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"job_id": item["job_id"]})
        return len(items)

    def get_pending_for_agent(self, agent_id: str) -> list:
        return _TABLE_JOBS.query(
            IndexName="agent-status-index",
            KeyConditionExpression=DKey("agent_id").eq(agent_id) & DKey("status").eq("PENDING"),
            Limit=1,
        ).get("Items", [])

    def list_admin(self, agent_id: Optional[str], tenant_id: Optional[str], created_by: Optional[str], limit: int, cursor: Optional[str] = None) -> list:
        def _combine(f_list):
            expr = f_list[0]
            for f in f_list[1:]:
                expr = expr & f
            return expr

        attr_filters = []
        if agent_id:
            attr_filters.append(Attr("agent_id").eq(agent_id))
        if created_by:
            attr_filters.append(Attr("created_by").eq(created_by))

        if tenant_id:
            kce = DKey("tenant_id").eq(tenant_id)
            if cursor:
                kce = kce & DKey("created_at").lt(cursor)
            kwargs: dict = {
                "IndexName": "tenant-history-index",
                "KeyConditionExpression": kce,
                "ScanIndexForward": False,
                "Limit": limit,
            }
            if attr_filters:
                kwargs["FilterExpression"] = _combine(attr_filters)
            return _TABLE_JOBS.query(**kwargs).get("Items", [])
        else:
            scan_filters = list(attr_filters)
            if cursor:
                scan_filters.append(Attr("created_at").lt(cursor))
            kwargs = {"Limit": limit}
            if scan_filters:
                kwargs["FilterExpression"] = _combine(scan_filters)
            return _TABLE_JOBS.scan(**kwargs).get("Items", [])

    def list_by_tenant(self, tenant_id: str, agent_id: Optional[str], limit: int, created_by: Optional[str] = None, cursor: Optional[str] = None) -> list:
        filters = []
        if agent_id:
            filters.append(Attr("agent_id").eq(agent_id))
        if created_by:
            filters.append(Attr("created_by").eq(created_by))
        kce = DKey("tenant_id").eq(tenant_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        kwargs: dict = {
            "IndexName": "tenant-history-index",
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if filters:
            expr = filters[0]
            for f in filters[1:]:
                expr = expr & f
            kwargs["FilterExpression"] = expr
        return _TABLE_JOBS.query(**kwargs).get("Items", [])

    def list_by_run(self, tenant_id: str, run_id: str) -> list:
        """Every job in one fan-out (a "run"), via the run-index GSI. Powers run
        status and idempotency-key dedupe."""
        items: list = []
        kwargs: dict = {
            "IndexName": "run-index",
            "KeyConditionExpression": DKey("run_id").eq(run_id),
            "FilterExpression": Attr("tenant_id").eq(tenant_id),
        }
        while True:
            resp = _TABLE_JOBS.query(**kwargs)
            items.extend(resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return items


class TenantRepo:
    def get(self, tenant_id: str) -> Optional[dict]:
        return _TABLE_TENANTS.get_item(Key={"tenant_id": tenant_id}).get("Item")

    def get_by_name(self, name: str) -> Optional[dict]:
        import boto3.dynamodb.conditions as c
        resp = _TABLE_TENANTS.scan(FilterExpression=c.Attr("name").eq(name), Limit=1)
        items = resp.get("Items", [])
        return items[0] if items else None

    def create(self, tenant: dict) -> None:
        from shared.exceptions import NameTakenError
        name = tenant.get("name", "")
        if name and self.get_by_name(name):
            raise NameTakenError(name)
        _TABLE_TENANTS.put_item(Item=tenant)

    def list_all(self) -> list:
        results = []
        kwargs: dict = {}
        while True:
            resp = _TABLE_TENANTS.scan(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return results

    def set_status(self, tenant_id: str, status: str) -> None:
        _TABLE_TENANTS.update_item(
            Key={"tenant_id": tenant_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": status},
        )

    def set_settings(self, tenant_id: str, settings: dict) -> None:
        _TABLE_TENANTS.update_item(
            Key={"tenant_id": tenant_id},
            UpdateExpression="SET settings = :s",
            ExpressionAttributeValues={":s": settings},
        )


class FleetRepo:
    def get(self, fleet_id: str) -> Optional[dict]:
        return _TABLE_FLEETS.get_item(Key={"fleet_id": fleet_id}).get("Item")

    def get_by_name(self, tenant_id: str, name: str) -> Optional[dict]:
        items = _TABLE_FLEETS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
            FilterExpression=Attr("name").eq(name),
            Limit=1,
        ).get("Items", [])
        return items[0] if items else None

    def get_by_join_token_hash(self, token_hash: str, now: int) -> Optional[dict]:
        if not token_hash:
            return None
        items = _TABLE_FLEETS.query(
            IndexName="join-token-hash-index",
            KeyConditionExpression=DKey("join_token_hash").eq(token_hash),
        ).get("Items", [])
        if items:
            return items[0]
        # Previous token still valid during the rotation grace window.
        items = _TABLE_FLEETS.query(
            IndexName="prev-join-token-hash-index",
            KeyConditionExpression=DKey("prev_join_token_hash").eq(token_hash),
        ).get("Items", [])
        for f in items:
            exp = f.get("prev_join_token_expires_at")
            if exp is not None and int(exp) > now:
                return f
        return None

    def create(self, fleet: dict) -> None:
        from shared.exceptions import NameTakenError
        name = fleet.get("name", "")
        if name and self.get_by_name(fleet.get("tenant_id", ""), name):
            raise NameTakenError(name)
        _TABLE_FLEETS.put_item(Item=fleet)

    def list_by_tenant(self, tenant_id: str) -> list:
        results: list = []
        kwargs = {"IndexName": "tenant-index",
                  "KeyConditionExpression": DKey("tenant_id").eq(tenant_id)}
        while True:
            resp = _TABLE_FLEETS.query(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return results

    def scan_all(self) -> list:
        """Every fleet across all tenants - used by the heartbeat reaper."""
        results: list = []
        kwargs: dict = {}
        while True:
            resp = _TABLE_FLEETS.scan(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return results

    def member_counts(self, tenant_id: str) -> dict:
        counts: dict = {}
        kwargs = {"IndexName": "tenant-index",
                  "KeyConditionExpression": DKey("tenant_id").eq(tenant_id),
                  "ProjectionExpression": "fleet_id"}
        while True:
            resp = _TABLE_AGENTS.query(**kwargs)
            for item in resp.get("Items", []):
                fid = item.get("fleet_id")
                if fid:
                    counts[fid] = counts.get(fid, 0) + 1
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return counts

    def rotate_token(self, fleet_id: str, new_hash: str,
                     prev_hash: Optional[str], prev_expires_at: Optional[int]) -> None:
        expr = "SET join_token_hash = :n, #st = :a"
        vals = {":n": new_hash, ":a": "ACTIVE"}
        if prev_hash is not None:
            expr += ", prev_join_token_hash = :ph, prev_join_token_expires_at = :pe"
            vals[":ph"] = prev_hash
            vals[":pe"] = prev_expires_at
        _TABLE_FLEETS.update_item(
            Key={"fleet_id": fleet_id},
            UpdateExpression=expr,
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues=vals,
        )

    def set_status(self, fleet_id: str, status: str) -> None:
        _TABLE_FLEETS.update_item(
            Key={"fleet_id": fleet_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": status},
        )

    def update_settings(self, fleet_id: str, fields: dict) -> None:
        if not fields:
            return
        from shared.exceptions import NameTakenError
        if "name" in fields:
            fleet = self.get(fleet_id)
            existing = self.get_by_name(fleet.get("tenant_id", ""), fields["name"]) if fleet else None
            if existing and existing.get("fleet_id") != fleet_id:
                raise NameTakenError(fields["name"])
        sets, names, vals = [], {}, {}
        for i, (k, v) in enumerate(fields.items()):
            nk, ph = f"#k{i}", f":v{i}"
            sets.append(f"{nk} = {ph}")
            names[nk] = k
            vals[ph] = v
        _TABLE_FLEETS.update_item(
            Key={"fleet_id": fleet_id},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=vals,
        )

    def delete(self, fleet_id: str) -> None:
        _TABLE_FLEETS.delete_item(Key={"fleet_id": fleet_id})


class UserRepo:
    def get(self, user_id: str) -> Optional[dict]:
        return _TABLE_USERS.get_item(Key={"user_id": user_id}).get("Item")

    def create(self, user: dict) -> None:
        _TABLE_USERS.put_item(Item=user)

    def list_by_tenant(self, tenant_id: str) -> list:
        return _TABLE_USERS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
        ).get("Items", [])

    def delete(self, user_id: str) -> None:
        _TABLE_USERS.delete_item(Key={"user_id": user_id})

    def set_allowed_agents(self, user_id: str, agent_ids: list) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET readwrite_agent_ids = :ids",
            ExpressionAttributeValues={":ids": agent_ids},
        )

    def set_agent_access(self, user_id: str, readwrite_agent_ids, readonly_agent_ids,
                         readwrite_fleet_ids=None, readonly_fleet_ids=None) -> None:
        """Set the full access scope: read-write / read-only, agents and fleets."""
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression=(
                "SET readwrite_agent_ids = :a, readonly_agent_ids = :r,"
                " readwrite_fleet_ids = :fa, readonly_fleet_ids = :fr"
            ),
            ExpressionAttributeValues={
                ":a": readwrite_agent_ids, ":r": readonly_agent_ids,
                ":fa": readwrite_fleet_ids, ":fr": readonly_fleet_ids,
            },
        )

    def get_by_username(self, tenant_id: str, username: str) -> Optional[dict]:
        items = _TABLE_USERS.query(
            IndexName="tenant-username-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id) & DKey("username").eq(username),
            Limit=1,
        ).get("Items", [])
        return items[0] if items else None

    def update_password(self, user_id: str, password_hash: str, must_reset: bool = False) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET password_hash = :ph, must_reset_password = :mr",
            ExpressionAttributeValues={":ph": password_hash, ":mr": must_reset},
        )

    def set_last_login(self, user_id: str, now_iso: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET last_login_at = :t",
            ExpressionAttributeValues={":t": now_iso},
        )

    def disable(self, user_id: str, now_iso: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET disabled_at = :t, #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":t": now_iso, ":s": "REVOKED"},
        )

    def enable(self, user_id: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="REMOVE disabled_at SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "ACTIVE"},
        )

    def set_role(self, user_id: str, role: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET #r = :r",
            ExpressionAttributeNames={"#r": "role"},
            ExpressionAttributeValues={":r": role},
        )

    def update_name(self, user_id: str, name: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET #n = :n",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={":n": name},
        )

    def revoke(self, user_id: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "REVOKED"},
        )

    def remove_agent_from_all_users(self, agent_id: str, tenant_id: str) -> None:
        users = _TABLE_USERS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
        ).get("Items", [])
        for user in users:
            sets, values = [], {}
            allowed = user.get("readwrite_agent_ids") or []
            if agent_id in allowed:
                sets.append("readwrite_agent_ids = :a")
                values[":a"] = [a for a in allowed if a != agent_id]
            readonly = user.get("readonly_agent_ids") or []
            if agent_id in readonly:
                sets.append("readonly_agent_ids = :r")
                values[":r"] = [a for a in readonly if a != agent_id]
            if sets:
                _TABLE_USERS.update_item(
                    Key={"user_id": user["user_id"]},
                    UpdateExpression="SET " + ", ".join(sets),
                    ExpressionAttributeValues=values,
                )


class ApprovalRepo:
    def create(self, approval: dict) -> None:
        item = dict(approval)
        # Lowercased shadow fields so `contains` search is case-insensitive
        # (DynamoDB has no ILIKE). The SQL repo uses ILIKE and stores neither.
        item["command_lc"] = (item.get("command") or "").lower()
        item["requester_name_lc"] = (item.get("requester_name") or "").lower()
        _TABLE_APPROVALS.put_item(Item=item)

    def get(self, approval_id: str) -> Optional[dict]:
        return _TABLE_APPROVALS.get_item(Key={"approval_id": approval_id}).get("Item")

    def list_by_agent(self, agent_id: str, status: Optional[str] = None, requested_by: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> list:
        kce = DKey("agent_id").eq(agent_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        kwargs: dict = {
            "IndexName": "agent-approvals-index",
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
        }
        if status is not None:
            kwargs["FilterExpression"] = Attr("status").eq(status)
        results = []
        while True:
            resp = _TABLE_APPROVALS.query(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        if status == "approved":
            results = self._lazy_expire(results)
            results = self._dedup_by_command(results)
        if requested_by is not None:
            results = [r for r in results if r.get("requested_by") == requested_by]
        if limit is not None:
            results = results[:limit]
        return results

    def list_by_tenant(self, tenant_id: str, agent_id: Optional[str] = None, status: Optional[str] = None, requested_by: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> list:
        kce = DKey("tenant_id").eq(tenant_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        kwargs: dict = {
            "IndexName": "tenant-approvals-index",
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
        }
        filters = []
        if agent_id is not None:
            filters.append(Attr("agent_id").eq(agent_id))
        if status is not None:
            filters.append(Attr("status").eq(status))
        if requested_by is not None:
            filters.append(Attr("requested_by").eq(requested_by))
        if filters:
            expr = filters[0]
            for f in filters[1:]:
                expr = expr & f
            kwargs["FilterExpression"] = expr
        results = []
        while True:
            resp = _TABLE_APPROVALS.query(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        if status == "approved":
            results = self._lazy_expire(results)
            results = self._dedup_by_command(results)
        if limit is not None:
            results = results[:limit]
        return results

    def search_by_tenant(self, tenant_id: str, *, status: Optional[str] = None, agent_id: Optional[str] = None,
                         agent_ids: Optional[list] = None, fleet_id: Optional[str] = None, fleet_ids: Optional[list] = None,
                         scope: Optional[str] = None, requested_by: Optional[str] = None, kind: Optional[str] = None,
                         k8s_agent_ids: Optional[list] = None,
                         q: Optional[str] = None, limit: int = 20, offset: int = 0) -> tuple:
        """Server-side search + pagination for DynamoDB.

        DynamoDB has no LIKE and no OFFSET. Status/agent/requester and the text
        query (`contains`, which is case-sensitive) are pushed into a
        FilterExpression so the filtering happens in Dynamo, not after transfer.
        Kind is applied in Python on the deserialized `k8s_rule` (robust to how
        None is stored) and offset/limit are sliced after the effective-list
        dedup. Returns (page_items, total).
        """
        kce = DKey("tenant_id").eq(tenant_id)
        kwargs: dict = {
            "IndexName": "tenant-approvals-index",
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
        }
        filters = []
        # Scope: an approval is in view if it matches any given agent/fleet filter.
        scope_conds = []
        if agent_id is not None:
            scope_conds.append(Attr("agent_id").eq(agent_id))
        if fleet_id is not None:
            scope_conds.append(Attr("fleet_id").eq(fleet_id))
        for aid in (agent_ids or []):
            scope_conds.append(Attr("agent_id").eq(aid))
        for fid in (fleet_ids or []):
            scope_conds.append(Attr("fleet_id").eq(fid))
        scoped = any(x is not None for x in (agent_id, fleet_id, agent_ids, fleet_ids))
        if scoped and not scope_conds:
            return [], 0   # restricted to an empty allow-list → sees nothing
        if scope_conds:
            expr = scope_conds[0]
            for s in scope_conds[1:]:
                expr = expr | s
            filters.append(expr)
        if status is not None:
            filters.append(Attr("status").eq(status))
        if requested_by is not None:
            filters.append(Attr("requested_by").eq(requested_by))
        if q:
            # Match the lowercased shadow fields for case-insensitive search.
            ql = q.lower()
            filters.append(Attr("command_lc").contains(ql) | Attr("requester_name_lc").contains(ql))
        if filters:
            expr = filters[0]
            for f in filters[1:]:
                expr = expr & f
            kwargs["FilterExpression"] = expr
        results = []
        while True:
            resp = _TABLE_APPROVALS.query(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        if status == "approved":
            results = self._lazy_expire(results)
            results = self._dedup_by_command(results)
        # host/k8s is the AGENT's type, not the rule type: a k8s agent's non-kubectl
        # (helm/flux) approval carries a host_rule but still belongs under Kubernetes.
        k8s_ids = set(k8s_agent_ids or [])
        if kind == "k8s":
            results = [r for r in results if r.get("k8s_rule") or r.get("agent_id") in k8s_ids]
        elif kind == "host":
            results = [r for r in results if not r.get("k8s_rule") and r.get("agent_id") not in k8s_ids]
        # scope: 'agent' = standalone (no fleet), 'fleet' = fleet-scoped.
        if scope == "agent":
            results = [r for r in results if not r.get("fleet_id")]
        elif scope == "fleet":
            results = [r for r in results if r.get("fleet_id")]
        total = len(results)
        page = results[offset: offset + limit] if limit else results[offset:]
        return page, total

    def _lazy_expire(self, records: list) -> list:
        now = _iso()
        active = []
        for r in records:
            if r.get("expires_at") and r["expires_at"] <= now:
                try:
                    _TABLE_APPROVALS.update_item(
                        Key={"approval_id": r["approval_id"]},
                        UpdateExpression="SET #st = :s",
                        ConditionExpression="attribute_exists(approval_id) AND #st = :cur",
                        ExpressionAttributeNames={"#st": "status"},
                        ExpressionAttributeValues={":s": "expired", ":cur": "approved"},
                    )
                except ClientError as e:
                    if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
            else:
                active.append(r)
        return active

    def _dedup_by_command(self, records: list) -> list:
        from collections import defaultdict
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
            with _TABLE_APPROVALS.batch_writer() as batch:
                for aid in to_delete_ids:
                    batch.delete_item(Key={"approval_id": aid})
        return kept

    def exists_pending(self, agent_id: str, command: str) -> bool:
        resp = _TABLE_APPROVALS.query(
            IndexName="agent-approvals-index",
            KeyConditionExpression=DKey("agent_id").eq(agent_id),
            FilterExpression=Attr("status").eq("pending") & Attr("command").eq(command),
            Limit=1,
        )
        return len(resp.get("Items", [])) > 0

    def exists_pending_fleet(self, fleet_id: str, command: str) -> bool:
        resp = _TABLE_APPROVALS.query(
            IndexName="fleet-approvals-index",
            KeyConditionExpression=DKey("fleet_id").eq(fleet_id),
            FilterExpression=Attr("status").eq("pending") & Attr("command").eq(command),
            Limit=1,
        )
        return len(resp.get("Items", [])) > 0

    def list_by_fleet(self, fleet_id: str, status: Optional[str] = None, requested_by: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> list:
        kce = DKey("fleet_id").eq(fleet_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        kwargs: dict = {
            "IndexName": "fleet-approvals-index",
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
        }
        if status is not None:
            kwargs["FilterExpression"] = Attr("status").eq(status)
        results = []
        while True:
            resp = _TABLE_APPROVALS.query(**kwargs)
            results.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        if status == "approved":
            results = self._lazy_expire(results)
        if requested_by is not None:
            results = [r for r in results if r.get("requested_by") == requested_by]
        if limit is not None:
            results = results[:limit]
        return results

    def delete(self, approval_id: str) -> None:
        _TABLE_APPROVALS.delete_item(Key={"approval_id": approval_id})

    def _delete_by_index(self, index: str, key: str, value: str) -> int:
        ids = []
        kwargs: dict = {"IndexName": index, "KeyConditionExpression": DKey(key).eq(value),
                        "ProjectionExpression": "approval_id"}
        while True:
            resp = _TABLE_APPROVALS.query(**kwargs)
            ids.extend(i["approval_id"] for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        if ids:
            with _TABLE_APPROVALS.batch_writer() as batch:
                for aid in ids:
                    batch.delete_item(Key={"approval_id": aid})
        return len(ids)

    def delete_by_agent(self, agent_id: str) -> int:
        """Purge every approval scoped to an agent (agent removal cleanup)."""
        return self._delete_by_index("agent-approvals-index", "agent_id", agent_id)

    def delete_by_fleet(self, fleet_id: str) -> int:
        """Purge every approval scoped to a fleet (fleet revoke-remove / delete cleanup)."""
        return self._delete_by_index("fleet-approvals-index", "fleet_id", fleet_id)

    def mark_expired(self, now_iso: str) -> int:
        resp = _TABLE_APPROVALS.scan(
            FilterExpression=Attr("status").eq("approved") & Attr("expires_at").lt(now_iso),
            ProjectionExpression="approval_id",
        )
        items = resp.get("Items", [])
        for item in items:
            try:
                _TABLE_APPROVALS.update_item(
                    Key={"approval_id": item["approval_id"]},
                    UpdateExpression="SET #st = :s",
                    ConditionExpression="#st = :cur",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={":s": "expired", ":cur": "approved"},
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        return len(items)

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        fe = (
            (Attr("status").eq("denied") & Attr("reviewed_at").lt(before_iso)) |
            (Attr("status").eq("expired") & Attr("expires_at").lt(before_iso))
        )
        if tenant_id is not None:
            fe = fe & Attr("tenant_id").eq(tenant_id)
        resp = _TABLE_APPROVALS.scan(FilterExpression=fe, ProjectionExpression="approval_id")
        items = resp.get("Items", [])
        with _TABLE_APPROVALS.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"approval_id": item["approval_id"]})
        return len(items)

    def update_status(self, approval_id: str, status: str, reviewed_at: str, reviewed_by: str, expires_at: Optional[str] = None) -> None:
        expr = "SET #st = :s, reviewed_at = :ra, reviewed_by = :rb"
        values: dict = {":s": status, ":ra": reviewed_at, ":rb": reviewed_by}
        if expires_at is not None:
            expr += ", expires_at = :exp"
            values[":exp"] = expires_at
        else:
            # expires_at=None means permanent - explicitly remove any existing expiry
            # so a re-approve-as-permanent actually clears the old timestamp.
            expr += " REMOVE expires_at"
        _TABLE_APPROVALS.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression=expr,
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues=values,
        )


class ApiTokenRepo:
    def create(self, token: dict) -> None:
        _TABLE_API_TOKENS.put_item(Item=token)

    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        items = _TABLE_API_TOKENS.query(
            IndexName="token-hash-index",
            KeyConditionExpression=DKey("token_hash").eq(token_hash),
            Limit=1,
        ).get("Items", [])
        return items[0] if items else None

    def list_by_user(self, user_id: str) -> list:
        return _TABLE_API_TOKENS.query(
            IndexName="user-index",
            KeyConditionExpression=DKey("user_id").eq(user_id),
        ).get("Items", [])

    def list_by_tenant(self, tenant_id: str) -> list:
        return _TABLE_API_TOKENS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
        ).get("Items", [])

    def revoke(self, token_id: str, now_iso: str) -> bool:
        try:
            _TABLE_API_TOKENS.update_item(
                Key={"token_id": token_id},
                UpdateExpression="SET #st = :s, revoked_at = :ra",
                ConditionExpression="attribute_exists(token_id)",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":s": "REVOKED", ":ra": now_iso},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def delete(self, token_id: str) -> None:
        """Hard-delete a token item (only meaningful after it has been revoked)."""
        try:
            _TABLE_API_TOKENS.delete_item(Key={"token_id": token_id})
        except ClientError:
            pass

    def touch(self, token_id: str, now_iso: str) -> None:
        try:
            _TABLE_API_TOKENS.update_item(
                Key={"token_id": token_id},
                UpdateExpression="SET last_used_at = :t",
                ExpressionAttributeValues={":t": now_iso},
            )
        except ClientError:
            pass

    def rename(self, token_id: str, name: str) -> bool:
        try:
            _TABLE_API_TOKENS.update_item(
                Key={"token_id": token_id},
                UpdateExpression="SET #n = :name",
                ConditionExpression="attribute_exists(token_id)",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={":name": name},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise


_TABLE_AGENT_HISTORY = _ddb.Table("reach-agent-history")


class RunRepo:
    def create(self, run: dict) -> None:
        _TABLE_RUNS.put_item(Item=run)

    def get(self, run_id: str) -> Optional[dict]:
        return _TABLE_RUNS.get_item(Key={"run_id": run_id}).get("Item")

    def set_counts(self, run_id: str, state: str, counts: dict, current_wave: Optional[int] = None) -> None:
        expr = "SET #s = :s, #c = :c"
        names = {"#s": "state", "#c": "counts"}
        values = {":s": state, ":c": counts}
        if current_wave is not None:
            expr += ", current_wave = :cw"
            values[":cw"] = current_wave
        _TABLE_RUNS.update_item(
            Key={"run_id": run_id},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def set_state(self, run_id: str, state: str) -> None:
        """Set only the run's control state (pause/cancel), leaving cached counts alone."""
        _TABLE_RUNS.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":s": state},
        )

    def list_by_tenant(self, tenant_id: str, limit: int = 50, cursor: Optional[str] = None) -> list:
        # created_at is the GSI RANGE key, so a cursor pages older-than newest-first.
        kce = DKey("tenant_id").eq(tenant_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        return _TABLE_RUNS.query(
            IndexName="tenant-runs-index",
            KeyConditionExpression=kce,
            ScanIndexForward=False,
            Limit=limit,
        ).get("Items", [])

    def list_by_fleet(self, fleet_id: str, limit: int = 50, cursor: Optional[str] = None) -> list:
        kce = DKey("fleet_id").eq(fleet_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        return _TABLE_RUNS.query(
            IndexName="fleet-runs-index",
            KeyConditionExpression=kce,
            ScanIndexForward=False,
            Limit=limit,
        ).get("Items", [])

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        fe = Attr("created_at").lt(before_iso)
        if tenant_id is not None:
            fe = fe & Attr("tenant_id").eq(tenant_id)
        items = _TABLE_RUNS.scan(
            FilterExpression=fe,
            ProjectionExpression="run_id",
        ).get("Items", [])
        with _TABLE_RUNS.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"run_id": item["run_id"]})
        return len(items)


class AgentHistoryRepo:
    def create(self, entry: dict) -> None:
        _TABLE_AGENT_HISTORY.put_item(Item=entry)

    def list_by_agent(self, agent_id: str, limit: int = 50) -> list:
        return _TABLE_AGENT_HISTORY.query(
            IndexName="agent-id-index",
            KeyConditionExpression=DKey("agent_id").eq(agent_id),
            ScanIndexForward=False,
            Limit=limit,
        ).get("Items", [])

    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None) -> int:
        # History rows carry a TTL; DynamoDB reaps them, so retention is a no-op here.
        return 0


class AuditRepo:
    def create(self, entry: dict) -> None:
        item = dict(entry)
        # Lowercased shadow fields so `contains` search is case-insensitive
        # (DynamoDB has no ILIKE). The SQL repo uses ILIKE and stores none of these.
        for field in ("actor_name", "resource_id", "ip_address"):
            item[f"{field}_lc"] = (item.get(field) or "").lower()
        _TABLE_AUDIT_LOGS.put_item(Item=item)

    def list_platform(self, limit: int = 100, cursor: Optional[str] = None,
                      action: Optional[str] = None, actor: Optional[str] = None,
                      resource: Optional[str] = None, ip: Optional[str] = None,
                      since: Optional[str] = None, until: Optional[str] = None,
                      tenant: Optional[str] = None) -> list:
        fe = Attr("created_at").lt(cursor) if cursor else Attr("log_id").exists()
        if since:
            fe = fe & Attr("created_at").gte(since)
        if until:
            fe = fe & Attr("created_at").lte(until)
        if action:
            fe = fe & Attr("action").eq(action)
        if tenant:
            fe = fe & Attr("tenant_id").contains(tenant)
        if actor:
            fe = fe & Attr("actor_name_lc").contains(actor.lower())
        if resource:
            fe = fe & Attr("resource_id_lc").contains(resource.lower())
        if ip:
            fe = fe & Attr("ip_address_lc").contains(ip.lower())
        resp = _TABLE_AUDIT_LOGS.scan(FilterExpression=fe, Limit=limit * 5)
        items = resp.get("Items", [])
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return [_remap_audit(i) for i in items[:limit]]

    def list_by_tenant(self, tenant_id: str, limit: int = 100, cursor: Optional[str] = None,
                       action: Optional[str] = None, actor: Optional[str] = None,
                       resource: Optional[str] = None, ip: Optional[str] = None,
                       since: Optional[str] = None, until: Optional[str] = None) -> list:
        kce = DKey("tenant_id").eq(tenant_id)
        if cursor:
            kce = kce & DKey("created_at").lt(cursor)
        fe = None
        if since:
            fe = Attr("created_at").gte(since)
        if until:
            fe = fe & Attr("created_at").lte(until) if fe else Attr("created_at").lte(until)
        if action:
            fe = fe & Attr("action").eq(action) if fe else Attr("action").eq(action)
        if actor:
            _f = Attr("actor_name_lc").contains(actor.lower())
            fe = fe & _f if fe else _f
        if resource:
            _f = Attr("resource_id_lc").contains(resource.lower())
            fe = fe & _f if fe else _f
        if ip:
            _f = Attr("ip_address_lc").contains(ip.lower())
            fe = fe & _f if fe else _f
        kwargs: dict = {
            "IndexName": "tenant-created-index",
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if fe:
            kwargs["FilterExpression"] = fe
        items = _TABLE_AUDIT_LOGS.query(**kwargs).get("Items", [])
        return [_remap_audit(i) for i in items]


    def delete_stale(self, before_iso: str, tenant_id: Optional[str] = None,
                     platform_only: bool = False) -> int:
        # Audit rows carry a TTL; DynamoDB reaps them, so retention is a no-op here.
        return 0


def _remap_audit(item: dict) -> dict:
    out = dict(item)
    if "event_metadata" in out:
        out["metadata"] = out.pop("event_metadata")
    return out
