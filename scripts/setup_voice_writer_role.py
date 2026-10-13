import os
import secrets
from pathlib import Path
import psycopg2

db_url = os.environ.get("STUDIO_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/studio")
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'django_voice_writer';")
exists = cur.fetchone()

password = secrets.token_hex(24)
if not exists:
    print("Creating role django_voice_writer...")
    cur.execute(f"CREATE ROLE django_voice_writer WITH LOGIN PASSWORD '{password}';")
else:
    print("Updating password for existing django_voice_writer role...")
    cur.execute(f"ALTER ROLE django_voice_writer WITH LOGIN PASSWORD '{password}';")

print("Granting database and schema privileges...")
cur.execute("GRANT CONNECT ON DATABASE studio TO django_voice_writer;")
cur.execute("GRANT USAGE ON SCHEMA public TO django_voice_writer;")

print("Revoking any default privileges...")
cur.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM django_voice_writer;")
cur.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM django_voice_writer;")

print("Granting narrow SELECT privileges...")
cur.execute("""
    GRANT SELECT ON TABLE 
        workspaces, projects, revisions, assets, voice_takes
    TO django_voice_writer;
""")

print("Granting INSERT privileges on voice_takes, assets, revisions...")
cur.execute("GRANT INSERT ON TABLE voice_takes, assets, revisions TO django_voice_writer;")

print("Granting UPDATE privileges on voice_takes, projects...")
cur.execute("GRANT UPDATE ON TABLE voice_takes, projects TO django_voice_writer;")

print("Granting sequence usage on revisions_id_seq...")
cur.execute("GRANT USAGE, SELECT ON SEQUENCE revisions_id_seq TO django_voice_writer;")

# Save password in backups/.voice_writer_db_url
url = f"postgresql://django_voice_writer:{password}@127.0.0.1:5433/studio"
url_file = Path(__file__).resolve().parent.parent / "backups" / ".voice_writer_db_url"
url_file.parent.mkdir(parents=True, exist_ok=True)
url_file.write_text(url, encoding="utf-8")
print(f"Saved connection URL to {url_file.name}")

print("Verifying privileges...")
cur.execute("""
    SELECT table_name, privilege_type 
    FROM information_schema.table_privileges 
    WHERE grantee = 'django_voice_writer'
    ORDER BY table_name, privilege_type;
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

conn.close()
