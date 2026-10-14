import json
import psycopg2
from pathlib import Path

DB_A_URL = 'postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_disposable'
DB_B_URL = 'postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_fresh'

conn_a = psycopg2.connect(DB_A_URL)
cur_a = conn_a.cursor()

conn_b = psycopg2.connect(DB_B_URL)
cur_b = conn_b.cursor()

def introspect_db(cur):
    # Tables in public
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    tables = [r[0] for r in cur.fetchall()]

    schema = {}
    for tbl in tables:
        # Columns
        cur.execute("""
            SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = 'public'
            ORDER BY ordinal_position;
        """, (tbl,))
        cols = {
            r[0]: {
                'type': r[1],
                'max_len': r[2],
                'nullable': r[3] == 'YES',
                'default': r[4]
            }
            for r in cur.fetchall()
        }

        # Primary Key
        cur.execute("""
            SELECT ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage AS ccu USING (constraint_schema, constraint_name)
            WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = %s AND tc.table_schema = 'public';
        """, (tbl,))
        pks = sorted([r[0] for r in cur.fetchall()])

        # Foreign Keys
        cur.execute("""
            SELECT
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s AND tc.table_schema = 'public';
        """, (tbl,))
        fks = sorted([(r[0], r[1], r[2]) for r in cur.fetchall()])

        # Indexes
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = %s AND schemaname = 'public'
            ORDER BY indexname;
        """, (tbl,))
        indexes = {r[0]: r[1] for r in cur.fetchall()}

        schema[tbl] = {
            'columns': cols,
            'pks': pks,
            'fks': fks,
            'indexes': indexes
        }
    return schema

schema_a = introspect_db(cur_a)
schema_b = introspect_db(cur_b)

conn_a.close()
conn_b.close()

# Report generation
report_lines = [
    "# M9 Schema Diff Report: Alembic vs Django Baseline",
    "",
    "**Source A (Alembic Baseline / Restored Live DB)**: `studio_m9_disposable`",
    "**Source B (Django Migrations Baseline / Fresh DB)**: `studio_m9_fresh`",
    "",
    "## 1. Table Inventory Comparison",
    "",
    "| Table | In Alembic DB (A) | In Django Fresh DB (B) | Status |",
    "| :--- | :--- | :--- | :--- |"
]

all_tables = sorted(set(schema_a.keys()) | set(schema_b.keys()))
for tbl in all_tables:
    in_a = "YES" if tbl in schema_a else "NO"
    in_b = "YES" if tbl in schema_b else "NO"
    status = "MATCH"
    if tbl == "alembic_version":
        status = "RETIRED (Alembic Authority Table)"
    elif tbl == "django_migrations":
        status = "ACTIVE (Django Authority Table)"
    elif in_a != in_b:
        status = "MISMATCH"
    report_lines.append(f"| `{tbl}` | {in_a} | {in_b} | {status} |")

report_lines.append("")
report_lines.append("## 2. Application Tables Detailed Analysis")
report_lines.append("")

app_tables = [
    'workspaces', 'projects', 'revisions', 'drafts', 'assets',
    'jobs', 'job_stages', 'application_receipts', 'outbox', 'voice_takes', 'usage'
]

differences_found = []

for tbl in app_tables:
    report_lines.append(f"### Table `{tbl}`")
    if tbl not in schema_a or tbl not in schema_b:
        diff = f"Table {tbl} missing in one schema!"
        differences_found.append(diff)
        report_lines.append(f"- **ERROR**: {diff}")
        continue

    ta = schema_a[tbl]
    tb = schema_b[tbl]

    # Columns
    cols_a = set(ta['columns'].keys())
    cols_b = set(tb['columns'].keys())
    col_diff_a = cols_a - cols_b
    col_diff_b = cols_b - cols_a

    if col_diff_a or col_diff_b:
        diff = f"Column mismatch in {tbl}: extra in A: {col_diff_a}, extra in B: {col_diff_b}"
        differences_found.append(diff)
        report_lines.append(f"- **Column mismatch**: {diff}")
    else:
        report_lines.append(f"- **Columns ({len(cols_a)})**: 100% Match ({', '.join(sorted(cols_a))})")

    # Column Types & Nullability
    type_mismatches = []
    for c in sorted(cols_a & cols_b):
        ca = ta['columns'][c]
        cb = tb['columns'][c]
        if ca['type'] != cb['type']:
            type_mismatches.append(f"{c}: A={ca['type']} vs B={cb['type']}")
        if ca['nullable'] != cb['nullable']:
            type_mismatches.append(f"{c} nullable: A={ca['nullable']} vs B={cb['nullable']}")

    if type_mismatches:
        report_lines.append(f"- **Type/Nullability differences**: {', '.join(type_mismatches)}")
        differences_found.extend(type_mismatches)
    else:
        report_lines.append("- **Types & Nullability**: 100% Match across all columns")

    # PKs
    if ta['pks'] == tb['pks']:
        report_lines.append(f"- **Primary Key**: Match `{ta['pks']}`")
    else:
        diff = f"PK mismatch on {tbl}: A={ta['pks']} vs B={tb['pks']}"
        differences_found.append(diff)
        report_lines.append(f"- **PK Mismatch**: {diff}")

    # FKs
    if ta['fks'] == tb['fks']:
        report_lines.append(f"- **Foreign Keys ({len(ta['fks'])})**: Match {ta['fks']}")
    else:
        diff = f"FK mismatch on {tbl}: A={ta['fks']} vs B={tb['fks']}"
        differences_found.append(diff)
        report_lines.append(f"- **FK Mismatch**: {diff}")

    # Indexes
    idx_a = set(ta['indexes'].keys())
    idx_b = set(tb['indexes'].keys())
    idx_common = idx_a & idx_b
    report_lines.append(f"- **Indexes**: {len(idx_common)} matching index names")
    if idx_a - idx_b:
        report_lines.append(f"  - In Alembic only: {list(idx_a - idx_b)}")
    if idx_b - idx_a:
        report_lines.append(f"  - In Django only: {list(idx_b - idx_a)}")

    report_lines.append("")

report_lines.append("## 3. Schema Equivalence Verdict")
report_lines.append("")
if not differences_found:
    report_lines.append("**VERDICT: PASS — MATERIALLY EQUIVALENT**")
    report_lines.append("The fresh Django-created schema exactly replicates all 11 application tables, column types, nullabilities, primary keys, and foreign keys created historically by Alembic.")
else:
    report_lines.append("**VERDICT: DIFFERENCES DETECTED**")
    for d in differences_found:
        report_lines.append(f"- {d}")

report_content = "\n".join(report_lines)
out_file = Path('docs/M9_SCHEMA_DIFF.md')
out_file.write_text(report_content, encoding='utf-8')
print("Wrote docs/M9_SCHEMA_DIFF.md successfully!")
print(f"Differences found: {len(differences_found)}")
