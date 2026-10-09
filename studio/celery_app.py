"""Celery configuration for Telugu Reel Studio (Phase 3)."""
import os
from celery import Celery

# Redis connection URLs
DEFAULT_BROKER_URL = 'redis://127.0.0.1:6379/0'
DEFAULT_RESULT_BACKEND = 'redis://127.0.0.1:6379/1'

broker_url = os.environ.get('CELERY_BROKER_URL') or os.environ.get('REDIS_URL') or DEFAULT_BROKER_URL
result_backend = os.environ.get('CELERY_RESULT_BACKEND') or DEFAULT_RESULT_BACKEND

app = Celery('studio', broker=broker_url, backend=result_backend)

app.conf.update(
    # Serialization security - JSON ONLY
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='UTC',
    enable_utc=True,

    # Reliability and visibility
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    # Result retention
    result_expires=86400,  # 24 hours

    # Task queues and routing
    task_queues={
        'render': {
            'exchange': 'render',
            'routing_key': 'render',
        },
        'network': {
            'exchange': 'network',
            'routing_key': 'network',
        },
        'default': {
            'exchange': 'default',
            'routing_key': 'default',
        },
    },
    task_default_queue='default',
    task_routes={
        'studio.tasks.task_render_pipeline': {'queue': 'render'},
        'studio.tasks.task_render_scene': {'queue': 'render'},
        'studio.tasks.task_assemble': {'queue': 'render'},
        'studio.tasks.task_mix': {'queue': 'render'},
        'studio.tasks.task_validate': {'queue': 'render'},
        'studio.tasks.task_research': {'queue': 'network'},
        'studio.tasks.task_draft': {'queue': 'network'},
        'studio.tasks.task_stock': {'queue': 'network'},
        'studio.tasks.task_stock_all': {'queue': 'network'},
        'studio.tasks.task_voice_pipeline': {'queue': 'network'},
        'studio.tasks.task_synthesize_scene': {'queue': 'network'},
    },
)

# Autodiscover tasks from studio package
app.autodiscover_tasks(['studio'])
