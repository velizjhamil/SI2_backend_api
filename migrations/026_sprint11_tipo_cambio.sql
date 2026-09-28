-- CU-W32: cooperative exchange rates used to value foreign-currency statements.
CREATE TABLE IF NOT EXISTS tipo_cambio (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id) ON DELETE CASCADE,
    moneda_id SMALLINT NOT NULL REFERENCES moneda(id),
    fecha DATE NOT NULL,
    valor NUMERIC(12,5) NOT NULL,
    fuente VARCHAR(30) NOT NULL DEFAULT 'BCB',
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    fecha_registro TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_tipo_cambio_coop_moneda_fecha UNIQUE (cooperativa_id, moneda_id, fecha),
    CONSTRAINT chk_tipo_cambio_valor_positivo CHECK (valor > 0)
);

CREATE INDEX IF NOT EXISTS idx_tipo_cambio_lookup
    ON tipo_cambio(cooperativa_id, moneda_id, fecha DESC);
