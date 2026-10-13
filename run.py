"""Launch one local API and one independent worker, using the same Python environment.

Stage M7: Primary web server is now Django on http://127.0.0.1:8788.
FastAPI runtime on http://127.0.0.1:8787 is preserved for rollback/debugging via:
  python run.py --fastapi
  or setting STUDIO_SERVER=fastapi
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

    use_fastapi = '--fastapi' in sys.argv or os.environ.get('STUDIO_SERVER', '').lower() == 'fastapi'
    target_port = 8787 if use_fastapi else int(os.environ.get('STUDIO_PORT', 8788))
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
        if use_fastapi:
            import uvicorn
            print(f'\n[FastAPI Rollback Server] Open http://127.0.0.1:{target_port} in your browser.\n')
            uvicorn.run('studio.api:app', host=bind_host, port=target_port, log_level='warning')
        else:
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

