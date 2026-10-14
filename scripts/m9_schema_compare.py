import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import django
import psycopg2

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.apps import apps

conn = psycopg2.connect('postgresql://postgres:postgres@127.0.0.1:5433/studio')
cur = conn.cursor()

core_models = apps.get_app_config('core').get_models()
model_map = {m._meta.db_table: m for m in core_models}

tables = [
    'workspaces', 'projects', 'revisions', 'drafts', 'assets',
    'jobs', 'job_stages', 'application_receipts', 'outbox', 'voice_takes', 'usage'
]

print("=== M9 SCHEMA COMPARISON: POSTGRESQL VS DJANGO MODELS ===")

for tbl in tables:
    cur.execute("""
        SELECT column_name, data_type, is_nullable, character_maximum_length, column_default
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (tbl,))
    pg_cols = {r[0]: {'type': r[1], 'nullable': r[2] == 'YES', 'len': r[3], 'default': r[4]} for r in cur.fetchall()}

    model = model_map.get(tbl)
    if not model:
        print(f"\n[!] Table '{tbl}': No Django model mapped (e.g. usage composite-key table handled via raw SQL)")
        continue

    django_fields = {f.column: f for f in model._meta.fields}

    missing_in_django = set(pg_cols.keys()) - set(django_fields.keys())
    missing_in_pg = set(django_fields.keys()) - set(pg_cols.keys())

    print(f"\nTable: {tbl} (Model: {model.__name__})")
    if missing_in_django:
        print(f"  [!] Missing in Django: {missing_in_django}")
    if missing_in_pg:
        print(f"  [!] Missing in PostgreSQL: {missing_in_pg}")
    if not missing_in_django and not missing_in_pg:
        print("  [OK] Column count and column names match exactly.")

    # Check nullability and type details
    for c_name, c_info in pg_cols.items():
        if c_name in django_fields:
            df = django_fields[c_name]
            if df.null != c_info['nullable']:
                print(f"  [*] Nullability difference on '{c_name}': PG is_nullable={c_info['nullable']}, Django null={df.null}")

    # PK check
    cur.execute("""
        SELECT ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage AS ccu USING (constraint_schema, constraint_name)
        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = %s
    """, (tbl,))
    pg_pks = [r[0] for r in cur.fetchall()]
    django_pks = [f.column for f in model._meta.fields if f.primary_key]
    if pg_pks != django_pks:
        print(f"  [*] PK mismatch on {tbl}: PG={pg_pks}, Django={django_pks}")
    else:
        print(f"  [OK] Primary key matches: {pg_pks}")

    # FK check
    cur.execute("""
        SELECT kcu.column_name, ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s
    """, (tbl,))
    pg_fks = sorted([(r[0], r[1], r[2]) for r in cur.fetchall()])
    django_fks = sorted([
        (f.column, f.related_model._meta.db_table, f.target_field.column)
        for f in model._meta.fields if f.is_relation and f.many_to_one or (f.one_to_one if hasattr(f, 'one_to_one') else False)
    ])
    if pg_fks != django_fks:
        print(f"  [*] FK mismatch on {tbl}: PG={pg_fks}, Django={django_fks}")
    else:
        print(f"  [OK] Foreign keys match: {len(pg_fks)} FK(s)")

    # Index check
    cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", (tbl,))
    pg_indexes = {r[0]: r[1] for r in cur.fetchall()}
    print(f"  [INFO] PostgreSQL indexes ({len(pg_indexes)}): {list(pg_indexes.keys())}")

cur.close()
conn.close()

