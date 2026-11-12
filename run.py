"""Launch Django production studio and local workers.

Django server runs on http://127.0.0.1:8788. With STUDIO_LAN=1 (START_PHONE_ACCESS.bat) it also
listens on this computer's network address, so a phone on the same Wi-Fi can use it after
signing in with the studio password.
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
    lan = os.environ.get('STUDIO_LAN') == '1'
    bind_host = '0.0.0.0' if os.environ.get('STUDIO_DOCKER') == '1' or lan else '127.0.0.1'

    with socket.socket() as check:
        try:
            check.bind((bind_host, target_port))
        except OSError:
            raise SystemExit(f'Port {target_port} is in use. An existing studio may already be running: http://127.0.0.1:{target_port}')

    distributed = os.environ.get('STUDIO_DISTRIBUTED') == '1' or os.environ.get('STUDIO_WORKER_MODE') == 'celery'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    try:
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise SystemExit('Database setup is pending. Run: python manage.py migrate, then restart the studio.')
    except Exception:
        raise SystemExit('PostgreSQL is unavailable or its schema cannot be read. Check STUDIO_DATABASE_URL and run python manage.py migrate.') from None
    worker = None
    if not distributed:
        worker = Supervised([sys.executable, '-m', 'studio.worker'])
    else:
        print('Running in distributed mode: Celery workers and Outbox dispatcher handle tasks.', flush=True)

    try:
        print(f'\n[Django Production Studio] Open http://127.0.0.1:{target_port} in your browser. Keep this window open.\n')
        if lan:
            from studio import access
            addresses = network_addresses()
            print('Phone and other devices on the same Wi-Fi can open:')
            for n, address in enumerate(addresses or ['(no network address found; check Wi-Fi)']):
                print(f'    http://{address}:{target_port}' + ('   <- most likely' if n == 0 and len(addresses) > 1 else ''))
            print('Allow Python through Windows Firewall (private networks) if Windows asks.')
            if not access.password_set():
                print('\nNo password is set yet, so other devices are refused. On this computer open '
                      'Settings & providers and set "Password for phone and other devices".')
            print(flush=True)
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


class Supervised:
    """The background worker, started again if it ever stops (a crash would otherwise leave every new
    job waiting forever while the page still works)."""

    def __init__(self, args):
        import threading
        self.args, self.stopping = args, threading.Event()
        self.proc = subprocess.Popen(args)
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        import time
        delay = 2
        while not self.stopping.wait(delay):
            if self.proc.poll() is None:
                delay = 2
                continue
            print(f'Background worker stopped (exit code {self.proc.returncode}); starting it again.', flush=True)
            self.proc = subprocess.Popen(self.args)
            delay = min(60, delay * 2)

    def terminate(self):
        self.stopping.set()
        self.proc.terminate()

    def wait(self, timeout=None):
        return self.proc.wait(timeout=timeout)

    def kill(self):
        self.proc.kill()


def network_addresses():
    """This computer's private IPv4 addresses (home or office network), best guess first."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))  # no packet is sent; picks the outgoing interface
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    private = [a for a in dict.fromkeys(found) if a.startswith(('192.168.', '10.')) or
               (a.startswith('172.') and 16 <= int(a.split('.')[1]) <= 31)]
    return private


if __name__ == '__main__':
    main()

