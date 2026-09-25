BEGIN;

ALTER TABLE declaracion_jurada_uif
    ADD COLUMN IF NOT EXISTS tipo_operacion VARCHAR(30),
    ADD COLUMN IF NOT EXISTS monto NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS moneda_id INT REFERENCES moneda(id),
    ADD COLUMN IF NOT EXISTS socio_id INT REFERENCES socio(id),
    ADD COLUMN IF NOT EXISTS realizado_por VARCHAR(10),
    ADD COLUMN IF NOT EXISTS tercero_nombre VARCHAR(150),
    ADD COLUMN IF NOT EXISTS tercero_ci VARCHAR(20),
    ADD COLUMN IF NOT EXISTS tercero_parentesco VARCHAR(50),
    ADD COLUMN IF NOT EXISTS actividad_economica VARCHAR(150),
    ADD COLUMN IF NOT EXISTS origen_detalle TEXT,
    ADD COLUMN IF NOT EXISTS destino_detalle TEXT,
    ADD COLUMN IF NOT EXISTS declara_bajo_juramento BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS fraccionada BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id),
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id),
    ADD COLUMN IF NOT EXISTS fecha TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$ BEGIN
    ALTER TABLE declaracion_jurada_uif ADD CONSTRAINT chk_uif_realizado_por
        CHECK (realizado_por IS NULL OR realizado_por IN ('TITULAR','TERCERO'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE deposito_plazo_fijo
    ADD COLUMN IF NOT EXISTS numero_certificado VARCHAR(30),
    ADD COLUMN IF NOT EXISTS modalidad_pago_interes VARCHAR(12) DEFAULT 'VENCIMIENTO',
    ADD COLUMN IF NOT EXISTS interes_bruto NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS retencion_rciva NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS interes_neto NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS origen_fondos VARCHAR(10),
    ADD COLUMN IF NOT EXISTS cuenta_origen_id INT REFERENCES cuenta_ahorro(id),
    ADD COLUMN IF NOT EXISTS cuenta_abono_id INT REFERENCES cuenta_ahorro(id),
    ADD COLUMN IF NOT EXISTS codigo_verificacion VARCHAR(16),
    ADD COLUMN IF NOT EXISTS dpf_origen_id INT REFERENCES deposito_plazo_fijo(id),
    ADD COLUMN IF NOT EXISTS declaracion_jurada_uif_id INT REFERENCES declaracion_jurada_uif(id),
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id),
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id),
    ADD COLUMN IF NOT EXISTS fecha_emision TIMESTAMPTZ DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_dpf_numero_certificado
    ON deposito_plazo_fijo(numero_certificado) WHERE numero_certificado IS NOT NULL;

CREATE TABLE IF NOT EXISTS tasa_dpf (
    id SERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    moneda_id INT NOT NULL REFERENCES moneda(id),
    plazo_min_dias INT NOT NULL,
    plazo_max_dias INT,
    tna NUMERIC(5,2) NOT NULL,
    CONSTRAINT uq_tasa_dpf_banda UNIQUE (cooperativa_id, moneda_id, plazo_min_dias)
);

CREATE TABLE IF NOT EXISTS dpf_cronograma (
    id BIGSERIAL PRIMARY KEY,
    deposito_plazo_fijo_id INT NOT NULL REFERENCES deposito_plazo_fijo(id) ON DELETE CASCADE,
    numero INT NOT NULL,
    fecha_pago DATE NOT NULL,
    dias INT NOT NULL,
    interes_bruto NUMERIC(12,2) NOT NULL,
    retencion_rciva NUMERIC(12,2) NOT NULL,
    interes_neto NUMERIC(12,2) NOT NULL,
    estado VARCHAR(10) NOT NULL DEFAULT 'PENDIENTE',
    CONSTRAINT uq_dpf_cronograma_numero UNIQUE (deposito_plazo_fijo_id, numero)
);

ALTER TABLE cooperativa
    ADD COLUMN IF NOT EXISTS dpf_permite_cancelacion_anticipada BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS dpf_tasa_penalizacion NUMERIC(5,2) NOT NULL DEFAULT 1.00;

ALTER TABLE liquidacion
    ADD COLUMN IF NOT EXISTS tipo VARCHAR(20),
    ADD COLUMN IF NOT EXISTS retencion_rciva NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id),
    ADD COLUMN IF NOT EXISTS cuenta_abono_id INT REFERENCES cuenta_ahorro(id),
    ADD COLUMN IF NOT EXISTS dpf_renovado_id INT REFERENCES deposito_plazo_fijo(id);

INSERT INTO tasa_dpf (cooperativa_id, moneda_id, plazo_min_dias, plazo_max_dias, tna)
SELECT c.id, m.id, v.plazo_min, v.plazo_max, v.tna
FROM cooperativa c
CROSS JOIN moneda m
CROSS JOIN (VALUES
    ('BOB',30,59,2.00), ('BOB',60,89,2.50), ('BOB',90,179,3.00),
    ('BOB',180,359,4.00), ('BOB',360,719,5.50), ('BOB',720,1079,6.00),
    ('BOB',1080,NULL,6.50), ('USD',30,179,0.50), ('USD',180,359,1.00),
    ('USD',360,719,1.50), ('USD',720,NULL,2.00)
) AS v(codigo, plazo_min, plazo_max, tna)
WHERE m.codigo_iso = v.codigo
ON CONFLICT (cooperativa_id, moneda_id, plazo_min_dias) DO NOTHING;

COMMIT;
