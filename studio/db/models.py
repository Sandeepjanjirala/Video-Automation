"""SQLAlchemy models for Telugu Reel Studio (Phase 2 - PostgreSQL primary store)."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = 'workspaces'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default='Local Workspace')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), server_default=func.now())

    projects = relationship('Project', back_populates='workspace', cascade='all, delete-orphan')
    assets = relationship('Asset', back_populates='workspace', cascade='all, delete-orphan')
    jobs = relationship('Job', back_populates='workspace', cascade='all, delete-orphan')


class Project(Base):
    __tablename__ = 'projects'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey('workspaces.id'), default='default', index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    approved_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True)

    workspace = relationship('Workspace', back_populates='projects')
    revisions = relationship('Revision', back_populates='project', cascade='all, delete-orphan', order_by='Revision.revision.desc()')
    jobs = relationship('Job', back_populates='project', cascade='all, delete-orphan')
    draft = relationship('Draft', back_populates='project', uselist=False, cascade='all, delete-orphan')
    voice_takes = relationship('VoiceTake', back_populates='project', cascade='all, delete-orphan', order_by='VoiceTake.created_at.desc()')

    __table_args__ = (
        Index('ix_projects_workspace_updated', 'workspace_id', 'updated_at'),
    )


class Revision(Base):
    __tablename__ = 'revisions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())

    project = relationship('Project', back_populates='revisions')

    __table_args__ = (
        UniqueConstraint('project_id', 'revision', name='uq_project_revision'),
        Index('ix_revisions_project_rev', 'project_id', 'revision'),
    )


class Asset(Base):
    __tablename__ = 'assets'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey('workspaces.id'), default='default', index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 'video' or 'audio'
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(64), default='upload', index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, default='')
    license_url: Mapped[Optional[str]] = mapped_column(Text, default='')
    meta: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True)

    workspace = relationship('Workspace', back_populates='assets')

    __table_args__ = (
        Index('ix_assets_provider_ext', 'provider', 'external_id'),
    )


class Job(Base):
    __tablename__ = 'jobs'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey('workspaces.id'), default='default', index=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default='queued', nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), default='queued', nullable=False)
    progress: Mapped[str] = mapped_column(Text, default='')
    snapshot: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    applied: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    operation_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True, index=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    subprocess_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True)

    workspace = relationship('Workspace', back_populates='jobs')
    project = relationship('Project', back_populates='jobs')
    stages = relationship('JobStage', back_populates='job', cascade='all, delete-orphan', order_by='JobStage.id')
    receipts = relationship('ApplicationReceipt', back_populates='job', cascade='all, delete-orphan')

    __table_args__ = (
        Index('ix_jobs_claim_queue', 'state', 'created_at'),
        Index('ix_jobs_proj_kind_state', 'project_id', 'kind', 'state'),
    )


class JobStage(Base):
    __tablename__ = 'job_stages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    billable_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ambiguous_outcome: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), server_default=func.now())

    job = relationship('Job', back_populates='stages')


class Draft(Base):
    __tablename__ = 'drafts'

    project_id: Mapped[str] = mapped_column(String(64), ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    body: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), server_default=func.now())

    project = relationship('Project', back_populates='draft')


class ApplicationReceipt(Base):
    __tablename__ = 'application_receipts'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    applied_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    conflicts: Mapped[List[Any]] = mapped_column(JSONB, default=list)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())

    job = relationship('Job', back_populates='receipts')


class Outbox(Base):
    __tablename__ = 'outbox'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())

    __table_args__ = (
        Index('ix_outbox_unprocessed', 'processed', 'locked_until', 'created_at'),
    )


class Usage(Base):
    __tablename__ = 'usage'

    day: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_billable_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_billable_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uncertain_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_call_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class VoiceTake(Base):
    __tablename__ = 'voice_takes'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    scene_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    voice_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    preset: Mapped[str] = mapped_column(String(32), default='conversational')
    spoken_text: Mapped[str] = mapped_column(Text, nullable=False)
    roman_text: Mapped[Optional[str]] = mapped_column(Text, default='')
    asset_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey('assets.id', ondelete='SET NULL'), nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    timing_data: Mapped[List[Any]] = mapped_column(JSONB, default=list)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    contract_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True)

    project = relationship('Project', back_populates='voice_takes')
    asset = relationship('Asset')

    __table_args__ = (
        Index('ix_voice_takes_proj_scene', 'project_id', 'scene_id'),
    )

