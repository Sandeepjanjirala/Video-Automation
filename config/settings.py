"""Django settings for Telugu Reel Studio (Phase M1).

Safe coexistence settings:
- Read-only connection to existing PostgreSQL database.
- Zero internal migration-backed apps installed (preserves Alembic schema authority).
- Local loopback binding only (127.0.0.1, localhost).
"""
import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# Security Settings
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-studio-dev-key-7f2d37c50ff9429f0970aacf8f4aa068'
)

# Debug disabled by default; enable only with explicit DJANGO_DEBUG=1
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() in ('true', '1')

# Local development loopback restriction only
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']

# Minimal application definition for M1 (No migration-backed auth/session apps)
INSTALLED_APPS = [
    'rest_framework',
    'core.apps.CoreConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.LocalSecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'config.urls'

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database Configuration (Connects to existing PostgreSQL instance)
# Prefer read-only connection if configured or locally available for M2 safety
db_url = os.environ.get('STUDIO_READONLY_DATABASE_URL')
if not db_url:
    readonly_file = BASE_DIR / 'backups' / '.readonly_db_url'
    if readonly_file.exists():
        try:
            db_url = readonly_file.read_text(encoding='utf-8').strip()
        except Exception:
            pass
if not db_url:
    db_url = os.environ.get(
        'STUDIO_DATABASE_URL',
        'postgresql://postgres:postgres@127.0.0.1:5433/studio'
    )

# Handle sqlalchemy-style postgresql+psycopg2:// if passed
if db_url.startswith('postgresql+psycopg2://'):
    db_url = db_url.replace('postgresql+psycopg2://', 'postgresql://', 1)

parsed = urlparse(db_url)

# Stage M4: Narrowly scoped media writer database identity
# Used exclusively for controlled asset creation (POST /api/assets)
media_writer_url = os.environ.get('STUDIO_MEDIA_WRITER_DATABASE_URL')
if not media_writer_url:
    writer_file = BASE_DIR / 'backups' / '.media_writer_db_url'
    if writer_file.exists():
        try:
            media_writer_url = writer_file.read_text(encoding='utf-8').strip()
        except Exception:
            pass
if not media_writer_url:
    media_writer_url = db_url

if media_writer_url.startswith('postgresql+psycopg2://'):
    media_writer_url = media_writer_url.replace('postgresql+psycopg2://', 'postgresql://', 1)

parsed_writer = urlparse(media_writer_url)

# Stage M5: Narrowly scoped job writer database identity
# Used exclusively for job creation, retry, and cancellation (POST /api/projects/{pid}/jobs, etc.)
job_writer_url = os.environ.get('STUDIO_JOB_WRITER_DATABASE_URL')
if not job_writer_url:
    job_writer_file = BASE_DIR / 'backups' / '.job_writer_db_url'
    if job_writer_file.exists():
        try:
            job_writer_url = job_writer_file.read_text(encoding='utf-8').strip()
        except Exception:
            pass
if not job_writer_url:
    job_writer_url = db_url

if job_writer_url.startswith('postgresql+psycopg2://'):
    job_writer_url = job_writer_url.replace('postgresql+psycopg2://', 'postgresql://', 1)

parsed_job_writer = urlparse(job_writer_url)

# Stage M6: Narrowly scoped project writer database identity
# Used for project creation, updates, approvals, drafts, and job result application.
project_writer_url = os.environ.get('STUDIO_PROJECT_WRITER_DATABASE_URL')
if not project_writer_url:
    project_writer_file = BASE_DIR / 'backups' / '.project_writer_db_url'
    if project_writer_file.exists():
        try:
            project_writer_url = project_writer_file.read_text(encoding='utf-8').strip()
        except Exception:
            pass
if not project_writer_url:
    project_writer_url = db_url

if project_writer_url.startswith('postgresql+psycopg2://'):
    project_writer_url = project_writer_url.replace('postgresql+psycopg2://', 'postgresql://', 1)

parsed_project_writer = urlparse(project_writer_url)

# Stage M7: Narrowly scoped voice writer database identity
# Used for Voice Lab take creation and take acceptance.
voice_writer_url = os.environ.get('STUDIO_VOICE_WRITER_DATABASE_URL')
if not voice_writer_url:
    voice_writer_file = BASE_DIR / 'backups' / '.voice_writer_db_url'
    if voice_writer_file.exists():
        try:
            voice_writer_url = voice_writer_file.read_text(encoding='utf-8').strip()
        except Exception:
            pass
if not voice_writer_url:
    voice_writer_url = db_url

if voice_writer_url.startswith('postgresql+psycopg2://'):
    voice_writer_url = voice_writer_url.replace('postgresql+psycopg2://', 'postgresql://', 1)

parsed_voice_writer = urlparse(voice_writer_url)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed.path.lstrip('/') or 'studio',
        'USER': parsed.username or 'postgres',
        'PASSWORD': parsed.password or 'postgres',
        'HOST': parsed.hostname or '127.0.0.1',
        'PORT': str(parsed.port or 5433),
        'CONN_MAX_AGE': 0,
    },
    'media_writer': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed_writer.path.lstrip('/') or 'studio',
        'USER': parsed_writer.username or 'django_media_writer',
        'PASSWORD': parsed_writer.password or '',
        'HOST': parsed_writer.hostname or '127.0.0.1',
        'PORT': str(parsed_writer.port or 5433),
        'CONN_MAX_AGE': 0,
    },
    'job_writer': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed_job_writer.path.lstrip('/') or 'studio',
        'USER': parsed_job_writer.username or 'django_job_writer',
        'PASSWORD': parsed_job_writer.password or '',
        'HOST': parsed_job_writer.hostname or '127.0.0.1',
        'PORT': str(parsed_job_writer.port or 5433),
        'CONN_MAX_AGE': 0,
    },
    'project_writer': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed_project_writer.path.lstrip('/') or 'studio',
        'USER': parsed_project_writer.username or 'django_project_writer',
        'PASSWORD': parsed_project_writer.password or '',
        'HOST': parsed_project_writer.hostname or '127.0.0.1',
        'PORT': str(parsed_project_writer.port or 5433),
        'CONN_MAX_AGE': 0,
    },
    'voice_writer': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed_voice_writer.path.lstrip('/') or 'studio',
        'USER': parsed_voice_writer.username or 'django_voice_writer',
        'PASSWORD': parsed_voice_writer.password or '',
        'HOST': parsed_voice_writer.hostname or '127.0.0.1',
        'PORT': str(parsed_voice_writer.port or 5433),
        'CONN_MAX_AGE': 0,
    },
}

# Upload Limits: Stream large files to disk without buffering 200MB into memory
DATA_UPLOAD_MAX_MEMORY_SIZE = 220 * 1024 * 1024  # 220 MB request payload limit
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024    # 2 MB in-memory chunk limit before disk spill


# Redis URL for non-mutating health checks
REDIS_URL = os.environ.get('CELERY_BROKER_URL') or os.environ.get('REDIS_URL') or 'redis://127.0.0.1:6379/0'

# Django REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'UNAUTHENTICATED_USER': None,
}

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
