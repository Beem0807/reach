"""Canonical DynamoDB table schema.

This mirrors the table/GSI definitions in ``deploy/lambda/template.yaml`` (the
source of truth for the Lambda deployment) so that a FastAPI/container
deployment using ``STORAGE_BACKEND=dynamo`` can create the same tables itself
via ``dynamo_bootstrap``. ``test_dynamo_schema_matches_template`` asserts the
two stay in sync.

All tables use on-demand billing (PAY_PER_REQUEST) and project ALL attributes
into every index, matching the CloudFormation template.
"""

TABLES = [
    {
        "TableName": "reach-agents",
        "KeySchema": [{"AttributeName": "agent_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "agent_id", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "install_token_hash", "AttributeType": "S"},
            {"AttributeName": "agent_token_hash", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "tenant-index", "KeySchema": [{"AttributeName": "tenant_id", "KeyType": "HASH"}]},
            {"IndexName": "install-token-hash-index", "KeySchema": [{"AttributeName": "install_token_hash", "KeyType": "HASH"}]},
            {"IndexName": "agent-token-hash-index", "KeySchema": [{"AttributeName": "agent_token_hash", "KeyType": "HASH"}]},
        ],
    },
    {
        "TableName": "reach-tenants",
        "KeySchema": [{"AttributeName": "tenant_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "tenant_id", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [],
    },
    {
        "TableName": "reach-users",
        "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "token_hash", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "username", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "token-hash-index", "KeySchema": [{"AttributeName": "token_hash", "KeyType": "HASH"}]},
            {"IndexName": "tenant-index", "KeySchema": [{"AttributeName": "tenant_id", "KeyType": "HASH"}]},
            {"IndexName": "tenant-username-index", "KeySchema": [
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "username", "KeyType": "RANGE"},
            ]},
        ],
    },
    {
        "TableName": "reach-jobs",
        "KeySchema": [{"AttributeName": "job_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "agent_id", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "agent-status-index", "KeySchema": [
                {"AttributeName": "agent_id", "KeyType": "HASH"},
                {"AttributeName": "status", "KeyType": "RANGE"},
            ]},
            {"IndexName": "tenant-history-index", "KeySchema": [
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ]},
        ],
    },
    {
        "TableName": "reach-approvals",
        "KeySchema": [{"AttributeName": "approval_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "approval_id", "AttributeType": "S"},
            {"AttributeName": "agent_id", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "agent-approvals-index", "KeySchema": [
                {"AttributeName": "agent_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ]},
            {"IndexName": "tenant-approvals-index", "KeySchema": [
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ]},
        ],
    },
    {
        "TableName": "reach-api-tokens",
        "KeySchema": [{"AttributeName": "token_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "token_id", "AttributeType": "S"},
            {"AttributeName": "token_hash", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "token-hash-index", "KeySchema": [{"AttributeName": "token_hash", "KeyType": "HASH"}]},
            {"IndexName": "user-index", "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}]},
            {"IndexName": "tenant-index", "KeySchema": [{"AttributeName": "tenant_id", "KeyType": "HASH"}]},
        ],
    },
    {
        "TableName": "reach-audit-logs",
        "KeySchema": [{"AttributeName": "log_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "log_id", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "tenant-created-index", "KeySchema": [
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ]},
        ],
    },
    {
        "TableName": "reach-agent-history",
        "KeySchema": [{"AttributeName": "history_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "history_id", "AttributeType": "S"},
            {"AttributeName": "agent_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {"IndexName": "agent-id-index", "KeySchema": [
                {"AttributeName": "agent_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ]},
        ],
    },
]
