CREATE TABLE IF NOT EXISTS backup (
    id BIGSERIAL PRIMARY KEY,
    tipo VARCHAR(12) NOT NULL CHECK (tipo IN ('MANUAL', 'AUTOMATICO')),
    estado VARCHAR(12) NOT NULL CHECK (estado IN ('EN_PROCESO', 'COMPLETADO', 'FALLIDO')),
    archivo TEXT,
    tamano_bytes BIGINT CHECK (tamano_bytes IS NULL OR tamano_bytes >= 0),
    checksum_sha256 VARCHAR(64),
    error TEXT,
    usuario_id BIGINT REFERENCES usuario(id) ON DELETE SET NULL,
    iniciado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_en TIMESTAMPTZ,
    CHECK ((estado = 'EN_PROCESO' AND finalizado_en IS NULL) OR estado <> 'EN_PROCESO')
);
CREATE INDEX IF NOT EXISTS ix_backup_iniciado_en ON backup (iniciado_en DESC);
CREATE INDEX IF NOT EXISTS ix_backup_estado ON backup (estado);
CREATE UNIQUE INDEX IF NOT EXISTS uq_backup_single_running
    ON backup ((estado)) WHERE estado = 'EN_PROCESO';
