"""Run regression tests in a fresh, disposable PostgreSQL database and media folder.

Never points tests at the live studio database or provider settings. No API keys needed.
"""
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def main():
    import django
    django.setup()
    from django.conf import settings
    import psycopg2
    from psycopg2 import sql

    cfg = settings.DATABASES['admin']
    name = 'studio_audit_' + uuid.uuid4().hex[:12]
    output = ROOT / 'test-outputs' / name
    output.mkdir(parents=True)
    admin = psycopg2.connect(dbname='postgres', user=cfg['USER'], password=cfg['PASSWORD'],
                             host=cfg['HOST'], port=cfg['PORT'], connect_timeout=5)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    env = dict(os.environ)
    url = (f"postgresql://{quote(cfg['USER'], safe='')}:{quote(cfg['PASSWORD'], safe='')}"
           f"@{cfg['HOST']}:{cfg['PORT']}/{name}")
    for key in ('STUDIO_DATABASE_URL', 'STUDIO_MIGRATION_DATABASE_URL', 'STUDIO_READONLY_DATABASE_URL',
                'STUDIO_MEDIA_WRITER_DATABASE_URL', 'STUDIO_JOB_WRITER_DATABASE_URL',
                'STUDIO_PROJECT_WRITER_DATABASE_URL', 'STUDIO_VOICE_WRITER_DATABASE_URL'):
        env[key] = url
    env['REEL_DATA_DIR'] = str(output / 'data')
    # Never the developer's real keys from .env: tests use fixtures only.
    env['STUDIO_ENV_FILE'] = ''
    for secret in ('STUDIO_GEMINI_KEY', 'STUDIO_PIXABAY_KEY', 'STUDIO_PEXELS_KEY', 'STUDIO_AZURE_KEY', 'STUDIO_SARVAM_KEY',
                   'STUDIO_ONLINE', 'SARVAM_API_KEY'):
        env.pop(secret, None)
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], cwd=ROOT, env=env, check=True)
        result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_audit.py', '-q', *sys.argv[1:]],
                                cwd=ROOT, env=env)
        print(f'Audit artifacts: {output}', flush=True)
        return result.returncode
    finally:
        # Only the unique database created by this invocation can be dropped.
        with admin.cursor() as cur:
            cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        admin.close()


if __name__ == '__main__':
    raise SystemExit(main())
