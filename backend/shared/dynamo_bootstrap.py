"""Create the DynamoDB tables for a self-managed (non-Lambda) deployment.

The Lambda deployment creates its tables via CloudFormation. A FastAPI/container
deployment running with ``STORAGE_BACKEND=dynamo`` has no such bootstrap, so this
module creates the same tables (idempotently) from the canonical schema in
``dynamo_schema``. It is the DynamoDB counterpart to ``alembic upgrade head``.

Usage:
    python -m shared.dynamo_bootstrap

Requires AWS credentials (env vars, ECS task role, EKS IRSA, or instance profile)
and ``AWS_REGION`` (or ``AWS_DEFAULT_REGION``). Tables are created on-demand
(PAY_PER_REQUEST). Existing tables are left untouched.
"""
import logging
import os
import sys

from shared.dynamo_schema import TABLES

# boto3 is imported lazily inside the functions below so this module (and the
# schema it exposes) can be imported in Postgres-only environments where boto3
# is installed but unused.

logger = logging.getLogger("dynamo_bootstrap")


def _build_args(spec: dict) -> dict:
    args = {
        "TableName": spec["TableName"],
        "KeySchema": spec["KeySchema"],
        "AttributeDefinitions": spec["AttributeDefinitions"],
        "BillingMode": "PAY_PER_REQUEST",
    }
    gsis = spec.get("GlobalSecondaryIndexes") or []
    if gsis:
        args["GlobalSecondaryIndexes"] = [
            {**g, "Projection": {"ProjectionType": "ALL"}} for g in gsis
        ]
    return args


def bootstrap(client=None) -> dict:
    """Create any missing tables. Returns {"created": [...], "existing": [...]}."""
    from botocore.exceptions import ClientError
    if client is None:
        import boto3
        client = boto3.client("dynamodb")
    created, existing = [], []
    for spec in TABLES:
        name = spec["TableName"]
        try:
            client.create_table(**_build_args(spec))
            created.append(name)
            logger.info("Creating DynamoDB table %s ...", name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                existing.append(name)
                logger.info("DynamoDB table %s already exists - skipping", name)
            else:
                raise

    # Wait for newly-created tables to become ACTIVE before returning.
    waiter = client.get_waiter("table_exists")
    for name in created:
        waiter.wait(TableName=name)
        logger.info("DynamoDB table %s is ACTIVE", name)

    return {"created": created, "existing": existing}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        logger.error("AWS_REGION (or AWS_DEFAULT_REGION) must be set for DynamoDB bootstrap")
        return 1
    result = bootstrap()
    logger.info(
        "DynamoDB bootstrap complete: %d created, %d already existed.",
        len(result["created"]), len(result["existing"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
