-- Claim deadline extensions (RNSF Libro 4°, Título I, Capítulo I, Sección 4, Art. 3)
-- and the accrued interest / deferred insurance recorded by credit rescheduling.
-- Idempotent: init_db() mirrors these columns.
BEGIN;

ALTER TABLE reclamo ADD COLUMN IF NOT EXISTS ampliaciones INT NOT NULL DEFAULT 0;
ALTER TABLE reclamo ADD COLUMN IF NOT EXISTS motivo_ampliacion TEXT NULL;
ALTER TABLE reclamo ADD COLUMN IF NOT EXISTS notificado_asfi BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE reprogramacion_credito ADD COLUMN IF NOT EXISTS interes_corrido NUMERIC(14, 2) NULL;
ALTER TABLE reprogramacion_credito ADD COLUMN IF NOT EXISTS seguro_diferido NUMERIC(14, 2) NULL;

COMMIT;
