CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE permiso (
    id          SMALLSERIAL  PRIMARY KEY,                
    nombre      VARCHAR(100) NOT NULL,
    descripcion TEXT,
    CONSTRAINT uq_permiso_nombre UNIQUE (nombre)
);

CREATE TABLE rol (
    id          SMALLSERIAL PRIMARY KEY,                   
    nombre      VARCHAR(50) NOT NULL,
    descripcion TEXT,
    CONSTRAINT uq_rol_nombre UNIQUE (nombre)
);

CREATE TABLE rol_permiso (
    rol_id     SMALLINT NOT NULL REFERENCES rol(id)     ON DELETE CASCADE,
    permiso_id SMALLINT NOT NULL REFERENCES permiso(id) ON DELETE CASCADE,
    PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE cooperativa (
    id             BIGSERIAL    PRIMARY KEY,
    uuid           UUID         NOT NULL DEFAULT gen_random_uuid(),
    nombre         VARCHAR(150) NOT NULL,
    razon_social   VARCHAR(200),
    nit            VARCHAR(20),
    correo         VARCHAR(150),
    telefono       VARCHAR(20),
    direccion      TEXT,
    estado         VARCHAR(20)  NOT NULL DEFAULT 'ACTIVO',
    fecha_creacion TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    fecha_baja     TIMESTAMPTZ,
    CONSTRAINT uq_cooperativa_uuid   UNIQUE (uuid),
    CONSTRAINT uq_cooperativa_nit    UNIQUE (nit),
    CONSTRAINT chk_cooperativa_estado CHECK (estado IN ('ACTIVO','INACTIVO'))
);

CREATE INDEX idx_cooperativa_estado ON cooperativa(estado);
CREATE INDEX idx_cooperativa_nombre ON cooperativa(nombre);

CREATE TABLE usuario (
    id             BIGSERIAL    PRIMARY KEY,
    uuid           UUID         NOT NULL DEFAULT gen_random_uuid(),
    rol_id         SMALLINT     NOT NULL REFERENCES rol(id),
    cooperativa_id BIGINT       REFERENCES cooperativa(id),
    nombre         VARCHAR(100) NOT NULL,
    contrasena     VARCHAR(255)  NOT NULL,
    correo         VARCHAR(150) NOT NULL,
    estado         VARCHAR(20)  NOT NULL DEFAULT 'ACTIVO',
    fecha_creacion TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    fecha_baja     TIMESTAMPTZ,
    CONSTRAINT uq_usuario_correo UNIQUE (correo),
    CONSTRAINT uq_usuario_uuid   UNIQUE (uuid),
    CONSTRAINT chk_usuario_estado CHECK (estado IN ('ACTIVO','INACTIVO','BLOQUEADO')),
    CONSTRAINT chk_usuario_correo CHECK (correo ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE INDEX idx_usuario_uuid   ON usuario(uuid);
CREATE INDEX idx_usuario_correo ON usuario(correo) WHERE fecha_baja IS NULL;
CREATE INDEX idx_usuario_rol    ON usuario(rol_id);
CREATE INDEX idx_usuario_cooperativa ON usuario(cooperativa_id);

CREATE TABLE bitacora (
    id          BIGSERIAL   PRIMARY KEY,
    usuario_id  BIGINT      NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    modulo      VARCHAR(50) NOT NULL,
    accion      VARCHAR(50) NOT NULL,
    descripcion TEXT,
    ip          INET,                                            
    user_agent  TEXT,                                            
    fecha_hora  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_bitacora_usuario ON bitacora(usuario_id);
CREATE INDEX idx_bitacora_fecha   ON bitacora(fecha_hora DESC);         
CREATE INDEX idx_bitacora_modulo  ON bitacora(modulo, accion);

CREATE TABLE reporte (
    id               BIGSERIAL   PRIMARY KEY,              
    usuario_id       BIGINT      NOT NULL REFERENCES usuario(id),
    tipo             VARCHAR(50) NOT NULL,
    formato          VARCHAR(10) NOT NULL,
    parametros       JSONB,                                  
    fecha_generacion TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_reporte_formato CHECK (formato IN ('PDF','EXCEL','CSV'))
);

CREATE TABLE socio (
    id             BIGSERIAL    PRIMARY KEY,
    uuid           UUID         NOT NULL DEFAULT gen_random_uuid(),
    ci             VARCHAR(20)  NOT NULL,
    nombre         VARCHAR(100) NOT NULL,
    apellido       VARCHAR(100) NOT NULL,
    direccion      TEXT,
    telefono       VARCHAR(20),
    correo         VARCHAR(150),
    estado         VARCHAR(20)  NOT NULL DEFAULT 'ACTIVO',
    fecha_registro DATE         NOT NULL DEFAULT CURRENT_DATE,
    fecha_baja     DATE,                                         
    CONSTRAINT uq_socio_ci   UNIQUE (ci),
    CONSTRAINT uq_socio_uuid UNIQUE (uuid),
    CONSTRAINT chk_socio_estado  CHECK (estado IN ('ACTIVO','INACTIVO','SUSPENDIDO')),
    CONSTRAINT chk_socio_correo  CHECK (correo IS NULL OR correo ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE INDEX idx_socio_uuid ON socio(uuid);
CREATE INDEX idx_socio_ci   ON socio(ci);

CREATE TABLE certificado_aportacion (
    id            BIGSERIAL      PRIMARY KEY,
    socio_id      BIGINT         NOT NULL REFERENCES socio(id) ON DELETE RESTRICT,
    monto         NUMERIC(15, 2) NOT NULL,                       
    fecha_emision DATE           NOT NULL DEFAULT CURRENT_DATE,
    estado        VARCHAR(20)    NOT NULL DEFAULT 'EMITIDO',
    CONSTRAINT chk_cert_monto  CHECK (monto > 0),
    CONSTRAINT chk_cert_estado CHECK (estado IN ('EMITIDO','CANJEADO','ANULADO'))
);

CREATE TABLE cuenta_ahorro (
    id               BIGSERIAL      PRIMARY KEY,
    uuid             UUID           NOT NULL DEFAULT gen_random_uuid(),
    socio_id         BIGINT         NOT NULL REFERENCES socio(id) ON DELETE RESTRICT,
    numero           VARCHAR(30)    NOT NULL,
    saldo_disponible NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    saldo_bloqueado  NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    estado           VARCHAR(20)    NOT NULL DEFAULT 'ACTIVA',
    fecha_registro   DATE           NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT uq_cuenta_numero UNIQUE (numero),
    CONSTRAINT uq_cuenta_uuid   UNIQUE (uuid),
    CONSTRAINT chk_cuenta_saldo_disp CHECK (saldo_disponible >= 0),
    CONSTRAINT chk_cuenta_saldo_bloq CHECK (saldo_bloqueado  >= 0),
    CONSTRAINT chk_cuenta_estado     CHECK (estado IN ('ACTIVA','INACTIVA','BLOQUEADA','CERRADA'))
);

CREATE INDEX idx_cuenta_uuid   ON cuenta_ahorro(uuid);
CREATE INDEX idx_cuenta_numero ON cuenta_ahorro(numero);
CREATE INDEX idx_cuenta_socio  ON cuenta_ahorro(socio_id);

CREATE TABLE deposito_plazo_fijo (
    id                  BIGSERIAL      PRIMARY KEY,
    uuid                UUID           NOT NULL DEFAULT gen_random_uuid(),
    socio_id            BIGINT         NOT NULL REFERENCES socio(id) ON DELETE RESTRICT,
    monto               NUMERIC(15, 2) NOT NULL,
    tasa_interes_anual  NUMERIC(5, 2)  NOT NULL,
    plazo_dias          SMALLINT       NOT NULL,                 
    fecha_inicio        DATE           NOT NULL,
    fecha_vencimiento   DATE           NOT NULL,
    interes_calculado   NUMERIC(15, 2) NOT NULL,
    estado              VARCHAR(20)    NOT NULL DEFAULT 'VIGENTE',
    CONSTRAINT uq_dpf_uuid      UNIQUE (uuid),
    CONSTRAINT chk_dpf_monto    CHECK (monto > 0),
    CONSTRAINT chk_dpf_tasa     CHECK (tasa_interes_anual > 0),
    CONSTRAINT chk_dpf_plazo    CHECK (plazo_dias > 0),
    CONSTRAINT chk_dpf_fechas   CHECK (fecha_vencimiento > fecha_inicio), 
    CONSTRAINT chk_dpf_estado   CHECK (estado IN ('VIGENTE','VENCIDO','CANCELADO'))
);

CREATE INDEX idx_dpf_uuid            ON deposito_plazo_fijo(uuid);
CREATE INDEX idx_dpf_socio           ON deposito_plazo_fijo(socio_id);
CREATE INDEX idx_dpf_fecha_venc      ON deposito_plazo_fijo(fecha_vencimiento) WHERE estado = 'VIGENTE'; 

CREATE TABLE liquidacion (
    id                      BIGSERIAL      PRIMARY KEY,
    deposito_plazo_fijo_id  BIGINT         NOT NULL UNIQUE REFERENCES deposito_plazo_fijo(id) ON DELETE RESTRICT,
    monto_capital_retornado NUMERIC(15, 2) NOT NULL,
    monto_interes_pagado    NUMERIC(15, 2) NOT NULL,
    tipo_operacion          VARCHAR(30)    NOT NULL,
    fecha                   TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_liq_tipo CHECK (tipo_operacion IN ('CANCELACION_ANTICIPADA','VENCIMIENTO'))
);

CREATE TABLE solicitud_credito (
    id                   BIGSERIAL      PRIMARY KEY,
    uuid                 UUID           NOT NULL DEFAULT gen_random_uuid(),
    socio_id             BIGINT         NOT NULL REFERENCES socio(id) ON DELETE RESTRICT,
    usuario_id           BIGINT         NOT NULL REFERENCES usuario(id),
    monto                NUMERIC(15, 2) NOT NULL,
    plazo                SMALLINT       NOT NULL,              
    tasa_interes         NUMERIC(5, 2)  NOT NULL,
    sistema_amortizacion VARCHAR(20)    NOT NULL,
    estado               VARCHAR(30)    NOT NULL DEFAULT 'PENDIENTE',
    fecha_solicitud      DATE           NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT uq_solicitud_uuid    UNIQUE (uuid),
    CONSTRAINT chk_sol_monto        CHECK (monto > 0),
    CONSTRAINT chk_sol_plazo        CHECK (plazo > 0),
    CONSTRAINT chk_sol_tasa         CHECK (tasa_interes > 0),
    CONSTRAINT chk_sol_amortizacion CHECK (sistema_amortizacion IN ('FRANCES','ALEMAN','AMERICANO')),
    CONSTRAINT chk_sol_estado       CHECK (estado IN ('PENDIENTE','EN_REVISION','APROBADO','RECHAZADO','DESEMBOLSADO'))
);

CREATE INDEX idx_solicitud_uuid    ON solicitud_credito(uuid);
CREATE INDEX idx_solicitud_socio   ON solicitud_credito(socio_id);
CREATE INDEX idx_solicitud_usuario ON solicitud_credito(usuario_id);
CREATE INDEX idx_solicitud_estado  ON solicitud_credito(estado) WHERE estado IN ('PENDIENTE','EN_REVISION');

CREATE TABLE evaluacion_campo (
    id                   BIGSERIAL      PRIMARY KEY,
    solicitud_credito_id BIGINT         NOT NULL REFERENCES solicitud_credito(id) ON DELETE CASCADE,
    usuario_id           BIGINT         NOT NULL REFERENCES usuario(id),
    ingreso_mensual      NUMERIC(15, 2) NOT NULL,
    egreso_mensual       NUMERIC(15, 2) NOT NULL,
    capacidad_pago       NUMERIC(15, 2) NOT NULL,
    fotografias_respaldo JSONB,                           
    latitud              NUMERIC(9, 6),                   
    longitud             NUMERIC(9, 6),                   
    fecha                DATE           NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT chk_eval_ingresos CHECK (ingreso_mensual >= 0),
    CONSTRAINT chk_eval_egresos  CHECK (egreso_mensual  >= 0),
    CONSTRAINT chk_eval_lat      CHECK (latitud  BETWEEN -90  AND 90),
    CONSTRAINT chk_eval_lon      CHECK (longitud BETWEEN -180 AND 180)
);

CREATE TABLE prediccion_morosidad (
    id                          BIGSERIAL      PRIMARY KEY,      -- SERIAL → BIGSERIAL
    solicitud_credito_id        BIGINT         NOT NULL REFERENCES solicitud_credito(id) ON DELETE CASCADE,
    probabilidad_incumplimiento NUMERIC(5, 4)  NOT NULL,
    nivel_riesgo                VARCHAR(20)    NOT NULL,
    factores_riesgo_json        JSONB,
    fecha_prediccion            TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_pred_prob        CHECK (probabilidad_incumplimiento BETWEEN 0 AND 1),
    CONSTRAINT chk_pred_nivel_riesgo CHECK (nivel_riesgo IN ('BAJO','MEDIO','ALTO'))
);

CREATE INDEX idx_prediccion_solicitud ON prediccion_morosidad(solicitud_credito_id);

CREATE TABLE alerta_cobranza (
    id                      BIGSERIAL   PRIMARY KEY,
    prediccion_morosidad_id BIGINT      NOT NULL REFERENCES prediccion_morosidad(id) ON DELETE CASCADE,
    tipo                    VARCHAR(20) NOT NULL,
    mensaje                 TEXT        NOT NULL,
    estado                  VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    fecha_emision           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_alerta_tipo   CHECK (tipo   IN ('PREVENTIVA','INTENSIVA')),
    CONSTRAINT chk_alerta_estado CHECK (estado IN ('PENDIENTE','ENVIADA','GESTIONADA'))
);

CREATE INDEX idx_alerta_estado ON alerta_cobranza(estado) WHERE estado = 'PENDIENTE'; 

CREATE TABLE tabla_amortizacion (
    id                   BIGSERIAL      PRIMARY KEY,
    solicitud_credito_id BIGINT         NOT NULL REFERENCES solicitud_credito(id) ON DELETE CASCADE,
    numero_cuota         SMALLINT       NOT NULL,               
    fecha_vencimiento    DATE           NOT NULL,
    monto_capital        NUMERIC(15, 2) NOT NULL,
    monto_interes        NUMERIC(15, 2) NOT NULL,
    monto_cuota_total    NUMERIC(15, 2) NOT NULL,
    estado_pago          VARCHAR(20)    NOT NULL DEFAULT 'PENDIENTE',
    CONSTRAINT chk_amort_capital  CHECK (monto_capital >= 0),
    CONSTRAINT chk_amort_interes  CHECK (monto_interes >= 0),
    CONSTRAINT chk_amort_total    CHECK (monto_cuota_total > 0),
    CONSTRAINT chk_amort_estado   CHECK (estado_pago IN ('PENDIENTE','PAGADO','VENCIDO','PARCIAL'))
);

CREATE INDEX idx_amortizacion_solicitud ON tabla_amortizacion(solicitud_credito_id);
CREATE INDEX idx_amortizacion_estado    ON tabla_amortizacion(estado_pago) WHERE estado_pago IN ('PENDIENTE','VENCIDO');

CREATE TABLE pago_cuota (
    id                    BIGSERIAL      PRIMARY KEY,
    tabla_amortizacion_id BIGINT         NOT NULL REFERENCES tabla_amortizacion(id) ON DELETE RESTRICT,
    monto_capital         NUMERIC(15, 2) NOT NULL,
    monto_interes_pagado  NUMERIC(15, 2) NOT NULL,
    monto_mora            NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    fecha                 TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_pago_capital  CHECK (monto_capital        >= 0),
    CONSTRAINT chk_pago_interes  CHECK (monto_interes_pagado >= 0),
    CONSTRAINT chk_pago_mora     CHECK (monto_mora           >= 0)
);

CREATE TABLE caja (
    id     SMALLSERIAL PRIMARY KEY,                       
    nombre VARCHAR(50) NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'CERRADA',
    CONSTRAINT uq_caja_nombre  UNIQUE (nombre),
    CONSTRAINT chk_caja_estado CHECK (estado IN ('ABIERTA','CERRADA','SUSPENDIDA'))
);

CREATE TABLE control_caja (
    id             BIGSERIAL      PRIMARY KEY,
    caja_id        SMALLINT       NOT NULL REFERENCES caja(id),
    usuario_id     BIGINT         NOT NULL REFERENCES usuario(id),
    monto_apertura NUMERIC(15, 2) NOT NULL,
    monto_cierre   NUMERIC(15, 2),
    saldo_sistema  NUMERIC(15, 2),
    fecha_apertura TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    fecha_cierre   TIMESTAMPTZ,
    estado         VARCHAR(20)    NOT NULL DEFAULT 'ABIERTA',
    CONSTRAINT chk_ctrl_apertura CHECK (monto_apertura >= 0),
    CONSTRAINT chk_ctrl_estado   CHECK (estado IN ('ABIERTA','CERRADA'))
);

CREATE INDEX idx_control_caja_abierta ON control_caja(caja_id) WHERE estado = 'ABIERTA'; 

CREATE TABLE transaccion (
    id                     BIGSERIAL      PRIMARY KEY,
    uuid                   UUID           NOT NULL DEFAULT gen_random_uuid(),
    control_caja_id        BIGINT         REFERENCES control_caja(id),
    cuenta_ahorro_id       BIGINT         REFERENCES cuenta_ahorro(id),
    deposito_plazo_fijo_id BIGINT         REFERENCES deposito_plazo_fijo(id),
    pago_cuota_id          BIGINT         REFERENCES pago_cuota(id),
    tipo                   VARCHAR(30)    NOT NULL,
    monto                  NUMERIC(15, 2) NOT NULL,
    canal                  VARCHAR(20)    NOT NULL,
    descripcion            TEXT,
    fecha_hora             TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_transaccion_uuid  UNIQUE (uuid),
    CONSTRAINT chk_trans_monto      CHECK (monto > 0),
    CONSTRAINT chk_trans_tipo       CHECK (tipo  IN ('DEPOSITO','RETIRO','APERTURA_DPF','PAGO_CREDITO','TRANSFERENCIA')),
    CONSTRAINT chk_trans_canal      CHECK (canal IN ('VENTANILLA','APP_MOBILE','WEB'))
);

CREATE INDEX idx_trans_uuid    ON transaccion(uuid);
CREATE INDEX idx_trans_cuenta  ON transaccion(cuenta_ahorro_id);
CREATE INDEX idx_trans_caja    ON transaccion(control_caja_id);
CREATE INDEX idx_trans_fecha   ON transaccion(fecha_hora DESC);

CREATE TABLE plan_cuenta (
    id      SERIAL       PRIMARY KEY,                          
    codigo  VARCHAR(30)  NOT NULL,
    nombre  VARCHAR(150) NOT NULL,
    nivel   SMALLINT     NOT NULL,                             
    tipo    VARCHAR(20)  NOT NULL,
    CONSTRAINT uq_plan_codigo  UNIQUE (codigo),
    CONSTRAINT chk_plan_tipo   CHECK (tipo  IN ('ACTIVO','PASIVO','PATRIMONIO','INGRESO','EGRESO')),
    CONSTRAINT chk_plan_nivel  CHECK (nivel > 0)
);

CREATE TABLE comprobante_contable (
    id             BIGSERIAL   PRIMARY KEY,
    transaccion_id BIGINT      NOT NULL UNIQUE REFERENCES transaccion(id) ON DELETE RESTRICT,
    tipo           VARCHAR(20) NOT NULL,
    glosa          TEXT        NOT NULL,
    es_automatico  BOOLEAN     NOT NULL DEFAULT TRUE,
    fecha          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_comp_tipo CHECK (tipo IN ('INGRESO','EGRESO','TRASPASO'))
);

CREATE INDEX idx_comprobante_fecha ON comprobante_contable(fecha DESC);

CREATE TABLE detalle_asiento (
    id                      BIGSERIAL      PRIMARY KEY,
    comprobante_contable_id BIGINT         NOT NULL REFERENCES comprobante_contable(id) ON DELETE CASCADE,
    plan_cuenta_id          INT            NOT NULL REFERENCES plan_cuenta(id) ON DELETE RESTRICT,
    debe                    NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    haber                   NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    CONSTRAINT chk_asiento_debe  CHECK (debe  >= 0),
    CONSTRAINT chk_asiento_haber CHECK (haber >= 0),
    CONSTRAINT chk_asiento_mov   CHECK (debe > 0 OR haber > 0) 
);

CREATE INDEX idx_detalle_comprobante ON detalle_asiento(comprobante_contable_id);
CREATE INDEX idx_detalle_plan_cuenta ON detalle_asiento(plan_cuenta_id);


-- seeder

INSERT INTO permiso (nombre, descripcion) VALUES
    ('USUARIO_VER',              'Ver listado de usuarios'),
    ('USUARIO_CREAR',            'Crear nuevos usuarios'),
    ('USUARIO_EDITAR',           'Editar usuarios existentes'),
    ('USUARIO_ELIMINAR',         'Desactivar usuarios'),
    ('SOCIO_VER',                'Ver listado de socios'),
    ('SOCIO_CREAR',              'Registrar nuevos socios'),
    ('SOCIO_EDITAR',             'Editar datos de socios'),
    ('CREDITO_VER',              'Ver solicitudes de crédito'),
    ('CREDITO_CREAR',            'Crear solicitudes de crédito'),
    ('CREDITO_APROBAR',          'Aprobar o rechazar créditos'),
    ('CAJA_ABRIR',               'Apertura de caja'),
    ('CAJA_CERRAR',              'Cierre de caja'),
    ('TRANSACCION_DEPOSITO',     'Realizar depósitos'),
    ('TRANSACCION_RETIRO',       'Realizar retiros'),
    ('DPF_VER',                  'Ver depósitos a plazo fijo'),
    ('DPF_CREAR',                'Crear depósitos a plazo fijo'),
    ('DPF_LIQUIDAR',             'Liquidar depósitos a plazo fijo'),
    ('CONTABILIDAD_VER',         'Ver comprobantes contables'),
    ('REPORTE_GENERAR',          'Generar reportes del sistema'),
    ('BITACORA_VER',             'Ver registros de auditoría');

INSERT INTO rol (nombre, descripcion) VALUES
    ('ADMINISTRADOR',  'Acceso total al sistema'),
    ('GERENTE',        'Supervisión general de operaciones'),
    ('ASESOR_CREDITO', 'Gestión y evaluación de créditos'),
    ('CAJERO',         'Manejo de cajas y transacciones'),
    ('CONTADOR',       'Acceso al módulo contable'),
    ('SOCIO',          'Acceso limitado a su propia información'),
    ('SUPERADMIN',     'Super Administrador SaaS: gestiona cooperativas (tenants)');

INSERT INTO rol_permiso (rol_id, permiso_id)
SELECT 1, id FROM permiso;

INSERT INTO rol_permiso (rol_id, permiso_id)
SELECT 2, id FROM permiso
WHERE nombre IN (
    'USUARIO_VER','SOCIO_VER','CREDITO_VER','CREDITO_APROBAR',
    'DPF_VER','CONTABILIDAD_VER','REPORTE_GENERAR','BITACORA_VER'
);

INSERT INTO rol_permiso (rol_id, permiso_id)
SELECT 3, id FROM permiso
WHERE nombre IN (
    'SOCIO_VER','SOCIO_CREAR','SOCIO_EDITAR',
    'CREDITO_VER','CREDITO_CREAR','DPF_VER'
);

INSERT INTO rol_permiso (rol_id, permiso_id)
SELECT 4, id FROM permiso
WHERE nombre IN (
    'SOCIO_VER','CAJA_ABRIR','CAJA_CERRAR',
    'TRANSACCION_DEPOSITO','TRANSACCION_RETIRO',
    'DPF_VER','DPF_CREAR','DPF_LIQUIDAR'
);

INSERT INTO rol_permiso (rol_id, permiso_id)
SELECT 5, id FROM permiso
WHERE nombre IN (
    'CONTABILIDAD_VER','REPORTE_GENERAR','BITACORA_VER'
);

INSERT INTO rol_permiso (rol_id, permiso_id)
SELECT 6, id FROM permiso
WHERE nombre IN ('SOCIO_VER','CREDITO_VER','DPF_VER');

INSERT INTO usuario (rol_id, nombre, contrasena, correo, estado) VALUES
    (1, 'Carlos Mendoza',  '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'admin@cooperativa.com',    'ACTIVO'),
    (2, 'Maria Rodriguez', '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'gerente@cooperativa.com',  'ACTIVO'),
    (3, 'Juan Perez',      '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'asesor1@cooperativa.com',  'ACTIVO'),
    (3, 'Ana Torres',      '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'asesor2@cooperativa.com',  'ACTIVO'),
    (4, 'Pedro Quispe',    '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'cajero1@cooperativa.com',  'ACTIVO'),
    (4, 'Lucia Mamani',    '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'cajero2@cooperativa.com',  'ACTIVO'),
    (5, 'Rosa Condori',    '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'contador@cooperativa.com', 'ACTIVO'),
    (6, 'Roberto Chavez',  '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'rchavez@gmail.com',        'ACTIVO'),
    (6, 'Elena Flores',    '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'eflores@gmail.com',        'ACTIVO'),
    (6, 'Diego Vargas',    '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'dvargas@gmail.com',        'INACTIVO');

-- Tenant demo y vinculación de usuarios de ejemplo a su cooperativa
INSERT INTO cooperativa (nombre, razon_social, nit, correo, telefono, direccion) VALUES
    ('Cooperativa Demo SI2', 'Cooperativa Demo SI2 Ltda.', '1020304050',
     'contacto@coopdemo.bo', '+591 3 1234567', 'Av. Principal #123, Santa Cruz');

UPDATE usuario
SET cooperativa_id = (SELECT id FROM cooperativa WHERE nombre = 'Cooperativa Demo SI2')
WHERE correo NOT LIKE '%@si2.com';

INSERT INTO usuario (rol_id, nombre, contrasena, correo, estado) VALUES
    (7, 'Valeria Rojas', '$2b$12$KIXqZ2Z6v3R4T5Y7U8I9O.abcdefghijklmnopqrstuvwxyzABCDEF', 'superadmin@si2.com', 'ACTIVO');

INSERT INTO socio (ci, nombre, apellido, direccion, telefono, correo, estado) VALUES
    ('1234567', 'Roberto',   'Chavez',   'Av. 6 de Agosto 123, La Paz',      '76543210', 'rchavez@gmail.com',   'ACTIVO'),
    ('2345678', 'Elena',     'Flores',   'Calle Potosi 456, Cochabamba',      '77891234', 'eflores@gmail.com',   'ACTIVO'),
    ('3456789', 'Diego',     'Vargas',   'Av. Montes 789, La Paz',            '78123456', 'dvargas@gmail.com',   'INACTIVO'),
    ('4567890', 'Carmen',    'Huanca',   'Av. Blanco Galindo Km3, Cbba',      '71234567', 'chuanca@hotmail.com', 'ACTIVO'),
    ('5678901', 'Miguel',    'Condori',  'Calle Junin 321, Oruro',            '72345678', 'mcondori@yahoo.com',  'ACTIVO'),
    ('6789012', 'Patricia',  'Morales',  'Av. Bush 654, Santa Cruz',          '73456789', 'pmorales@gmail.com',  'ACTIVO'),
    ('7890123', 'Fernando',  'Ticona',   'Calle Sucre 987, Potosi',           '74567890', 'fticona@gmail.com',   'ACTIVO'),
    ('8901234', 'Gloria',    'Apaza',    'Av. Costanera 147, Trinidad',       '75678901', 'gapaza@hotmail.com',  'ACTIVO'),
    ('9012345', 'Raul',      'Mamani',   'Calle Comercio 258, La Paz',        '76789012', 'rmamani@gmail.com',   'ACTIVO'),
    ('0123456', 'Veronica',  'Colque',   'Av. Heroinas 369, Cochabamba',      '77890123', 'vcolque@gmail.com',   'ACTIVO');

INSERT INTO certificado_aportacion (socio_id, monto, fecha_emision, estado) VALUES
    (1,  500.00,  '2024-01-15', 'EMITIDO'),
    (2,  1000.00, '2024-01-20', 'EMITIDO'),
    (4,  750.00,  '2024-02-01', 'EMITIDO'),
    (5,  500.00,  '2024-02-10', 'EMITIDO'),
    (6,  2000.00, '2024-02-15', 'EMITIDO'),
    (7,  500.00,  '2024-03-01', 'CANJEADO'),
    (8,  1500.00, '2024-03-10', 'EMITIDO'),
    (9,  500.00,  '2024-03-15', 'EMITIDO'),
    (10, 1000.00, '2024-04-01', 'EMITIDO'),
    (3,  500.00,  '2023-12-01', 'ANULADO');

INSERT INTO cuenta_ahorro (socio_id, numero, saldo_disponible, saldo_bloqueado, estado) VALUES
    (1,  'CA-2024-00001', 1500.00,  0.00,    'ACTIVA'),
    (2,  'CA-2024-00002', 3200.50,  500.00,  'ACTIVA'),
    (3,  'CA-2024-00003', 0.00,     0.00,    'INACTIVA'),
    (4,  'CA-2024-00004', 8750.00,  0.00,    'ACTIVA'),
    (5,  'CA-2024-00005', 430.75,   0.00,    'ACTIVA'),
    (6,  'CA-2024-00006', 12000.00, 2000.00, 'ACTIVA'),
    (7,  'CA-2024-00007', 675.25,   0.00,    'ACTIVA'),
    (8,  'CA-2024-00008', 5100.00,  0.00,    'ACTIVA'),
    (9,  'CA-2024-00009', 920.00,   100.00,  'ACTIVA'),
    (10, 'CA-2024-00010', 2400.00,  0.00,    'ACTIVA');

INSERT INTO deposito_plazo_fijo (socio_id, monto, tasa_interes_anual, plazo_dias, fecha_inicio, fecha_vencimiento, interes_calculado, estado) VALUES
    (1,  5000.00,  5.50, 180, '2024-01-10', '2024-07-08',  135.62, 'VENCIDO'),
    (4,  10000.00, 6.00, 365, '2024-02-01', '2025-02-01',  600.00, 'VIGENTE'),
    (6,  20000.00, 6.50, 270, '2024-03-01', '2024-11-26', 1315.07, 'VIGENTE'),
    (8,  8000.00,  5.75, 90,  '2024-04-01', '2024-06-30',  113.42, 'VENCIDO'),
    (10, 15000.00, 6.25, 365, '2024-04-15', '2025-04-15',  937.50, 'VIGENTE');

INSERT INTO liquidacion (deposito_plazo_fijo_id, monto_capital_retornado, monto_interes_pagado, tipo_operacion) VALUES
    (1, 5000.00, 135.62, 'VENCIMIENTO'),
    (4, 8000.00, 113.42, 'VENCIMIENTO');

INSERT INTO solicitud_credito (socio_id, usuario_id, monto, plazo, tasa_interes, sistema_amortizacion, estado, fecha_solicitud) VALUES
    (1,  3, 5000.00,  12, 1.50, 'FRANCES',   'DESEMBOLSADO', '2024-01-20'),
    (2,  3, 10000.00, 24, 1.20, 'FRANCES',   'DESEMBOLSADO', '2024-02-01'),
    (4,  4, 3000.00,  6,  1.80, 'ALEMAN',    'APROBADO',     '2024-02-10'),
    (5,  3, 7500.00,  18, 1.35, 'FRANCES',   'EN_REVISION',  '2024-03-01'),
    (6,  4, 20000.00, 36, 1.10, 'FRANCES',   'APROBADO',     '2024-03-05'),
    (7,  3, 2000.00,  6,  1.80, 'ALEMAN',    'RECHAZADO',    '2024-03-10'),
    (8,  4, 15000.00, 24, 1.20, 'FRANCES',   'PENDIENTE',    '2024-04-01'),
    (9,  3, 4500.00,  12, 1.50, 'AMERICANO', 'EN_REVISION',  '2024-04-05'),
    (10, 4, 8000.00,  18, 1.35, 'FRANCES',   'PENDIENTE',    '2024-04-10'),
    (1,  3, 3000.00,  12, 1.50, 'FRANCES',   'PENDIENTE',    '2024-04-15');

INSERT INTO evaluacion_campo (solicitud_credito_id, usuario_id, ingreso_mensual, egreso_mensual, capacidad_pago, fotografias_respaldo, latitud, longitud) VALUES
    (1, 3, 2500.00, 1200.00, 1300.00, '["https://storage.coop/eval/1/foto1.jpg","https://storage.coop/eval/1/foto2.jpg"]', -16.500100, -68.150200),
    (2, 3, 4200.00, 2100.00, 2100.00, '["https://storage.coop/eval/2/foto1.jpg"]',                                         -17.393900, -66.157200),
    (3, 4, 1800.00,  900.00,  900.00, '["https://storage.coop/eval/3/foto1.jpg","https://storage.coop/eval/3/foto2.jpg"]', -17.980000, -67.106100),
    (4, 3, 3100.00, 1500.00, 1600.00, '["https://storage.coop/eval/4/foto1.jpg"]',                                         -16.500100, -68.150200),
    (5, 4, 8500.00, 4000.00, 4500.00, '["https://storage.coop/eval/5/foto1.jpg","https://storage.coop/eval/5/foto2.jpg"]', -17.783300, -63.182100),
    (7, 4, 6000.00, 2800.00, 3200.00, '["https://storage.coop/eval/7/foto1.jpg"]',                                         -16.500100, -68.150200),
    (8, 3, 2200.00, 1100.00, 1100.00, '["https://storage.coop/eval/8/foto1.jpg"]',                                         -17.393900, -66.157200);

INSERT INTO prediccion_morosidad (solicitud_credito_id, probabilidad_incumplimiento, nivel_riesgo, factores_riesgo_json) VALUES
    (1, 0.0823, 'BAJO',  '{"capacidad_pago": 1300.00, "ratio_deuda_ingreso": 0.26, "historial_pagos": "BUENO"}'),
    (2, 0.1540, 'BAJO',  '{"capacidad_pago": 2100.00, "ratio_deuda_ingreso": 0.31, "historial_pagos": "BUENO"}'),
    (3, 0.3210, 'MEDIO', '{"capacidad_pago":  900.00, "ratio_deuda_ingreso": 0.45, "historial_pagos": "REGULAR"}'),
    (4, 0.2870, 'MEDIO', '{"capacidad_pago": 1600.00, "ratio_deuda_ingreso": 0.39, "historial_pagos": "REGULAR"}'),
    (5, 0.0510, 'BAJO',  '{"capacidad_pago": 4500.00, "ratio_deuda_ingreso": 0.19, "historial_pagos": "EXCELENTE"}'),
    (6, 0.7340, 'ALTO',  '{"capacidad_pago":  900.00, "ratio_deuda_ingreso": 0.68, "historial_pagos": "MALO"}'),
    (7, 0.2100, 'MEDIO', '{"capacidad_pago": 3200.00, "ratio_deuda_ingreso": 0.33, "historial_pagos": "BUENO"}');

INSERT INTO alerta_cobranza (prediccion_morosidad_id, tipo, mensaje, estado) VALUES
    (3, 'PREVENTIVA', 'Socio con ratio deuda/ingreso elevado. Se recomienda seguimiento mensual.',          'ENVIADA'),
    (4, 'PREVENTIVA', 'Historial de pagos regular. Monitorear primeras cuotas.',                            'PENDIENTE'),
    (6, 'INTENSIVA',  'Alto riesgo de incumplimiento. Solicitud rechazada. Notificar al area de cobranza.', 'GESTIONADA'),
    (7, 'PREVENTIVA', 'Riesgo medio. Verificar estabilidad laboral del socio.',                             'PENDIENTE');

INSERT INTO tabla_amortizacion (solicitud_credito_id, numero_cuota, fecha_vencimiento, monto_capital, monto_interes, monto_cuota_total, estado_pago) VALUES
    (1,  1,  '2024-02-20', 387.05, 75.00, 462.05, 'PAGADO'),
    (1,  2,  '2024-03-20', 392.86, 69.19, 462.05, 'PAGADO'),
    (1,  3,  '2024-04-20', 398.76, 63.29, 462.05, 'PAGADO'),
    (1,  4,  '2024-05-20', 404.74, 57.31, 462.05, 'PAGADO'),
    (1,  5,  '2024-06-20', 410.81, 51.24, 462.05, 'PAGADO'),
    (1,  6,  '2024-07-20', 416.97, 45.08, 462.05, 'PAGADO'),
    (1,  7,  '2024-08-20', 423.22, 38.83, 462.05, 'PENDIENTE'),
    (1,  8,  '2024-09-20', 429.57, 32.48, 462.05, 'PENDIENTE'),
    (1,  9,  '2024-10-20', 436.01, 26.04, 462.05, 'PENDIENTE'),
    (1,  10, '2024-11-20', 442.55, 19.50, 462.05, 'PENDIENTE'),
    (1,  11, '2024-12-20', 449.18, 12.87, 462.05, 'PENDIENTE'),
    (1,  12, '2025-01-20', 455.91,  6.14, 462.05, 'PENDIENTE');

INSERT INTO pago_cuota (tabla_amortizacion_id, monto_capital, monto_interes_pagado, monto_mora) VALUES
    (1,  387.05, 75.00, 0.00),
    (2,  392.86, 69.19, 0.00),
    (3,  398.76, 63.29, 0.00),
    (4,  404.74, 57.31, 0.00),
    (5,  410.81, 51.24, 0.00),
    (6,  416.97, 45.08, 5.00);


INSERT INTO caja (nombre, estado) VALUES
    ('CAJA PRINCIPAL',  'ABIERTA'),
    ('CAJA SECUNDARIA', 'ABIERTA'),
    ('CAJA NOCTURNA',   'CERRADA');

INSERT INTO control_caja (caja_id, usuario_id, monto_apertura, monto_cierre, saldo_sistema, fecha_apertura, fecha_cierre, estado) VALUES
    (1, 5, 1000.00, NULL,    NULL,    '2024-04-15 08:00:00+00', NULL,                      'ABIERTA'),
    (2, 6, 500.00,  NULL,    NULL,    '2024-04-15 08:05:00+00', NULL,                      'ABIERTA'),
    (1, 5, 1000.00, 4200.00, 4200.00, '2024-04-14 08:00:00+00', '2024-04-14 17:00:00+00', 'CERRADA'),
    (2, 6, 500.00,  2800.00, 2800.00, '2024-04-14 08:05:00+00', '2024-04-14 17:05:00+00', 'CERRADA'),
    (3, 5, 800.00,  1500.00, 1500.00, '2024-04-13 08:00:00+00', '2024-04-13 17:00:00+00', 'CERRADA');

INSERT INTO transaccion (control_caja_id, cuenta_ahorro_id, deposito_plazo_fijo_id, pago_cuota_id, tipo, monto, canal, descripcion) VALUES
    (1, 1,    NULL, NULL, 'DEPOSITO',      500.00,  'VENTANILLA',  'Deposito en efectivo'),
    (1, 2,    NULL, NULL, 'DEPOSITO',      1000.00, 'VENTANILLA',  'Deposito en efectivo'),
    (2, 4,    NULL, NULL, 'DEPOSITO',      2000.00, 'VENTANILLA',  'Deposito en efectivo'),
    (2, 5,    NULL, NULL, 'RETIRO',        200.00,  'VENTANILLA',  'Retiro en ventanilla'),
    (1, 6,    NULL, NULL, 'DEPOSITO',      5000.00, 'VENTANILLA',  'Deposito empresarial'),
    (2, 8,    NULL, NULL, 'RETIRO',        300.00,  'VENTANILLA',  'Retiro en ventanilla'),
    (NULL, 1, NULL, NULL, 'DEPOSITO',      250.00,  'APP_MOBILE',  'Deposito desde app movil'),
    (NULL, 2, NULL, NULL, 'RETIRO',        150.00,  'WEB',         'Retiro por banca web'),
    (1, NULL, 2,    NULL, 'APERTURA_DPF',  10000.00,'VENTANILLA',  'Apertura deposito a plazo fijo'),
    (2, NULL, 3,    NULL, 'APERTURA_DPF',  20000.00,'VENTANILLA',  'Apertura deposito a plazo fijo'),
    (1, NULL, NULL, 1,    'PAGO_CREDITO',  462.05,  'VENTANILLA',  'Pago cuota 1 credito socio Roberto'),
    (1, NULL, NULL, 2,    'PAGO_CREDITO',  462.05,  'VENTANILLA',  'Pago cuota 2 credito socio Roberto'),
    (NULL, NULL,NULL,3,   'PAGO_CREDITO',  462.05,  'APP_MOBILE',  'Pago cuota 3 desde app movil'),
    (NULL, NULL,NULL,4,   'PAGO_CREDITO',  462.05,  'APP_MOBILE',  'Pago cuota 4 desde app movil'),
    (NULL, NULL,NULL,5,   'PAGO_CREDITO',  462.05,  'WEB',         'Pago cuota 5 por banca web'),
    (1, NULL, NULL, 6,    'PAGO_CREDITO',  467.05,  'VENTANILLA',  'Pago cuota 6 con mora incluida');

INSERT INTO plan_cuenta (codigo, nombre, nivel, tipo) VALUES
    ('1',        'ACTIVO',                          1, 'ACTIVO'),
    ('1.1',      'ACTIVO CORRIENTE',                2, 'ACTIVO'),
    ('1.1.01',   'CAJA',                            3, 'ACTIVO'),
    ('1.1.01.01','CAJA PRINCIPAL',                  4, 'ACTIVO'),
    ('1.1.01.02','CAJA SECUNDARIA',                 4, 'ACTIVO'),
    ('1.1.02',   'CUENTAS DE AHORRO',               3, 'ACTIVO'),
    ('1.1.03',   'DEPOSITOS A PLAZO FIJO',          3, 'ACTIVO'),
    ('1.1.04',   'CARTERA DE CREDITOS',             3, 'ACTIVO'),
    ('2',        'PASIVO',                          1, 'PASIVO'),
    ('2.1',      'PASIVO CORRIENTE',                2, 'PASIVO'),
    ('2.1.01',   'OBLIGACIONES CON SOCIOS',         3, 'PASIVO'),
    ('2.1.02',   'INTERESES POR PAGAR DPF',         3, 'PASIVO'),
    ('3',        'PATRIMONIO',                      1, 'PATRIMONIO'),
    ('3.1',      'CAPITAL SOCIAL',                  2, 'PATRIMONIO'),
    ('3.1.01',   'CERTIFICADOS DE APORTACION',      3, 'PATRIMONIO'),
    ('4',        'INGRESOS',                        1, 'INGRESO'),
    ('4.1',      'INGRESOS FINANCIEROS',            2, 'INGRESO'),
    ('4.1.01',   'INTERESES GANADOS CREDITOS',      3, 'INGRESO'),
    ('4.1.02',   'INTERESES GANADOS DPF',           3, 'INGRESO'),
    ('4.1.03',   'INGRESOS POR MORA',               3, 'INGRESO'),
    ('5',        'EGRESOS',                         1, 'EGRESO'),
    ('5.1',      'EGRESOS FINANCIEROS',             2, 'EGRESO'),
    ('5.1.01',   'INTERESES PAGADOS DPF',           3, 'EGRESO'),
    ('5.1.02',   'GASTOS ADMINISTRATIVOS',          3, 'EGRESO');

INSERT INTO comprobante_contable (transaccion_id, tipo, glosa, es_automatico) VALUES
    (1,  'INGRESO',   'Deposito en efectivo cuenta CA-2024-00001',       TRUE),
    (2,  'INGRESO',   'Deposito en efectivo cuenta CA-2024-00002',       TRUE),
    (3,  'INGRESO',   'Deposito en efectivo cuenta CA-2024-00004',       TRUE),
    (4,  'EGRESO',    'Retiro en ventanilla cuenta CA-2024-00005',       TRUE),
    (9,  'INGRESO',   'Apertura DPF socio Carmen Huanca 10000.00',       TRUE),
    (10, 'INGRESO',   'Apertura DPF socio Patricia Morales 20000.00',    TRUE),
    (11, 'INGRESO',   'Pago cuota 1 credito Roberto Chavez',             TRUE),
    (16, 'INGRESO',   'Pago cuota 6 con mora credito Roberto Chavez',    TRUE);

INSERT INTO detalle_asiento (comprobante_contable_id, plan_cuenta_id, debe, haber) VALUES
    (1, 3, 500.00,   0.00),   
    (1, 6, 0.00,   500.00),   
    (2, 3, 1000.00,  0.00),
    (2, 6, 0.00,  1000.00),
    (3, 4, 2000.00,  0.00),   
    (3, 6, 0.00,  2000.00),

    (4, 11, 200.00,  0.00),    
    (4, 3,  0.00,  200.00),    

    (5, 3,  10000.00, 0.00),
    (5, 7,  0.00, 10000.00),
    (6, 4,  20000.00, 0.00),
    (6, 7,  0.00, 20000.00),

    (7, 3,  462.05,  0.00),
    (7, 8,  0.00,  387.05),    
    (7, 18, 0.00,   75.00),    
    
    (8, 3,  467.05,  0.00),
    (8, 8,  0.00,  416.97),
    (8, 18, 0.00,   45.08),
    (8, 20, 0.00,    5.00); 

INSERT INTO bitacora (usuario_id, modulo, accion, descripcion, ip, user_agent) VALUES
    (1, 'USUARIO',     'CREAR',    'Creacion de usuario cajero1',                 '192.168.1.10', 'Angular/FastAPI'),
    (1, 'ROL',         'EDITAR',   'Modificacion de permisos rol CAJERO',         '192.168.1.10', 'Angular/FastAPI'),
    (2, 'REPORTE',     'GENERAR',  'Reporte mensual de transacciones',            '192.168.1.15', 'Angular/FastAPI'),
    (3, 'CREDITO',     'CREAR',    'Solicitud credito socio Roberto Chavez',      '192.168.1.20', 'Angular/FastAPI'),
    (3, 'CREDITO',     'EDITAR',   'Actualizacion estado a DESEMBOLSADO',         '192.168.1.20', 'Angular/FastAPI'),
    (5, 'CAJA',        'ABRIR',    'Apertura caja principal monto 1000.00',       '192.168.1.25', 'Angular/FastAPI'),
    (5, 'TRANSACCION', 'CREAR',    'Deposito cuenta CA-2024-00001 monto 500.00',  '192.168.1.25', 'Flutter/1.0'),
    (6, 'CAJA',        'ABRIR',    'Apertura caja secundaria monto 500.00',       '192.168.1.26', 'Angular/FastAPI'),
    (4, 'DPF',         'CREAR',    'Apertura DPF socio Carmen Huanca 10000.00',   '192.168.1.25', 'Angular/FastAPI'),
    (7, 'CONTABILIDAD','VER',      'Consulta comprobantes mes abril 2024',        '192.168.1.30', 'Angular/FastAPI');

INSERT INTO reporte (usuario_id, tipo, formato, parametros) VALUES
    (2, 'TRANSACCIONES_MENSUAL',  'PDF',   '{"mes": 3, "anio": 2024, "caja_id": 1}'),
    (2, 'SOCIOS_ACTIVOS',         'EXCEL', '{"estado": "ACTIVO", "fecha_corte": "2024-04-01"}'),
    (1, 'CREDITOS_PENDIENTES',    'PDF',   '{"estado": "PENDIENTE", "asesor_id": 3}'),
    (7, 'BALANCE_GENERAL',        'PDF',   '{"fecha_inicio": "2024-01-01", "fecha_fin": "2024-04-15"}'),
    (7, 'ESTADO_RESULTADOS',      'EXCEL', '{"mes": 4, "anio": 2024}'),
    (2, 'DPF_VENCIMIENTOS',       'CSV',   '{"fecha_inicio": "2024-04-01", "fecha_fin": "2024-12-31"}');