-- Demo subscription-plan seed for the Supabase SQL Editor.
-- All inserts run in this single DO statement. Any conflict raises an error
-- and rolls back the complete statement; there are no updates or deletes.
DO $seed$
DECLARE
    v_plan RECORD;
    v_plan_count INTEGER;
    v_admin_role_id BIGINT;
    v_slug TEXT;
    v_code_hash TEXT;
    v_cooperative_name TEXT;
    v_admin_email TEXT;
    v_cooperative_id BIGINT;
    v_row_count INTEGER;
    v_existing_status TEXT;
    v_existing_plan_id BIGINT;
    v_existing_role_id BIGINT;
    v_existing_cooperative_id BIGINT;
    v_existing_password_hash TEXT;
    v_number INTEGER;
    v_admin_password_hash CONSTANT TEXT := '$2b$12$cL0RnvfYRC/F/Ay4JbptEeLA.omENPaBMbnEpsZ5zYRYnBxdXX9uy';
BEGIN
    SELECT count(*) INTO v_plan_count FROM plan_suscripcion;
    IF v_plan_count = 0 THEN
        RAISE EXCEPTION 'No subscription plans exist; add plans through the normal plan-management workflow before running this demo seed.';
    END IF;

    SELECT id INTO v_admin_role_id
    FROM rol
    WHERE nombre = 'ADMINISTRADOR';
    IF v_admin_role_id IS NULL THEN
        RAISE EXCEPTION 'Required role ADMINISTRADOR does not exist; initialize application roles before running this demo seed.';
    END IF;

    FOR v_plan IN
        SELECT id, codigo
        FROM plan_suscripcion
        ORDER BY id
    LOOP
        v_slug := left(
            btrim(
                regexp_replace(lower(v_plan.codigo) COLLATE "C", '[^a-z0-9]+', '-', 'g'),
                '-'
            ),
            24
        );
        IF v_slug = '' THEN
            v_slug := 'plan';
        END IF;
        v_code_hash := left(encode(sha256(convert_to(v_plan.codigo, 'UTF8')), 'hex'), 8);

        FOR v_number IN 1..3 LOOP
            v_cooperative_name := 'SI2 Demo Subscription - '
                || v_plan.codigo || ' - ' || lpad(v_number::TEXT, 2, '0');
            v_admin_email := 'demo-plan-' || v_slug || '-' || v_code_hash || '-'
                || lpad(v_number::TEXT, 2, '0') || '@example.com';

            SELECT count(*), min(id)
            INTO v_row_count, v_cooperative_id
            FROM cooperativa
            WHERE nombre = v_cooperative_name;

            IF v_row_count > 1 THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Conflicting demo key: multiple cooperativas named '
                    || quote_literal(v_cooperative_name) || ' exist.';
            ELSIF v_row_count = 0 THEN
                INSERT INTO cooperativa (nombre, estado)
                VALUES (v_cooperative_name, 'ACTIVO')
                RETURNING id INTO v_cooperative_id;
            ELSE
                SELECT estado INTO v_existing_status
                FROM cooperativa
                WHERE id = v_cooperative_id;
                IF v_existing_status IS DISTINCT FROM 'ACTIVO' THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Conflicting demo key: cooperative '
                        || quote_literal(v_cooperative_name) || ' is not ACTIVO.';
                END IF;
            END IF;

            SELECT count(*) INTO v_row_count
            FROM suscripcion
            WHERE cooperativa_id = v_cooperative_id;

            IF v_row_count > 1 THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Conflicting demo key: cooperative '
                    || quote_literal(v_cooperative_name) || ' has multiple subscriptions.';
            ELSIF v_row_count = 1 THEN
                SELECT plan_id, estado
                INTO v_existing_plan_id, v_existing_status
                FROM suscripcion
                WHERE cooperativa_id = v_cooperative_id;
                IF v_existing_plan_id IS DISTINCT FROM v_plan.id
                   OR v_existing_status IS DISTINCT FROM 'ACTIVA' THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Conflicting demo key: subscription for '
                        || quote_literal(v_cooperative_name)
                        || ' is not active on its expected plan.';
                END IF;
            ELSE
                INSERT INTO suscripcion (cooperativa_id, plan_id, estado, fecha_inicio, fecha_fin)
                VALUES (v_cooperative_id, v_plan.id, 'ACTIVA', CURRENT_DATE, NULL);
            END IF;

            SELECT count(*) INTO v_row_count
            FROM usuario
            WHERE correo = v_admin_email;

            IF v_row_count > 1 THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Conflicting demo key: multiple usuarios use correo '
                    || quote_literal(v_admin_email) || '.';
            ELSIF v_row_count = 1 THEN
                SELECT rol_id, cooperativa_id, estado, contrasena
                INTO v_existing_role_id, v_existing_cooperative_id, v_existing_status, v_existing_password_hash
                FROM usuario
                WHERE correo = v_admin_email;
                IF v_existing_role_id IS DISTINCT FROM v_admin_role_id
                   OR v_existing_cooperative_id IS DISTINCT FROM v_cooperative_id
                   OR v_existing_status IS DISTINCT FROM 'ACTIVO'
                   OR v_existing_password_hash IS DISTINCT FROM v_admin_password_hash THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Conflicting demo key: administrator '
                        || quote_literal(v_admin_email)
                        || ' does not match the expected active demo account.';
                END IF;
            ELSE
                INSERT INTO usuario (rol_id, cooperativa_id, nombre, contrasena, correo, estado)
                VALUES (
                    v_admin_role_id,
                    v_cooperative_id,
                    'Demo Administrator ' || v_plan.codigo || ' '
                        || lpad(v_number::TEXT, 2, '0'),
                    v_admin_password_hash,
                    v_admin_email,
                    'ACTIVO'
                );
            END IF;
        END LOOP;
    END LOOP;
END;
$seed$;
