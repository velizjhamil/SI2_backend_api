-- 008_seed_transferencias_demo.sql
-- Datos de DEMO/DEV para probar Transferencias entre Cuentas desde la app
-- móvil. NO es una migración de esquema -- solo datos. Todos los INSERT son
-- idempotentes: correrla de nuevo resetea los saldos a estos valores.
--
-- Aplicación (contenedor Docker "coopDB" de compose.yml ya levantado):
--   docker exec -i coopDB psql -U yimysito -d cooperativa_db < migrations/008_seed_transferencias_demo.sql
--
-- Crea (si no existen ya, ej. por seed_users.py):
--   - Cooperativa de Prueba SI2
--   - Rol SOCIO
--   - Usuarios socio@test.com y socio2@test.com (contraseña 'Password123' para ambos)
--   - Los socios (KYC) ligados a esos usuarios
--
-- Y siempre (re)crea estas cuentas de ahorro:
--   socio@test.com  -> AH-DEMO-001 (BOB 2500.00), AH-DEMO-002 (BOB 850.50, 100.00 bloqueado),
--                      AH-DEMO-003 (USD 300.00)  [para probar el desglose multi-moneda del saldo]
--   socio2@test.com -> AH-DEMO-101 (BOB 1000.00)  [para probar transferir a "cuenta ajena" -> 404]

BEGIN;

-- ── Cooperativa de prueba ──────────────────────────────────────────────────
INSERT INTO cooperativa (nombre, razon_social, nit, correo, telefono, direccion, estado)
VALUES (
    'Cooperativa de Prueba SI2', 'Cooperativa de Prueba SI2 Ltda.', '9988776655',
    'contacto@cooptest.bo', '+591 3 9876543', 'Av. Testing #001, Santa Cruz', 'ACTIVO'
)
ON CONFLICT (nit) DO NOTHING;

-- ── Rol SOCIO (rol.nombre no tiene UNIQUE en este esquema, se evita ON CONFLICT) ──
INSERT INTO rol (nombre, descripcion)
SELECT 'SOCIO', 'Socio / Cliente: acceso limitado a su propia información dentro de la cooperativa'
WHERE NOT EXISTS (SELECT 1 FROM rol WHERE nombre = 'SOCIO');

-- ── Usuarios SOCIO (hash bcrypt de 'Password123') ─────────────────────────
INSERT INTO usuario (rol_id, cooperativa_id, nombre, contrasena, correo, estado)
SELECT
    (SELECT id FROM rol WHERE nombre = 'SOCIO'),
    (SELECT id FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2'),
    'Sofia Socia',
    '$2b$12$GxvnpVLgq2DSYdi9eLlsQOE8THSkLE8aih09mGZNV2HY/pEEA/dCi',
    'socio@test.com',
    'ACTIVO'
ON CONFLICT (correo) DO NOTHING;

INSERT INTO usuario (rol_id, cooperativa_id, nombre, contrasena, correo, estado)
SELECT
    (SELECT id FROM rol WHERE nombre = 'SOCIO'),
    (SELECT id FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2'),
    'Mateo Terceros',
    '$2b$12$GxvnpVLgq2DSYdi9eLlsQOE8THSkLE8aih09mGZNV2HY/pEEA/dCi',
    'socio2@test.com',
    'ACTIVO'
ON CONFLICT (correo) DO NOTHING;

-- ── Socios (KYC) ligados a esos usuarios ──────────────────────────────────
INSERT INTO socio (cooperativa_id, ci, nombre, apellido, correo, estado, usuario_id)
SELECT
    (SELECT id FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2'),
    '5551234', 'Sofia', 'Socia', 'socio@test.com', 'ACTIVO',
    (SELECT id FROM usuario WHERE correo = 'socio@test.com')
ON CONFLICT (ci) DO UPDATE SET usuario_id = EXCLUDED.usuario_id, estado = 'ACTIVO';

INSERT INTO socio (cooperativa_id, ci, nombre, apellido, correo, estado, usuario_id)
SELECT
    (SELECT id FROM cooperativa WHERE nombre = 'Cooperativa de Prueba SI2'),
    '5559876', 'Mateo', 'Terceros', 'socio2@test.com', 'ACTIVO',
    (SELECT id FROM usuario WHERE correo = 'socio2@test.com')
ON CONFLICT (ci) DO UPDATE SET usuario_id = EXCLUDED.usuario_id, estado = 'ACTIVO';

-- ── Cuentas de ahorro ──────────────────────────────────────────────────────
-- socio@test.com: dos cuentas BOB (para transferir entre sí) + una USD
-- (para probar que el saldo se desglosa por moneda, nunca se suma).
INSERT INTO cuenta_ahorro (numero, tipo_producto, saldo_disponible, saldo_bloqueado, estado, fecha_registro, socio_id, moneda_id)
SELECT 'AH-DEMO-001', 'VISTA', 2500.00, 0.00, 'ACTIVA', CURRENT_DATE,
       (SELECT id FROM socio WHERE ci = '5551234'),
       (SELECT id FROM moneda WHERE codigo_iso = 'BOB')
ON CONFLICT (numero) DO UPDATE SET
    saldo_disponible = EXCLUDED.saldo_disponible,
    saldo_bloqueado = EXCLUDED.saldo_bloqueado,
    estado = 'ACTIVA';

INSERT INTO cuenta_ahorro (numero, tipo_producto, saldo_disponible, saldo_bloqueado, estado, fecha_registro, socio_id, moneda_id)
SELECT 'AH-DEMO-002', 'PROGRAMADO', 850.50, 100.00, 'ACTIVA', CURRENT_DATE,
       (SELECT id FROM socio WHERE ci = '5551234'),
       (SELECT id FROM moneda WHERE codigo_iso = 'BOB')
ON CONFLICT (numero) DO UPDATE SET
    saldo_disponible = EXCLUDED.saldo_disponible,
    saldo_bloqueado = EXCLUDED.saldo_bloqueado,
    estado = 'ACTIVA';

INSERT INTO cuenta_ahorro (numero, tipo_producto, saldo_disponible, saldo_bloqueado, estado, fecha_registro, socio_id, moneda_id)
SELECT 'AH-DEMO-003', 'VISTA', 300.00, 0.00, 'ACTIVA', CURRENT_DATE,
       (SELECT id FROM socio WHERE ci = '5551234'),
       (SELECT id FROM moneda WHERE codigo_iso = 'USD')
ON CONFLICT (numero) DO UPDATE SET
    saldo_disponible = EXCLUDED.saldo_disponible,
    saldo_bloqueado = EXCLUDED.saldo_bloqueado,
    estado = 'ACTIVA';

-- socio2@test.com: una cuenta BOB, para probar transferir a "cuenta ajena" -> 404.
INSERT INTO cuenta_ahorro (numero, tipo_producto, saldo_disponible, saldo_bloqueado, estado, fecha_registro, socio_id, moneda_id)
SELECT 'AH-DEMO-101', 'VISTA', 1000.00, 0.00, 'ACTIVA', CURRENT_DATE,
       (SELECT id FROM socio WHERE ci = '5559876'),
       (SELECT id FROM moneda WHERE codigo_iso = 'BOB')
ON CONFLICT (numero) DO UPDATE SET
    saldo_disponible = EXCLUDED.saldo_disponible,
    saldo_bloqueado = EXCLUDED.saldo_bloqueado,
    estado = 'ACTIVA';

COMMIT;
