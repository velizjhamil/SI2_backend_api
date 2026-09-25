-- CU-W12 / CU-W16: withdrawal identification and formal cash closing.

ALTER TABLE transaccion
    ADD COLUMN IF NOT EXISTS retirante_tipo VARCHAR(10),
    ADD COLUMN IF NOT EXISTS retirante_nombre VARCHAR(150),
    ADD COLUMN IF NOT EXISTS retirante_ci VARCHAR(20);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_transaccion_retirante_tipo'
          AND conrelid = 'transaccion'::regclass
    ) THEN
        ALTER TABLE transaccion
            ADD CONSTRAINT chk_transaccion_retirante_tipo
            CHECK (retirante_tipo IS NULL OR retirante_tipo IN ('TITULAR', 'APODERADO'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS cierre_caja (
    id BIGSERIAL PRIMARY KEY,
    control_caja_id INT NOT NULL UNIQUE REFERENCES control_caja(id),
    arqueo_id BIGINT NOT NULL REFERENCES arqueo_caja(id),
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    observacion TEXT
);

CREATE TABLE IF NOT EXISTS cierre_caja_moneda (
    id BIGSERIAL PRIMARY KEY,
    cierre_id BIGINT NOT NULL REFERENCES cierre_caja(id) ON DELETE CASCADE,
    moneda_id INT NOT NULL REFERENCES moneda(id),
    monto_apertura NUMERIC(12,2) NOT NULL,
    total_depositos NUMERIC(12,2) NOT NULL,
    cantidad_depositos INT NOT NULL,
    total_retiros NUMERIC(12,2) NOT NULL,
    cantidad_retiros INT NOT NULL,
    cantidad_transferencias INT NOT NULL,
    saldo_teorico NUMERIC(12,2) NOT NULL,
    total_contado NUMERIC(12,2) NOT NULL,
    diferencia NUMERIC(12,2) NOT NULL,
    traspaso_boveda NUMERIC(12,2) NOT NULL
);
