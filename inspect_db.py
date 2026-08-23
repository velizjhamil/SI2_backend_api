from dotenv import load_dotenv; load_dotenv()
import os
from urllib.parse import urlparse
import psycopg2

parsed = urlparse(os.environ["DATABASE_URL"])
conn = psycopg2.connect(host=parsed.hostname, port=parsed.port or 5432,
                         user=parsed.username, password=parsed.password,
                         dbname=parsed.path.lstrip("/"))
cur = conn.cursor()

# Ver columnas de bitacora existente
cur.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'bitacora'
    ORDER BY ordinal_position;
""")
print("=== bitacora ===")
for row in cur.fetchall(): print(row)

# Ver un registro de ejemplo
cur.execute("SELECT * FROM bitacora LIMIT 3;")
print("\n=== Datos en bitacora ===")
for row in cur.fetchall(): print(row)

# Contar registros
cur.execute("SELECT COUNT(*) FROM bitacora;")
print(f"\nTotal registros: {cur.fetchone()[0]}")

cur.close(); conn.close()
