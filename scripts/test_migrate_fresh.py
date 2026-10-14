import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Target EMPTY disposable database
os.environ['STUDIO_DATABASE_URL'] = 'postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_fresh'
os.environ['STUDIO_MIGRATION_DATABASE_URL'] = 'postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_fresh'
os.environ.pop('STUDIO_READONLY_DATABASE_URL', None)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.core.management import call_command

print("=== RUNNING DJANGO MIGRATION ON FRESH EMPTY DATABASE ===")
call_command('migrate', verbosity=2)
print("Migration on studio_m9_fresh completed successfully!")
