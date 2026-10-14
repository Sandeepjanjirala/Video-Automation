"""Unmanaged Django ORM models for Telugu Reel Studio (Stage M2).

CRITICAL CONSTRAINTS:
- All models have `managed = True`.
- Existing PostgreSQL schema owned exclusively by Alembic.
- Primary keys faithfully map real PostgreSQL types and column names.
- Zero invented surrogate primary keys (e.g. Draft maps project_id as PK).
- Relationships use DO_NOTHING with explicit db_column.
"""
from django.db import models


class Workspace(models.Model):
    """Workspaces isolation entity."""
    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=128, default='Local Workspace')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'workspaces'
        verbose_name = 'Workspace'
        verbose_name_plural = 'Workspaces'

    def __str__(self):
        return f"{self.name} ({self.id})"


class Project(models.Model):
    """Core story reel project."""
    id = models.CharField(max_length=64, primary_key=True)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.DO_NOTHING,
        db_column='workspace_id',
        related_name='projects',
    )
    revision = models.IntegerField(default=1)
    approved_revision = models.IntegerField(null=True, blank=True)
    body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'projects'
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'
        indexes = [
            models.Index(fields=['workspace'], name='ix_projects_workspace_id'),
            models.Index(fields=['updated_at'], name='ix_projects_updated_at'),
            models.Index(fields=['workspace', 'updated_at'], name='ix_projects_workspace_updated'),
        ]

    def __str__(self):
        return f"Project {self.id} (rev {self.revision})"


class Revision(models.Model):
    """Immutable project snapshot at a specific revision number."""
    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.DO_NOTHING,
        db_column='project_id',
        related_name='revisions',
    )
    revision = models.IntegerField()
    body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'revisions'
        unique_together = (('project', 'revision'),)
        verbose_name = 'Revision'
        verbose_name_plural = 'Revisions'
        indexes = [
            models.Index(fields=['project'], name='ix_revisions_project_id'),
            models.Index(fields=['project', 'revision'], name='ix_revisions_project_rev'),
        ]

    def __str__(self):
        return f"Revision {self.project_id} r{self.revision}"


class Asset(models.Model):
    """Media assets (video footage, audio tracks, etc.)."""
    id = models.CharField(max_length=64, primary_key=True)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.DO_NOTHING,
        db_column='workspace_id',
        related_name='assets',
    )
    filename = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=32)
    duration = models.FloatField(default=0.0)
    sha256 = models.CharField(max_length=64, null=True, blank=True)
    provider = models.CharField(max_length=64, null=True, blank=True, default='upload')
    external_id = models.CharField(max_length=128, null=True, blank=True)
    source_url = models.TextField(null=True, blank=True, default='')
    license_url = models.TextField(null=True, blank=True, default='')
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'assets'
        verbose_name = 'Asset'
        verbose_name_plural = 'Assets'
        indexes = [
            models.Index(fields=['workspace'], name='ix_assets_workspace_id'),
            models.Index(fields=['kind'], name='ix_assets_kind'),
            models.Index(fields=['sha256'], name='ix_assets_sha256'),
            models.Index(fields=['provider'], name='ix_assets_provider'),
            models.Index(fields=['created_at'], name='ix_assets_created_at'),
            models.Index(fields=['provider', 'external_id'], name='ix_assets_provider_ext'),
        ]

    def __str__(self):
        return f"{self.name} ({self.kind}, {self.id})"


class Job(models.Model):
    """Distributed background render or research job."""
    id = models.CharField(max_length=64, primary_key=True)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.DO_NOTHING,
        db_column='workspace_id',
        related_name='jobs',
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.DO_NOTHING,
        db_column='project_id',
        related_name='jobs',
    )
    revision = models.IntegerField()
    kind = models.CharField(max_length=32)
    state = models.CharField(max_length=32, default='queued')
    stage = models.CharField(max_length=64, default='queued')
    progress = models.TextField(blank=True, default='')
    snapshot = models.JSONField()
    result = models.JSONField(default=dict)
    applied = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    attempt_count = models.IntegerField(default=0)
    operation_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    lease_token = models.CharField(max_length=64, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    subprocess_pid = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'jobs'
        verbose_name = 'Job'
        verbose_name_plural = 'Jobs'
        indexes = [
            models.Index(fields=['workspace'], name='ix_jobs_workspace_id'),
            models.Index(fields=['project'], name='ix_jobs_project_id'),
            models.Index(fields=['kind'], name='ix_jobs_kind'),
            models.Index(fields=['state'], name='ix_jobs_state'),
            models.Index(fields=['applied'], name='ix_jobs_applied'),
            models.Index(fields=['created_at'], name='ix_jobs_created_at'),
            models.Index(fields=['updated_at'], name='ix_jobs_updated_at'),
            models.Index(fields=['state', 'created_at'], name='ix_jobs_claim_queue'),
            models.Index(fields=['project', 'kind', 'state'], name='ix_jobs_proj_kind_state'),
            models.Index(fields=['lease_token'], name='ix_jobs_lease_token'),
            models.Index(fields=['lease_expires_at'], name='ix_jobs_lease_expires_at'),
            models.Index(fields=['cancelled'], name='ix_jobs_cancelled'),
        ]

    def __str__(self):
        return f"Job {self.id} ({self.kind}, {self.state})"


class JobStage(models.Model):
    """Discrete stage progression within a Job."""
    id = models.AutoField(primary_key=True)
    job = models.ForeignKey(
        Job,
        on_delete=models.DO_NOTHING,
        db_column='job_id',
        related_name='stages',
    )
    attempt = models.IntegerField(default=1)
    stage = models.CharField(max_length=64)
    state = models.CharField(max_length=32)
    details = models.JSONField(default=dict)
    billable_units = models.IntegerField(default=0)
    ambiguous_outcome = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'job_stages'
        verbose_name = 'Job Stage'
        verbose_name_plural = 'Job Stages'
        indexes = [
            models.Index(fields=['job'], name='ix_job_stages_job_id'),
        ]

    def __str__(self):
        return f"JobStage {self.id} ({self.job_id}:{self.stage})"


class Draft(models.Model):
    """Unsaved client editor draft.
    
    PRIMARY KEY IS `project_id`. No surrogate `id` exists on this table.
    """
    project = models.OneToOneField(
        Project,
        primary_key=True,
        on_delete=models.DO_NOTHING,
        db_column='project_id',
        related_name='draft',
    )
    body = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'drafts'
        verbose_name = 'Draft'
        verbose_name_plural = 'Drafts'

    def __str__(self):
        return f"Draft for {self.project_id}"


class ApplicationReceipt(models.Model):
    """Optimistic merge and job application record."""
    id = models.AutoField(primary_key=True)
    job = models.ForeignKey(
        Job,
        on_delete=models.DO_NOTHING,
        db_column='job_id',
        related_name='receipts',
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.DO_NOTHING,
        db_column='project_id',
        related_name='receipts',
    )
    applied_revision = models.IntegerField()
    conflicts = models.JSONField(default=list)
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'application_receipts'
        verbose_name = 'Application Receipt'
        verbose_name_plural = 'Application Receipts'
        indexes = [
            models.Index(fields=['job'], name='ix_app_receipts_job_id'),
            models.Index(fields=['project'], name='ix_app_receipts_project_id'),
        ]

    def __str__(self):
        return f"Receipt {self.id} (job={self.job_id}, r={self.applied_revision})"


class OutboxEvent(models.Model):
    """Transactional outbox event dispatched to Celery workers."""
    id = models.AutoField(primary_key=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'outbox'
        verbose_name = 'Outbox Event'
        verbose_name_plural = 'Outbox Events'
        indexes = [
            models.Index(fields=['event_type'], name='ix_outbox_event_type'),
            models.Index(fields=['processed'], name='ix_outbox_processed'),
            models.Index(fields=['processed', 'locked_until', 'created_at'], name='ix_outbox_unprocessed'),
        ]

    def __str__(self):
        return f"OutboxEvent {self.id} ({self.event_type}, processed={self.processed})"


class VoiceTake(models.Model):
    """Phase 4 Voice Lab versioned speech synthesis take."""
    id = models.CharField(max_length=64, primary_key=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.DO_NOTHING,
        db_column='project_id',
        related_name='voice_takes',
    )
    scene_id = models.CharField(max_length=64, null=True, blank=True)
    provider = models.CharField(max_length=32)
    voice_id = models.CharField(max_length=64)
    model = models.CharField(max_length=64, null=True, blank=True)
    preset = models.CharField(max_length=32, default='conversational')
    spoken_text = models.TextField()
    roman_text = models.TextField(null=True, blank=True, default='')
    asset = models.ForeignKey(
        Asset,
        on_delete=models.DO_NOTHING,
        db_column='asset_id',
        null=True,
        blank=True,
        related_name='voice_takes',
    )
    duration = models.FloatField(default=0.0)
    timing_data = models.JSONField(default=list)
    accepted = models.BooleanField(default=False)
    contract_key = models.CharField(max_length=64, null=True, blank=True)
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'voice_takes'
        verbose_name = 'Voice Take'
        verbose_name_plural = 'Voice Takes'
        indexes = [
            models.Index(fields=['project'], name='ix_voice_takes_project_id'),
            models.Index(fields=['scene_id'], name='ix_voice_takes_scene_id'),
            models.Index(fields=['provider'], name='ix_voice_takes_provider'),
            models.Index(fields=['accepted'], name='ix_voice_takes_accepted'),
            models.Index(fields=['contract_key'], name='ix_voice_takes_contract_key'),
            models.Index(fields=['project', 'scene_id'], name='ix_voice_takes_proj_scene'),
        ]

    def __str__(self):
        return f"VoiceTake {self.id} ({self.provider}/{self.voice_id})"
