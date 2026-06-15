#!/usr/bin/env python3
"""
Manage agent policy directly in DynamoDB.
Requires AWS credentials. Agent IDs or aliases accepted.

Commands:
    python scripts/policy.py show <agent>
    python scripts/policy.py mode <agent> <wild|readonly|approved>
    python scripts/policy.py approved add <agent> <command>
    python scripts/policy.py approved remove <agent> <command>
    python scripts/policy.py approved list <agent>
"""

import argparse
import json
import sys
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr

AGENTS_TABLE = "reach-agents"


def resolve_alias(name: str) -> str:
    config_file = Path.home() / ".reach" / "config.json"
    if config_file.exists():
        cfg = json.loads(config_file.read_text())
        return cfg.get("aliases", {}).get(name, name)
    return name


def get_agent(table, agent_id: str) -> dict:
    resp = table.get_item(Key={"agent_id": agent_id})
    agent = resp.get("Item")
    if not agent:
        print(f"Agent not found: {agent_id}")
        sys.exit(1)
    return agent


def get_table(region: str):
    return boto3.resource("dynamodb", region_name=region).Table(AGENTS_TABLE)


# ---------------------------------------------------------------------------

def cmd_show(agent_id: str, region: str):
    agent = get_agent(get_table(region), agent_id)
    mode = agent.get("mode", "wild")
    commands = agent.get("approved_commands", [])

    print(f"Agent:    {agent_id}")
    print(f"Hostname: {agent.get('hostname') or '-'}")
    print(f"Mode:     {mode}")
    if commands:
        print("Approved commands:")
        for c in commands:
            print(f"  - {c}")
    elif mode == "approved":
        print("Approved commands: (none — all commands blocked)")


def cmd_mode(agent_id: str, mode: str, region: str):
    table = get_table(region)
    get_agent(table, agent_id)  # verify exists
    table.update_item(
        Key={"agent_id": agent_id},
        UpdateExpression="SET #m = :mode",
        ExpressionAttributeNames={"#m": "mode"},
        ExpressionAttributeValues={":mode": mode},
    )
    print(f"✓ {agent_id}  mode={mode}")


def cmd_approved_add(agent_id: str, command: str, region: str):
    table = get_table(region)
    agent = get_agent(table, agent_id)
    existing = agent.get("approved_commands", [])
    if command in existing:
        print(f"Already in allowlist: {command}")
        return
    table.update_item(
        Key={"agent_id": agent_id},
        UpdateExpression="SET approved_commands = list_append(if_not_exists(approved_commands, :empty), :cmd)",
        ExpressionAttributeValues={":cmd": [command], ":empty": []},
    )
    print(f"✓ Added: {command}")


def cmd_approved_remove(agent_id: str, command: str, region: str):
    table = get_table(region)
    agent = get_agent(table, agent_id)
    existing = list(agent.get("approved_commands", []))
    if command not in existing:
        print(f"Not in allowlist: {command}")
        sys.exit(1)
    existing.remove(command)
    table.update_item(
        Key={"agent_id": agent_id},
        UpdateExpression="SET approved_commands = :cmds",
        ExpressionAttributeValues={":cmds": existing},
    )
    print(f"✓ Removed: {command}")


def cmd_approved_list(agent_id: str, region: str):
    agent = get_agent(get_table(region), agent_id)
    commands = agent.get("approved_commands", [])
    if not commands:
        print("No approved commands set.")
        return
    for c in commands:
        print(f"  - {c}")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Manage reach agent policy")
    parser.add_argument("--region", default="us-east-1")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="Show policy for an agent").add_argument("agent")

    p_mode = sub.add_parser("mode", help="Set agent mode")
    p_mode.add_argument("agent")
    p_mode.add_argument("mode", choices=["wild", "readonly", "approved"])

    p_approved = sub.add_parser("approved", help="Manage approved command list")
    approved_sub = p_approved.add_subparsers(dest="approved_cmd", required=True)

    p_add = approved_sub.add_parser("add", help="Add a command to the allowlist")
    p_add.add_argument("agent")
    p_add.add_argument("command")

    p_remove = approved_sub.add_parser("remove", help="Remove a command from the allowlist")
    p_remove.add_argument("agent")
    p_remove.add_argument("command")

    p_list = approved_sub.add_parser("list", help="List approved commands")
    p_list.add_argument("agent")

    args = parser.parse_args()

    if args.command == "show":
        cmd_show(resolve_alias(args.agent), args.region)
    elif args.command == "mode":
        cmd_mode(resolve_alias(args.agent), args.mode, args.region)
    elif args.command == "approved":
        agent_id = resolve_alias(args.agent)
        if args.approved_cmd == "add":
            cmd_approved_add(agent_id, args.command, args.region)
        elif args.approved_cmd == "remove":
            cmd_approved_remove(agent_id, args.command, args.region)
        elif args.approved_cmd == "list":
            cmd_approved_list(agent_id, args.region)


if __name__ == "__main__":
    main()
