-- Sprint 1: apertura de cuentas de ahorro y certificados de aportacion.
BEGIN;

ALTER TABLE socio
    ADD COLUMN IF NOT EXISTS usuario_id BIGINT REFERENCES usuario(id);

CREATE INDEX IF NOT EXISTS idx_socio_usuario ON socio(usuario_id);
CREATE INDEX IF NOT EXISTS idx_cuenta_socio_estado ON cuenta_ahorro(socio_id, estado);
CREATE INDEX IF NOT EXISTS idx_certificado_socio ON certificado_aportacion(socio_id);

COMMIT;