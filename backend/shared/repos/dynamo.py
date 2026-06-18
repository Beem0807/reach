from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key as DKey
from botocore.exceptions import ClientError

_ddb = boto3.resource("dynamodb")
_TABLE_AGENTS = _ddb.Table("reach-agents")
_TABLE_TENANTS = _ddb.Table("reach-tenants")
_TABLE_USERS = _ddb.Table("reach-users")
_TABLE_JOBS = _ddb.Table("reach-jobs")


class AgentRepo:
    def get(self, agent_id: str) -> Optional[dict]:
        return _TABLE_AGENTS.get_item(Key={"agent_id": agent_id}).get("Item")

    def claim(self, agent_id: str, fields: dict) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression=(
                "SET #st = :s, agent_token_hash = :h, machine_fingerprint = :fp,"
                " hostname = :hn, agent_version = :av, claimed_at = :ca,"
                " active_until = :au, last_heartbeat_at = :hb, token_issued_at = :ti"
            ),
            ExpressionAttributeNames={"#st": "status"},
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
            },
        )

    def update_heartbeat(self, agent_id: str, reactivate: bool, now_iso: str, agent_version: Optional[str] = None) -> None:
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
        kwargs: dict = {
            "Key": {"agent_id": agent_id},
            "UpdateExpression": "SET " + ", ".join(sets),
            "ExpressionAttributeValues": values,
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        _TABLE_AGENTS.update_item(**kwargs)

    def set_active_until(self, agent_id: str, active_until: int) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET active_until = :au",
            ExpressionAttributeValues={":au": active_until},
        )

    def list_by_tenant(self, tenant_id: str) -> list:
        return _TABLE_AGENTS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
        ).get("Items", [])

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

    def update_policy(self, agent_id: str, mode: str, approved_commands: list) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET #m = :m, approved_commands = :ac",
            ExpressionAttributeNames={"#m": "mode"},
            ExpressionAttributeValues={":m": mode, ":ac": approved_commands},
        )

    def reissue_install_token(self, agent_id: str, install_token_hash: str, expires_at: int) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression=(
                "SET #st = :created, install_token_hash = :ith, install_token_expires_at = :exp"
                " REMOVE agent_token_hash, machine_fingerprint, claimed_at"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":created": "CREATED",
                ":ith": install_token_hash,
                ":exp": expires_at,
            },
        )

    def update_agent_token_hash(self, agent_id: str, token_hash: str, token_issued_at: str) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET agent_token_hash = :th, token_issued_at = :ti",
            ExpressionAttributeValues={":th": token_hash, ":ti": token_issued_at},
        )

    def delete(self, agent_id: str) -> None:
        _TABLE_AGENTS.delete_item(Key={"agent_id": agent_id})

    def set_tags(self, agent_id: str, tags: list) -> None:
        _TABLE_AGENTS.update_item(
            Key={"agent_id": agent_id},
            UpdateExpression="SET tags = :t",
            ExpressionAttributeValues={":t": tags},
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
        return results


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
                " duration_ms = :dur, completed_at = :ca"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":s": fields["status"],
                ":ec": fields["exit_code"],
                ":out": fields["stdout"],
                ":err": fields["stderr"],
                ":dur": fields["duration_ms"],
                ":ca": fields["completed_at"],
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

    def create(self, tenant: dict) -> None:
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


class UserRepo:
    def get(self, user_id: str) -> Optional[dict]:
        return _TABLE_USERS.get_item(Key={"user_id": user_id}).get("Item")

    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        items = _TABLE_USERS.query(
            IndexName="token-hash-index",
            KeyConditionExpression=DKey("token_hash").eq(token_hash),
        ).get("Items", [])
        return items[0] if items else None

    def create(self, user: dict) -> None:
        _TABLE_USERS.put_item(Item=user)

    def list_by_tenant(self, tenant_id: str) -> list:
        return _TABLE_USERS.query(
            IndexName="tenant-index",
            KeyConditionExpression=DKey("tenant_id").eq(tenant_id),
        ).get("Items", [])

    def update_token_hash(self, user_id: str, token_hash: str) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET token_hash = :th",
            ExpressionAttributeValues={":th": token_hash},
        )

    def delete(self, user_id: str) -> None:
        _TABLE_USERS.delete_item(Key={"user_id": user_id})

    def set_allowed_agents(self, user_id: str, agent_ids: list) -> None:
        _TABLE_USERS.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET allowed_agent_ids = :ids",
            ExpressionAttributeValues={":ids": agent_ids},
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
