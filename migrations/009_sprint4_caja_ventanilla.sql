-- Sprint 4: apertura de caja y operaciones de ventanilla.
BEGIN;

ALTER TABLE caja
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id);

ALTER TABLE caja
    ADD COLUMN IF NOT EXISTS monto_maximo_efectivo NUMERIC(12, 2)
    NOT NULL DEFAULT 50000.00;

-- Pre-existing data may hold several open sessions for the same user or caja,
-- which would make the unique indexes below fail. Keep only the most recent
-- open session per user and per caja; close the rest.
UPDATE control_caja
SET estado = 'CERRADA',
    fecha_cierre = COALESCE(fecha_cierre, now()),
    monto_cierre = COALESCE(monto_cierre, saldo_sistema)
WHERE estado = 'ABIERTA'
  AND (
    id NOT IN (
        SELECT DISTINCT ON (usuario_id) id FROM control_caja
        WHERE estado = 'ABIERTA'
        ORDER BY usuario_id, fecha_apertura DESC, id DESC
    )
    OR id NOT IN (
        SELECT DISTINCT ON (caja_id) id FROM control_caja
        WHERE estado = 'ABIERTA'
        ORDER BY caja_id, fecha_apertura DESC, id DESC
    )
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_control_caja_usuario_abierta
    ON control_caja (usuario_id)
    WHERE estado = 'ABIERTA';

CREATE UNIQUE INDEX IF NOT EXISTS uq_control_caja_caja_abierta
    ON control_caja (caja_id)
    WHERE estado = 'ABIERTA';

ALTER TABLE transaccion
    ADD COLUMN IF NOT EXISTS depositante_nombre VARCHAR(150);

ALTER TABLE transaccion
    ADD COLUMN IF NOT EXISTS depositante_ci VARCHAR(20);

COMMIT;
