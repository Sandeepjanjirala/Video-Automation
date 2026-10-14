import psycopg2

conn = psycopg2.connect('postgresql://postgres:postgres@127.0.0.1:5433/studio')
cur = conn.cursor()
cur.execute("""
    SELECT grantee, privilege_type 
    FROM information_schema.table_privileges 
    WHERE table_name = 'usage'
    ORDER BY grantee, privilege_type;
""")
for r in cur.fetchall():
    print(r)
conn.close()
