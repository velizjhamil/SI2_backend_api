-- ============================================================
-- Migración 003: Tabla de Bitácora de Auditoría
-- Sprint 1 — Dashboard Admin + Audit Trail
-- Fecha: 2026-08-23
-- ============================================================

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

-- Índices para consultas frecuentes del dashboard
CREATE INDEX IF NOT EXISTS idx_bitacora_cooperativa  ON bitacora (cooperativa_id, fecha DESC);
CREATE INDEX IF NOT EXISTS idx_bitacora_usuario       ON bitacora (usuario_id);
CREATE INDEX IF NOT EXISTS idx_bitacora_modulo        ON bitacora (modulo, fecha DESC);

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
