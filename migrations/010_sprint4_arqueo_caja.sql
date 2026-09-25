-- Sprint 4: arqueo y conciliación de caja.
BEGIN;

ALTER TABLE caja
    ADD COLUMN IF NOT EXISTS umbral_diferencia_arqueo NUMERIC(12, 2)
    NOT NULL DEFAULT 50.00;

CREATE TABLE IF NOT EXISTS arqueo_caja (
    id BIGSERIAL PRIMARY KEY,
    control_caja_id INT NOT NULL REFERENCES control_caja(id),
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    supervisor_id BIGINT NULL REFERENCES usuario(id),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_autorizacion TIMESTAMPTZ NULL,
    cierre BOOLEAN NOT NULL DEFAULT false,
    requiere_supervisor BOOLEAN NOT NULL,
    observacion TEXT NULL
);

CREATE TABLE IF NOT EXISTS arqueo_caja_moneda (
    id BIGSERIAL PRIMARY KEY,
    arqueo_id BIGINT NOT NULL REFERENCES arqueo_caja(id) ON DELETE CASCADE,
    moneda_id INT NOT NULL REFERENCES moneda(id),
    saldo_teorico NUMERIC(12, 2) NOT NULL,
    total_contado NUMERIC(12, 2) NOT NULL,
    diferencia NUMERIC(12, 2) NOT NULL,
    resultado VARCHAR(10) NOT NULL
        CHECK (resultado IN ('CUADRADO', 'SOBRANTE', 'FALTANTE'))
);

CREATE TABLE IF NOT EXISTS arqueo_caja_detalle (
    id BIGSERIAL PRIMARY KEY,
    arqueo_moneda_id BIGINT NOT NULL
        REFERENCES arqueo_caja_moneda(id) ON DELETE CASCADE,
    tipo VARCHAR(10) NOT NULL CHECK (tipo IN ('BILLETE', 'MONEDA')),
    denominacion NUMERIC(12, 2) NOT NULL,
    cantidad INT NOT NULL CHECK (cantidad >= 0),
    subtotal NUMERIC(12, 2) NOT NULL
);

COMMIT;
