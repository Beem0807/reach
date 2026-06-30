from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key as DKey
from botocore.exceptions import ClientError

from shared.policy import compute_access_level
from shared.response import _iso

_ddb = boto3.resource("dynamodb")
_TABLE_AGENTS = _ddb.Table("reach-agents")
_TABLE_TENANTS = _ddb.Table("reach-tenants")
_TABLE_USERS = _ddb.Table("reach-users")
_TABLE_JOBS = _ddb.Table("reach-jobs")
_TABLE_APPROVALS = _ddb.Table("reach-approvals")
_TABLE_API_TOKENS = _ddb.Table("reach-api-tokens")
_TABLE_AUDIT_LOGS = _ddb.Table("reach-audit-logs")


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
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET k8s_permissions = :p, k8s_permissions_hash = :h",
            ExpressionAttributeValues={":p": permissions, ":h": perm_hash},
        )

    def acknowledge_k8s_permissions(self, agent_id: str, perm_hash: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET k8s_permissions_acked_hash = :h",
            ExpressionAttributeValues={":h": perm_hash},
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
                " duration_ms = :dur, completed_at = :ca, is_write = :iw"
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
            },
        )

    def mark_expired(self, job_id: str) -> None:
        _TABLE_JOBS.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "EXPIRED"},
        )

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

    def delete_stale(self, before_iso: str) -> int:
        terminal = ["SUCCEEDED", "FAILED", "REJECTED", "EXPIRED"]
        fe = Attr("created_at").lt(before_iso) & (
            Attr("status").eq("SUCCEEDED") |
            Attr("status").eq("FAILED") |
            Attr("status").eq("REJECTED") |
            Attr("status").eq("EXPIRED")
        )
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
            UpdateExpression="SET allowed_agent_ids = :ids",
            ExpressionAttributeValues={":ids": agent_ids},
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
            current = user.get("allowed_agent_ids") or []
            if agent_id in current:
                new_list = [a for a in current if a != agent_id]
                _TABLE_USERS.update_item(
                    Key={"user_id": user["user_id"]},
                    UpdateExpression="SET allowed_agent_ids = :ids",
                    ExpressionAttributeValues={":ids": new_list},
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
                         requested_by: Optional[str] = None, kind: Optional[str] = None, q: Optional[str] = None,
                         limit: int = 20, offset: int = 0) -> tuple:
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
        if agent_id is not None:
            filters.append(Attr("agent_id").eq(agent_id))
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
        if kind == "k8s":
            results = [r for r in results if r.get("k8s_rule")]
        elif kind == "host":
            results = [r for r in results if not r.get("k8s_rule")]
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
        kwargs: dict = {
            "IndexName": "agent-approvals-index",
            "KeyConditionExpression": DKey("agent_id").eq(agent_id),
            "FilterExpression": Attr("status").eq("pending") & Attr("command").eq(command),
            "Limit": 1,
        }
        resp = _TABLE_APPROVALS.query(**kwargs)
        return len(resp.get("Items", [])) > 0

    def delete(self, approval_id: str) -> None:
        _TABLE_APPROVALS.delete_item(Key={"approval_id": approval_id})

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

    def delete_stale(self, before_iso: str) -> int:
        resp = _TABLE_APPROVALS.scan(
            FilterExpression=(
                (Attr("status").eq("denied") & Attr("reviewed_at").lt(before_iso)) |
                (Attr("status").eq("expired") & Attr("expires_at").lt(before_iso))
            ),
            ProjectionExpression="approval_id",
        )
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

    def delete_stale(self, before_iso: str) -> int:
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
                      since: Optional[str] = None, until: Optional[str] = None) -> list:
        fe = Attr("created_at").lt(cursor) if cursor else Attr("log_id").exists()
        if since:
            fe = fe & Attr("created_at").gte(since)
        if until:
            fe = fe & Attr("created_at").lte(until)
        if action:
            fe = fe & Attr("action").eq(action)
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


    def delete_stale(self, before_iso: str) -> int:
        return 0


def _remap_audit(item: dict) -> dict:
    out = dict(item)
    if "event_metadata" in out:
        out["metadata"] = out.pop("event_metadata")
    return out
