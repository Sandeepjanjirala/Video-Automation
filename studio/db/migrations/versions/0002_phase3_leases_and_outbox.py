"""Phase 3 leases, outbox, stages, and usage fields

Revision ID: 0002_phase3_leases_and_outbox
Revises: 0001_phase2_initial
Create Date: 2026-10-09 05:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0002_phase3_leases_and_outbox'
down_revision: Union[str, None] = '0001_phase2_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update jobs table
    op.add_column('jobs', sa.Column('stage', sa.String(length=64), server_default='queued', nullable=False))
    op.add_column('jobs', sa.Column('operation_key', sa.String(length=128), nullable=True))
    op.add_column('jobs', sa.Column('lease_token', sa.String(length=64), nullable=True))
    op.add_column('jobs', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('cancelled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('jobs', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('subprocess_pid', sa.Integer(), nullable=True))

    op.create_index('ix_jobs_operation_key', 'jobs', ['operation_key'], unique=True)
    op.create_index('ix_jobs_lease_token', 'jobs', ['lease_token'])
    op.create_index('ix_jobs_lease_expires_at', 'jobs', ['lease_expires_at'])
    op.create_index('ix_jobs_cancelled', 'jobs', ['cancelled'])

    # 2. Update job_stages table
    op.add_column('job_stages', sa.Column('billable_units', sa.Integer(), server_default='0', nullable=False))
    op.add_column('job_stages', sa.Column('ambiguous_outcome', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('job_stages', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))

    # 3. Update outbox table
    op.add_column('outbox', sa.Column('attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('outbox', sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('outbox', sa.Column('error', sa.Text(), nullable=True))
    op.add_column('outbox', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_outbox_unprocessed', 'outbox', ['processed', 'locked_until', 'created_at'])

    # 4. Update usage table
    op.add_column('usage', sa.Column('estimated_billable_units', sa.Integer(), server_default='0', nullable=False))
    op.add_column('usage', sa.Column('actual_billable_units', sa.Integer(), server_default='0', nullable=False))
    op.add_column('usage', sa.Column('uncertain_calls', sa.Integer(), server_default='0', nullable=False))
    op.add_column('usage', sa.Column('last_call_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # 4. usage table
    op.drop_column('usage', 'last_call_at')
    op.drop_column('usage', 'uncertain_calls')
    op.drop_column('usage', 'actual_billable_units')
    op.drop_column('usage', 'estimated_billable_units')

    # 3. outbox table
    op.drop_index('ix_outbox_unprocessed', table_name='outbox')
    op.drop_column('outbox', 'locked_until')
    op.drop_column('outbox', 'error')
    op.drop_column('outbox', 'last_attempt_at')
    op.drop_column('outbox', 'attempts')

    # 2. job_stages table
    op.drop_column('job_stages', 'updated_at')
    op.drop_column('job_stages', 'ambiguous_outcome')
    op.drop_column('job_stages', 'billable_units')

    # 1. jobs table
    op.drop_index('ix_jobs_cancelled', table_name='jobs')
    op.drop_index('ix_jobs_lease_expires_at', table_name='jobs')
    op.drop_index('ix_jobs_lease_token', table_name='jobs')
    op.drop_index('ix_jobs_operation_key', table_name='jobs')

    op.drop_column('jobs', 'subprocess_pid')
    op.drop_column('jobs', 'cancelled_at')
    op.drop_column('jobs', 'cancelled')
    op.drop_column('jobs', 'heartbeat_at')
    op.drop_column('jobs', 'lease_expires_at')
    op.drop_column('jobs', 'lease_token')
    op.drop_column('jobs', 'operation_key')
    op.drop_column('jobs', 'stage')
