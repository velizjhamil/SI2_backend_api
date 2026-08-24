-- ============================================================
-- Migración 003: Tabla de Bitácora de Auditoría
-- Sprint 1 — Dashboard Admin + Audit Trail
-- Fecha: 2026-08-23
-- ============================================================

-- Si la tabla no existe, crearla con el esquema base (bd.sql puede no haber corrido)
CREATE TABLE IF NOT EXISTS bitacora (
    id              BIGSERIAL PRIMARY KEY,
    usuario_id      BIGINT      REFERENCES usuario(id)     ON DELETE SET NULL,
    modulo          VARCHAR(50) NOT NULL DEFAULT 'SISTEMA',
    accion          VARCHAR(50) NOT NULL,
    fecha_hora      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Añadir columnas del esquema multi-tenant 003 (idempotente)
ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT;
ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS detalles      TEXT;
ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS ip_origen     VARCHAR(45);
ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS fecha         TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Alinear tipos/defaults del esquema 003
ALTER TABLE bitacora ALTER COLUMN accion TYPE VARCHAR(100);
ALTER TABLE bitacora ALTER COLUMN modulo SET DEFAULT 'SISTEMA';

-- Foreign key cooperativa_id -> cooperativa(id)  (idempotente)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'bitacora' AND constraint_name = 'fk_bitacora_cooperativa'
    ) THEN
        ALTER TABLE bitacora
            ADD CONSTRAINT fk_bitacora_cooperativa
            FOREIGN KEY (cooperativa_id) REFERENCES cooperativa(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Check constraint para módulo  (idempotente)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'bitacora' AND constraint_name = 'chk_bitacora_modulo'
    ) THEN
        ALTER TABLE bitacora
            ADD CONSTRAINT chk_bitacora_modulo
            CHECK (modulo IN ('SISTEMA','SOCIOS','CREDITOS','CAJA','CONTABILIDAD','AUTH'));
    END IF;
END $$;

-- Índices para consultas frecuentes del dashboard
CREATE INDEX IF NOT EXISTS idx_bitacora_cooperativa ON bitacora (cooperativa_id, fecha DESC);
CREATE INDEX IF NOT EXISTS idx_bitacora_usuario      ON bitacora (usuario_id);
CREATE INDEX IF NOT EXISTS idx_bitacora_modulo       ON bitacora (modulo, fecha DESC);

COMMENT ON TABLE  bitacora                IS 'Registro de auditoría de acciones relevantes del sistema.';
COMMENT ON COLUMN bitacora.accion         IS 'Identificador de la acción (ej. LOGIN_EXITOSO, SOCIO_CREADO).';
COMMENT ON COLUMN bitacora.modulo         IS 'Módulo origen de la acción.';
COMMENT ON COLUMN bitacora.detalles       IS 'Información adicional en texto libre o JSON.';
COMMENT ON COLUMN bitacora.ip_origen      IS 'IP del cliente que originó la acción (IPv4 o IPv6).';

-- Insertar algunos registros de prueba para visualizar en el dashboard
INSERT INTO bitacora (usuario_id, cooperativa_id, accion, modulo, detalles)
SELECT
    u.id,
    u.cooperativa_id,
    'SISTEMA_INICIADO',
    'SISTEMA',
    'Migración 003 ejecutada — Bitácora inicializada'
FROM usuario u
INNER JOIN rol r ON r.id = u.rol_id
WHERE r.nombre = 'SUPERADMIN'
LIMIT 1;
