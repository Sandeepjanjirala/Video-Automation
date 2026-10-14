"""Launch Django production studio and local workers.

Django server runs on http://127.0.0.1:8788.
"""
import os
import socket
import subprocess
import sys
from pathlib import Path


def main():
    os.chdir(Path(__file__).resolve().parent)
    try:
        import django, edge_tts, httpx
    except ImportError:
        raise SystemExit('Dependencies are missing. Run SETUP_WINDOWS.bat first.')

    target_port = int(os.environ.get('STUDIO_PORT', 8788))
    bind_host = '0.0.0.0' if os.environ.get('STUDIO_DOCKER') == '1' else '127.0.0.1'

    with socket.socket() as check:
        try:
            check.bind((bind_host, target_port))
        except OSError:
            raise SystemExit(f'Port {target_port} is in use. An existing studio may already be running: http://127.0.0.1:{target_port}')

    distributed = os.environ.get('STUDIO_DISTRIBUTED') == '1' or os.environ.get('STUDIO_WORKER_MODE') == 'celery'
    worker = None
    if not distributed:
        worker = subprocess.Popen([sys.executable, '-m', 'studio.worker'])
    else:
        print('Running in distributed mode: Celery workers and Outbox dispatcher handle tasks.', flush=True)

    try:
        print(f'\n[Django Production Studio] Open http://127.0.0.1:{target_port} in your browser. Keep this window open.\n')
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        from django.core.management import execute_from_command_line
        execute_from_command_line(['manage.py', 'runserver', f'{bind_host}:{target_port}', '--noreload'])
    finally:
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.kill()


if __name__ == '__main__':
    main()

