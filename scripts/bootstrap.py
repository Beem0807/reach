#!/usr/bin/env python3
"""
Bootstrap script — creates a user token + agent record in DynamoDB and
prints ready-to-paste install commands for Linux and Mac.

Required:
    --pepper    <TOKEN_PEPPER>   Same value used in SAM deploy
    --api-url   <url>            API Gateway URL from CloudFormation output

Optional:
    --s3-bucket <bucket>         S3 bucket hosting binaries (default: reach-releases)
    --tenant-id <id>             Reuse existing tenant (generated if omitted)
    --agent-id  <id>             Agent ID (generated if omitted)
    --mode      <mode>           Initial policy mode: wild|readonly|approved (default: wild)
    --install-token-ttl-hours    Hours until install token expires (default: 24)
    --region    <region>         AWS region (default: us-east-1)

Example:
    python scripts/bootstrap.py \\
        --pepper  a3f9b2c1... \\
        --api-url https://abc123.execute-api.us-east-1.amazonaws.com

Add agent to existing tenant:
    python scripts/bootstrap.py \\
        --pepper    a3f9b2c1... \\
        --api-url   https://abc123.execute-api.us-east-1.amazonaws.com \\
        --tenant-id tenant_xxxxx
"""

import argparse
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timezone

import boto3

TENANT_PREFIX = "tenant_"
AGENT_TOKEN_PREFIX = "agent_"
TENANT_TOKEN_PREFIX = "tok_"
INSTALL_TOKEN_PREFIX = "install_"


def hmac_token(pepper: str, raw: str) -> str:
    return hmac.new(pepper.encode(), raw.encode(), hashlib.sha256).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap reach — create user token + agent record",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pepper",   required=True, help="TOKEN_PEPPER used in SAM deploy")
    parser.add_argument("--api-url",  required=True, help="API Gateway URL (CloudFormation output)")
    parser.add_argument("--s3-bucket", default="reach-releases", help="S3 bucket hosting agent binaries")
    parser.add_argument("--tenant-id", default=None, help="Existing tenant ID (generated if omitted)")
    parser.add_argument("--agent-id",  default=None, help="Agent ID (generated if omitted)")
    parser.add_argument("--mode", default="wild", choices=["wild", "readonly", "approved"],
                        help="Initial policy mode (default: wild)")
    parser.add_argument("--install-token-ttl-hours", type=int, default=24, metavar="HOURS",
                        help="Hours until install token expires (default: 24)")
    parser.add_argument("--region",    default="us-east-1", help="AWS region (default: us-east-1)")
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    s3_bucket = args.s3_bucket
    ddb = boto3.resource("dynamodb", region_name=args.region)
    tokens_table = ddb.Table("reach-tenant-tokens")
    agents_table = ddb.Table("reach-agents")

    tenant_id = args.tenant_id or (TENANT_PREFIX + secrets.token_urlsafe(8))
    pepper = args.pepper

    # --- Tenant token ---
    raw_tenant_token = TENANT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    tenant_token_hash = hmac_token(pepper, raw_tenant_token)

    tokens_table.put_item(Item={
        "token_hash": tenant_token_hash,
        "tenant_id": tenant_id,
        "created_at": now_iso(),
    })

    # --- Agent + install token ---
    agent_id = args.agent_id or (AGENT_TOKEN_PREFIX + secrets.token_urlsafe(8))
    raw_install_token = INSTALL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    install_token_hash = hmac_token(pepper, raw_install_token)
    expires_at = int(time.time()) + args.install_token_ttl_hours * 3600

    agents_table.put_item(Item={
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "status": "CREATED",
        "install_token_hash": install_token_hash,
        "install_token_expires_at": expires_at,
        "created_at": now_iso(),
        "mode": args.mode,
    })

    s3_base = f"https://{s3_bucket}.s3.amazonaws.com"
    expires_str = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()

    print("=" * 60)
    print(f"TENANT ID:     {tenant_id}")
    print(f"AGENT ID:      {agent_id}")
    print(f"Install token expires: {expires_str}")
    print("=" * 60)

    print()
    print("── Linux ──────────────────────────────────────────────────")
    print(f'curl -fsSL {s3_base}/install.sh | sudo bash -s -- \\')
    print(f'  --api-url       "{api_url}" \\')
    print(f'  --agent-id      "{agent_id}" \\')
    print(f'  --install-token "{raw_install_token}"')

    print()
    print("── Mac (Apple Silicon) ────────────────────────────────────")
    print(f'mkdir -p /tmp/reach-agent')
    print(f'curl -fsSL {s3_base}/reach-agent-darwin-arm64 -o /tmp/reach-agent/reach-agent')
    print(f'chmod +x /tmp/reach-agent/reach-agent')
    print(f'cat > /tmp/reach-agent/config.json <<\'EOF\'')
    print(f'{{"api_url":"{api_url}","agent_id":"{agent_id}","install_token":"{raw_install_token}"}}')
    print(f'EOF')
    print(f'REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent')

    print()
    print("── Mac (Intel) ────────────────────────────────────────────")
    print(f'mkdir -p /tmp/reach-agent')
    print(f'curl -fsSL {s3_base}/reach-agent-darwin-amd64 -o /tmp/reach-agent/reach-agent')
    print(f'chmod +x /tmp/reach-agent/reach-agent')
    print(f'cat > /tmp/reach-agent/config.json <<\'EOF\'')
    print(f'{{"api_url":"{api_url}","agent_id":"{agent_id}","install_token":"{raw_install_token}"}}')
    print(f'EOF')
    print(f'REACH_CONFIG_PATH=/tmp/reach-agent/config.json /tmp/reach-agent/reach-agent')

    # Read version from pyproject.toml to build correct wheel filename
    _pyproject = os.path.join(os.path.dirname(__file__), "../cli/pyproject.toml")
    _version = "0.1.0"
    try:
        with open(_pyproject) as _f:
            for _line in _f:
                _m = re.match(r'^version\s*=\s*"(.+)"', _line)
                if _m:
                    _version = _m.group(1)
                    break
    except OSError:
        pass
    _wheel = f"reach-{_version}-py3-none-any.whl"

    print()
    print("── CLI setup (your machine) ───────────────────────────────")
    print(f'pip install https://{s3_bucket}.s3.amazonaws.com/{_wheel}')
    print(f'reach login --api-url "{api_url}" --token "{raw_tenant_token}"')
    print(f'reach use {agent_id}')
    print("=" * 60)


if __name__ == "__main__":
    main()
