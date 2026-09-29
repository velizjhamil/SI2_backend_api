-- CU-W34 product classification and tenant-owned immutable report snapshots.
ALTER TABLE producto_credito
    ADD COLUMN IF NOT EXISTS tipo_credito_asfi VARCHAR(30),
    ADD COLUMN IF NOT EXISTS sector_productivo BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_producto_credito_tipo_asfi'
          AND conrelid = 'producto_credito'::regclass
    ) THEN
        ALTER TABLE producto_credito ADD CONSTRAINT chk_producto_credito_tipo_asfi
            CHECK (tipo_credito_asfi IS NULL OR tipo_credito_asfi IN (
                'MICROCREDITO', 'MICROCREDITO_AGROPECUARIO', 'CONSUMO',
                'VIVIENDA_HIPOTECARIA', 'VIVIENDA_SIN_GARANTIA'
            ));
    END IF;
END $$;

UPDATE producto_credito
SET tipo_credito_asfi = CASE
        WHEN codigo LIKE 'CONS-%' THEN 'CONSUMO'
        WHEN codigo LIKE 'VIV-%' THEN 'VIVIENDA_HIPOTECARIA'
        WHEN codigo LIKE 'MICRO-%' THEN 'MICROCREDITO'
        ELSE tipo_credito_asfi
    END,
    sector_productivo = CASE WHEN codigo LIKE 'MICRO-%' THEN true ELSE sector_productivo END
WHERE tipo_credito_asfi IS NULL OR (codigo LIKE 'MICRO-%' AND sector_productivo = false);

ALTER TABLE reporte
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id),
    ADD COLUMN IF NOT EXISTS periodo DATE,
    ADD COLUMN IF NOT EXISTS estado VARCHAR(12) NOT NULL DEFAULT 'GENERADO',
    ADD COLUMN IF NOT EXISTS contenido JSONB,
    ADD COLUMN IF NOT EXISTS resumen JSONB,
    ADD COLUMN IF NOT EXISTS hash_contenido VARCHAR(64);

-- Legacy/platform reports without a tenant remain NULL and tenant-invisible.
UPDATE reporte r
SET cooperativa_id = u.cooperativa_id
FROM usuario u
WHERE r.usuario_id = u.id AND r.cooperativa_id IS NULL AND u.cooperativa_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reporte_tenant_periodo
    ON reporte(cooperativa_id, periodo DESC, tipo);
