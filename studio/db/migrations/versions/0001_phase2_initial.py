"""Phase 2 initial PostgreSQL schema

Revision ID: 0001_phase2_initial
Revises: 
Create Date: 2026-10-09 04:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001_phase2_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. workspaces
    op.create_table(
        'workspaces',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('name', sa.String(length=128), server_default='Local Workspace', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Seed default local workspace
    op.execute("INSERT INTO workspaces (id, name) VALUES ('default', 'Local Workspace') ON CONFLICT (id) DO NOTHING")

    # 2. projects
    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('workspace_id', sa.String(length=64), sa.ForeignKey('workspaces.id'), server_default='default', nullable=False),
        sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
        sa.Column('approved_revision', sa.Integer(), nullable=True),
        sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_projects_workspace_id', 'projects', ['workspace_id'])
    op.create_index('ix_projects_updated_at', 'projects', ['updated_at'])
    op.create_index('ix_projects_workspace_updated', 'projects', ['workspace_id', 'updated_at'])

    # 3. revisions
    op.create_table(
        'revisions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('project_id', 'revision', name='uq_project_revision'),
    )
    op.create_index('ix_revisions_project_id', 'revisions', ['project_id'])
    op.create_index('ix_revisions_project_rev', 'revisions', ['project_id', 'revision'])

    # 4. assets
    op.create_table(
        'assets',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('workspace_id', sa.String(length=64), sa.ForeignKey('workspaces.id'), server_default='default', nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('duration', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('provider', sa.String(length=64), server_default='upload', nullable=True),
        sa.Column('external_id', sa.String(length=128), nullable=True),
        sa.Column('source_url', sa.Text(), server_default='', nullable=True),
        sa.Column('license_url', sa.Text(), server_default='', nullable=True),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_assets_workspace_id', 'assets', ['workspace_id'])
    op.create_index('ix_assets_kind', 'assets', ['kind'])
    op.create_index('ix_assets_sha256', 'assets', ['sha256'])
    op.create_index('ix_assets_provider', 'assets', ['provider'])
    op.create_index('ix_assets_created_at', 'assets', ['created_at'])
    op.create_index('ix_assets_provider_ext', 'assets', ['provider', 'external_id'])

    # 5. jobs
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('workspace_id', sa.String(length=64), sa.ForeignKey('workspaces.id'), server_default='default', nullable=False),
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('state', sa.String(length=32), server_default='queued', nullable=False),
        sa.Column('progress', sa.Text(), server_default='', nullable=False),
        sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('applied', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_jobs_workspace_id', 'jobs', ['workspace_id'])
    op.create_index('ix_jobs_project_id', 'jobs', ['project_id'])
    op.create_index('ix_jobs_kind', 'jobs', ['kind'])
    op.create_index('ix_jobs_state', 'jobs', ['state'])
    op.create_index('ix_jobs_applied', 'jobs', ['applied'])
    op.create_index('ix_jobs_created_at', 'jobs', ['created_at'])
    op.create_index('ix_jobs_updated_at', 'jobs', ['updated_at'])
    op.create_index('ix_jobs_claim_queue', 'jobs', ['state', 'created_at'])
    op.create_index('ix_jobs_proj_kind_state', 'jobs', ['project_id', 'kind', 'state'])

    # 6. job_stages
    op.create_table(
        'job_stages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('job_id', sa.String(length=64), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt', sa.Integer(), server_default='1', nullable=False),
        sa.Column('stage', sa.String(length=64), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_job_stages_job_id', 'job_stages', ['job_id'])

    # 7. drafts
    op.create_table(
        'drafts',
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 8. application_receipts
    op.create_table(
        'application_receipts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('job_id', sa.String(length=64), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('applied_revision', sa.Integer(), nullable=False),
        sa.Column('conflicts', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_app_receipts_job_id', 'application_receipts', ['job_id'])
    op.create_index('ix_app_receipts_project_id', 'application_receipts', ['project_id'])

    # 9. outbox
    op.create_table(
        'outbox',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('processed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_outbox_event_type', 'outbox', ['event_type'])
    op.create_index('ix_outbox_processed', 'outbox', ['processed'])

    # 10. usage
    op.create_table(
        'usage',
        sa.Column('day', sa.String(length=32), primary_key=True),
        sa.Column('provider', sa.String(length=64), primary_key=True),
        sa.Column('calls', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_table('usage')
    op.drop_table('outbox')
    op.drop_table('application_receipts')
    op.drop_table('drafts')
    op.drop_table('job_stages')
    op.drop_table('jobs')
    op.drop_table('assets')
    op.drop_table('revisions')
    op.drop_table('projects')
    op.drop_table('workspaces')
