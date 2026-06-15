import boto3

_ddb = boto3.resource("dynamodb")

TABLE_AGENTS = _ddb.Table("reach-agents")
TABLE_TOKENS = _ddb.Table("reach-tenant-tokens")
TABLE_JOBS = _ddb.Table("reach-jobs")
