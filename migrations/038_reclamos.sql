-- Customer claims (Punto de Reclamo, Ley 393): first instance at the cooperative,
-- escalation to ASFI (Defensoría del Consumidor Financiero). Idempotent:
-- init_db() also creates this table through create_all().
BEGIN;

CREATE TABLE IF NOT EXISTS reclamo (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    socio_id BIGINT NOT NULL REFERENCES socio(id),
    credito_id INT NULL REFERENCES credito(id),
    numero_reclamo VARCHAR(20) NOT NULL,
    tipo VARCHAR(20) NOT NULL,
    descripcion TEXT NOT NULL,
    canal VARCHAR(10) NOT NULL,
    estado VARCHAR(15) NOT NULL DEFAULT 'REGISTRADO',
    fecha_registro TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_limite_respuesta DATE NOT NULL,
    usuario_atencion_id BIGINT NULL REFERENCES usuario(id),
    respuesta TEXT NULL,
    procedente BOOLEAN NULL,
    fecha_respuesta TIMESTAMPTZ NULL,
    conforme BOOLEAN NULL,
    motivo_disconformidad TEXT NULL,
    fecha_cierre TIMESTAMPTZ NULL,
    fecha_escalamiento TIMESTAMPTZ NULL,
    CONSTRAINT uq_reclamo_cooperativa_numero UNIQUE (cooperativa_id, numero_reclamo),
    CONSTRAINT chk_reclamo_tipo CHECK (tipo IN ('CREDITO','AHORRO','CAJA','COBRO_INDEBIDO','ATENCION','OTRO')),
    CONSTRAINT chk_reclamo_canal CHECK (canal IN ('MOVIL','VENTANILLA')),
    CONSTRAINT chk_reclamo_estado CHECK (estado IN ('REGISTRADO','EN_ATENCION','RESPONDIDO','CERRADO','ESCALADO_ASFI'))
);
CREATE INDEX IF NOT EXISTS ix_reclamo_cooperativa_estado ON reclamo (cooperativa_id, estado);

COMMIT;
