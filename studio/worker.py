import os
import time
from . import store
from .config import DATA


def process(job):
    """Use the same leased pipeline locally and in Celery; no broker call needed."""
    from .tasks import (task_research, task_draft, task_stock, task_stock_all,
                        task_voice_pipeline, task_render_pipeline)
    tasks = {'research': task_research, 'draft': task_draft, 'stock': task_stock,
             'stock_all': task_stock_all, 'voice': task_voice_pipeline,
             'render': task_render_pipeline}
    try:
        return tasks[job['kind']].run(job['id'])
    except Exception:
        # The pipeline persists a sanitized failure; keep the local queue alive.
        return None


def main():
    if os.environ.get('STUDIO_DISTRIBUTED') == '1' or os.environ.get('STUDIO_WORKER_MODE') == 'celery':
        raise SystemExit('Legacy worker is disabled in distributed mode. Celery and legacy workers must never own the same stream.')
    store.init()
    lock = (DATA / 'worker.lock').open('a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('A worker is already running for this data folder')
    recovered = store.recover_expired_leases()
    # Pre-lease versions left running jobs without any expiry after a crash.
    # The exclusive local lock proves no other local worker owns those jobs.
    from core.models import Job
    recovered += Job.objects.using(store.DB_ALIAS).filter(
        state='running', lease_token__isnull=True, lease_expires_at__isnull=True
    ).update(state='failed', progress='Local worker was interrupted. Retry to resume.')
    if recovered:
        print(f'Recovered {recovered} expired worker leases', flush=True)
    print('Telugu Reel Studio worker ready', flush=True)
    pause = 1.0
    try:
        while True:
            try:
                (DATA / 'worker-heartbeat.txt').write_text(str(time.time()), 'utf-8')
                store.recover_expired_leases()
                job = store.claim()
                pause = 1.0
            except Exception as exc:  # e.g. PostgreSQL restarted: reconnect and keep serving the queue
                print(f'Worker could not reach the database ({type(exc).__name__}); retrying in {pause:.0f} s', flush=True)
                from django.db import connections
                connections.close_all()
                time.sleep(pause)
                pause = min(30.0, pause * 2)
                continue
            if job:
                process(job)
            else:
                time.sleep(0.5)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
