"""initial schema

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
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('tenant_id'),
    )

    op.create_table(
        'users',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_index('ix_users_tenant_id', 'users', ['tenant_id'])
    op.create_index('ix_users_token_hash', 'users', ['token_hash'], unique=True)

    op.create_table(
        'agents',
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('hostname', sa.String(), nullable=True),
        sa.Column('agent_version', sa.String(), nullable=True),
        sa.Column('machine_fingerprint', sa.String(), nullable=True),
        sa.Column('mode', sa.String(), nullable=False),
        sa.Column('approved_commands', sa.JSON(), nullable=True),
        sa.Column('agent_token_hash', sa.String(), nullable=True),
        sa.Column('install_token_hash', sa.String(), nullable=True),
        sa.Column('install_token_expires_at', sa.Integer(), nullable=True),
        sa.Column('claimed_at', sa.String(), nullable=True),
        sa.Column('last_heartbeat_at', sa.String(), nullable=True),
        sa.Column('active_until', sa.Integer(), nullable=True),
        sa.Column('token_issued_at', sa.String(), nullable=True),
        sa.Column('type', sa.String(), nullable=True, server_default='manual'),
        sa.Column('fleet_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('agent_id'),
    )
    op.create_index('ix_agents_tenant_id', 'agents', ['tenant_id'])

    op.create_table(
        'jobs',
        sa.Column('job_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('mode', sa.String(), nullable=True),
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


def downgrade() -> None:
    op.drop_table('jobs')
    op.drop_table('agents')
    op.drop_table('users')
    op.drop_table('tenants')
