import os
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Target the restored disposable copy
os.environ['STUDIO_DATABASE_URL'] = 'postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_disposable'
os.environ['STUDIO_MIGRATION_DATABASE_URL'] = 'postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_disposable'
os.environ.pop('STUDIO_READONLY_DATABASE_URL', None)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.core.management import call_command
from core.models import Workspace, Project, Revision, Asset, Job, JobStage, ApplicationReceipt, OutboxEvent, VoiceTake
import psycopg2

print("=== 1. APPLYING BASELINE MIGRATION WITH --fake-initial TO RESTORED COPY ===")
call_command('migrate', fake_initial=True, verbosity=2)

print("\n=== 2. VERIFYING DJANGO_MIGRATIONS TABLE ON RESTORED COPY ===")
conn = psycopg2.connect('postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_disposable')
cur = conn.cursor()
cur.execute("SELECT app, name, applied FROM django_migrations;")
migrations = cur.fetchall()
print("Applied migrations:", migrations)
assert len(migrations) == 1 and migrations[0][0] == 'core' and migrations[0][1] == '0001_initial'

print("\n=== 3. VERIFYING DATA INTEGRITY VIA DJANGO ORM ===")
# Workspaces
ws_count = Workspace.objects.count()
print(f"Workspaces: {ws_count}")
assert ws_count >= 1

# Projects & Telugu verification
proj_count = Project.objects.count()
print(f"Projects: {proj_count}")
assert proj_count >= 1

telugu_found = False
for p in Project.objects.all():
    scenes = p.body.get('scenes', [])
    for sc in scenes:
        narration = sc.get('narration', '')
        roman = sc.get('roman', '')
        if any('\u0c00' <= c <= '\u0c7f' for c in narration):
            print(f"  Verified Telugu in project {p.id}: {narration[:30]}... / Roman: {roman[:30]}...")
            telugu_found = True
            break
    if telugu_found:
        break
assert telugu_found, "Telugu characters not found in projects!"

# Revisions
rev_count = Revision.objects.count()
print(f"Revisions: {rev_count}")
assert rev_count >= 1

# Assets
asset_count = Asset.objects.count()
print(f"Assets: {asset_count}")
assert asset_count >= 1

# Jobs & JobStages
job_count = Job.objects.count()
stage_count = JobStage.objects.count()
print(f"Jobs: {job_count}, Stages: {stage_count}")
assert job_count >= 1

# VoiceTakes
take_count = VoiceTake.objects.count()
print(f"VoiceTakes: {take_count}")
assert take_count >= 1
first_take = VoiceTake.objects.first()
print(f"  Sample take: id={first_take.id}, provider={first_take.provider}, duration={first_take.duration}, timing_data={type(first_take.timing_data)}")

# Receipts
receipt_count = ApplicationReceipt.objects.count()
print(f"ApplicationReceipts: {receipt_count}")

# Outbox
outbox_count = OutboxEvent.objects.count()
print(f"OutboxEvents: {outbox_count}")

# Usage (via raw SQL cursor)
cur.execute("SELECT COUNT(*) FROM usage;")
usage_count = cur.fetchone()[0]
print(f"Usage rows: {usage_count}")

conn.close()
print("\nALL DATA VERIFICATION CHECKS PASSED ON RESTORED COPY!")
