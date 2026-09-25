-- ============================================================
-- Migración 003: Tabla de Bitácora de Auditoría
-- Sprint 1 — Dashboard Admin + Audit Trail
-- Fecha: 2026-08-23
-- Revisada: 2026-09-24 — alineada a bd.sql y al modelo Bitacora
--   (sin CHECK de módulo, sin columnas duplicadas, sin datos de prueba)
-- ============================================================

BEGIN;

-- Si la tabla no existe, crearla con el esquema de bd.sql
CREATE TABLE IF NOT EXISTS bitacora (
    id          BIGSERIAL    PRIMARY KEY,
    usuario_id  BIGINT       NOT NULL REFERENCES usuario(id),
    modulo      VARCHAR(50)  NOT NULL,
    accion      VARCHAR(100) NOT NULL,
    descripcion TEXT,
    ip          VARCHAR(45),
    user_agent  TEXT,
    fecha_hora  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Columna multi-tenant (idempotente)
ALTER TABLE bitacora ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT;

-- Foreign key cooperativa_id -> cooperativa(id) (idempotente)
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

-- La versión anterior de esta migración agregaba un CHECK de módulo que no
-- coincide con los módulos que registra la aplicación (USUARIO, SOCIO,
-- AHORROS, APORTES, ROL, ...). Se retira en bases donde ya se aplicó.
ALTER TABLE bitacora DROP CONSTRAINT IF EXISTS chk_bitacora_modulo;

-- Índices para consultas frecuentes del dashboard
CREATE INDEX IF NOT EXISTS idx_bitacora_cooperativa ON bitacora (cooperativa_id, fecha_hora DESC);
CREATE INDEX IF NOT EXISTS idx_bitacora_usuario     ON bitacora (usuario_id);
CREATE INDEX IF NOT EXISTS idx_bitacora_modulo      ON bitacora (modulo, fecha_hora DESC);

COMMENT ON TABLE  bitacora        IS 'Registro de auditoría de acciones relevantes del sistema.';
COMMENT ON COLUMN bitacora.accion IS 'Identificador de la acción (ej. LOGIN, CREAR, ACTUALIZAR).';
COMMENT ON COLUMN bitacora.modulo IS 'Módulo origen de la acción.';

COMMIT;
