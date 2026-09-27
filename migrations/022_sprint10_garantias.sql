DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'producto_credito'
          AND column_name = 'cobertura_minima_garantia'
    ) THEN
        ALTER TABLE producto_credito
            ADD COLUMN cobertura_minima_garantia NUMERIC(6,2) NOT NULL DEFAULT 100.00;
        UPDATE producto_credito
        SET cobertura_minima_garantia = CASE codigo
            WHEN 'MICRO-BOB' THEN 100.00
            WHEN 'VIV-USD' THEN 125.00
            WHEN 'CONS-BOB' THEN 0.00
            ELSE 100.00
        END
        WHERE codigo IN ('MICRO-BOB', 'VIV-USD', 'CONS-BOB');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS garantia (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    solicitud_credito_id INT NOT NULL REFERENCES solicitud_credito(id),
    tipo VARCHAR(12) NOT NULL CHECK (tipo IN ('HIPOTECARIA','PRENDARIA','PERSONAL')),
    descripcion TEXT NOT NULL,
    moneda_id INT NOT NULL REFERENCES moneda(id),
    valor_comercial NUMERIC(14,2),
    valor_realizable NUMERIC(14,2) NOT NULL,
    documento_referencia VARCHAR(120),
    avalista_nombre VARCHAR(150),
    avalista_ci VARCHAR(20),
    avalista_ingreso_mensual NUMERIC(14,2),
    avalista_relacion VARCHAR(60),
    avalista_telefono VARCHAR(30),
    socio_avalista_id INT REFERENCES socio(id),
    estado VARCHAR(12) NOT NULL DEFAULT 'REGISTRADA'
        CHECK (estado IN ('REGISTRADA','VERIFICADA','RECHAZADA','LIBERADA')),
    observacion_verificacion TEXT,
    usuario_registro_id BIGINT NOT NULL REFERENCES usuario(id),
    usuario_verificacion_id BIGINT REFERENCES usuario(id),
    fecha_registro TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_verificacion TIMESTAMPTZ,
    fecha_liberacion TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_garantia_coop_solicitud
    ON garantia (cooperativa_id, solicitud_credito_id);
