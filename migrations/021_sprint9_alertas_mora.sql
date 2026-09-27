-- CU-W28: idempotent additions for arrears monitoring and collection management.
CREATE TABLE IF NOT EXISTS alerta_credito (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    credito_id INT NOT NULL REFERENCES credito(id),
    tabla_amortizacion_id INT NULL REFERENCES tabla_amortizacion(id),
    tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('CUOTA_POR_VENCER','CUOTA_VENCIDA','MORA','RIESGO_ALTO')),
    severidad VARCHAR(12) NOT NULL CHECK (severidad IN ('INFO','ADVERTENCIA','CRITICA')),
    mensaje TEXT NOT NULL,
    datos JSONB NOT NULL DEFAULT '{}',
    estado VARCHAR(12) NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA','ATENDIDA','DESCARTADA','RESUELTA')),
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_cierre TIMESTAMPTZ NULL,
    usuario_cierre_id BIGINT NULL REFERENCES usuario(id),
    comentario_cierre TEXT NULL,
    gestion_id INT NULL REFERENCES historial_gestion_de_cobranza(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerta_credito_activa_clave
    ON alerta_credito (credito_id, tipo, (COALESCE(tabla_amortizacion_id, 0)))
    WHERE estado = 'ACTIVA';
CREATE INDEX IF NOT EXISTS ix_alerta_credito_cooperativa_estado
    ON alerta_credito (cooperativa_id, estado, fecha_creacion DESC);

ALTER TABLE historial_gestion_de_cobranza
    ADD COLUMN IF NOT EXISTS cooperativa_id BIGINT REFERENCES cooperativa(id);
ALTER TABLE historial_gestion_de_cobranza
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id);
ALTER TABLE historial_gestion_de_cobranza
    ADD COLUMN IF NOT EXISTS fecha TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE historial_gestion_de_cobranza
    ADD COLUMN IF NOT EXISTS alerta_id BIGINT NULL;
