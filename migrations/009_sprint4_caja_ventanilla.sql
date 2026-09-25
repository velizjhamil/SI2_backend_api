-- Sprint 4: apertura de caja y operaciones de ventanilla.
BEGIN;

ALTER TABLE caja
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id);

ALTER TABLE caja
    ADD COLUMN IF NOT EXISTS monto_maximo_efectivo NUMERIC(12, 2)
    NOT NULL DEFAULT 50000.00;

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
