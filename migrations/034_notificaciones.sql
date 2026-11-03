-- Gestionar notificaciones: staff (ADMINISTRADOR/CAJERO) sends payment reminders,
-- promotions or notices to socios; each socio reads them in their web inbox.
-- Idempotent: init_db() also creates these tables through create_all().
BEGIN;

CREATE TABLE IF NOT EXISTS notificacion (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    tipo VARCHAR(20) NOT NULL,
    titulo VARCHAR(120) NOT NULL,
    mensaje TEXT NOT NULL,
    destino VARCHAR(20) NOT NULL,
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    total_destinatarios INT NOT NULL DEFAULT 0,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_notificacion_tipo CHECK (tipo IN ('RECORDATORIO_PAGO','PROMOCION','AVISO')),
    CONSTRAINT chk_notificacion_destino CHECK (destino IN ('SOCIO','TODOS','CUOTAS_POR_VENCER','EN_MORA'))
);
CREATE INDEX IF NOT EXISTS ix_notificacion_cooperativa_fecha
    ON notificacion (cooperativa_id, fecha_creacion);

CREATE TABLE IF NOT EXISTS notificacion_destinatario (
    id BIGSERIAL PRIMARY KEY,
    notificacion_id BIGINT NOT NULL REFERENCES notificacion(id) ON DELETE CASCADE,
    socio_id BIGINT NOT NULL REFERENCES socio(id),
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    mensaje TEXT NOT NULL,
    leida_en TIMESTAMPTZ NULL,
    CONSTRAINT uq_notificacion_destinatario_socio UNIQUE (notificacion_id, socio_id)
);
CREATE INDEX IF NOT EXISTS ix_notificacion_destinatario_bandeja
    ON notificacion_destinatario (usuario_id, leida_en);

COMMIT;
