BEGIN;

ALTER TABLE pago_cuota
    ADD COLUMN IF NOT EXISTS credito_id INT REFERENCES credito(id),
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id),
    ADD COLUMN IF NOT EXISTS numero_recibo VARCHAR(20),
    ADD COLUMN IF NOT EXISTS modalidad VARCHAR(10),
    ADD COLUMN IF NOT EXISTS monto_total NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS dias_atraso INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS transaccion_id INT REFERENCES transaccion(id),
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id);

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'pago_cuota'::regclass
          AND conname = 'uq_pago_cuota_cooperativa_recibo'
    ) THEN
        ALTER TABLE pago_cuota
            ADD CONSTRAINT uq_pago_cuota_cooperativa_recibo
            UNIQUE (cooperativa_id, numero_recibo);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'pago_cuota'::regclass
          AND conname = 'ck_pago_cuota_modalidad'
    ) THEN
        ALTER TABLE pago_cuota
            ADD CONSTRAINT ck_pago_cuota_modalidad
            CHECK (modalidad IS NULL OR modalidad IN ('EFECTIVO', 'CUENTA'));
    END IF;
END $$;

ALTER TABLE tabla_amortizacion
    ADD COLUMN IF NOT EXISTS fecha_pago TIMESTAMPTZ;

ALTER TABLE morosidad
    ADD COLUMN IF NOT EXISTS fecha_actualizacion TIMESTAMPTZ DEFAULT now();

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'morosidad'::regclass
          AND conname = 'uq_morosidad_credito'
    ) AND NOT EXISTS (
        SELECT credito_id FROM morosidad
        GROUP BY credito_id
        HAVING COUNT(*) > 1
    ) THEN
        ALTER TABLE morosidad
            ADD CONSTRAINT uq_morosidad_credito UNIQUE (credito_id);
    END IF;
END $$;

UPDATE pago_cuota p
SET credito_id = ta.credito_id
FROM tabla_amortizacion ta
WHERE ta.id = p.tabla_amortizacion_id
  AND p.credito_id IS NULL;

UPDATE pago_cuota p
SET cooperativa_id = c.cooperativa_id
FROM credito c
WHERE c.id = p.credito_id
  AND p.cooperativa_id IS NULL
  AND c.cooperativa_id IS NOT NULL;

COMMIT;
