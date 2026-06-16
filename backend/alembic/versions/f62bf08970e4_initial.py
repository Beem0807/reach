"""initial

Revision ID: f62bf08970e4
Revises:
Create Date: 2026-06-16 05:31:00.085245

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f62bf08970e4'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('agent_id'),
    )
    op.create_index('ix_agents_tenant_id', 'agents', ['tenant_id'])

    op.create_table(
        'tenant_tokens',
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('token_hash'),
    )

    op.create_table(
        'jobs',
        sa.Column('job_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
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


def downgrade() -> None:
    op.drop_table('jobs')
    op.drop_table('tenant_tokens')
    op.drop_table('agents')
