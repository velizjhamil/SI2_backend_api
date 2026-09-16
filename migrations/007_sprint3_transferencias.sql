-- Sprint 3: transferencias entre cuentas propias.
-- Columna self-FK nullable para vincular las dos patas de una transferencia
-- (TRANSFERENCIA_SALIDA / TRANSFERENCIA_ENTRADA) en `transaccion`,
-- al estilo de `transaccion_reversion_id` (bd.sql).
BEGIN;

ALTER TABLE transaccion
    ADD COLUMN IF NOT EXISTS transaccion_contraparte_id INT REFERENCES transaccion(id);

CREATE INDEX IF NOT EXISTS idx_transaccion_contraparte ON transaccion(transaccion_contraparte_id);

COMMIT;
