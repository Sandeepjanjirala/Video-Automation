"""Container-only health and maintenance checks; never print credentials."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def command(*args):
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=30).decode()


def check(mode):
    if mode == 'dispatcher':
        assert time.time() - Path('/tmp/studio-dispatcher-heartbeat').stat().st_mtime < 90
        return
    import django
    django.setup()
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.conf import settings
    from core.models import Job
    from studio.config import DATA
    if mode == 'idle':
        count = Job.objects.filter(state__in=['queued', 'running']).count()
        print(f'Outstanding jobs: {count}')
        if count:
            raise RuntimeError('Wait for jobs to finish or cancel them in the UI before maintenance')
        return
    if mode == 'cleanup':
        assert not Job.objects.filter(state__in=['queued', 'running']).exists()
        # API and workers must be stopped by the caller. Only old direct children of tmp.
        import shutil
        root = DATA / 'tmp'
        assert not root.is_symlink()
        cutoff = time.time() - max(1, int(os.environ.get('STUDIO_TEMP_DAYS', '7'))) * 86400
        for entry in root.iterdir() if root.exists() else []:
            if entry.is_symlink():
                continue
            descendants = [entry, *entry.rglob('*')] if entry.is_dir() else [entry]
            if any(p.is_symlink() or p.stat().st_mtime >= cutoff for p in descendants):
                continue
            print('Expired temporary artifact:', entry.name)
            if '--apply' in sys.argv:
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        assert cursor.fetchone() == (1,)
    assert not MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
    import redis
    broker = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=3, socket_connect_timeout=3)
    assert broker.ping()
    if mode.startswith('worker_'):
        from studio.celery_app import app
        destination = mode + '@' + socket.gethostname()
        assert app.control.inspect(destination=[destination], timeout=5).ping().get(destination, {}).get('ok') == 'pong'
    elif mode == 'api':
        with urllib.request.urlopen('http://127.0.0.1:8788/login', timeout=5) as response:
            assert response.status == 200
    elif mode in ('tools', 'synthetic', 'report'):
        assert os.access(DATA, os.W_OK)
        command('ffprobe', '-version')
        assert 'libx264' in command('ffmpeg', '-hide_banner', '-encoders')
        assert 'subtitles' in command('ffmpeg', '-hide_banner', '-filters')
        assert 'Telugu' in command('fc-match', 'Noto Sans Telugu', '-f', '%{family}')
        if mode == 'report':
            from django.utils import timezone
            stuck = Job.objects.filter(state='running', lease_expires_at__lt=timezone.now()).count()
            print(json.dumps({'queued': Job.objects.filter(state='queued').count(),
                              'running': Job.objects.filter(state='running').count(),
                              'expired_leases': stuck,
                              'redis_queue_lengths': {q: broker.llen(q) for q in ('network', 'render', 'default')}}))
            assert not stuck, 'Expired worker leases require investigation'
        if mode == 'synthetic':
            with tempfile.TemporaryDirectory(prefix='studio-health-') as folder:
                for quality, crf in [('draft', '24'), ('full', '20')]:
                    output = str(Path(folder) / (quality + '.mp4'))
                    command('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=320x240:d=1',
                            '-c:v', 'libx264', '-threads', '1', '-crf', crf, output)
                    assert float(command('ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                         '-of', 'default=nw=1:nk=1', output)) >= 1
    else:
        raise ValueError('Unknown mode')


if __name__ == '__main__':
    try:
        check(sys.argv[1])
    except Exception as exc:
        print(f'FAIL {sys.argv[1]} ({type(exc).__name__}); inspect service state/configuration privately.', file=sys.stderr)
        sys.exit(1)
