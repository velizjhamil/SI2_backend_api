-- 002_sprint0_multi_tenant.sql
-- Sprint 0: Multi-Tenant y Gestión de Cooperativas
-- Migración IDEMPOTENTE para bases de datos existentes.
-- Para instalaciones nuevas basta ejecutar bd.sql (ya incluye estos objetos).
--
-- Aplicación:
--   psql "postgresql://<usuario>:<password>@localhost:5433/cooperativa_db" -f migrations/002_sprint0_multi_tenant.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS cooperativa (
    id             BIGSERIAL    PRIMARY KEY,
    uuid           UUID         NOT NULL DEFAULT gen_random_uuid(),
    nombre         VARCHAR(150) NOT NULL,
    razon_social   VARCHAR(200),
    nit            VARCHAR(20),
    correo         VARCHAR(150),
    telefono       VARCHAR(20),
    direccion      TEXT,
    estado         VARCHAR(20)  NOT NULL DEFAULT 'ACTIVO',
    fecha_creacion TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    fecha_baja     TIMESTAMPTZ,
    CONSTRAINT uq_cooperativa_uuid    UNIQUE (uuid),
    CONSTRAINT uq_cooperativa_nit     UNIQUE (nit),
    CONSTRAINT chk_cooperativa_estado CHECK (estado IN ('ACTIVO','INACTIVO'))
);

CREATE INDEX IF NOT EXISTS idx_cooperativa_estado ON cooperativa(estado);
CREATE INDEX IF NOT EXISTS idx_cooperativa_nombre ON cooperativa(nombre);

ALTER TABLE usuario
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id);

CREATE INDEX IF NOT EXISTS idx_usuario_cooperativa ON usuario(cooperativa_id);

INSERT INTO rol (nombre, descripcion)
VALUES ('SUPERADMIN', 'Super Administrador SaaS: gestiona cooperativas (tenants)')
ON CONFLICT (nombre) DO NOTHING;

COMMIT;
