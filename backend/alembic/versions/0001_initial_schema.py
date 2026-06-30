"""initial schema - full table definitions including auth, audit, and api tokens

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0001_initial_schema'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenants',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, server_default='ACTIVE'),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('tenant_id'),
    )
    op.create_index('ix_tenants_name', 'tenants', ['name'], unique=True)

    op.create_table(
        'users',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('allowed_agent_ids', sa.JSON(), nullable=True),
        sa.Column('allowed_fleet_ids', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, server_default='ACTIVE'),
        # Password-based login fields
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('password_hash', sa.String(), nullable=True),
        sa.Column('role', sa.String(), nullable=True),
        sa.Column('must_reset_password', sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column('disabled_at', sa.String(), nullable=True),
        sa.Column('last_login_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_index('ix_users_tenant_id', 'users', ['tenant_id'])
    op.create_index('ix_users_tenant_username', 'users', ['tenant_id', 'username'], unique=True)

    op.create_table(
        'agents',
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('hostname', sa.String(), nullable=True),
        sa.Column('agent_version', sa.String(), nullable=True),
        sa.Column('machine_fingerprint', sa.String(), nullable=True),
        sa.Column('mode', sa.String(), nullable=False),
        sa.Column('running_as_root', sa.String(), nullable=True),
        sa.Column('agent_token_hash', sa.String(), nullable=True),
        sa.Column('install_token_hash', sa.String(), nullable=True),
        sa.Column('install_token_expires_at', sa.Integer(), nullable=True),
        sa.Column('claimed_at', sa.String(), nullable=True),
        sa.Column('last_heartbeat_at', sa.String(), nullable=True),
        sa.Column('active_until', sa.Integer(), nullable=True),
        sa.Column('token_issued_at', sa.String(), nullable=True),
        sa.Column('type', sa.String(), nullable=True, server_default='host'),
        sa.Column('k8s_permissions', sa.JSON(), nullable=True),
        sa.Column('k8s_permissions_hash', sa.String(), nullable=True),
        sa.Column('k8s_permissions_acked_hash', sa.String(), nullable=True),
        sa.Column('fleet_id', sa.String(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('rotation_requested', sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column('grant_service_mgmt', sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column('grant_docker', sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column('service_mgmt_detected', sa.Boolean(), nullable=True),
        sa.Column('docker_detected', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('agent_id'),
    )
    op.create_index('ix_agents_tenant_id', 'agents', ['tenant_id'])
    # Credential-only auth: agents are looked up by token hash, never by a
    # client-supplied agent_id. Unique so each hash maps to one agent.
    op.create_index('ix_agents_install_token_hash', 'agents', ['install_token_hash'], unique=True)
    op.create_index('ix_agents_agent_token_hash', 'agents', ['agent_token_hash'], unique=True)

    op.create_table(
        'jobs',
        sa.Column('job_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('mode', sa.String(), nullable=True),
        sa.Column('is_write', sa.Boolean(), nullable=True),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('stdout', sa.Text(), nullable=True),
        sa.Column('stderr', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('started_at', sa.String(), nullable=True),
        sa.Column('completed_at', sa.String(), nullable=True),
        sa.Column('expires_at', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('job_id'),
    )
    op.create_index('ix_jobs_tenant_id', 'jobs', ['tenant_id'])
    op.create_index('ix_jobs_agent_id', 'jobs', ['agent_id'])
    op.create_index('ix_jobs_created_at', 'jobs', ['created_at'])
    op.create_index('ix_jobs_created_by', 'jobs', ['created_by'])

    op.create_table(
        'approvals',
        sa.Column('approval_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('k8s_rule', sa.JSON(), nullable=True),
        sa.Column('requested_by', sa.String(), nullable=False),
        sa.Column('requester_name', sa.String(), nullable=True),
        sa.Column('job_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('expires_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('reviewed_at', sa.String(), nullable=True),
        sa.Column('reviewed_by', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('approval_id'),
    )
    op.create_index('ix_approvals_tenant_id', 'approvals', ['tenant_id'])
    op.create_index('ix_approvals_agent_id', 'approvals', ['agent_id'])
    op.create_index('ix_approvals_created_at', 'approvals', ['created_at'])

    # API tokens - named tokens created by tenant admins for CLI/MCP
    op.create_table(
        'api_tokens',
        sa.Column('token_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, server_default='ACTIVE'),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('last_used_at', sa.String(), nullable=True),
        sa.Column('revoked_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('token_id'),
    )
    op.create_index('ix_api_tokens_token_hash', 'api_tokens', ['token_hash'], unique=True)
    op.create_index('ix_api_tokens_user_id', 'api_tokens', ['user_id'])
    op.create_index('ix_api_tokens_tenant_id', 'api_tokens', ['tenant_id'])

    # Audit logs - immutable event stream
    op.create_table(
        'audit_logs',
        sa.Column('log_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=True),     # null = platform-level event
        sa.Column('actor_id', sa.String(), nullable=True),      # user_id or 'platform_admin'
        sa.Column('actor_name', sa.String(), nullable=True),
        sa.Column('actor_role', sa.String(), nullable=True),    # PLATFORM_ADMIN | TENANT_ADMIN | TENANT_USER
        sa.Column('action', sa.String(), nullable=False),       # e.g. tenant.created, user.login
        sa.Column('resource_type', sa.String(), nullable=True),
        sa.Column('resource_id', sa.String(), nullable=True),
        sa.Column('event_metadata', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('log_id'),
    )
    op.create_index('ix_audit_logs_tenant_id', 'audit_logs', ['tenant_id'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])
    op.create_index('ix_audit_logs_actor_id', 'audit_logs', ['actor_id'])

    # Agent history - immutable state-transition log
    op.create_table(
        'agent_history',
        sa.Column('history_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('from_status', sa.String(), nullable=True),
        sa.Column('to_status', sa.String(), nullable=False),
        sa.Column('triggered_by', sa.String(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('history_id'),
    )
    op.create_index('ix_agent_history_agent_id', 'agent_history', ['agent_id'])
    op.create_index('ix_agent_history_tenant_id', 'agent_history', ['tenant_id'])
    op.create_index('ix_agent_history_created_at', 'agent_history', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_agent_history_created_at', table_name='agent_history')
    op.drop_index('ix_agent_history_tenant_id', table_name='agent_history')
    op.drop_index('ix_agent_history_agent_id', table_name='agent_history')
    op.drop_table('agent_history')
    op.drop_index('ix_tenants_name', table_name='tenants')
    op.drop_table('audit_logs')
    op.drop_table('api_tokens')
    op.drop_table('approvals')
    op.drop_table('jobs')
    op.drop_table('agents')
    op.drop_table('users')
    op.drop_table('tenants')
