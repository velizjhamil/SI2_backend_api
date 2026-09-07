-- Sprint 1: aislamiento real de socios y bitácora por cooperativa.
BEGIN;

ALTER TABLE socio
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id);

ALTER TABLE bitacora
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id);

CREATE INDEX IF NOT EXISTS idx_socio_cooperativa ON socio(cooperativa_id);
CREATE INDEX IF NOT EXISTS idx_bitacora_cooperativa ON bitacora(cooperativa_id, fecha_hora DESC);

-- Los registros históricos sin tenant quedan NULL deliberadamente: solo
-- SUPERADMIN puede verlos hasta que se asignen a una cooperativa.
COMMIT;