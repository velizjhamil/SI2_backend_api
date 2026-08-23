"""Ejecuta la migración 003 de la bitácora directamente desde Python."""
import os
import sys

from dotenv import load_dotenv
load_dotenv()

database_url = os.environ.get("DATABASE_URL", "")
from urllib.parse import urlparse
parsed = urlparse(database_url)

import psycopg2

conn = psycopg2.connect(
    host=parsed.hostname,
    port=parsed.port or 5432,
    user=parsed.username,
    password=parsed.password,
    dbname=parsed.path.lstrip("/"),
)
conn.autocommit = False
cur = conn.cursor()

# Solo el DDL de la tabla (sin el INSERT de prueba que falló)
sql_ddl = """
CREATE TABLE IF NOT EXISTS bitacora (
    id              BIGSERIAL PRIMARY KEY,
    usuario_id      BIGINT      REFERENCES usuario(id)      ON DELETE SET NULL,
    cooperativa_id  BIGINT      REFERENCES cooperativa(id)  ON DELETE SET NULL,
    accion          VARCHAR(100) NOT NULL,
    modulo          VARCHAR(50)  NOT NULL DEFAULT 'SISTEMA',
    detalles        TEXT,
    ip_origen       VARCHAR(45),
    fecha           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_bitacora_modulo
        CHECK (modulo IN ('SISTEMA','SOCIOS','CREDITOS','CAJA','CONTABILIDAD','AUTH'))
);

CREATE INDEX IF NOT EXISTS idx_bitacora_cooperativa  ON bitacora (cooperativa_id, fecha DESC);
CREATE INDEX IF NOT EXISTS idx_bitacora_usuario       ON bitacora (usuario_id);
CREATE INDEX IF NOT EXISTS idx_bitacora_modulo        ON bitacora (modulo, fecha DESC);
"""

try:
    cur.execute(sql_ddl)
    conn.commit()
    print("OK: Tabla bitacora creada.")
    cur.execute("SELECT COUNT(*) FROM bitacora;")
    count = cur.fetchone()[0]
    print(f"Registros en bitacora: {count}")

    # Insertar registro inicial de prueba usando el primer superadmin disponible
    cur.execute("""
        INSERT INTO bitacora (usuario_id, cooperativa_id, accion, modulo, detalles)
        SELECT u.id, u.cooperativa_id, 'SISTEMA_INICIADO', 'SISTEMA',
               'Migracion 003 ejecutada - Bitacora inicializada'
        FROM usuario u
        INNER JOIN rol r ON r.id = u.rol_id
        WHERE r.nombre = 'SUPERADMIN'
        LIMIT 1;
    """)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM bitacora;")
    print(f"Registros tras seed: {cur.fetchone()[0]}")

except Exception as e:
    conn.rollback()
    print(f"ERROR: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
finally:
    cur.close()
    conn.close()
