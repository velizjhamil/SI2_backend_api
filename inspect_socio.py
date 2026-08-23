from dotenv import load_dotenv; load_dotenv()
import os
from urllib.parse import urlparse
import psycopg2

p = urlparse(os.environ["DATABASE_URL"])
conn = psycopg2.connect(host=p.hostname, port=p.port or 5432,
                        user=p.username, password=p.password, dbname=p.path.lstrip("/"))
cur = conn.cursor()

# Modulos existentes en bitacora
cur.execute("SELECT DISTINCT modulo, COUNT(*) FROM bitacora GROUP BY modulo ORDER BY modulo;")
print("=== Modulos en bitacora ===")
for r in cur.fetchall(): print(r)

# Estructura constraints
cur.execute("""
    SELECT conname, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'bitacora'::regclass;
""")
print("\n=== Constraints bitacora ===")
for r in cur.fetchall(): print(r)

# Contar socios por estado
cur.execute("SELECT estado, COUNT(*) FROM socio GROUP BY estado;")
print("\n=== Socios por estado ===")
for r in cur.fetchall(): print(r)

# Contar cuentas de ahorro activas
cur.execute("SELECT estado, COUNT(*) FROM cuenta_ahorro GROUP BY estado;")
print("\n=== Cuentas ahorro por estado ===")
for r in cur.fetchall(): print(r)

# Ver estructura de socio con cooperativa_id?
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='socio' AND column_name LIKE '%cooperativa%';")
print("\n=== socio.cooperativa? ===")
for r in cur.fetchall(): print(r)

cur.close(); conn.close()
