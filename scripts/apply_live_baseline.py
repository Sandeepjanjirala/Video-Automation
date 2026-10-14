import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ['STUDIO_DATABASE_URL'] = 'postgresql://postgres:postgres@127.0.0.1:5433/studio'
os.environ['STUDIO_MIGRATION_DATABASE_URL'] = 'postgresql://postgres:postgres@127.0.0.1:5433/studio'
os.environ.pop('STUDIO_READONLY_DATABASE_URL', None)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.core.management import call_command
import psycopg2

conn = psycopg2.connect('postgresql://postgres:postgres@127.0.0.1:5433/studio')
cur = conn.cursor()

print("=== APPLYING BASELINE MIGRATION --fake-initial TO LIVE STUDIO DATABASE ===")
call_command('migrate', fake_initial=True, verbosity=2)

cur.execute('SELECT app, name, applied FROM django_migrations;')
print('Live django_migrations entries:', cur.fetchall())

# Count rows across all tables
for tbl in ['workspaces', 'projects', 'revisions', 'drafts', 'assets', 'jobs', 'job_stages', 'application_receipts', 'outbox', 'voice_takes', 'usage']:
    cur.execute(f'SELECT count(*) FROM {tbl};')
    print(f'Live {tbl}: {cur.fetchone()[0]} rows')

conn.close()
print("Live database migration ownership successfully transferred to Django!")
