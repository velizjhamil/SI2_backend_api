-- ASFI credit types: adds EMPRESARIAL, PYME and VIVIENDA_SOCIAL (vivienda de interés social).
-- Idempotent: existing rows keep their type; init_db() mirrors this change.
BEGIN;

ALTER TABLE producto_credito DROP CONSTRAINT IF EXISTS chk_producto_credito_tipo_asfi;
ALTER TABLE producto_credito
    ADD CONSTRAINT chk_producto_credito_tipo_asfi CHECK (
        tipo_credito_asfi IS NULL OR tipo_credito_asfi IN (
            'EMPRESARIAL', 'PYME', 'MICROCREDITO', 'MICROCREDITO_AGROPECUARIO', 'CONSUMO',
            'VIVIENDA_HIPOTECARIA', 'VIVIENDA_SIN_GARANTIA', 'VIVIENDA_SOCIAL'
        )
    );

COMMIT;
