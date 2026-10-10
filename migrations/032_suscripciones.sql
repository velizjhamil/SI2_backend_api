-- Suscripciones institucionales (SaaS): catálogo de planes y suscripción por
-- cooperativa (tenant). Idempotente.

CREATE TABLE IF NOT EXISTS plan_suscripcion (
    id BIGSERIAL PRIMARY KEY,
    codigo VARCHAR(50) NOT NULL,
    nombre VARCHAR(120) NOT NULL,
    descripcion TEXT,
    features JSONB,
    incluye JSONB,
    precio_mensual NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    moneda VARCHAR(10) NOT NULL DEFAULT 'BOB',
    limite_usuarios INTEGER,
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVO'
        CHECK (estado IN ('ACTIVO', 'INACTIVO')),
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_plan_suscripcion_codigo UNIQUE (codigo)
);

CREATE TABLE IF NOT EXISTS suscripcion (
    id BIGSERIAL PRIMARY KEY,
    cooperativa_id BIGINT NOT NULL REFERENCES cooperativa(id) ON DELETE CASCADE,
    plan_id BIGINT NOT NULL REFERENCES plan_suscripcion(id),
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVA'
        CHECK (estado IN ('ACTIVA', 'SUSPENDIDA', 'VENCIDA', 'CANCELADA')),
    fecha_inicio DATE NOT NULL DEFAULT CURRENT_DATE,
    fecha_fin DATE,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_suscripcion_cooperativa UNIQUE (cooperativa_id)
);

CREATE INDEX IF NOT EXISTS idx_suscripcion_plan ON suscripcion(plan_id);

-- Catálogo de planes por defecto
INSERT INTO plan_suscripcion (codigo, nombre, descripcion, features, incluye, precio_mensual, moneda, limite_usuarios)
VALUES
    (
        'BASICO', 'Básico',
        'Para cooperativas de ahorro y crédito en etapa inicial',
        '[{"text":"Hasta 500 socios","icon":"group"},{"text":"Módulos de ahorros y créditos","icon":"savings"},{"text":"Reportes regulatorios ASFI","icon":"assessment"}]'::jsonb,
        '["Gestión de socios y KYC","Ahorros, aportes y créditos","Comprobantes y contabilidad básica","Soporte por correo"]'::jsonb,
        990.00, 'BOB', 10
    ),
    (
        'CRECIMIENTO_PRO', 'Crecimiento Pro',
        'Para cooperativas en expansión con operación multicanal',
        '[{"text":"Socios ilimitados","icon":"group"},{"text":"Banca móvil y comprobantes digitales","icon":"smartphone"},{"text":"Scoring crediticio e informes ASFI","icon":"analytics"}]'::jsonb,
        '["Todo lo de Básico, más:","Integración con banca móvil","Motor de scoring crediticio","Respaldo automático y bitácora de auditoría"]'::jsonb,
        2490.00, 'BOB', 100
    ),
    (
        'RED_ENTERPRISE', 'Red Enterprise',
        'Para federaciones, redes intercooperativas y grandes uniones de crédito fiduciarias',
        '[{"text":"Multi-tenant federado ilimitado","icon":"corporate_fare"},{"text":"Almacenamiento HSM sin límite","icon":"database"},{"text":"Cluster dedicado de alta disponibilidad","icon":"dns"}]'::jsonb,
        '["Aislamiento de bases de datos por tenant","Matriz regulatoria Maker-Checker","Integración API Core interbancaria","SLA garantizado 99.99% con HSM"]'::jsonb,
        4900.00, 'BOB', NULL
    )
ON CONFLICT (codigo) DO UPDATE SET
    nombre = EXCLUDED.nombre,
    descripcion = EXCLUDED.descripcion,
    features = EXCLUDED.features,
    incluye = EXCLUDED.incluye,
    precio_mensual = EXCLUDED.precio_mensual,
    limite_usuarios = EXCLUDED.limite_usuarios,
    estado = 'ACTIVO';

-- Suscripción por defecto para las cooperativas existentes sin suscripción
INSERT INTO suscripcion (cooperativa_id, plan_id, estado, fecha_inicio, fecha_fin)
SELECT c.id, p.id, 'ACTIVA', CURRENT_DATE, NULL
FROM cooperativa c
CROSS JOIN plan_suscripcion p
WHERE p.codigo = 'RED_ENTERPRISE'
  AND NOT EXISTS (
      SELECT 1 FROM suscripcion s WHERE s.cooperativa_id = c.id
  );
