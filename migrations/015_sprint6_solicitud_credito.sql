BEGIN;

ALTER TABLE solicitud_credito
    ADD COLUMN IF NOT EXISTS numero_solicitud VARCHAR(20),
    ADD COLUMN IF NOT EXISTS destino VARCHAR(20),
    ADD COLUMN IF NOT EXISTS destino_detalle TEXT,
    ADD COLUMN IF NOT EXISTS observaciones TEXT,
    ADD COLUMN IF NOT EXISTS motivo_anulacion TEXT,
    ADD COLUMN IF NOT EXISTS fecha_solicitud TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS moneda_id INT,
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'solicitud_credito'::regclass
          AND conname = 'fk_solicitud_credito_cooperativa'
    ) THEN
        ALTER TABLE solicitud_credito
            ADD CONSTRAINT fk_solicitud_credito_cooperativa
            FOREIGN KEY (cooperativa_id) REFERENCES cooperativa(id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'solicitud_credito'::regclass
          AND conname = 'fk_solicitud_credito_moneda'
    ) THEN
        ALTER TABLE solicitud_credito
            ADD CONSTRAINT fk_solicitud_credito_moneda
            FOREIGN KEY (moneda_id) REFERENCES moneda(id);
    END IF;
END $$;

ALTER TABLE evaluacion_campo
    ADD COLUMN IF NOT EXISTS socio_id INT,
    ADD COLUMN IF NOT EXISTS cuota_deudas_mensual NUMERIC(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS actividad_economica VARCHAR(150),
    ADD COLUMN IF NOT EXISTS fuente_ingresos VARCHAR(20),
    ADD COLUMN IF NOT EXISTS antiguedad_laboral_meses INT,
    ADD COLUMN IF NOT EXISTS calificacion_asfi VARCHAR(1),
    ADD COLUMN IF NOT EXISTS observaciones TEXT;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'evaluacion_campo'::regclass
          AND conname = 'fk_evaluacion_campo_socio'
    ) THEN
        ALTER TABLE evaluacion_campo
            ADD CONSTRAINT fk_evaluacion_campo_socio
            FOREIGN KEY (socio_id) REFERENCES socio(id);
    END IF;
END $$;

UPDATE solicitud_credito sc
SET cooperativa_id = s.cooperativa_id
FROM socio s
WHERE s.id = sc.socio_id
  AND sc.cooperativa_id IS NULL;

UPDATE solicitud_credito sc
SET moneda_id = p.moneda_id
FROM producto_credito p
WHERE p.id = sc.producto_credito_id
  AND sc.moneda_id IS NULL;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1
        FROM solicitud_credito
        WHERE evaluacion_campo_id IS NOT NULL
        GROUP BY evaluacion_campo_id
        HAVING COUNT(DISTINCT socio_id) > 1
    ) THEN
        RAISE EXCEPTION 'No se puede asociar una evaluación de campo a más de un socio';
    END IF;
END $$;

UPDATE evaluacion_campo e
SET socio_id = (
    SELECT sc.socio_id
    FROM solicitud_credito sc
    WHERE sc.evaluacion_campo_id = e.id
    ORDER BY sc.id
    LIMIT 1
)
WHERE e.socio_id IS NULL
  AND EXISTS (SELECT 1 FROM solicitud_credito sc WHERE sc.evaluacion_campo_id = e.id);

WITH existing_numbers AS (
    SELECT COALESCE(cooperativa_id, 0) AS tenant_id,
           MAX(SUBSTRING(numero_solicitud FROM '^SOL-([0-9]+)$')::BIGINT) AS last_number
    FROM solicitud_credito
    WHERE numero_solicitud ~ '^SOL-[0-9]+$'
    GROUP BY COALESCE(cooperativa_id, 0)
), missing_numbers AS (
    SELECT sc.id,
           COALESCE(sc.cooperativa_id, 0) AS tenant_id,
           COALESCE(en.last_number, 0) + ROW_NUMBER() OVER (
               PARTITION BY COALESCE(sc.cooperativa_id, 0) ORDER BY sc.id
           ) AS number_value
    FROM solicitud_credito sc
    LEFT JOIN existing_numbers en ON en.tenant_id = COALESCE(sc.cooperativa_id, 0)
    WHERE sc.numero_solicitud IS NULL
)
UPDATE solicitud_credito sc
SET numero_solicitud = 'SOL-' || LPAD(mn.number_value::TEXT, 6, '0')
FROM missing_numbers mn
WHERE mn.id = sc.id;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'solicitud_credito'::regclass
          AND conname = 'uq_solicitud_credito_coop_numero'
    ) THEN
        ALTER TABLE solicitud_credito
            ADD CONSTRAINT uq_solicitud_credito_coop_numero
            UNIQUE (cooperativa_id, numero_solicitud);
    END IF;
END $$;

INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
SELECT COALESCE(cooperativa_id, 0)::INT,
       'SOLICITUD_CREDITO',
       COALESCE(MAX(SUBSTRING(numero_solicitud FROM '^SOL-([0-9]+)$')::INT), 0) + 1
FROM solicitud_credito
WHERE numero_solicitud ~ '^SOL-[0-9]+$'
GROUP BY COALESCE(cooperativa_id, 0)::INT
ON CONFLICT (cooperativa_id, tipo)
DO UPDATE SET siguiente = GREATEST(
    secuencia_documento.siguiente,
    EXCLUDED.siguiente
);

DO $$ BEGIN
    IF EXISTS (
        SELECT socio_id
        FROM solicitud_credito
        WHERE estado IN ('PENDIENTE', 'OBSERVADA', 'EN_EVALUACION')
        GROUP BY socio_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'No se puede crear índice: hay socios con más de una solicitud en curso';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_solicitud_credito_un_socio_en_curso
    ON solicitud_credito (socio_id)
    WHERE estado IN ('PENDIENTE', 'OBSERVADA', 'EN_EVALUACION');

COMMIT;
