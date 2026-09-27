BEGIN;

ALTER TABLE credito
    ADD COLUMN IF NOT EXISTS numero_credito VARCHAR(20),
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT,
    ADD COLUMN IF NOT EXISTS socio_id INT,
    ADD COLUMN IF NOT EXISTS producto_credito_id INT,
    ADD COLUMN IF NOT EXISTS moneda_id INT,
    ADD COLUMN IF NOT EXISTS tasa_interes NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS plazo_meses INT,
    ADD COLUMN IF NOT EXISTS tipo_amortizacion VARCHAR(10),
    ADD COLUMN IF NOT EXISTS fecha_desembolso DATE,
    ADD COLUMN IF NOT EXISTS modalidad_desembolso VARCHAR(10),
    ADD COLUMN IF NOT EXISTS cuenta_desembolso_id INT,
    ADD COLUMN IF NOT EXISTS transaccion_desembolso_id INT,
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT,
    ADD COLUMN IF NOT EXISTS fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_cooperativa') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_cooperativa FOREIGN KEY (cooperativa_id) REFERENCES cooperativa(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_socio') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_socio FOREIGN KEY (socio_id) REFERENCES socio(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_producto_credito') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_producto_credito FOREIGN KEY (producto_credito_id) REFERENCES producto_credito(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_moneda') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_moneda FOREIGN KEY (moneda_id) REFERENCES moneda(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_cuenta_desembolso') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_cuenta_desembolso FOREIGN KEY (cuenta_desembolso_id) REFERENCES cuenta_ahorro(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_transaccion_desembolso') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_transaccion_desembolso FOREIGN KEY (transaccion_desembolso_id) REFERENCES transaccion(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='fk_credito_usuario') THEN
        ALTER TABLE credito ADD CONSTRAINT fk_credito_usuario FOREIGN KEY (usuario_id) REFERENCES usuario(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='uq_credito_cooperativa_numero') THEN
        ALTER TABLE credito ADD CONSTRAINT uq_credito_cooperativa_numero UNIQUE (cooperativa_id, numero_credito);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='ck_credito_modalidad_desembolso') THEN
        ALTER TABLE credito ADD CONSTRAINT ck_credito_modalidad_desembolso
            CHECK (modalidad_desembolso IS NULL OR modalidad_desembolso IN ('CUENTA','EFECTIVO'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='credito'::regclass AND conname='ck_credito_tipo_amortizacion') THEN
        ALTER TABLE credito ADD CONSTRAINT ck_credito_tipo_amortizacion
            CHECK (tipo_amortizacion IS NULL OR tipo_amortizacion IN ('FRANCES','ALEMAN'));
    END IF;
END $$;

ALTER TABLE tabla_amortizacion
    ADD COLUMN IF NOT EXISTS saldo_inicial NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS saldo_final NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS monto_pagado NUMERIC(14,2) NOT NULL DEFAULT 0.00;

ALTER TABLE transaccion ADD COLUMN IF NOT EXISTS credito_id INT;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='transaccion'::regclass AND conname='fk_transaccion_credito') THEN
        ALTER TABLE transaccion ADD CONSTRAINT fk_transaccion_credito FOREIGN KEY (credito_id) REFERENCES credito(id);
    END IF;
END $$;

UPDATE credito c
SET cooperativa_id = COALESCE(c.cooperativa_id, sc.cooperativa_id, s.cooperativa_id),
    socio_id = COALESCE(c.socio_id, sc.socio_id),
    producto_credito_id = COALESCE(c.producto_credito_id, sc.producto_credito_id),
    moneda_id = COALESCE(c.moneda_id, sc.moneda_id, pc.moneda_id),
    tasa_interes = COALESCE(c.tasa_interes, sc.tasa_interes),
    plazo_meses = COALESCE(c.plazo_meses, sc.plazo_meses),
    tipo_amortizacion = COALESCE(c.tipo_amortizacion, pc.tipo_amortizacion)
FROM solicitud_credito sc
JOIN socio s ON s.id = sc.socio_id
LEFT JOIN producto_credito pc ON pc.id = sc.producto_credito_id
WHERE sc.id = c.solicitud_credito_id;

-- The legacy demo credit has no tenant references on its request, socio, or product.
-- Attribute only that exact seed row when the canonical natural key is unique.
UPDATE credito c
SET cooperativa_id = (
    SELECT id FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2'
)
WHERE c.id = 1
  AND c.solicitud_credito_id = 1
  AND c.cooperativa_id IS NULL
  AND (
      SELECT COUNT(*) FROM cooperativa
      WHERE nombre = 'Cooperativa de Prueba SI2'
  ) = 1;

WITH existing_numbers AS (
    SELECT cooperativa_id,
           COALESCE(MAX(SUBSTRING(numero_credito FROM '^CRE-([0-9]+)$')::BIGINT), 0) AS max_number
    FROM credito
    WHERE cooperativa_id IS NOT NULL AND numero_credito ~ '^CRE-[0-9]+$'
    GROUP BY cooperativa_id
), missing_numbers AS (
    SELECT c.id, c.cooperativa_id,
           COALESCE(en.max_number, 0) + ROW_NUMBER() OVER (
               PARTITION BY c.cooperativa_id ORDER BY c.id
           ) AS number_value
    FROM credito c
    LEFT JOIN existing_numbers en ON en.cooperativa_id = c.cooperativa_id
    WHERE c.cooperativa_id IS NOT NULL AND c.numero_credito IS NULL
)
UPDATE credito c
SET numero_credito = 'CRE-' || LPAD(mn.number_value::TEXT, 6, '0')
FROM missing_numbers mn
WHERE mn.id = c.id;

INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
SELECT cooperativa_id::INT, 'CREDITO',
       COALESCE(MAX(SUBSTRING(numero_credito FROM '^CRE-([0-9]+)$')::INT), 0) + 1
FROM credito
WHERE cooperativa_id IS NOT NULL AND numero_credito ~ '^CRE-[0-9]+$'
GROUP BY cooperativa_id
ON CONFLICT (cooperativa_id, tipo)
DO UPDATE SET siguiente = GREATEST(secuencia_documento.siguiente, EXCLUDED.siguiente);

COMMIT;
