-- One-year history seed for the ML (mora prediction), reports, dashboard and feedback CRM.
-- Run it once: paste the whole file into the Supabase SQL Editor, or run
-- `psql -f scripts/seed_historial_1_anio.sql`. It is a single DO block, so any error
-- rolls everything back. It installs no extensions and uses no psql meta-commands.
--
-- Requires (same as the other demo seeds): the cooperative "Cooperativa de Prueba SI2",
-- the active OFICIAL_CREDITO user acredito@cooperativa.com and at least one active credit
-- product with a currency. It only INSERTS rows prefixed HIST1Y; it never updates or deletes
-- existing data, and refuses to run twice.
--
-- What it creates (deterministic, no random()):
--   * 36 socios registered 14-48 months ago, each with a VISTA savings account.
--   * 36 disbursed credits spread over the last 15 months (2-3 per month), each with a
--     field evaluation (income, debts, ASFI rating, job tenure), a FRANCES payment plan
--     with credit-life insurance, and every due installment paid on time, paid late, or
--     left unpaid according to the socio's risk profile (good / regular / bad).
--   * Credits whose plan ended are CANCELADO; the rest stay VIGENTE.
--   * 2-3 feedback answers per socio spread over the last 12 months.
-- After running it, open "Mora predictiva" and press "Actualizar predicciones" so the model
-- scores the new VIGENTE credits with a year of payment behaviour.

DO $seed$
DECLARE
    v_coop_id BIGINT;
    v_coop_count INTEGER;
    v_official_id BIGINT;
    v_product RECORD;
    v_socio_id BIGINT;
    v_account_id BIGINT;
    v_eval_id INTEGER;
    v_request_id INTEGER;
    v_credit_id INTEGER;
    v_installment_id INTEGER;
    v_i INTEGER;
    v_k INTEGER;
    v_profile TEXT;
    v_amount NUMERIC(12, 2);
    v_term INTEGER;
    v_rate NUMERIC(6, 2);
    v_monthly_rate NUMERIC;
    v_insurance_rate NUMERIC;
    v_payment NUMERIC(12, 2);
    v_balance NUMERIC(12, 2);
    v_interest NUMERIC(12, 2);
    v_capital NUMERIC(12, 2);
    v_insurance NUMERIC(12, 2);
    v_total NUMERIC(12, 2);
    v_disbursed DATE;
    v_due DATE;
    v_late_days INTEGER;
    v_paid_capital NUMERIC(12, 2);
    v_income NUMERIC(12, 2);
    v_receipt INTEGER := 0;
    v_services TEXT[] := ARRAY['CREDITO', 'AHORRO', 'CAJA', 'ATENCION', 'APP_MOVIL', 'OTRO'];
    v_comments TEXT[] := ARRAY[
        'Excelente atención, rápida y amable.',
        'El trámite del crédito fue claro.',
        'La fila en caja fue larga.',
        'La app móvil me facilita pagar mis cuotas.',
        'Podrían mejorar los horarios de atención.',
        'Me explicaron bien el plan de pagos y el seguro.',
        'Tardaron en responder mi consulta.',
        'Muy buena experiencia en general.'];
    v_first_names TEXT[] := ARRAY['Ana', 'Luis', 'María', 'Jorge', 'Carla', 'Pedro', 'Rosa', 'Diego', 'Lucía',
                                  'Marco', 'Elena', 'Raúl'];
    v_last_names TEXT[] := ARRAY['Quispe', 'Mamani', 'Choque', 'Flores', 'Rojas', 'Vargas', 'Gutiérrez',
                                 'Condori', 'Torrez', 'Suárez', 'Apaza', 'Rivera'];
BEGIN
    SELECT count(*) INTO v_coop_count FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2';
    IF v_coop_count <> 1 THEN
        RAISE EXCEPTION 'Se necesita exactamente una cooperativa "Cooperativa de Prueba SI2" (hay %).', v_coop_count;
    END IF;
    SELECT id INTO v_coop_id FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2';

    SELECT u.id INTO v_official_id
    FROM usuario u JOIN rol r ON r.id = u.rol_id
    WHERE u.correo = 'acredito@cooperativa.com' AND u.estado = 'ACTIVO'
      AND r.nombre = 'OFICIAL_CREDITO' AND u.cooperativa_id = v_coop_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Falta el oficial ACTIVO acredito@cooperativa.com en Cooperativa de Prueba SI2.';
    END IF;

    SELECT p.id, p.moneda_id, p.monto_min, p.monto_max, p.plazo_min_meses, p.plazo_max_meses,
           p.tasa_interes_anual, COALESCE(p.tasa_seguro_desgravamen, 0) AS tasa_seguro
    INTO v_product
    FROM producto_credito p
    WHERE p.cooperativa_id = v_coop_id AND p.estado = 'ACTIVO' AND p.moneda_id IS NOT NULL
    ORDER BY p.id LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No hay un producto de crédito ACTIVO con moneda en Cooperativa de Prueba SI2.';
    END IF;

    IF EXISTS (SELECT 1 FROM socio WHERE ci LIKE 'HIST1Y-%') THEN
        RAISE EXCEPTION 'El historial HIST1Y ya fue cargado; no se volvió a insertar nada.';
    END IF;

    v_rate := v_product.tasa_interes_anual;
    v_monthly_rate := v_rate / 100 / 12;
    -- Insurance: the product rate, or 0.05 % monthly when the product has none, so the
    -- statements show credit-life insurance.
    v_insurance_rate := CASE WHEN v_product.tasa_seguro > 0 THEN v_product.tasa_seguro ELSE 0.05 END / 100;

    FOR v_i IN 1..36 LOOP
        -- Risk profile: 60 % good, 25 % regular, 15 % bad.
        v_profile := CASE WHEN v_i % 20 IN (3, 7, 13) THEN 'MALO'
                          WHEN v_i % 4 = 1 THEN 'REGULAR'
                          ELSE 'BUENO' END;
        v_income := 2500 + (v_i * 173) % 6500;

        INSERT INTO socio (ci, nombre, apellido, cooperativa_id, estado, fecha_registro)
        VALUES (format('HIST1Y-%s', lpad(v_i::text, 3, '0')),
                v_first_names[1 + v_i % 12], v_last_names[1 + (v_i * 5) % 12],
                v_coop_id, 'ACTIVO', current_date - ((14 + (v_i * 7) % 35) * INTERVAL '1 month'))
        RETURNING id INTO v_socio_id;

        INSERT INTO cuenta_ahorro (numero, tipo_producto, saldo_disponible, saldo_bloqueado, estado,
                                   fecha_registro, socio_id, moneda_id)
        VALUES (format('HIST1Y-CA-%s', lpad(v_i::text, 3, '0')), 'VISTA',
                CASE v_profile WHEN 'BUENO' THEN 1500 + v_i * 90 WHEN 'REGULAR' THEN 400 + v_i * 20 ELSE 50 + v_i END,
                0, 'ACTIVA', current_date - ((12 + (v_i * 7) % 30) * INTERVAL '1 month'),
                v_socio_id, v_product.moneda_id)
        RETURNING id INTO v_account_id;

        -- Credit terms inside the product limits.
        v_amount := round(LEAST(v_product.monto_max,
                                GREATEST(v_product.monto_min, 3000 + (v_i * 611) % 17000)) / 100) * 100;
        v_amount := LEAST(GREATEST(v_amount, v_product.monto_min), v_product.monto_max);
        v_term := LEAST(v_product.plazo_max_meses, GREATEST(v_product.plazo_min_meses, 6 + (v_i % 4) * 6));
        -- Disbursements spread over the last 15 months.
        v_disbursed := (date_trunc('month', current_date) - ((v_i - 1) % 15) * INTERVAL '1 month')::date
                       + ((v_i * 3) % 25);
        IF v_disbursed >= current_date THEN
            v_disbursed := current_date - 1;
        END IF;

        INSERT INTO evaluacion_campo (ingreso_mensual, egreso_mensual, capacidad_pago, fecha, usuario_id, socio_id,
                                      cuota_deudas_mensual, actividad_economica, fuente_ingresos,
                                      antiguedad_laboral_meses, calificacion_asfi, observaciones)
        VALUES (v_income, round(v_income * 0.45, 2), round(v_income * 0.55, 2), v_disbursed - 7,
                v_official_id, v_socio_id,
                CASE v_profile WHEN 'BUENO' THEN round(v_income * 0.05, 2)
                               WHEN 'REGULAR' THEN round(v_income * 0.20, 2) ELSE round(v_income * 0.40, 2) END,
                (ARRAY['Comercio minorista', 'Transporte', 'Servicios', 'Agricultura', 'Asalariado'])[1 + v_i % 5],
                (ARRAY['DEPENDIENTE', 'INDEPENDIENTE', 'MIXTO'])[1 + v_i % 3],
                CASE v_profile WHEN 'BUENO' THEN 36 + v_i ELSE 6 + v_i % 18 END,
                CASE v_profile WHEN 'BUENO' THEN 'A' WHEN 'REGULAR' THEN 'B' ELSE 'C' END,
                'Evaluación histórica de demostración (HIST1Y).')
        RETURNING id INTO v_eval_id;

        INSERT INTO solicitud_credito (monto, plazo_meses, tasa_interes, calificacion_asfi, tiene_deudas, estado,
                                       socio_id, usuario_id, evaluacion_campo_id, producto_credito_id,
                                       numero_solicitud, destino, destino_detalle, fecha_solicitud,
                                       fecha_actualizacion, moneda_id, cooperativa_id, resultado_comite,
                                       fecha_resolucion_comite, canal_origen)
        VALUES (v_amount, v_term, v_rate, CASE v_profile WHEN 'BUENO' THEN 'A' WHEN 'REGULAR' THEN 'B' ELSE 'C' END,
                v_profile <> 'BUENO', 'DESEMBOLSADO', v_socio_id, v_official_id, v_eval_id, v_product.id,
                format('HIST1Y-S-%s', lpad(v_i::text, 3, '0')),
                (ARRAY['CAPITAL_TRABAJO', 'ACTIVO_FIJO', 'CONSUMO', 'VIVIENDA'])[1 + v_i % 4],
                'Crédito histórico de demostración', v_disbursed - 10, v_disbursed, v_product.moneda_id, v_coop_id,
                'APROBADO', v_disbursed - 3, CASE WHEN v_i % 3 = 0 THEN 'MOVIL' ELSE 'VENTANILLA' END)
        RETURNING id INTO v_request_id;

        INSERT INTO credito (monto_aprobado, saldo_pendiente, estado, solicitud_credito_id, numero_credito,
                             cooperativa_id, socio_id, producto_credito_id, moneda_id, tasa_interes, plazo_meses,
                             tipo_amortizacion, fecha_desembolso, modalidad_desembolso, cuenta_desembolso_id,
                             usuario_id, fecha_creacion)
        VALUES (v_amount, v_amount, 'VIGENTE', v_request_id, format('HIST1Y-C-%s', lpad(v_i::text, 3, '0')),
                v_coop_id, v_socio_id, v_product.id, v_product.moneda_id, v_rate, v_term, 'FRANCES', v_disbursed,
                'CUENTA', v_account_id, v_official_id, v_disbursed)
        RETURNING id INTO v_credit_id;

        -- FRANCES plan: fixed capital+interest payment, plus insurance on the opening balance.
        IF v_monthly_rate > 0 THEN
            v_payment := round(v_amount * v_monthly_rate / (1 - power(1 + v_monthly_rate, -v_term)), 2);
        ELSE
            v_payment := round(v_amount / v_term, 2);
        END IF;
        v_balance := v_amount;
        v_paid_capital := 0;
        FOR v_k IN 1..v_term LOOP
            v_interest := round(v_balance * v_monthly_rate, 2);
            v_capital := CASE WHEN v_k = v_term THEN v_balance ELSE v_payment - v_interest END;
            v_insurance := round(v_balance * v_insurance_rate, 2);
            v_total := v_capital + v_interest + v_insurance;
            v_due := (v_disbursed + v_k * INTERVAL '1 month')::date;

            INSERT INTO tabla_amortizacion (numero_cuota, fecha_vencimiento, monto_capital, monto_interes,
                                            monto_seguro, monto_cuota_total, estado_pago, credito_id,
                                            saldo_inicial, saldo_final, monto_pagado)
            VALUES (v_k, v_due, v_capital, v_interest, v_insurance, v_total, 'PENDIENTE', v_credit_id,
                    v_balance, v_balance - v_capital, 0)
            RETURNING id INTO v_installment_id;

            IF v_due < current_date THEN
                -- Payment behaviour by profile. Bad payers leave their last two due installments unpaid.
                v_late_days := CASE v_profile
                    WHEN 'BUENO' THEN CASE WHEN (v_i + v_k) % 11 = 0 THEN 2 ELSE 0 END
                    WHEN 'REGULAR' THEN CASE WHEN (v_i + v_k) % 3 = 0 THEN 5 + (v_i + v_k) % 20 ELSE 0 END
                    ELSE 15 + (v_i * v_k) % 45 END;
                IF v_profile = 'MALO' AND v_due >= current_date - INTERVAL '2 months' THEN
                    v_late_days := NULL;
                END IF;
                IF v_late_days IS NOT NULL AND v_due + v_late_days < current_date THEN
                    v_receipt := v_receipt + 1;
                    INSERT INTO pago_cuota (monto_capital, monto_interes_pagado, monto_seguro, monto_mora, monto_total,
                                            fecha, tabla_amortizacion_id, credito_id, cooperativa_id, numero_recibo,
                                            modalidad, dias_atraso, usuario_id)
                    VALUES (v_capital, v_interest, v_insurance,
                            round(v_capital * 0.03 / 30 * v_late_days, 2),
                            v_total + round(v_capital * 0.03 / 30 * v_late_days, 2),
                            (v_due + v_late_days) + TIME '10:00', v_installment_id, v_credit_id, v_coop_id,
                            format('HIST1Y-R-%s', lpad(v_receipt::text, 5, '0')),
                            CASE WHEN v_k % 2 = 0 THEN 'CUENTA' ELSE 'EFECTIVO' END, v_late_days, v_official_id);
                    UPDATE tabla_amortizacion
                    SET estado_pago = 'PAGADA', monto_pagado = v_total,
                        fecha_pago = (v_due + v_late_days) + TIME '10:00'
                    WHERE id = v_installment_id;
                    v_paid_capital := v_paid_capital + v_capital;
                END IF;
            END IF;
            v_balance := v_balance - v_capital;
        END LOOP;

        UPDATE credito
        SET saldo_pendiente = v_amount - v_paid_capital,
            estado = CASE WHEN v_amount - v_paid_capital <= 0 THEN 'CANCELADO' ELSE 'VIGENTE' END
        WHERE id = v_credit_id;

        -- Feedback: 2-3 answers per socio over the last 12 months; ratings follow the profile.
        FOR v_k IN 1..(2 + v_i % 2) LOOP
            INSERT INTO encuesta_satisfaccion (cooperativa_id, socio_id, servicio, calificacion, recomendaria,
                                               comentario, canal, fecha_creacion)
            VALUES (v_coop_id, v_socio_id, v_services[1 + (v_i + v_k) % 6],
                    CASE v_profile WHEN 'BUENO' THEN 4 + (v_i + v_k) % 2
                                   WHEN 'REGULAR' THEN 3 + (v_i + v_k) % 2 ELSE 1 + (v_i + v_k) % 3 END,
                    CASE WHEN (v_i + v_k) % 4 = 0 THEN NULL
                         WHEN v_profile = 'BUENO' THEN 8 + (v_i + v_k) % 3
                         WHEN v_profile = 'REGULAR' THEN 6 + (v_i + v_k) % 3 ELSE 2 + (v_i + v_k) % 5 END,
                    CASE WHEN (v_i + v_k) % 3 = 0 THEN NULL ELSE v_comments[1 + (v_i * v_k) % 8] END,
                    CASE WHEN (v_i + v_k) % 2 = 0 THEN 'MOVIL' ELSE 'WEB' END,
                    now() - (((v_i * 11 + v_k * 37) % 360) * INTERVAL '1 day'));
        END LOOP;
    END LOOP;

    RAISE NOTICE 'Historial HIST1Y cargado: 36 socios, 36 créditos, % pagos.', v_receipt;
END
$seed$;
