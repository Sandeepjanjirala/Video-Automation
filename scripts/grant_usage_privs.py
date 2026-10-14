import psycopg2

conn = psycopg2.connect('postgresql://postgres:postgres@127.0.0.1:5433/studio')
cur = conn.cursor()
for role in ('django_project_writer', 'django_job_writer', 'django_voice_writer'):
    cur.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE usage TO {role};")
conn.commit()
print("Granted SELECT, INSERT, UPDATE on usage to roles.")
conn.close()
