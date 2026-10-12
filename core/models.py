"""Unmanaged Django ORM models for Telugu Reel Studio (Stage M2).

CRITICAL CONSTRAINTS:
- All models have `managed = False`.
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
        managed = False
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
        managed = False
        db_table = 'projects'
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'

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
        managed = False
        db_table = 'revisions'
        unique_together = (('project', 'revision'),)
        verbose_name = 'Revision'
        verbose_name_plural = 'Revisions'

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
    source_url = models.TextField(blank=True, default='')
    license_url = models.TextField(blank=True, default='')
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'assets'
        verbose_name = 'Asset'
        verbose_name_plural = 'Assets'

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
        managed = False
        db_table = 'jobs'
        verbose_name = 'Job'
        verbose_name_plural = 'Jobs'

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
        managed = False
        db_table = 'job_stages'
        verbose_name = 'Job Stage'
        verbose_name_plural = 'Job Stages'

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
        managed = False
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
        managed = False
        db_table = 'application_receipts'
        verbose_name = 'Application Receipt'
        verbose_name_plural = 'Application Receipts'

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
        managed = False
        db_table = 'outbox'
        verbose_name = 'Outbox Event'
        verbose_name_plural = 'Outbox Events'

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
        managed = False
        db_table = 'voice_takes'
        verbose_name = 'Voice Take'
        verbose_name_plural = 'Voice Takes'

    def __str__(self):
        return f"VoiceTake {self.id} ({self.provider}/{self.voice_id})"
