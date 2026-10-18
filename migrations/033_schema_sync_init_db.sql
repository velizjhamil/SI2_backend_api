-- Brings an existing database in line with the schema that init_db() expects
-- since commit 9a64172 (CU-M5/CU-M6). Every statement is idempotent: safe to
-- run on a database where migrations 020, 022, 027 and 028 were applied fully,
-- partially, or not at all.
BEGIN;

-- 1) Composite UNIQUE keys targeted by voto_comite foreign keys.
--    Migration 020 created them as plain unique indexes; init_db() looks for
--    table constraints. Promote the existing index to a constraint (keeps the
--    same index, so dependent foreign keys stay valid) or create it if missing.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_solicitud_credito_id_cooperativa'
          AND conrelid = 'solicitud_credito'::regclass
    ) THEN
        IF to_regclass('uq_solicitud_credito_id_cooperativa') IS NOT NULL THEN
            ALTER TABLE solicitud_credito
                ADD CONSTRAINT uq_solicitud_credito_id_cooperativa
                UNIQUE USING INDEX uq_solicitud_credito_id_cooperativa;
        ELSE
            ALTER TABLE solicitud_credito
                ADD CONSTRAINT uq_solicitud_credito_id_cooperativa
                UNIQUE (id, cooperativa_id);
        END IF;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_usuario_id_cooperativa'
          AND conrelid = 'usuario'::regclass
    ) THEN
        IF to_regclass('uq_usuario_id_cooperativa') IS NOT NULL THEN
            ALTER TABLE usuario
                ADD CONSTRAINT uq_usuario_id_cooperativa
                UNIQUE USING INDEX uq_usuario_id_cooperativa;
        ELSE
            ALTER TABLE usuario
                ADD CONSTRAINT uq_usuario_id_cooperativa
                UNIQUE (id, cooperativa_id);
        END IF;
    END IF;
END $$;

-- 2) producto_credito: ASFI classification, guarantees and approval limits.
--    Backfills run only when the column is created here, as in 020/022/027.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'producto_credito'
          AND column_name = 'monto_aprobacion_directa'
    ) THEN
        ALTER TABLE producto_credito
            ADD COLUMN monto_aprobacion_directa NUMERIC(14,2) NOT NULL DEFAULT 0.00;
        UPDATE producto_credito
        SET monto_aprobacion_directa = CASE codigo
            WHEN 'CONS-BOB' THEN 20000.00
            WHEN 'MICRO-BOB' THEN 30000.00
            WHEN 'VIV-USD' THEN 0.00
        END
        WHERE codigo IN ('CONS-BOB', 'MICRO-BOB', 'VIV-USD');
    END IF;

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

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'producto_credito'
          AND column_name = 'tipo_credito_asfi'
    ) THEN
        ALTER TABLE producto_credito
            ADD COLUMN tipo_credito_asfi VARCHAR(30),
            ADD COLUMN IF NOT EXISTS sector_productivo BOOLEAN NOT NULL DEFAULT false;
        UPDATE producto_credito
        SET tipo_credito_asfi = CASE
                WHEN codigo LIKE 'CONS-%' THEN 'CONSUMO'
                WHEN codigo LIKE 'VIV-%' THEN 'VIVIENDA_HIPOTECARIA'
                WHEN codigo LIKE 'MICRO-%' THEN 'MICROCREDITO'
                ELSE tipo_credito_asfi
            END,
            sector_productivo = CASE WHEN codigo LIKE 'MICRO-%' THEN true ELSE sector_productivo END;
    END IF;
END $$;

ALTER TABLE producto_credito
    ADD COLUMN IF NOT EXISTS sector_productivo BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_producto_credito_tipo_asfi'
          AND conrelid = 'producto_credito'::regclass
    ) THEN
        ALTER TABLE producto_credito ADD CONSTRAINT chk_producto_credito_tipo_asfi
            CHECK (tipo_credito_asfi IS NULL OR tipo_credito_asfi IN (
                'MICROCREDITO', 'MICROCREDITO_AGROPECUARIO', 'CONSUMO',
                'VIVIENDA_HIPOTECARIA', 'VIVIENDA_SIN_GARANTIA'
            ));
    END IF;
END $$;

-- 3) solicitud_credito: credit committee and mobile origin channel.
ALTER TABLE solicitud_credito
    ADD COLUMN IF NOT EXISTS ronda_comite INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS resultado_comite VARCHAR(12),
    ADD COLUMN IF NOT EXISTS fecha_resolucion_comite TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS canal_origen VARCHAR(12) NOT NULL DEFAULT 'VENTANILLA',
    ADD COLUMN IF NOT EXISTS datos_declarados JSONB;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_solicitud_credito_canal_origen'
          AND conrelid = 'solicitud_credito'::regclass
    ) THEN
        ALTER TABLE solicitud_credito
            ADD CONSTRAINT ck_solicitud_credito_canal_origen
            CHECK (canal_origen IN ('VENTANILLA', 'MOVIL'));
    END IF;
END $$;

-- 4) voto_comite: committee votes (depends on the keys from step 1).
CREATE TABLE IF NOT EXISTS voto_comite (
    id BIGSERIAL PRIMARY KEY,
    solicitud_credito_id INT NOT NULL,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id),
    ronda INT NOT NULL CHECK (ronda > 0),
    usuario_id BIGINT NOT NULL,
    voto VARCHAR(10) NOT NULL CHECK (voto IN ('APROBAR','RECHAZAR','OBSERVAR')),
    comentario TEXT NOT NULL CHECK (char_length(btrim(comentario)) >= 10),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_voto_comite_solicitud_ronda_usuario UNIQUE (solicitud_credito_id, ronda, usuario_id),
    CONSTRAINT fk_voto_comite_solicitud_cooperativa
        FOREIGN KEY (solicitud_credito_id, cooperativa_id)
        REFERENCES solicitud_credito(id, cooperativa_id),
    CONSTRAINT fk_voto_comite_usuario_cooperativa
        FOREIGN KEY (usuario_id, cooperativa_id)
        REFERENCES usuario(id, cooperativa_id)
);

CREATE INDEX IF NOT EXISTS idx_voto_comite_solicitud_ronda_fecha
    ON voto_comite(solicitud_credito_id, ronda, fecha, id);

-- 5) reporte: tenant ownership and immutable ASFI report snapshots.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'reporte'
          AND column_name = 'cooperativa_id'
    ) THEN
        ALTER TABLE reporte ADD COLUMN cooperativa_id BIGINT REFERENCES cooperativa(id);
        -- Legacy/platform reports without a tenant remain NULL and tenant-invisible.
        UPDATE reporte r
        SET cooperativa_id = u.cooperativa_id
        FROM usuario u
        WHERE r.usuario_id = u.id AND u.cooperativa_id IS NOT NULL;
    END IF;
END $$;

ALTER TABLE reporte
    ADD COLUMN IF NOT EXISTS periodo DATE,
    ADD COLUMN IF NOT EXISTS estado VARCHAR(12) NOT NULL DEFAULT 'GENERADO',
    ADD COLUMN IF NOT EXISTS contenido JSONB,
    ADD COLUMN IF NOT EXISTS resumen JSONB,
    ADD COLUMN IF NOT EXISTS hash_contenido VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_reporte_tenant_periodo
    ON reporte(cooperativa_id, periodo DESC, tipo);

COMMIT;
