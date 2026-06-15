from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key as DKey
from botocore.exceptions import ClientError

_ddb = boto3.resource("dynamodb")
_TABLE_AGENTS = _ddb.Table("reach-agents")
_TABLE_TOKENS = _ddb.Table("reach-tenant-tokens")
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
                " active_until = :au, last_heartbeat_at = :hb"
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
            },
        )

    def update_heartbeat(self, agent_id: str, reactivate: bool, now_iso: str) -> None:
        expr = "SET last_heartbeat_at = :hb"
        names: dict = {}
        values: dict = {":hb": now_iso}
        if reactivate:
            expr += ", #st = :active"
            names["#st"] = "status"
            values[":active"] = "ACTIVE"
        kwargs: dict = {
            "Key": {"agent_id": agent_id},
            "UpdateExpression": expr,
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

    def get_pending_for_agent(self, agent_id: str) -> list:
        return _TABLE_JOBS.query(
            IndexName="agent-status-index",
            KeyConditionExpression=DKey("agent_id").eq(agent_id) & DKey("status").eq("PENDING"),
            Limit=1,
        ).get("Items", [])

    def list_by_tenant(self, tenant_id: str, agent_id: Optional[str], limit: int) -> list:
        kwargs: dict = {
            "IndexName": "tenant-history-index",
            "KeyConditionExpression": DKey("tenant_id").eq(tenant_id),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if agent_id:
            kwargs["FilterExpression"] = Attr("agent_id").eq(agent_id)
        return _TABLE_JOBS.query(**kwargs).get("Items", [])


class TokenRepo:
    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        return _TABLE_TOKENS.get_item(Key={"token_hash": token_hash}).get("Item")

    def create(self, token: dict) -> None:
        _TABLE_TOKENS.put_item(Item=token)
