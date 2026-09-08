-- Sprint 2: tipo de producto y monto de apertura en cuentas de ahorro;
-- titulos, valor unitario y correlativo en certificados de aportacion.
BEGIN;

-- ── Cuentas de ahorro ──────────────────────────────────────────────────────
ALTER TABLE cuenta_ahorro
    ADD COLUMN IF NOT EXISTS tipo_producto VARCHAR(20) NOT NULL DEFAULT 'VISTA';

DO $$ BEGIN
    ALTER TABLE cuenta_ahorro
        ADD CONSTRAINT chk_cuenta_tipo_producto CHECK (tipo_producto IN ('VISTA', 'PROGRAMADO'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Los estados del criterio de aceptacion son Activa/Bloqueada/Cancelada
-- (el estado legado INACTIVA pasa a CANCELADA).
UPDATE cuenta_ahorro SET estado = 'CANCELADA' WHERE estado = 'INACTIVA';

DO $$ BEGIN
    ALTER TABLE cuenta_ahorro
        ADD CONSTRAINT chk_cuenta_estado CHECK (estado IN ('ACTIVA', 'BLOQUEADA', 'CANCELADA'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Certificados de aportacion ───────────────────────────────────────────
ALTER TABLE certificado_aportacion
    ADD COLUMN IF NOT EXISTS numero_titulos INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS valor_unitario NUMERIC(12, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS correlativo INT;

-- Certificados existentes: se asume 1 titulo con valor unitario = monto ya emitido.
UPDATE certificado_aportacion SET valor_unitario = monto WHERE valor_unitario = 0;

-- ── Secuencias parametrizadas por cooperativa ───────────────────────────
-- Numeracion de cuentas de ahorro y correlativo oficial de certificados,
-- ambos independientes por tenant (cooperativa_id = 0 agrupa registros legados sin tenant).
CREATE TABLE IF NOT EXISTS secuencia_documento (
    cooperativa_id INT NOT NULL,
    tipo VARCHAR(30) NOT NULL,
    siguiente INT NOT NULL DEFAULT 1,
    PRIMARY KEY (cooperativa_id, tipo)
);

-- Asigna correlativo a certificados existentes segun su orden de emision, por cooperativa.
WITH numerados AS (
    SELECT ca.id,
           ROW_NUMBER() OVER (PARTITION BY COALESCE(s.cooperativa_id, 0) ORDER BY ca.id) AS rn,
           COALESCE(s.cooperativa_id, 0) AS coop
    FROM certificado_aportacion ca
    JOIN socio s ON s.id = ca.socio_id
)
UPDATE certificado_aportacion ca
SET correlativo = numerados.rn
FROM numerados
WHERE numerados.id = ca.id;

ALTER TABLE certificado_aportacion
    ALTER COLUMN correlativo SET NOT NULL;

-- Inicializa el contador de cada cooperativa a partir de lo ya emitido.
INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
SELECT COALESCE(s.cooperativa_id, 0), 'CERTIFICADO_APORTACION', COALESCE(MAX(ca.correlativo), 0) + 1
FROM certificado_aportacion ca
JOIN socio s ON s.id = ca.socio_id
GROUP BY COALESCE(s.cooperativa_id, 0)
ON CONFLICT (cooperativa_id, tipo) DO NOTHING;

INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
SELECT COALESCE(s.cooperativa_id, 0), 'CUENTA_AHORRO', COUNT(*) + 1
FROM cuenta_ahorro c
JOIN socio s ON s.id = c.socio_id
GROUP BY COALESCE(s.cooperativa_id, 0)
ON CONFLICT (cooperativa_id, tipo) DO NOTHING;

COMMIT;
