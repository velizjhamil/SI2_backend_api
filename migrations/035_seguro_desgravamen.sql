ALTER TABLE producto_credito
    ADD COLUMN IF NOT EXISTS tasa_seguro_desgravamen NUMERIC(5, 2) NOT NULL DEFAULT 0.00;

ALTER TABLE tabla_amortizacion
    ADD COLUMN IF NOT EXISTS monto_seguro NUMERIC(12, 2) NOT NULL DEFAULT 0.00;

ALTER TABLE pago_cuota
    ADD COLUMN IF NOT EXISTS monto_seguro NUMERIC(12, 2) NOT NULL DEFAULT 0.00;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_producto_credito_tasa_seguro_desgravamen'
          AND conrelid = 'producto_credito'::regclass
    ) THEN
        ALTER TABLE producto_credito
            ADD CONSTRAINT chk_producto_credito_tasa_seguro_desgravamen
            CHECK (tasa_seguro_desgravamen >= 0 AND tasa_seguro_desgravamen <= 5);
    END IF;
END $$;
