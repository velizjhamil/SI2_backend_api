BEGIN;

CREATE TABLE IF NOT EXISTS evaluacion_crediticia (
    id BIGSERIAL PRIMARY KEY,
    solicitud_credito_id INT NOT NULL REFERENCES solicitud_credito(id),
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    version_modelo VARCHAR(20) NOT NULL,
    score INT NOT NULL CHECK (score BETWEEN 0 AND 1000),
    dictamen VARCHAR(20) NOT NULL CHECK (dictamen IN ('APROBADO', 'RECHAZADO', 'REVISION_MANUAL')),
    factores JSONB NOT NULL,
    knockouts JSONB NOT NULL,
    explicacion TEXT NOT NULL,
    cuota_estimada NUMERIC(14,2),
    relacion_cuota_ingreso NUMERIC(7,2),
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolucion VARCHAR(20) NULL CHECK (resolucion IN ('APROBADO', 'RECHAZADO')),
    resolucion_justificacion TEXT NULL,
    resolucion_usuario_id BIGINT NULL REFERENCES usuario(id),
    resolucion_fecha TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluacion_crediticia_solicitud_fecha
    ON evaluacion_crediticia (solicitud_credito_id, fecha DESC);

COMMIT;
