-- CU-W18: monthly DPF interest payments and cooperative-scoped certificates.
ALTER TABLE dpf_cronograma
    ADD COLUMN IF NOT EXISTS fecha_pago_real TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS transaccion_id INT NULL REFERENCES transaccion(id);

ALTER TABLE liquidacion
    ADD COLUMN IF NOT EXISTS interes_ya_pagado NUMERIC(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS descuento_capital NUMERIC(12,2) NOT NULL DEFAULT 0;

-- Only assign ownership when the linked member establishes it unambiguously.
UPDATE deposito_plazo_fijo d
SET cooperativa_id = s.cooperativa_id
FROM socio s
WHERE d.socio_id = s.id
  AND d.cooperativa_id IS NULL
  AND s.cooperativa_id IS NOT NULL;

DROP INDEX IF EXISTS uq_dpf_numero_certificado;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dpf_coop_numero_certificado
    ON deposito_plazo_fijo (cooperativa_id, numero_certificado)
    WHERE numero_certificado IS NOT NULL;
