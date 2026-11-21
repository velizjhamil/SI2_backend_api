-- Feedback CRM: socios rate a service (1-5, optional NPS 0-10 and comment);
-- staff read the answers and their statistics. Idempotent: init_db() also
-- creates this table through create_all().
BEGIN;

CREATE TABLE IF NOT EXISTS encuesta_satisfaccion (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    socio_id BIGINT NOT NULL REFERENCES socio(id),
    servicio VARCHAR(20) NOT NULL,
    calificacion INT NOT NULL,
    recomendaria INT NULL,
    comentario TEXT NULL,
    canal VARCHAR(10) NOT NULL,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_encuesta_servicio CHECK (servicio IN ('CREDITO','AHORRO','CAJA','ATENCION','APP_MOVIL','OTRO')),
    CONSTRAINT chk_encuesta_calificacion CHECK (calificacion BETWEEN 1 AND 5),
    CONSTRAINT chk_encuesta_recomendaria CHECK (recomendaria IS NULL OR recomendaria BETWEEN 0 AND 10),
    CONSTRAINT chk_encuesta_canal CHECK (canal IN ('WEB','MOVIL'))
);
CREATE INDEX IF NOT EXISTS ix_encuesta_cooperativa_fecha
    ON encuesta_satisfaccion (cooperativa_id, fecha_creacion);

COMMIT;
