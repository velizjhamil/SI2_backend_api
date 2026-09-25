-- Idempotent, additive demo data for the cooperative application.
-- Run after bd.sql and ALL migrations 002–012, including 008: the DPF rate
-- bands come from 012, which only seeds them for cooperatives that already
-- exist, and the demo cooperative is created by 008. Existing caja/control_caja rows are
-- deliberately never updated; the historical sessions below use only the two
-- demo cajas named here and are inserted already CERRADA.
BEGIN;

DO $seed$
DECLARE
    v_coop_id BIGINT;
    v_role_admin SMALLINT;
    v_role_socio SMALLINT;
    v_role_cashier SMALLINT;
    v_role_credit SMALLINT;
    v_admin_id BIGINT;
    v_cashier_id BIGINT;
    v_credit_id BIGINT;
    v_bob_id INT;
    v_usd_id INT;
    v_socio_id BIGINT;
    v_user_id BIGINT;
    v_account_id BIGINT;
    v_caja1_id INT;
    v_caja2_id INT;
    v_control1_id INT;
    v_control2_id INT;
    v_arqueo1_id BIGINT;
    v_arqueo2_id BIGINT;
    v_cierre1_id BIGINT;
    v_cierre2_id BIGINT;
    v_dpf_id INT;
    v_dpf_old_id INT;
    v_dpf_new_id INT;
    v_liquidacion_id INT;
    v_seq INT;
    v_rate NUMERIC(5,2);
    v_amount NUMERIC(12,2);
    v_gross NUMERIC(12,2);
    v_tax NUMERIC(12,2);
    v_net NUMERIC(12,2);
    v_start DATE;
    v_maturity DATE;
    v_elapsed INT;
    v_period_count INT;
    v_period INT;
    v_days INT;
    v_cumulative_days INT;
    v_gross_part NUMERIC(12,2);
    v_tax_part NUMERIC(12,2);
    v_gross_so_far NUMERIC(12,2);
    v_tax_so_far NUMERIC(12,2);
    v_number VARCHAR(30);
    v_hash VARCHAR(16);
    v_scenario RECORD;
    v_tx RECORD;
    v_declaration_id INT;
    v_transfer_out INT;
    v_transfer_in INT;
    v_timestamp TIMESTAMP;
BEGIN
    -- Resolve/create the cooperative by its natural key; names are not unique
    -- in the schema, so fail closed if the target name is ambiguous.
    INSERT INTO cooperativa (nombre, razon_social, correo, telefono, direccion, estado)
    SELECT 'Cooperativa de Prueba SI2', 'Cooperativa de Prueba SI2 Ltda.',
           'contacto@cooptest.bo', '+591 3 9876543',
           'Av. Testing #001, Santa Cruz', 'ACTIVO'
    WHERE NOT EXISTS (
        SELECT 1 FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2'
    );
    IF (SELECT count(*) FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2') <> 1 THEN
        RAISE EXCEPTION 'La cooperativa de demo no existe de forma unívoca';
    END IF;
    SELECT id INTO v_coop_id
    FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2';

    SELECT id INTO v_role_admin FROM rol WHERE nombre = 'ADMINISTRADOR' ORDER BY id LIMIT 1;
    SELECT id INTO v_role_socio FROM rol WHERE nombre = 'SOCIO' ORDER BY id LIMIT 1;
    SELECT id INTO v_role_cashier FROM rol WHERE nombre = 'CAJERO' ORDER BY id LIMIT 1;
    SELECT id INTO v_role_credit FROM rol WHERE nombre = 'OFICIAL_CREDITO' ORDER BY id LIMIT 1;
    SELECT id INTO v_bob_id FROM moneda WHERE codigo_iso = 'BOB';
    SELECT id INTO v_usd_id FROM moneda WHERE codigo_iso = 'USD';
    IF v_role_admin IS NULL OR v_role_socio IS NULL OR v_role_cashier IS NULL
       OR v_role_credit IS NULL OR v_bob_id IS NULL OR v_usd_id IS NULL THEN
        RAISE EXCEPTION 'Faltan roles o monedas requeridos por el seed';
    END IF;

    -- The only tenant assignment updates allowed by the task.
    UPDATE usuario u SET cooperativa_id = v_coop_id
    FROM rol r
    WHERE r.id = u.rol_id AND r.nombre <> 'SUPERADMIN'
      AND u.cooperativa_id IS NULL
      AND u.correo IN ('admin@gmail.com', 'acredito@cooperativa.com',
                       'rcontador@cooperativa.com', 'juan.perez@email.com');

    -- The base file supplies the password hash. admin@test.com is absent from
    -- bd.sql + migrations 002–012, but is explicitly required as the signed
    -- supervisor for the demo shortage; create it only when absent.
    INSERT INTO usuario (rol_id, cooperativa_id, nombre, contrasena, correo, estado)
    SELECT v_role_admin, v_coop_id, 'Administradora de Demo',
           '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2',
           'admin@test.com', 'ACTIVO'
    WHERE NOT EXISTS (SELECT 1 FROM usuario WHERE correo = 'admin@test.com');
    SELECT u.id INTO v_admin_id
    FROM usuario u JOIN rol r ON r.id = u.rol_id
    WHERE u.correo = 'admin@test.com' AND u.estado = 'ACTIVO'
      AND u.cooperativa_id = v_coop_id AND r.nombre = 'ADMINISTRADOR';
    IF v_admin_id IS NULL THEN
        RAISE EXCEPTION 'admin@test.com existe, pero no es administrador activo de la cooperativa demo';
    END IF;

    -- Two member logins are needed for the demo socios; never rewrite an
    -- existing account with either address.
    INSERT INTO usuario (rol_id, cooperativa_id, nombre, contrasena, correo, estado)
    SELECT v_role_socio, v_coop_id, 'Elena Flores',
           '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2',
           'demo.socia1@si2.test', 'ACTIVO'
    WHERE NOT EXISTS (SELECT 1 FROM usuario WHERE correo = 'demo.socia1@si2.test');
    INSERT INTO usuario (rol_id, cooperativa_id, nombre, contrasena, correo, estado)
    SELECT v_role_socio, v_coop_id, 'Marco Rojas',
           '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2',
           'demo.socio2@si2.test', 'ACTIVO'
    WHERE NOT EXISTS (SELECT 1 FROM usuario WHERE correo = 'demo.socio2@si2.test');

    FOR v_scenario IN
        SELECT * FROM (VALUES
            ('8877001 SC', 'Elena', 'Flores', 'ACTIVO',  'demo.socia1@si2.test'),
            ('8877002 SC', 'Marco', 'Rojas',  'ACTIVO',  'demo.socio2@si2.test'),
            ('8877003 LP', 'Ana',   'Quiroga','ACTIVO',  NULL),
            ('8877004 SC', 'Diego', 'Vargas', 'ACTIVO',  NULL),
            ('8877005 CB', 'Lucía', 'Choque', 'ACTIVO',  NULL),
            ('8877006 SC', 'Jorge', 'Salvatierra','ACTIVO',NULL),
            ('8877007 LP', 'Carla', 'Mendoza','ACTIVO',  NULL),
            ('8877008 SC', 'Pablo', 'Arce',   'INACTIVO',NULL)
        ) AS socios(ci, nombre, apellido, estado, correo)
    LOOP
        SELECT id INTO v_user_id FROM usuario WHERE correo = v_scenario.correo;
        IF NOT EXISTS (SELECT 1 FROM socio WHERE ci = v_scenario.ci) THEN
            INSERT INTO socio (cooperativa_id, ci, nombre, apellido, direccion,
                               telefono, correo, estado, fecha_registro, usuario_id)
            VALUES (v_coop_id, v_scenario.ci, v_scenario.nombre, v_scenario.apellido,
                    'Santa Cruz, Bolivia', '70000000', v_scenario.correo,
                    v_scenario.estado, CURRENT_DATE - 10, v_user_id);
        END IF;
        SELECT id INTO v_socio_id FROM socio WHERE ci = v_scenario.ci;
        IF v_socio_id IS NULL THEN
            RAISE EXCEPTION 'No se pudo resolver socio %', v_scenario.ci;
        END IF;
    END LOOP;

    SELECT id INTO v_cashier_id FROM usuario WHERE correo = 'ccajero@cooperativa.com';
    SELECT id INTO v_credit_id FROM usuario WHERE correo = 'acredito@cooperativa.com';
    IF v_cashier_id IS NULL OR v_credit_id IS NULL THEN
        RAISE EXCEPTION 'Falta el cajero u oficial de crédito base';
    END IF;

    -- Initialize/advance account sequence to at least the largest existing
    -- account suffix for this tenant, then consume one value only for a newly
    -- inserted natural-key account.
    INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
    SELECT v_coop_id::INT, 'CUENTA_AHORRO', COALESCE(MAX(substring(c.numero FROM
           ('^CA-' || lpad(v_coop_id::TEXT, 3, '0') || '-([0-9]{6})$'))::INT), 0) + 1
    FROM cuenta_ahorro c JOIN socio s ON s.id = c.socio_id
    WHERE s.cooperativa_id = v_coop_id
    ON CONFLICT (cooperativa_id, tipo) DO NOTHING;
    UPDATE secuencia_documento sd SET siguiente = GREATEST(sd.siguiente, COALESCE((
        SELECT MAX(substring(c.numero FROM
               ('^CA-' || lpad(v_coop_id::TEXT, 3, '0') || '-([0-9]{6})$'))::INT) + 1
        FROM cuenta_ahorro c JOIN socio s ON s.id = c.socio_id
        WHERE s.cooperativa_id = v_coop_id
    ), 1)) WHERE sd.cooperativa_id = v_coop_id::INT AND sd.tipo = 'CUENTA_AHORRO';

    FOR v_scenario IN
        SELECT * FROM (VALUES
            ('8877001 SC','BOB','VISTA',     5000.00,  70700.00),
            ('8877002 SC','BOB','VISTA',     5000.00,   5500.00),
            ('8877003 LP','BOB','PROGRAMADO',2000.00,  82000.00),
            ('8877004 SC','USD','VISTA',     1500.00,   1100.00),
            ('8877005 CB','USD','PROGRAMADO',1200.00,   1200.12),
            ('8877006 SC','BOB','VISTA',     3000.00,   3020.00),
            ('8877007 LP','BOB','PROGRAMADO',3000.00,   2000.00)
        ) AS accounts(ci, currency, product, opening_balance, final_balance)
    LOOP
        SELECT id INTO v_socio_id FROM socio WHERE ci = v_scenario.ci;
        SELECT id INTO v_account_id FROM cuenta_ahorro c
        WHERE c.socio_id = v_socio_id
          AND c.moneda_id = (SELECT id FROM moneda WHERE codigo_iso = v_scenario.currency)
          AND c.tipo_producto = v_scenario.product;
        IF v_account_id IS NULL THEN
            UPDATE secuencia_documento SET siguiente = siguiente + 1
            WHERE cooperativa_id = v_coop_id::INT AND tipo = 'CUENTA_AHORRO'
            RETURNING siguiente - 1 INTO v_seq;
            v_number := 'CA-' || lpad(v_coop_id::TEXT, 3, '0') || '-' || lpad(v_seq::TEXT, 6, '0');
            INSERT INTO cuenta_ahorro (numero, tipo_producto, saldo_disponible,
                                       saldo_bloqueado, estado, fecha_registro,
                                       socio_id, moneda_id)
            SELECT v_number, v_scenario.product, v_scenario.final_balance,
                   0, 'ACTIVA', CURRENT_DATE - 5, v_socio_id,
                   m.id FROM moneda m WHERE m.codigo_iso = v_scenario.currency
              AND NOT EXISTS (SELECT 1 FROM cuenta_ahorro WHERE numero = v_number);
            SELECT id INTO v_account_id FROM cuenta_ahorro WHERE numero = v_number;
        END IF;
        IF v_account_id IS NULL THEN
            RAISE EXCEPTION 'No se pudo resolver la cuenta demo de %', v_scenario.ci;
        END IF;
        v_timestamp := (CURRENT_DATE - 5)::TIMESTAMP + TIME '09:00';
        IF NOT EXISTS (SELECT 1 FROM transaccion WHERE tipo = 'APERTURA'
                       AND cuenta_ahorro_id = v_account_id AND canal = 'WEB'
                       AND monto = v_scenario.opening_balance) THEN
            INSERT INTO transaccion (tipo, monto, canal, fecha_hora, moneda_id, cuenta_ahorro_id)
            SELECT 'APERTURA', v_scenario.opening_balance, 'WEB', v_timestamp,
                   c.moneda_id, c.id FROM cuenta_ahorro c WHERE c.id = v_account_id;
        END IF;
    END LOOP;

    -- Demo boxes are inserted closed. Existing caja/control_caja rows are not
    -- updated or reused if an unrelated name happens to exist.
    INSERT INTO caja (nombre, estado, cooperativa_id, monto_maximo_efectivo,
                      umbral_diferencia_arqueo)
    SELECT 'Ventanilla 3 - Plan 3000', 'CERRADA', v_coop_id, 50000.00, 50.00
    WHERE NOT EXISTS (SELECT 1 FROM caja WHERE cooperativa_id = v_coop_id
                      AND nombre = 'Ventanilla 3 - Plan 3000');
    INSERT INTO caja (nombre, estado, cooperativa_id, monto_maximo_efectivo,
                      umbral_diferencia_arqueo)
    SELECT 'Ventanilla 4 - Equipetrol', 'CERRADA', v_coop_id, 50000.00, 50.00
    WHERE NOT EXISTS (SELECT 1 FROM caja WHERE cooperativa_id = v_coop_id
                      AND nombre = 'Ventanilla 4 - Equipetrol');
    SELECT id INTO v_caja1_id FROM caja WHERE cooperativa_id = v_coop_id
      AND nombre = 'Ventanilla 3 - Plan 3000' ORDER BY id LIMIT 1;
    SELECT id INTO v_caja2_id FROM caja WHERE cooperativa_id = v_coop_id
      AND nombre = 'Ventanilla 4 - Equipetrol' ORDER BY id LIMIT 1;
    IF v_caja1_id IS NULL OR v_caja2_id IS NULL THEN
        RAISE EXCEPTION 'No se pudieron resolver las cajas demo';
    END IF;

    -- Stable close observations are natural seed markers. Their controls are
    -- inserted as closed and never alter the pre-existing open session.
    IF NOT EXISTS (SELECT 1 FROM cierre_caja WHERE observacion = 'Seed demo SI2: cierre turno 1') THEN
        INSERT INTO control_caja (monto_apertura, monto_cierre, saldo_sistema,
                                  fecha_apertura, fecha_cierre, estado, caja_id, usuario_id)
        VALUES (1000.00, 150000.00, 150000.00,
                (CURRENT_DATE - 2)::TIMESTAMP + TIME '08:00',
                (CURRENT_DATE - 2)::TIMESTAMP + TIME '11:05',
                'CERRADA', v_caja1_id, v_cashier_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cierre_caja WHERE observacion = 'Seed demo SI2: cierre turno 2') THEN
        INSERT INTO control_caja (monto_apertura, monto_cierre, saldo_sistema,
                                  fecha_apertura, fecha_cierre, estado, caja_id, usuario_id)
        VALUES (500.00, 600.00, 700.00,
                (CURRENT_DATE - 1)::TIMESTAMP + TIME '08:00',
                (CURRENT_DATE - 1)::TIMESTAMP + TIME '11:05',
                'CERRADA', v_caja2_id, v_cashier_id);
    END IF;
    SELECT cc.id INTO v_control1_id FROM control_caja cc
    JOIN caja c ON c.id = cc.caja_id
    JOIN cierre_caja cl ON cl.control_caja_id = cc.id
    WHERE c.id = v_caja1_id AND cl.observacion = 'Seed demo SI2: cierre turno 1';
    SELECT cc.id INTO v_control2_id FROM control_caja cc
    JOIN caja c ON c.id = cc.caja_id
    JOIN cierre_caja cl ON cl.control_caja_id = cc.id
    WHERE c.id = v_caja2_id AND cl.observacion = 'Seed demo SI2: cierre turno 2';
    -- On the first run the closing rows do not exist yet; resolve the two new
    -- controls by their stable cashier/caja/opening-time marker.
    IF v_control1_id IS NULL THEN
        SELECT id INTO v_control1_id FROM control_caja
        WHERE caja_id = v_caja1_id AND usuario_id = v_cashier_id
          AND fecha_apertura::TIME = TIME '08:00'
        ORDER BY id DESC LIMIT 1;
    END IF;
    IF v_control2_id IS NULL THEN
        SELECT id INTO v_control2_id FROM control_caja
        WHERE caja_id = v_caja2_id AND usuario_id = v_cashier_id
          AND fecha_apertura::TIME = TIME '08:00'
        ORDER BY id DESC LIMIT 1;
    END IF;
    IF v_control1_id IS NULL OR v_control2_id IS NULL THEN
        RAISE EXCEPTION 'No se pudieron resolver los controles de caja demo';
    END IF;

    -- UIF declaration for the threshold deposit and the same-day structured
    -- second 40,000 BOB deposit. Natural-key guards avoid duplicate records.
    SELECT id INTO v_socio_id FROM socio WHERE ci = '8877001 SC';
    IF NOT EXISTS (SELECT 1 FROM declaracion_jurada_uif
                   WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id
                     AND tipo_operacion = 'DEPOSITO' AND monto = 70000.00
                     AND moneda_id = v_bob_id AND fraccionada = false
                     AND origen = 'ACTIVIDAD_COMERCIAL' AND destino = 'AHORRO') THEN
        INSERT INTO declaracion_jurada_uif
            (origen, destino, tipo_operacion, monto, moneda_id, socio_id,
             realizado_por, actividad_economica, origen_detalle, destino_detalle,
             declara_bajo_juramento, fraccionada, usuario_id, cooperativa_id, fecha)
        VALUES ('ACTIVIDAD_COMERCIAL', 'AHORRO', 'DEPOSITO', 70000.00, v_bob_id,
                v_socio_id, 'TITULAR', 'Comercio minorista',
                'Ingresos por venta de abarrotes', 'Ahorro en cuenta', true, false,
                v_cashier_id, v_coop_id, (CURRENT_DATE - 2)::TIMESTAMP + TIME '09:15');
    END IF;
    SELECT id INTO v_declaration_id FROM declaracion_jurada_uif
    WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id
      AND tipo_operacion = 'DEPOSITO' AND monto = 70000.00
      AND moneda_id = v_bob_id AND fraccionada = false
      AND origen = 'ACTIVIDAD_COMERCIAL' AND destino = 'AHORRO'
    ORDER BY id LIMIT 1;
    IF v_declaration_id IS NULL THEN RAISE EXCEPTION 'Falta declaración UIF de umbral'; END IF;

    SELECT id INTO v_socio_id FROM socio WHERE ci = '8877003 LP';
    IF NOT EXISTS (SELECT 1 FROM declaracion_jurada_uif
                   WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id
                     AND tipo_operacion = 'DEPOSITO' AND monto = 40000.00
                     AND moneda_id = v_bob_id AND fraccionada = true
                     AND origen = 'ACTIVIDAD_COMERCIAL' AND destino = 'AHORRO') THEN
        INSERT INTO declaracion_jurada_uif
            (origen, destino, tipo_operacion, monto, moneda_id, socio_id,
             realizado_por, actividad_economica, origen_detalle, destino_detalle,
             declara_bajo_juramento, fraccionada, usuario_id, cooperativa_id, fecha)
        VALUES ('ACTIVIDAD_COMERCIAL', 'AHORRO', 'DEPOSITO', 40000.00, v_bob_id,
                v_socio_id, 'TITULAR', 'Comercio minorista',
                'Acumulación de ventas del día', 'Ahorro en cuenta', true, true,
                v_cashier_id, v_coop_id, (CURRENT_DATE - 2)::TIMESTAMP + TIME '09:05');
    END IF;
    SELECT id INTO v_declaration_id FROM declaracion_jurada_uif
    WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id
      AND tipo_operacion = 'DEPOSITO' AND monto = 40000.00
      AND moneda_id = v_bob_id AND fraccionada = true
      AND origen = 'ACTIVIDAD_COMERCIAL' AND destino = 'AHORRO'
    ORDER BY id LIMIT 1;
    IF v_declaration_id IS NULL THEN RAISE EXCEPTION 'Falta declaración UIF fraccionada'; END IF;

    -- Cash movements. Their timestamps precede the matching arqueo.
    FOR v_tx IN
        SELECT * FROM (VALUES
            (1, 'DEPOSITO', 70000.00, '8877001 SC', (CURRENT_DATE - 2)::TIMESTAMP + TIME '09:15', 'Elena Flores', '8877001 SC', false),
            (1, 'DEPOSITO', 40000.00, '8877003 LP', (CURRENT_DATE - 2)::TIMESTAMP + TIME '09:00', 'Ana Quiroga', '8877003 LP', false),
            (1, 'DEPOSITO', 40000.00, '8877003 LP', (CURRENT_DATE - 2)::TIMESTAMP + TIME '09:05', 'Ana Quiroga', '8877003 LP', true),
            (1, 'RETIRO',    1000.00, '8877001 SC', (CURRENT_DATE - 2)::TIMESTAMP + TIME '09:30', 'Elena Flores', '8877001 SC', false),
            (2, 'DEPOSITO',   500.00, '8877002 SC', (CURRENT_DATE - 1)::TIMESTAMP + TIME '09:10', 'Marco Rojas', '8877002 SC', false),
            (2, 'RETIRO',     300.00, '8877002 SC', (CURRENT_DATE - 1)::TIMESTAMP + TIME '09:20', 'Carlos Apoderado', '7000111 SC', false),
            (2, 'DEPOSITO',   100.00, '8877004 SC', (CURRENT_DATE - 1)::TIMESTAMP + TIME '09:25', 'Diego Vargas', '8877004 SC', false)
        ) AS movements(session_no, kind, amount, ci, happened, person, ci_person, uif)
    LOOP
        SELECT id INTO v_socio_id FROM socio WHERE ci = v_tx.ci;
        SELECT id INTO v_account_id FROM cuenta_ahorro WHERE socio_id = v_socio_id
        ORDER BY id LIMIT 1;
        IF v_tx.session_no = 1 AND v_tx.kind = 'DEPOSITO' AND v_tx.amount = 70000.00 THEN
            SELECT id INTO v_declaration_id FROM declaracion_jurada_uif
            WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id
              AND tipo_operacion = 'DEPOSITO' AND monto = 70000.00 AND moneda_id = v_bob_id
              AND fraccionada = false ORDER BY id LIMIT 1;
        ELSIF v_tx.session_no = 1 AND v_tx.kind = 'DEPOSITO' AND v_tx.amount = 40000.00 AND v_tx.uif THEN
            SELECT id INTO v_declaration_id FROM declaracion_jurada_uif
            WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id
              AND tipo_operacion = 'DEPOSITO' AND monto = 40000.00 AND moneda_id = v_bob_id
              AND fraccionada = true ORDER BY id LIMIT 1;
        ELSE
            v_declaration_id := NULL;
        END IF;
        SELECT (fecha_apertura::DATE::TIMESTAMP + v_tx.happened::TIME)
        INTO v_timestamp FROM control_caja
        WHERE id = CASE WHEN v_tx.session_no = 1 THEN v_control1_id ELSE v_control2_id END;
        IF NOT EXISTS (
            SELECT 1 FROM transaccion t
            WHERE t.control_caja_id = CASE WHEN v_tx.session_no = 1 THEN v_control1_id ELSE v_control2_id END
              AND t.tipo = v_tx.kind AND t.monto = v_tx.amount
              AND t.canal = 'VENTANILLA' AND t.cuenta_ahorro_id = v_account_id
              AND t.fecha_hora = v_timestamp
        ) THEN
            INSERT INTO transaccion (tipo, monto, canal, fecha_hora, control_caja_id,
                                     moneda_id, cuenta_ahorro_id, depositante_nombre,
                                     depositante_ci, retirante_tipo, retirante_nombre,
                                     retirante_ci, declaracion_jurada_uif_id)
            SELECT v_tx.kind, v_tx.amount, 'VENTANILLA', v_timestamp,
                   CASE WHEN v_tx.session_no = 1 THEN v_control1_id ELSE v_control2_id END,
                   c.moneda_id, c.id,
                   CASE WHEN v_tx.kind = 'DEPOSITO' THEN v_tx.person END,
                   CASE WHEN v_tx.kind = 'DEPOSITO' THEN v_tx.ci_person END,
                   CASE WHEN v_tx.kind = 'RETIRO' AND v_tx.ci_person = v_tx.ci THEN 'TITULAR'
                        WHEN v_tx.kind = 'RETIRO' THEN 'APODERADO' END,
                   CASE WHEN v_tx.kind = 'RETIRO' THEN v_tx.person END,
                   CASE WHEN v_tx.kind = 'RETIRO' THEN v_tx.ci_person END,
                   v_declaration_id
            FROM cuenta_ahorro c WHERE c.id = v_account_id;
        END IF;
    END LOOP;

    -- A ventanilla transfer is represented by both linked ledger legs. The
    -- IDs are allocated dynamically from the table identity sequence.
    SELECT c.id INTO v_account_id FROM cuenta_ahorro c JOIN socio s ON s.id = c.socio_id
    WHERE s.ci = '8877001 SC' AND c.moneda_id = v_bob_id;
    SELECT id INTO v_socio_id FROM socio WHERE ci = '8877002 SC';
    IF NOT EXISTS (SELECT 1 FROM transaccion WHERE control_caja_id = v_control1_id
                   AND tipo = 'TRANSFERENCIA_SALIDA' AND cuenta_ahorro_id = v_account_id
                   AND monto = 300.00 AND canal = 'VENTANILLA'
                   AND fecha_hora = (SELECT fecha_apertura::DATE::TIMESTAMP + TIME '10:00'
                                     FROM control_caja WHERE id=v_control1_id))
       AND NOT EXISTS (SELECT 1 FROM transaccion WHERE control_caja_id = v_control1_id
                   AND tipo = 'TRANSFERENCIA_ENTRADA' AND cuenta_ahorro_id =
                       (SELECT id FROM cuenta_ahorro WHERE socio_id = v_socio_id AND moneda_id = v_bob_id)
                   AND monto = 300.00 AND canal = 'VENTANILLA'
                   AND fecha_hora = (SELECT fecha_apertura::DATE::TIMESTAMP + TIME '10:00'
                                     FROM control_caja WHERE id=v_control1_id)) THEN
        v_transfer_out := nextval(pg_get_serial_sequence('transaccion','id'))::INT;
        v_transfer_in := nextval(pg_get_serial_sequence('transaccion','id'))::INT;
        SELECT fecha_apertura::DATE::TIMESTAMP + TIME '10:00' INTO v_timestamp
        FROM control_caja WHERE id=v_control1_id;
        INSERT INTO transaccion (id, tipo, monto, canal, fecha_hora, control_caja_id,
                                 moneda_id, cuenta_ahorro_id, transaccion_contraparte_id)
        OVERRIDING SYSTEM VALUE
        VALUES (v_transfer_out, 'TRANSFERENCIA_SALIDA', 300.00, 'VENTANILLA',
                v_timestamp, v_control1_id,
                v_bob_id, v_account_id, v_transfer_in),
               (v_transfer_in, 'TRANSFERENCIA_ENTRADA', 300.00, 'VENTANILLA',
                v_timestamp, v_control1_id, v_bob_id,
                (SELECT id FROM cuenta_ahorro WHERE socio_id = v_socio_id AND moneda_id = v_bob_id),
                v_transfer_out);
    END IF;

    -- DPF correlation starts at the current tenant sequence and remains
    -- stable because each scenario is looked up by its socio/product tuple.
    INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
    SELECT v_coop_id::INT, 'DPF', COALESCE(MAX(substring(d.numero_certificado FROM '^DPF-([0-9]{6})$')::INT),0)+1
    FROM deposito_plazo_fijo d WHERE d.cooperativa_id = v_coop_id
    ON CONFLICT (cooperativa_id, tipo) DO NOTHING;
    UPDATE secuencia_documento sd SET siguiente = GREATEST(sd.siguiente, COALESCE((
        SELECT MAX(substring(d.numero_certificado FROM '^DPF-([0-9]{6})$')::INT)+1
        FROM deposito_plazo_fijo d WHERE d.cooperativa_id = v_coop_id
    ),1)) WHERE sd.cooperativa_id = v_coop_id::INT AND sd.tipo = 'DPF';

    FOR v_scenario IN
        SELECT * FROM (VALUES
            ('8877001 SC','BOB',1000.00,360,  0,'VIGENTE','VENCIMIENTO','CUENTA'),
            ('8877001 SC','BOB',2000.00, 90,-87,'VIGENTE','MENSUAL',    'CUENTA'),
            ('8877004 SC','USD', 500.00, 90,-95,'VIGENTE','VENCIMIENTO','CUENTA'),
            ('8877006 SC','BOB',1000.00,180,-200,'LIQUIDADO','VENCIMIENTO','CUENTA'),
            ('8877007 LP','BOB',1000.00, 60,-70,'RENOVADO','VENCIMIENTO','CUENTA'),
            ('8877005 CB','USD', 500.00, 60,-10,'CANCELADO','VENCIMIENTO','CUENTA')
        ) AS dpfs(ci, currency, amount, term, start_offset, state, modality, funding)
    LOOP
        SELECT s.id INTO v_socio_id FROM socio s WHERE s.ci = v_scenario.ci;
        SELECT c.id INTO v_account_id FROM cuenta_ahorro c JOIN moneda m ON m.id = c.moneda_id
        WHERE c.socio_id = v_socio_id AND m.codigo_iso = v_scenario.currency
        ORDER BY c.id LIMIT 1;
        v_amount := v_scenario.amount;
        IF NOT EXISTS (SELECT 1 FROM deposito_plazo_fijo d
                       WHERE d.cooperativa_id = v_coop_id AND d.socio_id = v_socio_id
                         AND d.moneda_id = (SELECT id FROM moneda WHERE codigo_iso = v_scenario.currency)
                         AND d.monto = v_amount AND d.plazo_dias = v_scenario.term
                         AND d.estado = v_scenario.state AND d.origen_fondos = v_scenario.funding
                         AND d.cuenta_origen_id = v_account_id AND d.dpf_origen_id IS NULL) THEN
            SELECT t.tna INTO v_rate FROM tasa_dpf t
            WHERE t.cooperativa_id = v_coop_id
              AND t.moneda_id = (SELECT id FROM moneda WHERE codigo_iso = v_scenario.currency)
              AND t.plazo_min_dias <= v_scenario.term
              AND (t.plazo_max_dias IS NULL OR t.plazo_max_dias >= v_scenario.term)
            ORDER BY t.plazo_min_dias DESC LIMIT 1;
            IF v_rate IS NULL THEN RAISE EXCEPTION 'No hay banda DPF para % días / %', v_scenario.term, v_scenario.currency; END IF;
            v_start := CURRENT_DATE + v_scenario.start_offset;
            v_maturity := v_start + v_scenario.term;
            v_gross := round(v_amount * v_rate * v_scenario.term / 36000.0, 2);
            IF v_scenario.currency = 'BOB' AND v_scenario.term >= 30 THEN
                v_tax := 0.00;
            ELSE
                v_tax := round(v_gross * 0.13, 2);
            END IF;
            v_net := v_gross - v_tax;
            UPDATE secuencia_documento SET siguiente = siguiente + 1
            WHERE cooperativa_id = v_coop_id::INT AND tipo = 'DPF'
            RETURNING siguiente - 1 INTO v_seq;
            v_number := 'DPF-' || lpad(v_seq::TEXT, 6, '0');
            SELECT left(encode(digest(concat_ws('|', v_number, s.ci,
                to_char(v_amount,'FM999999999999990.00'), v_scenario.currency,
                to_char(v_start,'YYYY-MM-DD'), to_char(v_maturity,'YYYY-MM-DD'),
                to_char(v_rate,'FM999999990.00')), 'sha256'),'hex'),16)
            INTO v_hash FROM socio s WHERE s.id = v_socio_id;
            INSERT INTO deposito_plazo_fijo
                (monto, tasa_interes_anual, plazo_dias, fecha_inicio, fecha_vencimiento,
                 interes_calculado, estado, socio_id, moneda_id, numero_certificado,
                 modalidad_pago_interes, interes_bruto, retencion_rciva, interes_neto,
                 origen_fondos, cuenta_origen_id, cuenta_abono_id, codigo_verificacion,
                 usuario_id, cooperativa_id)
            SELECT v_amount, v_rate, v_scenario.term, v_start, v_maturity, v_gross,
                   v_scenario.state, v_socio_id, m.id, v_number, v_scenario.modality,
                   v_gross, v_tax, v_net, v_scenario.funding, v_account_id,
                   v_account_id, v_hash, v_admin_id, v_coop_id
            FROM moneda m WHERE m.codigo_iso = v_scenario.currency
              AND NOT EXISTS (SELECT 1 FROM deposito_plazo_fijo WHERE numero_certificado = v_number);
        END IF;
        SELECT id INTO v_dpf_id FROM deposito_plazo_fijo d
        WHERE d.cooperativa_id = v_coop_id AND d.socio_id = v_socio_id
          AND d.moneda_id = (SELECT id FROM moneda WHERE codigo_iso = v_scenario.currency)
          AND d.monto = v_amount AND d.plazo_dias = v_scenario.term
          AND d.estado = v_scenario.state AND d.origen_fondos = v_scenario.funding
          AND d.cuenta_origen_id = v_account_id AND d.dpf_origen_id IS NULL
        ORDER BY d.id LIMIT 1;
        IF v_dpf_id IS NULL THEN RAISE EXCEPTION 'No se pudo resolver DPF demo de %', v_scenario.ci; END IF;

        -- Seed the issue debit for every account-funded DPF.
        IF NOT EXISTS (SELECT 1 FROM transaccion WHERE deposito_plazo_fijo_id = v_dpf_id
                       AND tipo = 'RETIRO' AND canal = 'WEB' AND cuenta_ahorro_id = v_account_id
                       AND liquidacion_id IS NULL) THEN
            INSERT INTO transaccion (tipo, monto, canal, fecha_hora, moneda_id,
                                     cuenta_ahorro_id, deposito_plazo_fijo_id)
            SELECT 'RETIRO', v_amount, 'WEB', d.fecha_inicio::TIMESTAMP + TIME '12:00',
                   d.moneda_id, v_account_id, d.id
            FROM deposito_plazo_fijo d WHERE d.id = v_dpf_id;
        END IF;

        -- Build the same rounded installment schedule as the application.
        SELECT d.tasa_interes_anual, d.monto, d.plazo_dias, d.fecha_inicio,
               d.interes_bruto, d.retencion_rciva
        INTO v_rate, v_amount, v_elapsed, v_start, v_gross, v_tax
        FROM deposito_plazo_fijo d WHERE d.id = v_dpf_id;
        v_period_count := CASE WHEN v_scenario.modality = 'MENSUAL'
                               THEN ceil(v_elapsed / 30.0)::INT ELSE 1 END;
        v_period := 0; v_cumulative_days := 0;
        v_gross_so_far := 0; v_tax_so_far := 0;
        WHILE v_period < v_period_count LOOP
            v_period := v_period + 1;
            v_days := CASE WHEN v_scenario.modality = 'MENSUAL'
                           THEN LEAST(30, v_elapsed - v_cumulative_days) ELSE v_elapsed END;
            v_cumulative_days := v_cumulative_days + v_days;
            IF v_period = v_period_count THEN
                v_gross_part := v_gross - v_gross_so_far;
                v_tax_part := v_tax - v_tax_so_far;
            ELSE
                v_gross_part := round(v_amount * v_rate * v_days / 36000.0, 2);
                IF v_scenario.currency = 'BOB' AND v_elapsed >= 30 THEN
                    v_tax_part := 0;
                ELSE
                    v_tax_part := round(v_gross_part * 0.13, 2);
                END IF;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM dpf_cronograma WHERE deposito_plazo_fijo_id = v_dpf_id AND numero = v_period) THEN
                INSERT INTO dpf_cronograma (deposito_plazo_fijo_id, numero, fecha_pago, dias,
                                            interes_bruto, retencion_rciva, interes_neto, estado)
                VALUES (v_dpf_id, v_period, v_start + v_cumulative_days, v_days,
                        v_gross_part, v_tax_part, v_gross_part - v_tax_part, 'PENDIENTE');
            END IF;
            v_gross_so_far := v_gross_so_far + v_gross_part;
            v_tax_so_far := v_tax_so_far + v_tax_part;
        END LOOP;
    END LOOP;

    -- A new DPF created by renewal is linked to the stable RENOVADO origin.
    SELECT id INTO v_socio_id FROM socio WHERE ci = '8877007 LP';
    SELECT id INTO v_account_id FROM cuenta_ahorro WHERE socio_id = v_socio_id AND moneda_id = v_bob_id;
    SELECT id INTO v_dpf_old_id FROM deposito_plazo_fijo
    WHERE cooperativa_id = v_coop_id AND socio_id = v_socio_id AND monto = 1000.00
      AND plazo_dias = 60 AND estado = 'RENOVADO' AND dpf_origen_id IS NULL
    ORDER BY id LIMIT 1;
    IF v_dpf_old_id IS NULL THEN RAISE EXCEPTION 'Falta el DPF origen renovado'; END IF;
    IF NOT EXISTS (SELECT 1 FROM deposito_plazo_fijo WHERE dpf_origen_id = v_dpf_old_id) THEN
        SELECT t.tna INTO v_rate FROM tasa_dpf t
        WHERE t.cooperativa_id = v_coop_id AND t.moneda_id = v_bob_id
          AND t.plazo_min_dias <= 360 AND (t.plazo_max_dias IS NULL OR t.plazo_max_dias >= 360)
        ORDER BY t.plazo_min_dias DESC LIMIT 1;
        v_amount := 1004.17;
        v_start := CURRENT_DATE; v_maturity := v_start + 360;
        v_gross := round(v_amount * v_rate * 360 / 36000.0, 2);
        v_tax := 0; v_net := v_gross;
        UPDATE secuencia_documento SET siguiente = siguiente + 1
        WHERE cooperativa_id = v_coop_id::INT AND tipo = 'DPF'
        RETURNING siguiente - 1 INTO v_seq;
        v_number := 'DPF-' || lpad(v_seq::TEXT, 6, '0');
        SELECT left(encode(digest(concat_ws('|', v_number, s.ci,
            to_char(v_amount,'FM999999999999990.00'), 'BOB', to_char(v_start,'YYYY-MM-DD'),
            to_char(v_maturity,'YYYY-MM-DD'), to_char(v_rate,'FM999999990.00')),'sha256'),'hex'),16)
        INTO v_hash FROM socio s WHERE s.id = v_socio_id;
        INSERT INTO deposito_plazo_fijo
            (monto, tasa_interes_anual, plazo_dias, fecha_inicio, fecha_vencimiento,
             interes_calculado, estado, socio_id, moneda_id, numero_certificado,
             modalidad_pago_interes, interes_bruto, retencion_rciva, interes_neto,
             origen_fondos, cuenta_origen_id, cuenta_abono_id, codigo_verificacion,
             dpf_origen_id, usuario_id, cooperativa_id)
        VALUES (v_amount, v_rate, 360, v_start, v_maturity, v_gross, 'VIGENTE',
                v_socio_id, v_bob_id, v_number, 'VENCIMIENTO', v_gross, v_tax,
                v_net, 'CUENTA', v_account_id, v_account_id, v_hash,
                v_dpf_old_id, v_admin_id, v_coop_id);
    END IF;
    SELECT id INTO v_dpf_new_id FROM deposito_plazo_fijo WHERE dpf_origen_id = v_dpf_old_id ORDER BY id LIMIT 1;
    IF v_dpf_new_id IS NULL THEN RAISE EXCEPTION 'No se pudo resolver el nuevo DPF renovado'; END IF;

    -- Add the renewal's single maturity schedule.
    SELECT tasa_interes_anual, monto, fecha_inicio, interes_bruto, retencion_rciva
    INTO v_rate, v_amount, v_start, v_gross, v_tax FROM deposito_plazo_fijo WHERE id = v_dpf_new_id;
    IF NOT EXISTS (SELECT 1 FROM dpf_cronograma WHERE deposito_plazo_fijo_id = v_dpf_new_id AND numero = 1) THEN
        INSERT INTO dpf_cronograma (deposito_plazo_fijo_id, numero, fecha_pago, dias,
                                    interes_bruto, retencion_rciva, interes_neto, estado)
        VALUES (v_dpf_new_id, 1, v_start + 360, 360, v_gross, v_tax, v_gross-v_tax, 'PENDIENTE');
    END IF;
    -- Stable DPF liquidations. Unique deposito_plazo_fijo_id is used only for
    -- the actual schema constraint; the transaction rows are guarded by their
    -- logical DPF/liquidation/type signature.
    FOR v_scenario IN
        SELECT d.id, d.estado, d.monto, d.moneda_id, d.cuenta_abono_id,
               d.fecha_vencimiento, d.fecha_inicio, d.plazo_dias,
               d.tasa_interes_anual, d.interes_bruto, d.retencion_rciva,
               d.interes_neto, m.codigo_iso
        FROM deposito_plazo_fijo d JOIN moneda m ON m.id=d.moneda_id
        WHERE d.cooperativa_id=v_coop_id AND (
            (d.socio_id=(SELECT id FROM socio WHERE ci='8877006 SC') AND d.estado='LIQUIDADO' AND d.plazo_dias=180)
            OR (d.socio_id=(SELECT id FROM socio WHERE ci='8877005 CB') AND d.estado='CANCELADO' AND d.plazo_dias=60)
            OR (d.id=v_dpf_old_id AND d.estado='RENOVADO')
        )
    LOOP
        v_dpf_id := v_scenario.id;
        IF v_scenario.estado = 'CANCELADO' THEN
            v_elapsed := greatest(0, (CURRENT_DATE - v_scenario.fecha_inicio));
            v_gross := round(v_scenario.monto * 1.00 * v_elapsed / 36000.0, 2);
            IF v_scenario.codigo_iso = 'BOB' AND v_elapsed >= 30 THEN v_tax := 0;
            ELSE v_tax := round(v_gross * 0.13, 2); END IF;
            v_net := v_gross-v_tax;
            v_timestamp := CURRENT_DATE::TIMESTAMP + TIME '10:30';
        ELSE
            v_gross := v_scenario.interes_bruto;
            v_tax := v_scenario.retencion_rciva;
            v_net := v_scenario.interes_neto;
            v_timestamp := v_scenario.fecha_vencimiento::TIMESTAMP + TIME '12:00';
        END IF;
        INSERT INTO liquidacion (monto_capital_retornado, monto_interes_pagado,
                                 tipo_operacion, fecha, deposito_plazo_fijo_id,
                                 tipo, retencion_rciva, usuario_id, cuenta_abono_id,
                                 dpf_renovado_id)
        SELECT v_scenario.monto, v_net,
               CASE v_scenario.estado WHEN 'LIQUIDADO' THEN 'LIQUIDACION'
                    WHEN 'CANCELADO' THEN 'CANCELACION' ELSE 'RENOVACION' END,
               v_timestamp, v_dpf_id,
               CASE v_scenario.estado WHEN 'LIQUIDADO' THEN 'LIQUIDACION'
                    WHEN 'CANCELADO' THEN 'CANCELACION' ELSE 'RENOVACION' END,
               v_tax, v_admin_id, v_scenario.cuenta_abono_id,
               CASE WHEN v_scenario.estado='RENOVADO' THEN v_dpf_new_id END
        WHERE NOT EXISTS (SELECT 1 FROM liquidacion WHERE deposito_plazo_fijo_id=v_dpf_id);
        SELECT id INTO v_liquidacion_id FROM liquidacion WHERE deposito_plazo_fijo_id=v_dpf_id;
        IF v_liquidacion_id IS NULL THEN RAISE EXCEPTION 'No se pudo resolver liquidación DPF %', v_dpf_id; END IF;
        IF NOT EXISTS (SELECT 1 FROM transaccion WHERE liquidacion_id=v_liquidacion_id
                       AND deposito_plazo_fijo_id=v_dpf_id AND tipo='DEPOSITO' AND canal='WEB') THEN
            INSERT INTO transaccion (tipo, monto, canal, fecha_hora, moneda_id,
                                     cuenta_ahorro_id, deposito_plazo_fijo_id, liquidacion_id)
            VALUES ('DEPOSITO', v_scenario.monto+v_net, 'WEB', v_timestamp,
                    v_scenario.moneda_id, v_scenario.cuenta_abono_id,
                    v_dpf_id, v_liquidacion_id);
        END IF;
        IF v_scenario.estado = 'RENOVADO'
           AND NOT EXISTS (SELECT 1 FROM transaccion WHERE liquidacion_id=v_liquidacion_id
                           AND deposito_plazo_fijo_id=v_dpf_new_id
                           AND tipo='RETIRO' AND canal='WEB') THEN
            INSERT INTO transaccion (tipo, monto, canal, fecha_hora, moneda_id,
                                     cuenta_ahorro_id, deposito_plazo_fijo_id, liquidacion_id)
            SELECT 'RETIRO', d.monto, 'WEB', d.fecha_inicio::TIMESTAMP + TIME '12:00', d.moneda_id,
                   d.cuenta_origen_id, d.id, v_liquidacion_id
            FROM deposito_plazo_fijo d WHERE d.id=v_dpf_new_id;
        END IF;
    END LOOP;

    -- Arqueo 1 is exactly balanced (BOB 150,000); arqueo 2 has a BOB 100
    -- shortage, authorized by admin@test.com, while USD balances exactly.
    SELECT id INTO v_arqueo1_id FROM arqueo_caja
    WHERE control_caja_id=v_control1_id AND observacion='Seed demo SI2: arqueo turno 1';
    IF v_arqueo1_id IS NULL THEN
        INSERT INTO arqueo_caja (control_caja_id, usuario_id, fecha, cierre,
                                 requiere_supervisor, observacion)
        VALUES (v_control1_id, v_cashier_id, (CURRENT_DATE-2)::TIMESTAMPTZ + TIME '11:00',
                false, false, 'Seed demo SI2: arqueo turno 1') RETURNING id INTO v_arqueo1_id;
        INSERT INTO arqueo_caja_moneda (arqueo_id, moneda_id, saldo_teorico,
                                        total_contado, diferencia, resultado)
        VALUES (v_arqueo1_id, v_bob_id, 150000.00, 150000.00, 0.00, 'CUADRADO');
        SELECT id INTO v_account_id FROM arqueo_caja_moneda WHERE arqueo_id=v_arqueo1_id AND moneda_id=v_bob_id;
        INSERT INTO arqueo_caja_detalle (arqueo_moneda_id, tipo, denominacion, cantidad, subtotal)
        VALUES (v_account_id, 'BILLETE', 200.00, 750, 150000.00);
    END IF;

    SELECT id INTO v_arqueo2_id FROM arqueo_caja
    WHERE control_caja_id=v_control2_id AND observacion='Seed demo SI2: arqueo turno 2';
    IF v_arqueo2_id IS NULL THEN
        INSERT INTO arqueo_caja (control_caja_id, usuario_id, supervisor_id, fecha,
                                 fecha_autorizacion, cierre, requiere_supervisor, observacion)
        VALUES (v_control2_id, v_cashier_id, v_admin_id,
                (CURRENT_DATE-1)::TIMESTAMPTZ + TIME '11:00',
                (CURRENT_DATE-1)::TIMESTAMPTZ + TIME '11:01', false, true,
                'Seed demo SI2: arqueo turno 2') RETURNING id INTO v_arqueo2_id;
        INSERT INTO arqueo_caja_moneda (arqueo_id, moneda_id, saldo_teorico,
                                        total_contado, diferencia, resultado)
        VALUES (v_arqueo2_id, v_bob_id, 700.00, 600.00, -100.00, 'FALTANTE'),
               (v_arqueo2_id, v_usd_id, 100.00, 100.00, 0.00, 'CUADRADO');
        SELECT id INTO v_account_id FROM arqueo_caja_moneda WHERE arqueo_id=v_arqueo2_id AND moneda_id=v_bob_id;
        INSERT INTO arqueo_caja_detalle (arqueo_moneda_id, tipo, denominacion, cantidad, subtotal)
        VALUES (v_account_id, 'BILLETE', 200.00, 3, 600.00);
        SELECT id INTO v_account_id FROM arqueo_caja_moneda WHERE arqueo_id=v_arqueo2_id AND moneda_id=v_usd_id;
        INSERT INTO arqueo_caja_detalle (arqueo_moneda_id, tipo, denominacion, cantidad, subtotal)
        VALUES (v_account_id, 'BILLETE', 100.00, 1, 100.00);
    END IF;

    -- Formal close sheets and their per-currency totals.
    SELECT id INTO v_cierre1_id FROM cierre_caja WHERE control_caja_id=v_control1_id;
    IF v_cierre1_id IS NULL THEN
        INSERT INTO cierre_caja (control_caja_id, arqueo_id, usuario_id, fecha, observacion)
        VALUES (v_control1_id, v_arqueo1_id, v_cashier_id,
                (CURRENT_DATE-2)::TIMESTAMPTZ + TIME '11:05',
                'Seed demo SI2: cierre turno 1') RETURNING id INTO v_cierre1_id;
        INSERT INTO cierre_caja_moneda (cierre_id, moneda_id, monto_apertura,
            total_depositos, cantidad_depositos, total_retiros, cantidad_retiros,
            cantidad_transferencias, saldo_teorico, total_contado, diferencia, traspaso_boveda)
        VALUES (v_cierre1_id, v_bob_id, 1000.00, 150000.00, 3, 1000.00, 1, 1,
                150000.00, 150000.00, 0.00, 150000.00);
    END IF;
    SELECT id INTO v_cierre2_id FROM cierre_caja WHERE control_caja_id=v_control2_id;
    IF v_cierre2_id IS NULL THEN
        INSERT INTO cierre_caja (control_caja_id, arqueo_id, usuario_id, fecha, observacion)
        VALUES (v_control2_id, v_arqueo2_id, v_cashier_id,
                (CURRENT_DATE-1)::TIMESTAMPTZ + TIME '11:05',
                'Seed demo SI2: cierre turno 2') RETURNING id INTO v_cierre2_id;
        INSERT INTO cierre_caja_moneda (cierre_id, moneda_id, monto_apertura,
            total_depositos, cantidad_depositos, total_retiros, cantidad_retiros,
            cantidad_transferencias, saldo_teorico, total_contado, diferencia, traspaso_boveda)
        VALUES (v_cierre2_id, v_bob_id, 500.00, 500.00, 1, 300.00, 1, 0,
                700.00, 600.00, -100.00, 600.00),
               (v_cierre2_id, v_usd_id, 0.00, 100.00, 1, 0.00, 0, 0,
                100.00, 100.00, 0.00, 100.00);
    END IF;

    -- Contribution certificates use the cooperative sequence. The natural
    -- key is socio + amount/title signature; there is no unique cert number.
    INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
    SELECT v_coop_id::INT, 'CERTIFICADO_APORTACION',
           COALESCE((SELECT max(correlativo)+1 FROM certificado_aportacion ca
                     JOIN socio s ON s.id=ca.socio_id WHERE s.cooperativa_id=v_coop_id),1)
    ON CONFLICT (cooperativa_id, tipo) DO NOTHING;
    UPDATE secuencia_documento sd SET siguiente=GREATEST(sd.siguiente, COALESCE((
        SELECT max(ca.correlativo)+1 FROM certificado_aportacion ca
        JOIN socio s ON s.id=ca.socio_id WHERE s.cooperativa_id=v_coop_id
    ),1)) WHERE sd.cooperativa_id=v_coop_id::INT AND sd.tipo='CERTIFICADO_APORTACION';
    FOR v_scenario IN SELECT * FROM (VALUES
        ('8877001 SC', 500.00, 5, 100.00),
        ('8877003 LP', 300.00, 3, 100.00),
        ('8877006 SC', 250.00, 5, 50.00)
    ) AS certs(ci, total, titles, unit_value)
    LOOP
        SELECT id INTO v_socio_id FROM socio WHERE ci=v_scenario.ci;
        IF NOT EXISTS (SELECT 1 FROM certificado_aportacion
                       WHERE socio_id=v_socio_id AND monto=v_scenario.total
                         AND numero_titulos=v_scenario.titles
                         AND valor_unitario=v_scenario.unit_value
                         ) THEN
            UPDATE secuencia_documento SET siguiente=siguiente+1
            WHERE cooperativa_id=v_coop_id::INT AND tipo='CERTIFICADO_APORTACION'
            RETURNING siguiente-1 INTO v_seq;
            INSERT INTO certificado_aportacion (correlativo, numero_titulos, valor_unitario,
                monto, fecha_emision, estado, socio_id, moneda_id)
            VALUES (v_seq, v_scenario.titles, v_scenario.unit_value, v_scenario.total,
                    CURRENT_DATE-3, 'EMITIDO', v_socio_id, v_bob_id);
        END IF;
    END LOOP;

    -- Pending credit applications with field evaluations; no credit rows are
    -- created because credit workflow is not implemented in this task.
    FOR v_scenario IN SELECT * FROM (VALUES
        ('8877002 SC', 8000.00, 3000.00, 5000.00, 12000.00, 12, 14.50),
        ('8877004 SC', 6500.00, 2500.00, 4000.00,  8000.00, 10, 16.00)
    ) AS requests(ci, income, expense, capacity, amount, months, rate)
    LOOP
        SELECT id INTO v_socio_id FROM socio WHERE ci=v_scenario.ci;
        IF NOT EXISTS (SELECT 1 FROM evaluacion_campo e
                       WHERE e.usuario_id=v_credit_id
                         AND e.ingreso_mensual=v_scenario.income
                         AND e.egreso_mensual=v_scenario.expense
                         AND e.capacidad_pago=v_scenario.capacity
                         AND e.coordenadas='-17.7833,-63.1821') THEN
            INSERT INTO evaluacion_campo (ingreso_mensual, egreso_mensual, capacidad_pago,
                fotografias_respaldo, coordenadas, fecha, resumen_cualitativo_ia, usuario_id)
            VALUES (v_scenario.income, v_scenario.expense, v_scenario.capacity,
                    'negocio_demo.jpg,domicilio_demo.jpg', '-17.7833,-63.1821',
                    CURRENT_DATE-2, 'Actividad estable y capacidad de pago evaluada en campo.', v_credit_id);
        END IF;
        SELECT id INTO v_dpf_id FROM evaluacion_campo e
        WHERE e.usuario_id=v_credit_id
          AND e.ingreso_mensual=v_scenario.income AND e.egreso_mensual=v_scenario.expense
          AND e.capacidad_pago=v_scenario.capacity AND e.coordenadas='-17.7833,-63.1821'
        ORDER BY id LIMIT 1;
        IF NOT EXISTS (SELECT 1 FROM solicitud_credito
                       WHERE socio_id=v_socio_id AND usuario_id=v_credit_id
                         AND evaluacion_campo_id=v_dpf_id AND monto=v_scenario.amount
                         AND plazo_meses=v_scenario.months AND tasa_interes=v_scenario.rate
                         AND estado='PENDIENTE') THEN
            INSERT INTO solicitud_credito (monto, plazo_meses, tasa_interes,
                calificacion_asfi, tiene_deudas, estado, socio_id, usuario_id, evaluacion_campo_id)
            VALUES (v_scenario.amount, v_scenario.months, v_scenario.rate,
                    'B', false, 'PENDIENTE', v_socio_id, v_credit_id, v_dpf_id);
        END IF;
    END LOOP;

    -- Final monotonic sequence safeguards after all seeded certificates.
    UPDATE secuencia_documento sd SET siguiente=GREATEST(sd.siguiente, COALESCE((
        SELECT max(correlativo)+1 FROM certificado_aportacion ca
        JOIN socio s ON s.id=ca.socio_id WHERE s.cooperativa_id=v_coop_id
    ),1)) WHERE sd.cooperativa_id=v_coop_id::INT AND sd.tipo='CERTIFICADO_APORTACION';
    UPDATE secuencia_documento sd SET siguiente=GREATEST(sd.siguiente, COALESCE((
        SELECT max(substring(d.numero_certificado FROM '^DPF-([0-9]{6})$')::INT)+1
        FROM deposito_plazo_fijo d WHERE d.cooperativa_id=v_coop_id
    ),1)) WHERE sd.cooperativa_id=v_coop_id::INT AND sd.tipo='DPF';
    UPDATE secuencia_documento sd SET siguiente=GREATEST(sd.siguiente, COALESCE((
        SELECT max(substring(c.numero FROM ('^CA-' || lpad(v_coop_id::TEXT,3,'0') || '-([0-9]{6})$'))::INT)+1
        FROM cuenta_ahorro c JOIN socio s ON s.id=c.socio_id WHERE s.cooperativa_id=v_coop_id
    ),1)) WHERE sd.cooperativa_id=v_coop_id::INT AND sd.tipo='CUENTA_AHORRO';
END;
$seed$;

COMMIT;
