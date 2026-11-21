-- Seed pequeño para probar CU-M9 (evaluación de campo móvil).
-- Ejecútalo completo con `psql -f scripts/seed_demo_campo_m9.sql` o pega todo
-- este archivo en el SQL Editor de Supabase. Es un único bloque transaccional;
-- no instala extensiones ni contiene comandos meta de psql.
-- Crea tres socios ficticios y solicitudes PENDIENTE sin evaluación ni fotos:
-- dos en MOVIL y una en VENTANILLA. Requiere la cooperativa, el oficial y un
-- producto activo existentes; no modifica cuentas ni catálogos.

DO $seed$
DECLARE
    v_cooperative_count INTEGER;
    v_cooperative_id BIGINT;
    v_official_id BIGINT;
    v_official_cooperative_id BIGINT;
    v_official_role TEXT;
    v_official_state TEXT;
    v_product_id INTEGER;
    v_currency_id INTEGER;
    v_amount NUMERIC(12, 2);
    v_term_months INTEGER;
    v_rate NUMERIC(5, 2);
    v_demo RECORD;
    v_socio_id BIGINT;
    v_socio_cooperative_id BIGINT;
    v_socio_name TEXT;
    v_socio_surname TEXT;
    v_socio_state TEXT;
    v_socio_user_id BIGINT;
    v_request_id INTEGER;
    v_existing_request RECORD;
BEGIN
    SELECT count(*), min(id)
    INTO v_cooperative_count, v_cooperative_id
    FROM cooperativa
    WHERE nombre = 'Cooperativa de Prueba SI2';

    IF v_cooperative_count = 0 THEN
        RAISE EXCEPTION 'No existe la cooperativa requerida: Cooperativa de Prueba SI2.';
    ELSIF v_cooperative_count > 1 THEN
        RAISE EXCEPTION 'La cooperativa requerida no es única: Cooperativa de Prueba SI2.';
    END IF;

    SELECT u.id, u.cooperativa_id, u.estado, r.nombre
    INTO v_official_id, v_official_cooperative_id, v_official_state, v_official_role
    FROM usuario u
    JOIN rol r ON r.id = u.rol_id
    WHERE u.correo = 'acredito@cooperativa.com';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'No existe la cuenta requerida: acredito@cooperativa.com.';
    END IF;
    IF v_official_state IS DISTINCT FROM 'ACTIVO'
       OR v_official_role IS DISTINCT FROM 'OFICIAL_CREDITO' THEN
        RAISE EXCEPTION 'La cuenta requerida debe estar ACTIVO y tener rol OFICIAL_CREDITO: acredito@cooperativa.com.';
    END IF;
    IF v_official_cooperative_id IS DISTINCT FROM v_cooperative_id THEN
        RAISE EXCEPTION 'La cuenta acredito@cooperativa.com no pertenece a Cooperativa de Prueba SI2; no se modificó el usuario.';
    END IF;

    SELECT p.id, p.moneda_id, p.monto_min, p.plazo_min_meses, p.tasa_interes_anual
    INTO v_product_id, v_currency_id, v_amount, v_term_months, v_rate
    FROM producto_credito p
    JOIN moneda m ON m.id = p.moneda_id
    WHERE p.cooperativa_id = v_cooperative_id
      AND p.estado = 'ACTIVO'
    ORDER BY p.id
    LIMIT 1;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'No hay un producto de crédito activo y con moneda en Cooperativa de Prueba SI2.';
    END IF;

    FOR v_demo IN
        SELECT * FROM (VALUES
            ('M9-DEMO-SOCIO-01', 'Socio Demo M9', 'Uno', 'M9-DEMO-001', 'MOVIL',
             'CAPITAL_TRABAJO', 'Solicitud ficticia de capital de trabajo para prueba de campo.'),
            ('M9-DEMO-SOCIO-02', 'Socia Demo M9', 'Dos', 'M9-DEMO-002', 'MOVIL',
             'ACTIVO_FIJO', 'Solicitud ficticia de activo fijo para prueba de campo.'),
            ('M9-DEMO-SOCIO-03', 'Socio Demo M9', 'Tres', 'M9-DEMO-003', 'VENTANILLA',
             'CONSUMO', 'Solicitud ficticia de consumo para prueba de campo.')
        ) AS d(ci, nombre, apellido, numero_solicitud, canal_origen, destino, destino_detalle)
    LOOP
        INSERT INTO socio (cooperativa_id, ci, nombre, apellido, estado)
        VALUES (v_cooperative_id, v_demo.ci, v_demo.nombre, v_demo.apellido, 'ACTIVO')
        ON CONFLICT (ci) DO NOTHING;

        SELECT id, cooperativa_id, nombre, apellido, estado, usuario_id
        INTO v_socio_id, v_socio_cooperative_id, v_socio_name,
             v_socio_surname, v_socio_state, v_socio_user_id
        FROM socio
        WHERE ci = v_demo.ci;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING MESSAGE =
                'No se pudo resolver el socio demo ' || quote_literal(v_demo.ci) || '.';
        END IF;
        IF v_socio_cooperative_id IS DISTINCT FROM v_cooperative_id
           OR v_socio_name IS DISTINCT FROM v_demo.nombre
           OR v_socio_surname IS DISTINCT FROM v_demo.apellido
           OR v_socio_state IS DISTINCT FROM 'ACTIVO'
           OR v_socio_user_id IS NOT NULL THEN
            RAISE EXCEPTION USING MESSAGE =
                'La clave demo de socio ' || quote_literal(v_demo.ci)
                || ' ya existe con datos en conflicto; no se modificó.';
        END IF;

        INSERT INTO solicitud_credito (
            cooperativa_id, numero_solicitud, socio_id, usuario_id, estado,
            monto, plazo_meses, tasa_interes, producto_credito_id, moneda_id,
            canal_origen, destino, destino_detalle, tiene_deudas
        )
        VALUES (
            v_cooperative_id, v_demo.numero_solicitud, v_socio_id, v_official_id,
            'PENDIENTE', v_amount, v_term_months, v_rate, v_product_id, v_currency_id,
            v_demo.canal_origen, v_demo.destino, v_demo.destino_detalle, false
        )
        ON CONFLICT (cooperativa_id, numero_solicitud) DO NOTHING;

        SELECT id, socio_id, usuario_id, estado, monto, plazo_meses, tasa_interes,
               producto_credito_id, moneda_id, canal_origen, destino,
               destino_detalle, evaluacion_campo_id
        INTO v_existing_request
        FROM solicitud_credito
        WHERE cooperativa_id = v_cooperative_id
          AND numero_solicitud = v_demo.numero_solicitud;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING MESSAGE =
                'No se pudo resolver la solicitud demo '
                || quote_literal(v_demo.numero_solicitud) || '.';
        END IF;
        IF v_existing_request.socio_id IS DISTINCT FROM v_socio_id
           OR v_existing_request.usuario_id IS DISTINCT FROM v_official_id
           OR v_existing_request.estado IS DISTINCT FROM 'PENDIENTE'
           OR v_existing_request.monto IS DISTINCT FROM v_amount
           OR v_existing_request.plazo_meses IS DISTINCT FROM v_term_months
           OR v_existing_request.tasa_interes IS DISTINCT FROM v_rate
           OR v_existing_request.producto_credito_id IS DISTINCT FROM v_product_id
           OR v_existing_request.moneda_id IS DISTINCT FROM v_currency_id
           OR v_existing_request.canal_origen IS DISTINCT FROM v_demo.canal_origen
           OR v_existing_request.destino IS DISTINCT FROM v_demo.destino
           OR v_existing_request.destino_detalle IS DISTINCT FROM v_demo.destino_detalle
           OR v_existing_request.evaluacion_campo_id IS NOT NULL THEN
            RAISE EXCEPTION USING MESSAGE =
                'La clave demo de solicitud ' || quote_literal(v_demo.numero_solicitud)
                || ' ya existe con datos en conflicto; no se modificó.';
        END IF;
    END LOOP;
END
$seed$;
