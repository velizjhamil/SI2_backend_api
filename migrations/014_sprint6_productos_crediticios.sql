BEGIN;

CREATE TABLE IF NOT EXISTS producto_credito (
    id SERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    codigo VARCHAR(20) NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    descripcion TEXT,
    moneda_id INT NOT NULL REFERENCES moneda(id),
    monto_min NUMERIC(14,2) NOT NULL,
    monto_max NUMERIC(14,2) NOT NULL,
    plazo_min_meses INT NOT NULL,
    plazo_max_meses INT NOT NULL,
    tasa_interes_anual NUMERIC(5,2) NOT NULL,
    tipo_amortizacion VARCHAR(10) NOT NULL,
    dias_gracia_mora INT NOT NULL DEFAULT 0,
    tasa_mora_anual NUMERIC(5,2) NOT NULL DEFAULT 0,
    relacion_cuota_ingreso_max NUMERIC(5,2) NOT NULL DEFAULT 40.00,
    requiere_garantia BOOLEAN NOT NULL DEFAULT false,
    estado VARCHAR(10) NOT NULL DEFAULT 'ACTIVO',
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_producto_credito_cooperativa_codigo UNIQUE (cooperativa_id, codigo),
    CONSTRAINT chk_producto_credito_amortizacion CHECK (tipo_amortizacion IN ('FRANCES','ALEMAN')),
    CONSTRAINT chk_producto_credito_estado CHECK (estado IN ('ACTIVO','INACTIVO')),
    CONSTRAINT chk_producto_credito_montos CHECK (monto_min > 0 AND monto_min <= monto_max),
    CONSTRAINT chk_producto_credito_plazos CHECK (plazo_min_meses > 0 AND plazo_min_meses <= plazo_max_meses),
    CONSTRAINT chk_producto_credito_tasa CHECK (tasa_interes_anual >= 0 AND tasa_interes_anual <= 100),
    CONSTRAINT chk_producto_credito_tasa_mora CHECK (tasa_mora_anual >= 0 AND tasa_mora_anual <= 100),
    CONSTRAINT chk_producto_credito_ratio CHECK (relacion_cuota_ingreso_max > 0 AND relacion_cuota_ingreso_max <= 100),
    CONSTRAINT chk_producto_credito_gracia CHECK (dias_gracia_mora >= 0)
);

ALTER TABLE solicitud_credito ADD COLUMN IF NOT EXISTS producto_credito_id INT;
DO $$ BEGIN
    ALTER TABLE solicitud_credito
        ADD CONSTRAINT fk_solicitud_credito_producto
        FOREIGN KEY (producto_credito_id) REFERENCES producto_credito(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

INSERT INTO producto_credito
    (cooperativa_id, codigo, nombre, descripcion, moneda_id, monto_min, monto_max,
     plazo_min_meses, plazo_max_meses, tasa_interes_anual, tipo_amortizacion,
     dias_gracia_mora, tasa_mora_anual, relacion_cuota_ingreso_max, requiere_garantia)
SELECT c.id, v.codigo, v.nombre, v.descripcion, m.id, v.monto_min, v.monto_max,
       v.plazo_min, v.plazo_max, v.tasa, v.amortizacion,
       v.gracia, v.mora, v.ratio, v.garantia
FROM cooperativa c
CROSS JOIN (VALUES
    ('CONS-BOB', 'Crédito de Consumo', 'Financiamiento de consumo en bolivianos', 'BOB', 1000.00, 50000.00, 6, 48, 18.00, 'FRANCES', 3, 3.00, 40.00, false),
    ('MICRO-BOB', 'Microcrédito Productivo', 'Capital para actividades productivas en bolivianos', 'BOB', 2000.00, 100000.00, 6, 60, 15.00, 'FRANCES', 5, 3.00, 45.00, true),
    ('VIV-USD', 'Crédito de Vivienda', 'Financiamiento de vivienda en dólares estadounidenses', 'USD', 10000.00, 150000.00, 60, 240, 8.50, 'ALEMAN', 5, 2.00, 35.00, true)
) AS v(codigo, nombre, descripcion, moneda, monto_min, monto_max, plazo_min,
       plazo_max, tasa, amortizacion, gracia, mora, ratio, garantia)
JOIN moneda m ON m.codigo_iso = v.moneda
ON CONFLICT (cooperativa_id, codigo) DO NOTHING;

COMMIT;
