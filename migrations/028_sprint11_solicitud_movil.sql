-- Mobile-origin applications retain self-declared data separately from field evaluation.
ALTER TABLE solicitud_credito
    ADD COLUMN IF NOT EXISTS canal_origen VARCHAR(12) NOT NULL DEFAULT 'VENTANILLA';
ALTER TABLE solicitud_credito
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
