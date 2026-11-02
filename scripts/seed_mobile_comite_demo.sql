-- Additive, rerunnable seed for the backend mobile demo datasets and the three
-- credit-committee demo cases from seed_comite_demo.py.
-- Run after migrations 012, 014, 015, 016, 020, 022, 023, 027, and 033 (or equivalent
-- schema). No rows are deleted or rewritten.
--
-- Existing-row prerequisites: cooperative id 1; BOB and USD currencies; and same-cooperative users
-- oficial.credito@test.com, contador@test.com, and acredito@cooperativa.com
-- (the last falls back to oficial.credito@test.com as in the Python script).
--
-- The CONS-BOB/MICRO-BOB/VIV-USD rows below are pre-existing prerequisite
-- catalog data from the original SQL, retained for the committee cases. PEQ,
-- AGR, VIV, mobile member/account/request/DPF rows, and committee data are the
-- datasets added by the current demos. Mobile mock numeric IDs are NOT database
-- IDs; generated IDs are always resolved through natural keys.
-- Exclusions are documented at the end of this file.

BEGIN;

DO $$
DECLARE
    v_coop_id BIGINT;
    v_bob_id INT;
    v_usd_id INT;
    v_registrador_id BIGINT;
    v_oficial_id BIGINT;
    v_contador_id BIGINT;
    v_vivienda_id INT;
    v_productivo_id INT;
    v_consumo_id INT;
    v_socio1_id BIGINT;
    v_socio2_id BIGINT;
    v_socio3_id BIGINT;
    v_sol1_id INT;
    v_sol2_id INT;
    v_sol3_id INT;
    v_now TIMESTAMPTZ := now();
BEGIN
    -- Cooperative id 1 is explicitly selected by the original demo script.
    SELECT id INTO v_coop_id FROM cooperativa WHERE id = 1;
    IF v_coop_id IS NULL THEN
        RAISE EXCEPTION 'Committee demo prerequisite missing: cooperativa id 1';
    END IF;

    SELECT id INTO v_bob_id FROM moneda WHERE codigo_iso = 'BOB';
    SELECT id INTO v_usd_id FROM moneda WHERE codigo_iso = 'USD';
    IF v_bob_id IS NULL OR v_usd_id IS NULL THEN
        RAISE EXCEPTION 'Credit seed prerequisite missing: BOB and USD currencies';
    END IF;

    SELECT id INTO v_oficial_id FROM usuario
    WHERE correo = 'oficial.credito@test.com' AND cooperativa_id = v_coop_id;
    SELECT id INTO v_contador_id FROM usuario
    WHERE correo = 'contador@test.com' AND cooperativa_id = v_coop_id;
    SELECT id INTO v_registrador_id FROM usuario
    WHERE correo = 'acredito@cooperativa.com' AND cooperativa_id = v_coop_id;
    IF v_registrador_id IS NULL THEN
        v_registrador_id := v_oficial_id;
    END IF;
    IF v_oficial_id IS NULL OR v_contador_id IS NULL OR v_registrador_id IS NULL THEN
        RAISE EXCEPTION 'Demo prerequisite missing: expected same-cooperative committee users';
    END IF;

    -- Mobile API credit products; values and natural keys match migration 014.
    -- Extended fields reflect the later 020, 022, and 027 migration backfills.
    INSERT INTO producto_credito
        (cooperativa_id, codigo, nombre, descripcion, moneda_id, monto_min,
         monto_max, plazo_min_meses, plazo_max_meses, tasa_interes_anual,
         tipo_amortizacion, dias_gracia_mora, tasa_mora_anual,
         relacion_cuota_ingreso_max, requiere_garantia, estado,
         monto_aprobacion_directa, cobertura_minima_garantia,
         tipo_credito_asfi, sector_productivo)
    VALUES
        (v_coop_id, 'CONS-BOB', 'Crédito de Consumo',
         'Financiamiento de consumo en bolivianos', v_bob_id, 1000.00, 50000.00,
         6, 48, 18.00, 'FRANCES', 3, 3.00, 40.00, false, 'ACTIVO',
         20000.00, 0.00, 'CONSUMO', false),
        (v_coop_id, 'MICRO-BOB', 'Microcrédito Productivo',
         'Capital para actividades productivas en bolivianos', v_bob_id,
         2000.00, 100000.00, 6, 60, 15.00, 'FRANCES', 5, 3.00, 45.00,
         true, 'ACTIVO', 30000.00, 100.00, 'MICROCREDITO', true),
        (v_coop_id, 'VIV-USD', 'Crédito de Vivienda',
         'Financiamiento de vivienda en dólares estadounidenses', v_usd_id,
         10000.00, 150000.00, 60, 240, 8.50, 'ALEMAN', 5, 2.00, 35.00,
         true, 'ACTIVO', 0.00, 125.00, 'VIVIENDA_HIPOTECARIA', false)
     ON CONFLICT (cooperativa_id, codigo) DO NOTHING;

    -- New mobile demo catalog (demo_mode.dart). The app says FRANCESA while
    -- the persisted backend enum is FRANCES; use the schema's allowed value.
    INSERT INTO producto_credito
        (cooperativa_id, codigo, nombre, moneda_id, monto_min, monto_max,
         plazo_min_meses, plazo_max_meses, tasa_interes_anual,
         tipo_amortizacion, requiere_garantia, estado)
    VALUES
        (v_coop_id, 'PEQ', 'Crédito Emprendedor', v_bob_id,
         5000.00, 50000.00, 6, 36, 12.50, 'FRANCES', false, 'ACTIVO'),
        (v_coop_id, 'AGR', 'Crédito Agrícola', v_bob_id,
         10000.00, 100000.00, 12, 48, 11.80, 'FRANCES', true, 'ACTIVO'),
        (v_coop_id, 'VIV', 'Crédito Vivienda', v_bob_id,
         20000.00, 200000.00, 24, 120, 9.50, 'FRANCES', true, 'ACTIVO')
    ON CONFLICT (cooperativa_id, codigo) DO NOTHING;

    -- New mobile demo socios. Their IDs in Dart are presentation fixtures;
    -- CI is the unique natural key in the backend. Do not invent contact data.
    INSERT INTO socio (cooperativa_id, ci, nombre, apellido, estado)
    VALUES
        (v_coop_id, '4852147', 'María', 'Condori Mamani', 'ACTIVO'),
        (v_coop_id, '3852147', 'Juan', 'Pérez Quispe', 'ACTIVO'),
        (v_coop_id, '5221147', 'Rosa', 'Gutiérrez Flores', 'ACTIVO'),
        (v_coop_id, '6214589', 'Carlos', 'Choque Huanca', 'ACTIVO'),
        (v_coop_id, '7055123', 'Ana', 'Mamani Apaza', 'ACTIVO'),
        (v_coop_id, '5821047', 'Luis', 'Flores Vargas', 'ACTIVO')
    ON CONFLICT (ci) DO NOTHING;

    IF EXISTS (
        SELECT 1 FROM (VALUES ('4852147'), ('3852147'), ('5221147'),
                            ('6214589'), ('7055123'), ('5821047')) AS d(ci)
        WHERE NOT EXISTS (SELECT 1 FROM socio s
                          WHERE s.ci = d.ci AND s.cooperativa_id = v_coop_id
                            AND (s.ci, s.nombre, s.apellido) IN
                                (('4852147', 'María', 'Condori Mamani'),
                                 ('3852147', 'Juan', 'Pérez Quispe'),
                                 ('5221147', 'Rosa', 'Gutiérrez Flores'),
                                 ('6214589', 'Carlos', 'Choque Huanca'),
                                 ('7055123', 'Ana', 'Mamani Apaza'),
                                 ('5821047', 'Luis', 'Flores Vargas'))
                            AND (s.ci = d.ci))
    ) THEN
        RAISE EXCEPTION 'Mobile demo conflict: a demo CI is outside cooperative id 1 or has different identity data';
    END IF;

    -- Savings accounts from DemoSocioData; socio IDs resolve by CI, not Dart ID.
    INSERT INTO cuenta_ahorro
        (numero, tipo_producto, saldo_disponible, saldo_bloqueado, estado,
         fecha_registro, socio_id, moneda_id)
    SELECT x.numero, x.tipo_producto, x.saldo_disponible, 0.00, 'ACTIVA',
           x.fecha_registro, s.id, v_bob_id
    FROM (VALUES
        ('CA-1001-4852', 'CAJA DE AHORRO', 8450.50::NUMERIC, DATE '2022-03-15'),
        ('CA-1002-4852', 'AHORRO PROGRAMADO', 3200.00::NUMERIC, DATE '2023-08-02')
    ) AS x(numero, tipo_producto, saldo_disponible, fecha_registro)
    JOIN socio s ON s.ci = '4852147' AND s.cooperativa_id = v_coop_id
    ON CONFLICT (numero) DO NOTHING;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
            ('CA-1001-4852', 'CAJA DE AHORRO', 8450.50::NUMERIC, DATE '2022-03-15'),
            ('CA-1002-4852', 'AHORRO PROGRAMADO', 3200.00::NUMERIC, DATE '2023-08-02')
        ) AS x(numero, tipo_producto, saldo_disponible, fecha_registro)
        WHERE NOT EXISTS (
            SELECT 1 FROM cuenta_ahorro c
            JOIN socio s ON s.id = c.socio_id
            WHERE c.numero = x.numero AND s.ci = '4852147'
              AND s.cooperativa_id = v_coop_id AND c.moneda_id = v_bob_id
              AND c.tipo_producto = x.tipo_producto
              AND c.saldo_disponible = x.saldo_disponible
              AND c.saldo_bloqueado = 0 AND c.estado = 'ACTIVA'
              AND c.fecha_registro = x.fecha_registro
        )
    ) THEN
        RAISE EXCEPTION 'Mobile demo conflict: a savings account natural key exists with different data';
    END IF;

    -- The three CU-M8 worklist request payloads are not inserted: solicitud_credito
    -- requires usuario_id, but demo_mode.dart supplies no creating user. Reusing
    -- a committee user would invent a foreign-key attribution. Their field
    -- evaluation cannot be inserted either: evaluacion_campo also requires an
    -- evaluator user, and the fixture does not provide one.

    -- New mobile DPF records. Monthly interest values are reproducible from
    -- demo_mode.dart's formula; generated dates/statuses mirror its 2026-11-01
    -- snapshot. No savings-account links are supplied by that fixture.
    INSERT INTO deposito_plazo_fijo
        (monto, tasa_interes_anual, plazo_dias, fecha_inicio,
         fecha_vencimiento, interes_calculado, estado, socio_id, moneda_id,
         numero_certificado, modalidad_pago_interes, interes_bruto,
         retencion_rciva, interes_neto, cooperativa_id)
    SELECT x.monto, x.tasa, x.plazo_dias, x.fecha_inicio, x.fecha_vencimiento,
           x.interes_bruto * x.cuotas, x.estado, s.id, v_bob_id,
           x.certificado, 'MENSUAL', x.interes_bruto * x.cuotas,
           x.retencion * x.cuotas, x.neto * x.cuotas, v_coop_id
    FROM (VALUES
        ('DPF-2026-0041', 15000.00::NUMERIC, 5.80::NUMERIC, 360,
         DATE '2026-01-10', DATE '2027-01-05', 'VIGENTE',
         72.50::NUMERIC, 9.06::NUMERIC, 63.44::NUMERIC, 12),
        ('DPF-2025-0017', 8000.00::NUMERIC, 5.20::NUMERIC, 180,
         DATE '2025-11-02', DATE '2026-05-01', 'VENCIDO',
         34.67::NUMERIC, 4.33::NUMERIC, 30.33::NUMERIC, 6)
    ) AS x(certificado, monto, tasa, plazo_dias, fecha_inicio,
           fecha_vencimiento, estado, interes_bruto, retencion, neto, cuotas)
    JOIN socio s ON s.ci = '4852147' AND s.cooperativa_id = v_coop_id
    ON CONFLICT DO NOTHING;

    IF EXISTS (
        SELECT 1 FROM (VALUES
            ('DPF-2026-0041', 15000.00::NUMERIC, 5.80::NUMERIC, 360,
             DATE '2026-01-10', DATE '2027-01-05', 'VIGENTE'),
            ('DPF-2025-0017', 8000.00::NUMERIC, 5.20::NUMERIC, 180,
             DATE '2025-11-02', DATE '2026-05-01', 'VENCIDO')
        ) AS x(certificado, monto, tasa, plazo_dias, fecha_inicio,
               fecha_vencimiento, estado)
        WHERE NOT EXISTS (
            SELECT 1 FROM deposito_plazo_fijo d
            JOIN socio s ON s.id = d.socio_id
            WHERE d.cooperativa_id = v_coop_id
              AND d.numero_certificado = x.certificado
              AND s.ci = '4852147' AND s.cooperativa_id = v_coop_id
              AND d.monto = x.monto AND d.tasa_interes_anual = x.tasa
              AND d.plazo_dias = x.plazo_dias
              AND d.fecha_inicio = x.fecha_inicio
              AND d.fecha_vencimiento = x.fecha_vencimiento
              AND d.estado = x.estado AND d.moneda_id = v_bob_id
        )
    ) THEN
        RAISE EXCEPTION 'Mobile demo conflict: a DPF certificate exists outside the expected cooperative/member';
    END IF;

    INSERT INTO dpf_cronograma
        (deposito_plazo_fijo_id, numero, fecha_pago, dias, interes_bruto,
         retencion_rciva, interes_neto, estado, fecha_pago_real)
    SELECT d.id, n.numero,
           (d.fecha_inicio + make_interval(months => n.numero))::DATE,
           ((d.fecha_inicio + make_interval(months => n.numero))::DATE
              - (d.fecha_inicio + make_interval(months => n.numero - 1))::DATE),
           CASE d.numero_certificado WHEN 'DPF-2026-0041' THEN 72.50 ELSE 34.67 END,
           CASE d.numero_certificado WHEN 'DPF-2026-0041' THEN 9.06 ELSE 4.33 END,
           CASE d.numero_certificado WHEN 'DPF-2026-0041' THEN 63.44 ELSE 30.33 END,
           CASE WHEN (d.fecha_inicio + make_interval(months => n.numero))::DATE < DATE '2026-11-01'
                THEN 'PAGADA' ELSE 'PENDIENTE' END,
           CASE WHEN (d.fecha_inicio + make_interval(months => n.numero))::DATE < DATE '2026-11-01'
                THEN ((d.fecha_inicio + make_interval(months => n.numero))::DATE)::timestamptz
                ELSE NULL END
    FROM deposito_plazo_fijo d
    CROSS JOIN LATERAL generate_series(
        1, CASE d.numero_certificado WHEN 'DPF-2026-0041' THEN 12 ELSE 6 END
    ) AS n(numero)
    WHERE d.cooperativa_id = v_coop_id
      AND d.numero_certificado IN ('DPF-2026-0041', 'DPF-2025-0017')
    ON CONFLICT (deposito_plazo_fijo_id, numero) DO NOTHING;

    SELECT id INTO v_vivienda_id FROM producto_credito
    WHERE cooperativa_id = v_coop_id AND codigo = 'VIV-USD';
    SELECT id INTO v_productivo_id FROM producto_credito
    WHERE cooperativa_id = v_coop_id AND codigo = 'MICRO-BOB';
    SELECT id INTO v_consumo_id FROM producto_credito
    WHERE cooperativa_id = v_coop_id AND codigo = 'CONS-BOB';
    IF v_vivienda_id IS NULL OR v_productivo_id IS NULL OR v_consumo_id IS NULL THEN
        RAISE EXCEPTION 'Could not resolve the three seeded products by cooperative/code';
    END IF;

    -- The Python seed looks these users up by email; verify tenant membership
    -- because committee votes have composite tenant foreign keys.
    SELECT id INTO v_oficial_id FROM usuario
    WHERE correo = 'oficial.credito@test.com' AND cooperativa_id = v_coop_id;
    SELECT id INTO v_contador_id FROM usuario
    WHERE correo = 'contador@test.com' AND cooperativa_id = v_coop_id;
    SELECT id INTO v_registrador_id FROM usuario
    WHERE correo = 'acredito@cooperativa.com' AND cooperativa_id = v_coop_id;
    IF v_registrador_id IS NULL THEN
        v_registrador_id := v_oficial_id;
    END IF;
    IF v_oficial_id IS NULL OR v_contador_id IS NULL OR v_registrador_id IS NULL THEN
        RAISE EXCEPTION 'Committee demo prerequisite missing: expected same-cooperative committee users';
    END IF;

    -- get_or_create_socio() keys each demo member by its CI.
    INSERT INTO socio
        (cooperativa_id, ci, nombre, apellido, correo, telefono, direccion,
         estado, fecha_registro)
    VALUES
        (v_coop_id, 'COM-SOCIO-01', 'Valeria', 'Méndez Suárez',
         'valeria.mendez@test.bo', '77654321', 'Av. Las Américas #450, Santa Cruz',
         'ACTIVO', DATE '2023-01-15'),
        (v_coop_id, 'COM-SOCIO-02', 'Carlos', 'Gutiérrez Peña',
         'carlos.gutierrez@test.bo', '77654321', 'Av. Las Américas #450, Santa Cruz',
         'ACTIVO', DATE '2023-01-15'),
        (v_coop_id, 'COM-SOCIO-03', 'Mariana', 'Ríos Alarcón',
         'mariana.rios@test.bo', '77654321', 'Av. Las Américas #450, Santa Cruz',
         'ACTIVO', DATE '2023-01-15')
    ON CONFLICT (ci) DO NOTHING;

    SELECT id INTO v_socio1_id FROM socio WHERE ci = 'COM-SOCIO-01' AND cooperativa_id = v_coop_id;
    SELECT id INTO v_socio2_id FROM socio WHERE ci = 'COM-SOCIO-02' AND cooperativa_id = v_coop_id;
    SELECT id INTO v_socio3_id FROM socio WHERE ci = 'COM-SOCIO-03' AND cooperativa_id = v_coop_id;
    IF v_socio1_id IS NULL OR v_socio2_id IS NULL OR v_socio3_id IS NULL THEN
        RAISE EXCEPTION 'Committee demo CI already exists outside cooperative id 1';
    END IF;

    -- Stable application numbers replace the Python script's destructive
    -- delete/recreate cycle. Existing requests and their dependent history stay.
    INSERT INTO solicitud_credito
        (numero_solicitud, monto, plazo_meses, tasa_interes, calificacion_asfi,
         tiene_deudas, estado, socio_id, usuario_id, producto_credito_id,
         moneda_id, cooperativa_id, destino, destino_detalle, ronda_comite,
         canal_origen, fecha_solicitud, fecha_actualizacion)
    VALUES
        ('COM-DEMO-001', 85000.00, 48, 9.50, 'A', false, 'EN_COMITE',
         v_socio1_id, v_registrador_id, v_vivienda_id, v_usd_id, v_coop_id,
         'VIVIENDA', 'Ampliación y remodelación integral de vivienda familiar propia',
         1, 'VENTANILLA', v_now - INTERVAL '2 days', v_now - INTERVAL '2 days'),
        ('COM-DEMO-002', 45000.00, 24, 14.00, 'A', false, 'EN_COMITE',
         v_socio2_id, v_registrador_id, v_productivo_id, v_bob_id, v_coop_id,
         'CAPITAL_TRABAJO', 'Compra por mayor de insumos y mercadería para taller metalúrgico',
         1, 'MOVIL', v_now - INTERVAL '1 day', v_now - INTERVAL '1 day'),
        ('COM-DEMO-003', 35000.00, 18, 16.50, 'B', true, 'EN_COMITE',
         v_socio3_id, v_registrador_id, v_consumo_id, v_bob_id, v_coop_id,
         'CONSUMO', 'Consolidación de deudas comerciales y compra de equipamiento',
         1, 'VENTANILLA', v_now - INTERVAL '18 hours', v_now - INTERVAL '18 hours')
    ON CONFLICT (cooperativa_id, numero_solicitud) DO NOTHING;

    SELECT id INTO v_sol1_id FROM solicitud_credito
    WHERE cooperativa_id = v_coop_id AND numero_solicitud = 'COM-DEMO-001';
    SELECT id INTO v_sol2_id FROM solicitud_credito
    WHERE cooperativa_id = v_coop_id AND numero_solicitud = 'COM-DEMO-002';
    SELECT id INTO v_sol3_id FROM solicitud_credito
    WHERE cooperativa_id = v_coop_id AND numero_solicitud = 'COM-DEMO-003';
    IF v_sol1_id IS NULL OR v_sol2_id IS NULL OR v_sol3_id IS NULL THEN
        RAISE EXCEPTION 'Could not resolve all committee demo requests by cooperative/number';
    END IF;

    -- Credit evaluations are attached to the stable request IDs and guarded
    -- against duplicates without deleting or replacing existing evaluations.
    INSERT INTO evaluacion_crediticia
        (solicitud_credito_id, cooperativa_id, version_modelo, score, dictamen,
         factores, knockouts, explicacion, cuota_estimada,
         relacion_cuota_ingreso, usuario_id, fecha, probabilidad_mora,
         nivel_riesgo, version_modelo_mora)
    SELECT x.solicitud_id, v_coop_id, 'v1.2-asfi', x.score, 'REVISION_MANUAL',
           x.factores::JSONB, '[]'::JSONB, x.explicacion, x.cuota,
           x.rci, v_registrador_id, x.fecha, x.probabilidad, x.riesgo,
           'v1.0-logreg'
    FROM (VALUES
        (v_sol1_id, 745,
         '[{"codigo":"CAP_PAGO","descripcion":"Capacidad de Pago (RCI)","puntos":280,"maximo":300,"valor":"38.2%","motivo":"Relación cuota-ingreso holgada"},{"codigo":"ESTAB_LAB","descripcion":"Estabilidad Laboral","puntos":200,"maximo":200,"valor":"4 años","motivo":"Antigüedad demostrada en empleo actual"},{"codigo":"GARANTIA","descripcion":"Garantía Hipotecaria","puntos":265,"maximo":300,"valor":"Folio Real al día","motivo":"Inmueble propio sin gravamen previo"}]',
         'El monto solicitado (Bs. 85,000) supera el límite de delegación directa de la agencia. Presenta buena solvencia y estabilidad patrimonial. Requiere deliberación colegiada del Comité.',
         2135.50::NUMERIC, 38.20::NUMERIC, 0.12::NUMERIC, 'MEDIO', v_now - INTERVAL '2 days'),
        (v_sol2_id, 820,
         '[{"codigo":"FLUJO_CAJA","descripcion":"Flujo de Caja Negocio","puntos":350,"maximo":400,"valor":"Ventas > Bs. 25,000","motivo":"Flujo positivo sostenido en últimos 6 meses"},{"codigo":"MARGEN_OPERATIVO","descripcion":"Margen Operativo","puntos":270,"maximo":300,"valor":"32%","motivo":"Margen sólido para cubrir amortización"},{"codigo":"CAP_PAGO","descripcion":"Capacidad de Pago","puntos":200,"maximo":200,"valor":"27.5%","motivo":"Relación cuota-ingreso muy baja"}]',
         'Flujo de caja y capacidad de generación comprobados en la evaluación de campo. Supera el límite de aprobación directa del oficial.',
         2160.00::NUMERIC, 27.50::NUMERIC, 0.07::NUMERIC, 'BAJO', v_now - INTERVAL '1 day'),
        (v_sol3_id, 680,
         '[{"codigo":"NIVEL_ENDEUDAMIENTO","descripcion":"Nivel de Endeudamiento","puntos":210,"maximo":300,"valor":"41.5% RCI","motivo":"Endeudamiento cercano al límite superior"},{"codigo":"GARANTE_PERSONAL","descripcion":"Garantía Personal","puntos":250,"maximo":300,"valor":"Garante Dependiente","motivo":"Garante con respaldo salarial y estabilidad laboral"},{"codigo":"CALIF_ASFI","descripcion":"Calificación ASFI","puntos":220,"maximo":300,"valor":"Calificación B","motivo":"Regularizaciones cumplidas puntualmente"}]',
         'La socia presenta endeudamiento cercano al límite normativo. El garante cumple con los requisitos de patrimonio y estabilidad laboral.',
         2208.75::NUMERIC, 41.50::NUMERIC, 0.18::NUMERIC, 'MEDIO', v_now - INTERVAL '18 hours')
    ) AS x(solicitud_id, score, factores, explicacion, cuota, rci,
           probabilidad, riesgo, fecha)
    WHERE NOT EXISTS (
        SELECT 1 FROM evaluacion_crediticia e
        WHERE e.solicitud_credito_id = x.solicitud_id
          AND e.version_modelo = 'v1.2-asfi'
    );

    -- The Python script seeds one contador vote on case 2 and oficial+contador
    -- votes on case 3. Stable tuple uniqueness makes these inserts rerunnable.
    INSERT INTO voto_comite
        (solicitud_credito_id, cooperativa_id, ronda, usuario_id, voto,
         comentario, fecha)
    VALUES
        (v_sol2_id, v_coop_id, 1, v_contador_id, 'APROBAR',
         'Revisados los estados financieros y flujo de caja del taller. La liquidez cubre holgadamente la cuota proyectada con cobertura 2.1x.',
         v_now - INTERVAL '5 hours'),
        (v_sol3_id, v_coop_id, 1, v_oficial_id, 'APROBAR',
         'Se verificó in situ la actividad económica del garante personal. Cuenta con respaldo de bienes suficientes para la operación.',
         v_now - INTERVAL '4 hours'),
        (v_sol3_id, v_coop_id, 1, v_contador_id, 'OBSERVAR',
         'Recomiendo condicionar el desembolso a la presentación de boletas de pago actualizadas del garante y débito automático.',
         v_now - INTERVAL '2 hours')
    ON CONFLICT (solicitud_credito_id, ronda, usuario_id) DO NOTHING;
END $$;

COMMIT;

-- Deliberately not seeded because the source cannot be represented safely:
-- * CU-M8 request rows SOL-2026-0118/0105/0091 and their field evaluation:
--   solicitud_credito and evaluacion_campo require usuario_id, but demo_mode.dart
--   supplies no author/evaluator. The committee script's users are unrelated;
--   assigning one would fabricate FK attribution. Therefore no credit parents
--   are available for the CU-M6 CRE-2024-0032/CRE-2023-0018 rows or schedules.
-- * Extract statement balances/totals are mock-calculated, while the source
--   transaction IDs (1001-1004) and receipt IDs (5001-5003) are fixture IDs;
--   one movement references the missing credit above. The backend transaction
--   table has no stable demo natural key for these rows, so an idempotent,
--   additive insert cannot be guaranteed without inventing IDs or duplicate
--   handling semantics.
-- * Precalculation/scoring results, credit schedule amounts, payment history
--   (empty), photo bytes/IDs, GPS form state, session/auth/chat state, and
--   interface-only balances/totals have no persistent record supplied by the
--   fixture or are runtime-derived. The DPF schedules are seeded because their
--   source values can be reproduced and (DPF, installment number) is unique.
