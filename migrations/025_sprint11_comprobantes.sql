-- CU-W30 B1: tenant-scoped accounting vouchers and default account mappings.

ALTER TABLE comprobante_contable
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id),
    ADD COLUMN IF NOT EXISTS numero VARCHAR(20),
    ADD COLUMN IF NOT EXISTS gestion INT,
    ADD COLUMN IF NOT EXISTS fecha_contable DATE,
    ADD COLUMN IF NOT EXISTS moneda_id INT REFERENCES moneda(id),
    ADD COLUMN IF NOT EXISTS estado VARCHAR(10) NOT NULL DEFAULT 'REGISTRADO',
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id),
    ADD COLUMN IF NOT EXISTS origen VARCHAR(12),
    ADD COLUMN IF NOT EXISTS comprobante_reversion_id INT REFERENCES comprobante_contable(id),
    ADD COLUMN IF NOT EXISTS revierte_a_id INT REFERENCES comprobante_contable(id),
    ADD COLUMN IF NOT EXISTS motivo_anulacion TEXT,
    ADD COLUMN IF NOT EXISTS fecha_registro TIMESTAMP NOT NULL DEFAULT now();

-- Legacy transaction rows do not carry cooperative_id. Resolve tenant through
-- their operational FK and use the sole cooperative only for genuinely orphaned
-- legacy headers (never guess in a multi-cooperative database).
UPDATE comprobante_contable cc
SET cooperativa_id = COALESCE(
    (SELECT ca.cooperativa_id
       FROM transaccion t
       JOIN control_caja ctl ON ctl.id = t.control_caja_id
       JOIN caja ca ON ca.id = ctl.caja_id
      WHERE t.id = cc.transaccion_id),
    (SELECT so.cooperativa_id
       FROM transaccion t
       JOIN cuenta_ahorro a ON a.id = t.cuenta_ahorro_id
       JOIN socio so ON so.id = a.socio_id
      WHERE t.id = cc.transaccion_id),
    (SELECT so.cooperativa_id
       FROM transaccion t
       JOIN deposito_plazo_fijo dpf ON dpf.id = t.deposito_plazo_fijo_id
       JOIN socio so ON so.id = dpf.socio_id
      WHERE t.id = cc.transaccion_id),
    (SELECT so.cooperativa_id
       FROM transaccion t
       JOIN liquidacion liq ON liq.id = t.liquidacion_id
       JOIN deposito_plazo_fijo dpf ON dpf.id = liq.deposito_plazo_fijo_id
       JOIN socio so ON so.id = dpf.socio_id
      WHERE t.id = cc.transaccion_id),
    (SELECT so.cooperativa_id
       FROM transaccion t
       JOIN pago_cuota pc ON pc.id = t.pago_cuota_id
       JOIN tabla_amortizacion ta ON ta.id = pc.tabla_amortizacion_id
       JOIN credito cr ON cr.id = ta.credito_id
       JOIN solicitud_credito sc ON sc.id = cr.solicitud_credito_id
       JOIN socio so ON so.id = sc.socio_id
      WHERE t.id = cc.transaccion_id),
    CASE WHEN (SELECT count(*) FROM cooperativa) = 1
         THEN (SELECT id FROM cooperativa ORDER BY id LIMIT 1) END
)
WHERE cc.cooperativa_id IS NULL;

UPDATE comprobante_contable cc
SET fecha_contable = COALESCE(cc.fecha_contable, cc.fecha::date),
    moneda_id = COALESCE(cc.moneda_id, t.moneda_id,
                         (SELECT id FROM moneda WHERE es_moneda_base IS TRUE ORDER BY id LIMIT 1),
                         (SELECT min(id) FROM moneda)),
    origen = COALESCE(cc.origen, CASE WHEN cc.es_automatico IS TRUE THEN 'AUTOMATICO' ELSE 'MANUAL' END),
    estado = COALESCE(cc.estado, 'REGISTRADO')
FROM transaccion t
WHERE t.id = cc.transaccion_id
  AND (cc.fecha_contable IS NULL OR cc.moneda_id IS NULL OR cc.origen IS NULL OR cc.estado IS NULL);

UPDATE comprobante_contable cc
SET fecha_contable = COALESCE(cc.fecha_contable, cc.fecha::date),
    moneda_id = COALESCE(cc.moneda_id,
                         (SELECT id FROM moneda WHERE es_moneda_base IS TRUE ORDER BY id LIMIT 1),
                         (SELECT min(id) FROM moneda)),
    origen = COALESCE(cc.origen, CASE WHEN cc.es_automatico IS TRUE THEN 'AUTOMATICO' ELSE 'MANUAL' END),
    estado = COALESCE(cc.estado, 'REGISTRADO')
WHERE cc.transaccion_id IS NULL
  AND (cc.fecha_contable IS NULL OR cc.moneda_id IS NULL OR cc.origen IS NULL OR cc.estado IS NULL);

UPDATE comprobante_contable SET tipo = upper(tipo) WHERE tipo <> upper(tipo);
UPDATE comprobante_contable SET es_automatico = (origen = 'AUTOMATICO') WHERE es_automatico IS DISTINCT FROM (origen = 'AUTOMATICO');

-- Assign legacy voucher numbers deterministically and only where not yet set.
WITH numbered AS (
    SELECT id,
           CASE tipo WHEN 'INGRESO' THEN 'I' WHEN 'EGRESO' THEN 'E' ELSE 'T' END || '-' ||
           extract(year FROM fecha_contable)::INT::text || '-' ||
           lpad(row_number() OVER (PARTITION BY cooperativa_id, tipo, extract(year FROM fecha_contable)
                                   ORDER BY fecha_contable, id)::text, 6, '0') AS numero
    FROM comprobante_contable
    WHERE numero IS NULL
)
UPDATE comprobante_contable cc SET numero = numbered.numero
FROM numbered WHERE numbered.id = cc.id;

UPDATE comprobante_contable SET gestion = extract(year FROM fecha_contable)::INT WHERE gestion IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM comprobante_contable WHERE cooperativa_id IS NULL OR numero IS NULL
                    OR gestion IS NULL OR fecha_contable IS NULL OR moneda_id IS NULL OR origen IS NULL) THEN
        RAISE EXCEPTION 'CU-W30 migration cannot infer tenant, currency, or number for every legacy voucher';
    END IF;
    IF EXISTS (SELECT 1 FROM comprobante_contable WHERE tipo NOT IN ('INGRESO','EGRESO','TRASPASO')) THEN
        RAISE EXCEPTION 'CU-W30 migration found unsupported legacy voucher type(s)';
    END IF;
END $$;

ALTER TABLE comprobante_contable
    ALTER COLUMN cooperativa_id SET NOT NULL,
    ALTER COLUMN numero SET NOT NULL,
    ALTER COLUMN gestion SET NOT NULL,
    ALTER COLUMN fecha_contable SET NOT NULL,
    ALTER COLUMN moneda_id SET NOT NULL,
    ALTER COLUMN origen SET NOT NULL,
    ALTER COLUMN estado SET DEFAULT 'REGISTRADO',
    ALTER COLUMN fecha_registro SET DEFAULT now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'comprobante_contable'::regclass AND conname = 'chk_comprobante_tipo') THEN
        ALTER TABLE comprobante_contable ADD CONSTRAINT chk_comprobante_tipo CHECK (tipo IN ('INGRESO','EGRESO','TRASPASO'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'comprobante_contable'::regclass AND conname = 'chk_comprobante_estado') THEN
        ALTER TABLE comprobante_contable ADD CONSTRAINT chk_comprobante_estado CHECK (estado IN ('REGISTRADO','ANULADO'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'comprobante_contable'::regclass AND conname = 'chk_comprobante_origen') THEN
        ALTER TABLE comprobante_contable ADD CONSTRAINT chk_comprobante_origen CHECK (origen IN ('MANUAL','AUTOMATICO'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'comprobante_contable'::regclass AND conname = 'uq_comprobante_coop_tipo_gestion_numero') THEN
        ALTER TABLE comprobante_contable ADD CONSTRAINT uq_comprobante_coop_tipo_gestion_numero UNIQUE (cooperativa_id, tipo, gestion, numero);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_comprobante_automatico_transaccion
    ON comprobante_contable(transaccion_id)
    WHERE origen = 'AUTOMATICO' AND revierte_a_id IS NULL AND transaccion_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_comprobante_tenant_fecha
    ON comprobante_contable(cooperativa_id, fecha_contable DESC);

ALTER TABLE detalle_asiento
    ADD COLUMN IF NOT EXISTS glosa TEXT,
    ADD COLUMN IF NOT EXISTS orden INT;

-- Preserve signed accounting effect for invalid two-sided legacy lines by
-- netting their debit and credit; move a lone negative amount to its opposite
-- side. A 0/0 line has no recoverable accounting amount and is rejected below.
UPDATE detalle_asiento
SET debe = CASE
        WHEN debe > 0 AND haber > 0 THEN GREATEST(debe - haber, 0)
        WHEN debe < 0 AND haber = 0 THEN 0
        WHEN debe = 0 AND haber < 0 THEN abs(haber)
        ELSE GREATEST(debe, 0)
    END,
    haber = CASE
        WHEN debe > 0 AND haber > 0 THEN GREATEST(haber - debe, 0)
        WHEN debe < 0 AND haber = 0 THEN abs(debe)
        WHEN debe = 0 AND haber < 0 THEN 0
        ELSE GREATEST(haber, 0)
    END
WHERE debe < 0 OR haber < 0 OR (debe > 0 AND haber > 0);

-- The seeded legacy voucher posted the persisted 302.08 loan-interest component
-- to principal. Correct only that identifiable voucher/component; joining its
-- transaction and payment makes this idempotent without relying on row IDs.
UPDATE detalle_asiento d
SET plan_cuenta_id = interest.id
FROM comprobante_contable cc
JOIN transaccion t ON t.id = cc.transaccion_id
JOIN pago_cuota p ON p.id = t.pago_cuota_id
JOIN plan_cuenta principal ON principal.codigo = '131.05' AND principal.cooperativa_id IS NULL
JOIN plan_cuenta interest ON interest.codigo = '513.05' AND interest.cooperativa_id IS NULL
WHERE d.comprobante_contable_id = cc.id
  AND cc.tipo = 'INGRESO'
  AND cc.es_automatico IS TRUE
  AND cc.glosa = 'Comprobante de ingreso automático por amortización de cuota 1 crédito #1'
  AND d.debe = 0
  AND d.haber = 302.08
  AND p.monto_interes_pagado = 302.08
  AND d.plan_cuenta_id = principal.id;

WITH ordered AS (
    SELECT id, row_number() OVER (PARTITION BY comprobante_contable_id ORDER BY id)::INT AS orden
    FROM detalle_asiento
    WHERE orden IS NULL
)
UPDATE detalle_asiento d SET orden = ordered.orden FROM ordered WHERE ordered.id = d.id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM detalle_asiento WHERE debe = 0 AND haber = 0) THEN
        RAISE EXCEPTION 'CU-W30 migration cannot repair zero-value legacy accounting lines';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'detalle_asiento'::regclass AND conname = 'chk_detalle_asiento_un_solo_lado') THEN
        ALTER TABLE detalle_asiento ADD CONSTRAINT chk_detalle_asiento_un_solo_lado
            CHECK (debe >= 0 AND haber >= 0 AND ((debe = 0) <> (haber = 0)));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS parametro_contable (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    clave VARCHAR(40) NOT NULL,
    plan_cuenta_id INT NOT NULL REFERENCES plan_cuenta(id),
    CONSTRAINT uq_parametro_contable_coop_clave UNIQUE (cooperativa_id, clave)
);

INSERT INTO parametro_contable (cooperativa_id, clave, plan_cuenta_id)
SELECT c.id, defaults.clave, pc.id
FROM cooperativa c
CROSS JOIN (VALUES
    ('CAJA', '111.01'),
    ('AHORRO_VISTA', '212.01'),
    ('AHORRO_PROGRAMADO', '212.01'),
    ('CARTERA_VIGENTE', '131.05'),
    ('INTERES_CARTERA', '513.05'),
    ('INTERES_PENAL', '515.03'),
    ('DPF_30', '213.01'),
    ('DPF_31_60', '213.02'),
    ('DPF_61_90', '213.03'),
    ('DPF_91_180', '213.04'),
    ('DPF_181_360', '213.05'),
    ('DPF_361_720', '213.06'),
    ('DPF_721_1080', '213.07'),
    ('DPF_MAS_1080', '213.08'),
    ('INTERES_DPF', '411.04'),
    ('RETENCION_RCIVA', '242.03'),
    ('CERTIFICADOS_APORTACION', '311.02'),
    ('COMISIONES', '541.99')
) AS defaults(clave, codigo)
JOIN plan_cuenta pc ON pc.codigo = defaults.codigo AND pc.cooperativa_id IS NULL
ON CONFLICT (cooperativa_id, clave) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM cooperativa c
        WHERE (SELECT count(*) FROM parametro_contable p WHERE p.cooperativa_id = c.id) < 18
    ) THEN
        RAISE EXCEPTION 'CU-W30 parameter defaults are incomplete; verify MCEF plan_cuenta seed codes';
    END IF;
END $$;
