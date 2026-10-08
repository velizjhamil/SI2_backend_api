CREATE TABLE IF NOT EXISTS comprobante_secuencia (
    cooperativa_id BIGINT NOT NULL,
    tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('TRANSFERENCIA', 'PAGO_CUOTA')),
    anio INTEGER NOT NULL,
    ultimo BIGINT NOT NULL DEFAULT 0 CHECK (ultimo >= 0),
    PRIMARY KEY (cooperativa_id, tipo, anio)
);

CREATE TABLE IF NOT EXISTS comprobante_transaccion (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    numero VARCHAR(32) NOT NULL,
    tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('TRANSFERENCIA', 'PAGO_CUOTA')),
    canal VARCHAR(10) NOT NULL DEFAULT 'MOVIL' CHECK (canal = 'MOVIL'),
    socio_id BIGINT NOT NULL REFERENCES socio(id),
    usuario_id BIGINT NOT NULL REFERENCES usuario(id),
    monto NUMERIC(14, 2) NOT NULL,
    moneda VARCHAR(10) NOT NULL,
    cuenta_origen_id BIGINT NOT NULL,
    cuenta_destino_id BIGINT,
    credito_id INTEGER,
    numero_cuota INTEGER,
    transaccion_salida_id BIGINT,
    transaccion_entrada_id BIGINT,
    pago_cuota_id INTEGER,
    glosa TEXT,
    emitido_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    codigo_verificacion VARCHAR(24) NOT NULL UNIQUE,
    firma VARCHAR(64) NOT NULL,
    CONSTRAINT uq_comprobante_transaccion_numero UNIQUE (cooperativa_id, numero)
);

CREATE INDEX IF NOT EXISTS ix_comprobante_transaccion_socio_emitido
    ON comprobante_transaccion (socio_id, emitido_en DESC, id DESC);
