-- CU-W23 part 2: re-credit offers and advisory synthetic default predictions.
CREATE TABLE IF NOT EXISTS prediccion_de_morosidad (
    id BIGSERIAL PRIMARY KEY,
    credito_id INT NOT NULL REFERENCES credito(id) ON DELETE CASCADE,
    probabilidad_mora NUMERIC(5,4) NOT NULL,
    nivel_riesgo VARCHAR(10) NOT NULL,
    version_modelo VARCHAR(20),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE prediccion_de_morosidad ADD COLUMN IF NOT EXISTS probabilidad_mora NUMERIC(5,4);
ALTER TABLE prediccion_de_morosidad ADD COLUMN IF NOT EXISTS nivel_riesgo VARCHAR(10);
-- Preserve/backfill legacy spellings when the table predates this migration.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'prediccion_de_morosidad' AND column_name = 'probabilidad_de_incumplimiento') THEN
        EXECUTE 'UPDATE prediccion_de_morosidad SET probabilidad_mora = probabilidad_de_incumplimiento WHERE probabilidad_mora IS NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'prediccion_de_morosidad' AND column_name = 'nivel_de_riesgo') THEN
        EXECUTE 'UPDATE prediccion_de_morosidad SET nivel_riesgo = nivel_de_riesgo WHERE nivel_riesgo IS NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'prediccion_de_morosidad' AND column_name = 'probabilidad_de_incumplimiento') THEN
        EXECUTE 'ALTER TABLE prediccion_de_morosidad ALTER COLUMN probabilidad_de_incumplimiento DROP NOT NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'prediccion_de_morosidad' AND column_name = 'nivel_de_riesgo') THEN
        EXECUTE 'ALTER TABLE prediccion_de_morosidad ALTER COLUMN nivel_de_riesgo DROP NOT NULL';
    END IF;
END $$;
ALTER TABLE prediccion_de_morosidad ADD COLUMN IF NOT EXISTS version_modelo VARCHAR(20);
ALTER TABLE prediccion_de_morosidad ADD COLUMN IF NOT EXISTS fecha TIMESTAMPTZ NOT NULL DEFAULT now();
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM prediccion_de_morosidad GROUP BY credito_id HAVING count(*) > 1) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_prediccion_morosidad_credito ON prediccion_de_morosidad(credito_id);
    END IF;
END $$;
ALTER TABLE evaluacion_crediticia ADD COLUMN IF NOT EXISTS probabilidad_mora NUMERIC(5,4);
ALTER TABLE evaluacion_crediticia ADD COLUMN IF NOT EXISTS nivel_riesgo VARCHAR(10);
ALTER TABLE evaluacion_crediticia ADD COLUMN IF NOT EXISTS version_modelo_mora VARCHAR(20);
CREATE TABLE IF NOT EXISTS oferta_recredito (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    socio_id INT NOT NULL REFERENCES socio(id),
    credito_origen_id INT NOT NULL REFERENCES credito(id),
    producto_credito_id INT NOT NULL REFERENCES producto_credito(id),
    monto_sugerido NUMERIC(14,2) NOT NULL,
    plazo_meses INT NOT NULL,
    tasa_interes NUMERIC(5,2) NOT NULL,
    cuota_estimada NUMERIC(14,2) NOT NULL,
    probabilidad_mora NUMERIC(5,4),
    nivel_riesgo VARCHAR(10),
    motivos JSONB NOT NULL,
    estado VARCHAR(12) NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','ACEPTADA','DESCARTADA','EXPIRADA')),
    fecha_generacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_vencimiento DATE NOT NULL,
    solicitud_generada_id INT REFERENCES solicitud_credito(id),
    usuario_id BIGINT REFERENCES usuario(id),
    motivo_descarte TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_oferta_recredito_vigente_socio ON oferta_recredito(socio_id) WHERE estado = 'VIGENTE';
