"""Isolated Docker smoke test. No real .env, ports, volumes, keys or production resources.

Run: python scripts/test_deployment.py --image studio-deployment-test
Only containers/networks from this unique test are removed. Volumes are kept for inspection.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='studio-deployment-test')
    args = parser.parse_args()
    name = 'studio_deploy_test_' + uuid.uuid4().hex[:8]
    folder = ROOT / 'test-outputs' / name
    folder.mkdir(parents=True)
    for file in ('docker-compose.yml', 'docker-compose.production.yml'):
        shutil.copy2(ROOT / file, folder / file)
    (folder / '.env').write_text('POSTGRES_PASSWORD=test_only_password_123456\nDJANGO_SECRET_KEY=' + 'testonly' * 8 +
                               '\nSTUDIO_PASSWORD=test_only_password_123456\nSTUDIO_ONLINE=0\n')
    base = ['docker', 'compose', '-p', name, '--env-file', str(folder / '.env'),
            '-f', str(folder / 'docker-compose.yml'), '-f', str(folder / 'docker-compose.production.yml')]
    config = json.loads(run([*base, 'config', '--format', 'json'], capture_output=True).stdout)
    for service, spec in config['services'].items():
        spec.pop('ports', None)
        spec.pop('container_name', None)
        spec.pop('build', None)
        if service not in ('postgres', 'redis'):
            spec['image'] = args.image
            if service == 'api':
                spec['command'] = ['gunicorn', '--config', 'config/gunicorn.conf.py', 'config.wsgi:application']
            spec['volumes'] = [{'type': 'volume', 'source': 'test_media', 'target': '/app/data'},
                               {'type': 'bind', 'source': str(ROOT / 'scripts/production_check.py'),
                                'target': '/app/scripts/production_check.py', 'read_only': True}]
    config['volumes']['test_media'] = {'name': name + '_media'}
    path = folder / 'isolated.json'
    path.write_text(json.dumps(config))
    compose = ['docker', 'compose', '-p', name, '-f', str(path)]
    def dc(*items, **kwargs):
        return run([*compose, *items], **kwargs)
    def expect_failure(*items):
        result = subprocess.run([*compose, *items], capture_output=True, text=True)
        assert result.returncode != 0, 'Failure injection was not detected'
        assert 'FAIL ' in result.stderr, 'The health helper did not report the failure'
    try:
        dc('up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', 'postgres', 'redis')
        dc('run', '--rm', '--no-deps', 'migrate')
        dc('run', '--rm', '--no-deps', 'migrate')  # Repeated initialization.
        dc('run', '--rm', '--no-deps', 'migrate', 'python', 'manage.py', 'makemigrations', '--check', '--dry-run')
        dc('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'api', 'worker_network', 'worker_render', 'dispatcher')
        dc('exec', '-T', 'api', 'python', 'scripts/production_check.py', 'synthetic')
        dc('exec', '-T', 'api', 'python', 'scripts/production_check.py', 'report')
        dc('exec', '-T', 'api', 'python', '-c', '''
import os, httpx
from studio.models import Project
with httpx.Client(base_url='http://127.0.0.1:8788', follow_redirects=True,
                  headers={'X-Forwarded-For': '192.0.2.55'}, timeout=15) as client:
    assert client.get('/api/bootstrap').status_code == 401
    assert client.post('/login', data={'password': os.environ['STUDIO_PASSWORD']}).status_code == 200
    token = client.get('/api/bootstrap').json()['token']
    assert client.get('/static/app.css').status_code == 200
    client.headers['X-Studio-Token'] = token
    response = client.post('/api/projects', json=Project(title='HTTP deployment fixture').model_dump())
    assert response.status_code == 200, response.status_code
    assert client.get('/api/projects/' + response.json()['id']).status_code == 200
    assert client.get('/api/health').json()['status'] == 'ok'
print('PASS: real Gunicorn HTTP login, static files, browser token and project creation')
''')
        # Backup and real restore to a NEW isolated database, never --clean or production.
        archive = folder / 'fixture.dump'
        with archive.open('wb') as output:
            subprocess.run([*compose, 'exec', '-T', 'postgres', 'pg_dump', '-U', 'postgres', '-d', 'studio', '-Fc'], stdout=output, check=True)
        with archive.open('rb') as source:
            subprocess.run([*compose, 'exec', '-T', 'postgres', 'pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, check=True)
        dc('exec', '-T', 'postgres', 'createdb', '-U', 'postgres', 'studio_restore_fixture')
        with archive.open('rb') as source:
            subprocess.run([*compose, 'exec', '-T', 'postgres', 'pg_restore', '-U', 'postgres', '--exit-on-error', '--single-transaction', '-d', 'studio_restore_fixture'], stdin=source, check=True)
        result = dc('exec', '-T', 'postgres', 'psql', '-U', 'postgres', '-d', 'studio_restore_fixture', '-Atc', 'SELECT count(*) FROM django_migrations', capture_output=True)
        assert int(result.stdout.strip()) >= 3
        dc('exec', '-T', 'api', 'python', '-c', "from pathlib import Path; Path('/app/data/persistence-fixture').write_text('survives')")
        dc('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '180', 'api')
        dc('exec', '-T', 'api', 'python', '-c', "from pathlib import Path; assert Path('/app/data/persistence-fixture').read_text() == 'survives'")
        dc('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '120', 'postgres')
        dc('exec', '-T', 'api', 'python', 'scripts/production_check.py', 'api')
        dc('restart', 'worker_render', 'redis')
        dc('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'worker_render', 'redis')
        # Actual dependency outages affect this unique fixture only.
        for dependency in ('postgres', 'redis'):
            dc('stop', '--timeout', '30', dependency)
            expect_failure('exec', '-T', 'api', 'python', 'scripts/production_check.py', 'api')
            dc('up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', dependency)
            dc('exec', '-T', 'api', 'python', 'scripts/production_check.py', 'api')
            print('PASS: detected and recovered isolated', dependency, 'outage')
        # Inject unavailable tools/fonts without changing the image or host packages.
        for missing in ('ffprobe', 'telugu_font'):
            expect_failure('exec', '-T', 'api', 'python', '-c', '''
import runpy, subprocess, sys
from unittest.mock import patch
original = subprocess.check_output
missing = sys.argv[1]
def injected(command, *args, **kwargs):
    if missing == 'ffprobe' and command[0] == 'ffprobe':
        raise FileNotFoundError('injected missing ffprobe')
    if missing == 'telugu_font' and command[0] == 'fc-match':
        return b'DejaVu Sans'
    return original(command, *args, **kwargs)
sys.argv = ['scripts/production_check.py', 'tools']
with patch('subprocess.check_output', side_effect=injected):
    runpy.run_path('scripts/production_check.py', run_name='__main__')
''', missing)
            print('PASS: detected injected missing', missing)
        dc('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'api', 'worker_network', 'worker_render', 'dispatcher')
        # Dedicated audit runner creates another uniquely named database and mocks paid providers.
        dc('exec', '-T', 'api', 'python', 'scripts/run_audit_tests.py')
        print('PASS: fresh startup, repeated migrations, worker/Redis restart, persistent DB/media, backup restore, synthetic draft/full, regression suite')
    finally:
        dc('stop', '--timeout', '30')
        # No down -v, pruning, or unrelated resource removal.
        dc('down')
        print('Test artifacts/volumes retained:', name, folder)


if __name__ == '__main__':
    main()
