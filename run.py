"""Launch one local API and one independent worker, using the same Python environment."""
import os
import socket
import subprocess
import sys
from pathlib import Path


def main():
    os.chdir(Path(__file__).resolve().parent)
    try:
        import fastapi, uvicorn, edge_tts, httpx
    except ImportError:
        raise SystemExit('Dependencies are missing. Run SETUP_WINDOWS.bat first.')
    with socket.socket() as check:
        try:
            check.bind(('127.0.0.1', 8787))
        except OSError:
            raise SystemExit('Port 8787 is in use. An existing studio may already be running: http://127.0.0.1:8787')
    distributed = os.environ.get('STUDIO_DISTRIBUTED') == '1' or os.environ.get('STUDIO_WORKER_MODE') == 'celery'
    worker = None
    if not distributed:
        worker = subprocess.Popen([sys.executable, '-m', 'studio.worker'])
    else:
        print('Running in distributed mode: Celery workers and Outbox dispatcher handle tasks.', flush=True)

    print('\nOpen http://127.0.0.1:8787 in your browser. Keep this window open.\n')
    try:
        uvicorn.run('studio.api:app', host='127.0.0.1', port=8787, log_level='warning')
    finally:
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.kill()


if __name__ == '__main__':
    main()
