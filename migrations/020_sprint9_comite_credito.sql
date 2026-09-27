-- CU-W25: committee-based credit approval. Safe to apply repeatedly.
BEGIN;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'producto_credito'
          AND column_name = 'monto_aprobacion_directa'
    ) THEN
        ALTER TABLE producto_credito ADD COLUMN IF NOT EXISTS monto_aprobacion_directa NUMERIC(14,2) NOT NULL DEFAULT 0.00;
        UPDATE producto_credito pc
        SET monto_aprobacion_directa = CASE pc.codigo
            WHEN 'CONS-BOB' THEN 20000.00
            WHEN 'MICRO-BOB' THEN 30000.00
            WHEN 'VIV-USD' THEN 0.00
        END
        WHERE pc.codigo IN ('CONS-BOB', 'MICRO-BOB', 'VIV-USD');
    END IF;
END $$;

ALTER TABLE solicitud_credito
    ADD COLUMN IF NOT EXISTS ronda_comite INT NOT NULL DEFAULT 0;
ALTER TABLE solicitud_credito
    ADD COLUMN IF NOT EXISTS resultado_comite VARCHAR(12);
ALTER TABLE solicitud_credito
    ADD COLUMN IF NOT EXISTS fecha_resolucion_comite TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_solicitud_credito_id_cooperativa
    ON solicitud_credito(id, cooperativa_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_usuario_id_cooperativa
    ON usuario(id, cooperativa_id);

CREATE TABLE IF NOT EXISTS voto_comite (
    id BIGSERIAL PRIMARY KEY,
    solicitud_credito_id INT NOT NULL,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    ronda INT NOT NULL CHECK (ronda > 0),
    usuario_id BIGINT NOT NULL,
    voto VARCHAR(10) NOT NULL CHECK (voto IN ('APROBAR','RECHAZAR','OBSERVAR')),
    comentario TEXT NOT NULL CHECK (char_length(btrim(comentario)) >= 10),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_voto_comite_solicitud_ronda_usuario UNIQUE (solicitud_credito_id, ronda, usuario_id),
    CONSTRAINT fk_voto_comite_solicitud_cooperativa
        FOREIGN KEY (solicitud_credito_id, cooperativa_id)
        REFERENCES solicitud_credito(id, cooperativa_id),
    CONSTRAINT fk_voto_comite_usuario_cooperativa
        FOREIGN KEY (usuario_id, cooperativa_id)
        REFERENCES usuario(id, cooperativa_id)
);

CREATE INDEX IF NOT EXISTS idx_voto_comite_solicitud_ronda_fecha
    ON voto_comite(solicitud_credito_id, ronda, fecha, id);

-- Include committee-held requests in the existing single-open-request invariant.
DROP INDEX IF EXISTS uq_solicitud_credito_un_socio_en_curso;
DO $$ BEGIN
    IF EXISTS (
        SELECT socio_id FROM solicitud_credito
        WHERE estado IN ('PENDIENTE', 'OBSERVADA', 'EN_EVALUACION', 'EN_COMITE')
        GROUP BY socio_id HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'No se puede crear índice: hay socios con más de una solicitud en curso';
    END IF;
END $$;
CREATE UNIQUE INDEX uq_solicitud_credito_un_socio_en_curso
    ON solicitud_credito(socio_id)
    WHERE estado IN ('PENDIENTE', 'OBSERVADA', 'EN_EVALUACION', 'EN_COMITE');

COMMIT;
