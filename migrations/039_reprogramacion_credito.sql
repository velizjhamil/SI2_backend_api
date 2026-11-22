-- Credit rescheduling (reprogramación ASFI): requests with their outcome, and a
-- per-credit counter. Rescheduled installments keep their rows with
-- estado_pago = 'REPROGRAMADA'. Idempotent: init_db() mirrors this change.
BEGIN;

ALTER TABLE credito ADD COLUMN IF NOT EXISTS numero_reprogramaciones INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS reprogramacion_credito (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    credito_id INT NOT NULL REFERENCES credito(id),
    estado VARCHAR(12) NOT NULL DEFAULT 'SOLICITADA',
    canal VARCHAR(10) NOT NULL,
    motivo TEXT NOT NULL,
    nuevo_plazo_meses INT NOT NULL,
    saldo_capital NUMERIC(14, 2) NULL,
    interes_diferido NUMERIC(14, 2) NULL,
    mora_condonada NUMERIC(14, 2) NULL,
    plazo_anterior INT NULL,
    cuotas_reprogramadas INT NULL,
    motivo_rechazo TEXT NULL,
    usuario_solicitud_id BIGINT NULL REFERENCES usuario(id),
    usuario_resolucion_id BIGINT NULL REFERENCES usuario(id),
    fecha_solicitud TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_resolucion TIMESTAMPTZ NULL,
    CONSTRAINT chk_reprogramacion_estado CHECK (estado IN ('SOLICITADA','APROBADA','RECHAZADA')),
    CONSTRAINT chk_reprogramacion_canal CHECK (canal IN ('MOVIL','VENTANILLA')),
    CONSTRAINT chk_reprogramacion_plazo CHECK (nuevo_plazo_meses > 0)
);
CREATE INDEX IF NOT EXISTS ix_reprogramacion_cooperativa_estado ON reprogramacion_credito (cooperativa_id, estado);
CREATE INDEX IF NOT EXISTS ix_reprogramacion_credito ON reprogramacion_credito (credito_id);

COMMIT;
