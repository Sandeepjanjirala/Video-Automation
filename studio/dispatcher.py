"""Outbox dispatcher for Telugu Reel Studio (Phase 3).
Reliably bridges PostgreSQL transactional outbox to Celery/Redis.
Ensures broker outages or crashes never lose accepted jobs.
"""
import argparse
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import select, or_, and_
from .celery_app import app
from .db.session import session_scope, check_connection
from .db.models import Outbox, Job
from . import store
from .tasks import (
    task_research, task_draft, task_stock, task_stock_all,
    task_voice_pipeline, task_render_pipeline
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [dispatcher] %(message)s'
)
logger = logging.getLogger('studio.dispatcher')

_running = True


def handle_shutdown(signum, frame):
    global _running
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _running = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


TASK_ROUTING = {
    'render': (task_render_pipeline, 'render'),
    'voice': (task_voice_pipeline, 'network'),
    'research': (task_research, 'network'),
    'draft': (task_draft, 'network'),
    'stock': (task_stock, 'network'),
    'stock_all': (task_stock_all, 'network'),
}


def dispatch_single_event(event_type: str, payload: dict) -> None:
    """Route outbox event to appropriate Celery task with strict JSON parameters."""
    if event_type == 'job.enqueued':
        job_id = payload.get('job_id')
        kind = payload.get('kind')
        if not job_id or not kind:
            raise ValueError(f"Invalid job.enqueued payload: {payload}")

        route = TASK_ROUTING.get(kind)
        if not route:
            raise ValueError(f"Unknown job kind '{kind}' for dispatch")

        task_func, queue_name = route
        # Deliver small job ID string only (JSON-serializable)
        task_func.apply_async(args=[job_id], queue=queue_name)
    else:
        logger.info(f"Ignored non-dispatchable event type: {event_type}")


def dispatch_pending_outbox(batch_size: int = 25) -> int:
    """Process pending outbox messages using SKIP LOCKED for repeat-safe concurrency."""
    now_dt = datetime.now(timezone.utc)
    dispatched_count = 0

    with session_scope() as session:
        stmt = (
            select(Outbox)
            .where(
                Outbox.processed == False,
                or_(Outbox.locked_until.is_(None), Outbox.locked_until <= now_dt)
            )
            .order_by(Outbox.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        records = session.execute(stmt).scalars().all()

        for record in records:
            try:
                dispatch_single_event(record.event_type, record.payload)
                record.processed = True
                record.error = None
                record.last_attempt_at = now_dt
                record.locked_until = None
                dispatched_count += 1
                logger.info(f"Dispatched outbox event {record.id} ({record.event_type} for job {record.payload.get('job_id')})")
            except Exception as exc:
                record.attempts += 1
                record.error = str(exc)[:1000]
                record.last_attempt_at = now_dt
                # Exponential backoff with jitter (capped at 60 seconds)
                backoff = min(60.0, (2.0 ** min(record.attempts, 6)) + random.uniform(0.1, 1.5))
                record.locked_until = now_dt + timedelta(seconds=backoff)
                logger.warning(
                    f"Broker dispatch failed for outbox {record.id} (attempt {record.attempts}): {exc}. "
                    f"Retrying in {backoff:.1f}s."
                )

        session.flush()

    return dispatched_count


def reconcile_orphaned_jobs() -> int:
    """Reconcile orphaned queued jobs and recover expired worker leases."""
    # 1. Recover expired worker leases
    recovered = store.recover_expired_leases()
    if recovered:
        logger.info(f"Recovered {recovered} expired worker leases.")

    # 2. Check for queued jobs missing unprocessed outbox entries
    reconciled = 0
    now_dt = datetime.now(timezone.utc)
    threshold = now_dt - timedelta(seconds=15)

    with session_scope() as session:
        # Queued jobs created at least 15 seconds ago
        stmt = (
            select(Job)
            .where(Job.state == 'queued', Job.created_at <= threshold)
            .order_by(Job.created_at.asc())
            .limit(10)
        )
        queued_jobs = session.execute(stmt).scalars().all()

        for j in queued_jobs:
            # Check if there is already an unprocessed outbox message
            outbox_exists = session.execute(
                select(Outbox.id)
                .where(
                    Outbox.event_type == 'job.enqueued',
                    Outbox.processed == False,
                    Outbox.payload['job_id'].as_string() == j.id
                )
                .limit(1)
            ).scalar_one_or_none()

            if not outbox_exists:
                # Create reconciliation outbox message
                session.add(Outbox(
                    event_type='job.enqueued',
                    payload={
                        'job_id': j.id,
                        'project_id': j.project_id,
                        'revision': j.revision,
                        'kind': j.kind,
                        'options': j.snapshot.get('options', {}),
                        'operation_key': j.operation_key,
                        'reconciled': True
                    },
                    processed=False,
                    attempts=0,
                    created_at=now_dt
                ))
                reconciled += 1
                logger.info(f"Reconciled missing outbox event for queued job {j.id}")

        session.flush()

    return reconciled


def run_loop(poll_interval: float = 0.5, reconcile_interval: float = 30.0):
    """Main dispatcher daemon loop."""
    logger.info("Telugu Reel Studio Outbox Dispatcher starting...")
    store.init()
    last_reconcile = time.time()

    while _running:
        try:
            dispatched = dispatch_pending_outbox()
            now = time.time()
            if now - last_reconcile >= reconcile_interval:
                reconcile_orphaned_jobs()
                last_reconcile = now

            if dispatched == 0:
                time.sleep(poll_interval)
        except Exception as exc:
            logger.error(f"Error in dispatcher loop: {exc}")
            time.sleep(2.0)

    logger.info("Outbox Dispatcher shut down cleanly.")


def main():
    parser = argparse.ArgumentParser(description="Telugu Reel Studio Outbox Dispatcher")
    parser.add_argument('--once', action='store_true', help="Run one dispatch pass and exit")
    parser.add_argument('--poll-interval', type=float, default=0.5, help="Polling interval in seconds")
    parser.add_argument('--reconcile-interval', type=float, default=30.0, help="Reconciliation interval in seconds")
    args = parser.parse_args()

    if args.once:
        dispatched = dispatch_pending_outbox()
        reconciled = reconcile_orphaned_jobs()
        print(f"Dispatch pass complete: {dispatched} dispatched, {reconciled} reconciled.")
    else:
        run_loop(poll_interval=args.poll_interval, reconcile_interval=args.reconcile_interval)


if __name__ == '__main__':
    main()
