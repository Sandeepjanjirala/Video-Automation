"""Phase 4 Voice Lab and voice takes table

Revision ID: 0003_phase4_voice_lab
Revises: 0002_phase3_leases_and_outbox
Create Date: 2026-10-12 07:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0003_phase4_voice_lab'
down_revision: Union[str, None] = '0002_phase3_leases_and_outbox'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'voice_takes',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scene_id', sa.String(length=64), nullable=True),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('voice_id', sa.String(length=64), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('preset', sa.String(length=32), server_default='conversational', nullable=False),
        sa.Column('spoken_text', sa.Text(), nullable=False),
        sa.Column('roman_text', sa.Text(), server_default='', nullable=True),
        sa.Column('asset_id', sa.String(length=64), sa.ForeignKey('assets.id', ondelete='SET NULL'), nullable=True),
        sa.Column('duration', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('timing_data', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('accepted', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('contract_key', sa.String(length=64), nullable=True),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_voice_takes_project_id', 'voice_takes', ['project_id'])
    op.create_index('ix_voice_takes_scene_id', 'voice_takes', ['scene_id'])
    op.create_index('ix_voice_takes_provider', 'voice_takes', ['provider'])
    op.create_index('ix_voice_takes_accepted', 'voice_takes', ['accepted'])
    op.create_index('ix_voice_takes_contract_key', 'voice_takes', ['contract_key'])
    op.create_index('ix_voice_takes_proj_scene', 'voice_takes', ['project_id', 'scene_id'])


def downgrade() -> None:
    op.drop_index('ix_voice_takes_proj_scene', table_name='voice_takes')
    op.drop_index('ix_voice_takes_contract_key', table_name='voice_takes')
    op.drop_index('ix_voice_takes_accepted', table_name='voice_takes')
    op.drop_index('ix_voice_takes_provider', table_name='voice_takes')
    op.drop_index('ix_voice_takes_scene_id', table_name='voice_takes')
    op.drop_index('ix_voice_takes_project_id', table_name='voice_takes')
    op.drop_table('voice_takes')
