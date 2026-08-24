CREATE TABLE ROL (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL,
    descripcion VARCHAR(255)
);

CREATE TABLE PERMISO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    descripcion VARCHAR(255)
);

CREATE TABLE ROL_PERMISO (
    rol_id INT NOT NULL REFERENCES ROL(id) ON DELETE CASCADE,
    permiso_id INT NOT NULL REFERENCES PERMISO(id) ON DELETE CASCADE,
    PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE COOPERATIVA (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid(),
    nombre VARCHAR(150) NOT NULL,
    razon_social VARCHAR(200),
    nit VARCHAR(20) UNIQUE,
    correo VARCHAR(150),
    telefono VARCHAR(20),
    direccion TEXT,
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fecha_baja TIMESTAMPTZ,
    CONSTRAINT uq_cooperativa_uuid UNIQUE (uuid),
    CONSTRAINT chk_cooperativa_estado CHECK (estado IN ('ACTIVO','INACTIVO'))
);

CREATE INDEX idx_cooperativa_estado ON COOPERATIVA(estado);
CREATE INDEX idx_cooperativa_nombre ON COOPERATIVA(nombre);

CREATE TABLE USUARIO (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid(),
    rol_id SMALLINT NOT NULL REFERENCES ROL(id),
    cooperativa_id BIGINT REFERENCES COOPERATIVA(id),
    nombre VARCHAR(100) NOT NULL,
    contrasena VARCHAR(255) NOT NULL,
    correo VARCHAR(150) UNIQUE NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fecha_baja TIMESTAMPTZ,
    CONSTRAINT uq_usuario_uuid UNIQUE (uuid),
    CONSTRAINT chk_usuario_estado CHECK (estado IN ('ACTIVO','INACTIVO','BLOQUEADO')),
    CONSTRAINT chk_usuario_correo CHECK (correo ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
);

CREATE INDEX idx_usuario_estado ON USUARIO(estado);
CREATE INDEX idx_usuario_cooperativa ON USUARIO(cooperativa_id);

CREATE TABLE BITACORA (
    id BIGSERIAL PRIMARY KEY,
    usuario_id BIGINT NOT NULL REFERENCES USUARIO(id),
    modulo VARCHAR(50) NOT NULL,
    accion VARCHAR(100) NOT NULL,
    descripcion TEXT,
    ip VARCHAR(45),
    user_agent TEXT,
    fecha_hora TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE REPORTE (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo VARCHAR(50) NOT NULL,
    formato VARCHAR(10) NOT NULL,
    parametros TEXT,
    fecha_generacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usuario_id INT NOT NULL REFERENCES USUARIO(id)
);

CREATE TABLE MONEDA (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo_iso VARCHAR(3) UNIQUE NOT NULL,
    nombre VARCHAR(50) NOT NULL,
    simbolo VARCHAR(5) NOT NULL,
    es_moneda_base BOOLEAN DEFAULT FALSE
);

CREATE TABLE TIPO_DE_CAMBIO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tasa_compra NUMERIC(10, 4) NOT NULL,
    tasa_venta NUMERIC(10, 4) NOT NULL,
    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    moneda_id INT NOT NULL REFERENCES MONEDA(id)
);

CREATE TABLE SOCIO (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid(),
    ci VARCHAR(20) UNIQUE NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    apellido VARCHAR(100) NOT NULL,
    direccion TEXT,
    telefono VARCHAR(20),
    correo VARCHAR(150),
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
    fecha_registro DATE NOT NULL DEFAULT CURRENT_DATE,
    fecha_baja DATE,
    usuario_id BIGINT REFERENCES USUARIO(id),
    CONSTRAINT uq_socio_ci UNIQUE (ci),
    CONSTRAINT chk_socio_estado CHECK (estado IN ('ACTIVO','INACTIVO'))
);

CREATE TABLE CERTIFICADO_APORTACION (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto NUMERIC(12, 2) NOT NULL,
    fecha_emision DATE NOT NULL,
    estado VARCHAR(20) DEFAULT 'EMITIDO',
    socio_id INT NOT NULL REFERENCES SOCIO(id),
    moneda_id INT NOT NULL REFERENCES MONEDA(id)
);

CREATE TABLE EVALUACION_CAMPO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingreso_mensual NUMERIC(12, 2) NOT NULL,
    egreso_mensual NUMERIC(12, 2) NOT NULL,
    capacidad_pago NUMERIC(12, 2) NOT NULL,
    fotografias_respaldo TEXT,
    coordenadas VARCHAR(100),
    fecha DATE NOT NULL,
    resumen_cualitativo_ia TEXT,
    usuario_id INT NOT NULL REFERENCES USUARIO(id)
);

CREATE TABLE SOLICITUD_CREDITO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto NUMERIC(12, 2) NOT NULL,
    plazo_meses INT NOT NULL,
    tasa_interes NUMERIC(5, 2) NOT NULL,
    calificacion_asfi VARCHAR(10),
    tiene_deudas BOOLEAN DEFAULT FALSE,
    estado VARCHAR(20) DEFAULT 'PENDIENTE',
    socio_id INT NOT NULL REFERENCES SOCIO(id),
    usuario_id INT NOT NULL REFERENCES USUARIO(id),
    evaluacion_campo_id INT REFERENCES EVALUACION_CAMPO(id)
);

CREATE TABLE CREDITO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto_aprobado NUMERIC(12, 2) NOT NULL,
    saldo_pendiente NUMERIC(12, 2) NOT NULL,
    estado VARCHAR(20) DEFAULT 'VIGENTE',
    solicitud_credito_id INT UNIQUE NOT NULL REFERENCES SOLICITUD_CREDITO(id)
);

CREATE TABLE TABLA_AMORTIZACION (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    numero_cuota INT NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    monto_capital NUMERIC(12, 2) NOT NULL,
    monto_interes NUMERIC(12, 2) NOT NULL,
    monto_cuota_total NUMERIC(12, 2) NOT NULL,
    estado_pago VARCHAR(20) DEFAULT 'PENDIENTE',
    credito_id INT NOT NULL REFERENCES CREDITO(id)
);

CREATE TABLE PAGO_CUOTA (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto_capital NUMERIC(12, 2) NOT NULL,
    monto_interes_pagado NUMERIC(12, 2) NOT NULL,
    monto_mora NUMERIC(12, 2) DEFAULT 0.00,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tabla_amortizacion_id INT NOT NULL REFERENCES TABLA_AMORTIZACION(id)
);

CREATE TABLE HISTORIAL_GESTION_DE_COBRANZA (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo_contacto VARCHAR(50) NOT NULL,
    resultado_gestion TEXT NOT NULL,
    fecha_de_compromiso_de_pago DATE,
    credito_id INT NOT NULL REFERENCES CREDITO(id)
);

CREATE TABLE MOROSIDAD (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dias_de_retaso INT NOT NULL,
    monto_penalizado NUMERIC(12, 2) DEFAULT 0.00,
    estado VARCHAR(20) DEFAULT 'EN_MORA',
    credito_id INT NOT NULL REFERENCES CREDITO(id)
);

CREATE TABLE PREDICCION_DE_MOROSIDAD (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    probabilidad_de_incumplimiento NUMERIC(5, 4) NOT NULL,
    nivel_de_riesgo VARCHAR(20) NOT NULL,
    credito_id INT NOT NULL REFERENCES CREDITO(id)
);

CREATE TABLE CAJA (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL,
    estado VARCHAR(20) DEFAULT 'CERRADA'
);

CREATE TABLE CONTROL_CAJA (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto_apertura NUMERIC(12, 2) NOT NULL,
    monto_cierre NUMERIC(12, 2),
    saldo_sistema NUMERIC(12, 2) NOT NULL,
    fecha_apertura TIMESTAMP NOT NULL,
    fecha_cierre TIMESTAMP,
    estado VARCHAR(20) DEFAULT 'ABIERTA',
    caja_id INT NOT NULL REFERENCES CAJA(id),
    usuario_id INT NOT NULL REFERENCES USUARIO(id)
);

CREATE TABLE CUENTA_AHORRO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    numero VARCHAR(30) UNIQUE NOT NULL,
    saldo_disponible NUMERIC(12, 2) DEFAULT 0.00,
    saldo_bloqueado NUMERIC(12, 2) DEFAULT 0.00,
    estado VARCHAR(20) DEFAULT 'ACTIVA',
    fecha_registro DATE NOT NULL,
    socio_id INT NOT NULL REFERENCES SOCIO(id),
    moneda_id INT NOT NULL REFERENCES MONEDA(id)
);

CREATE TABLE DEPOSITO_PLAZO_FIJO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto NUMERIC(12, 2) NOT NULL,
    tasa_interes_anual NUMERIC(5, 2) NOT NULL,
    plazo_dias INT NOT NULL,
    fecha_inicio DATE NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    interes_calculado NUMERIC(12, 2) NOT NULL,
    estado VARCHAR(20) DEFAULT 'VIGENTE',
    socio_id INT NOT NULL REFERENCES SOCIO(id),
    moneda_id INT NOT NULL REFERENCES MONEDA(id)
);

CREATE TABLE LIQUIDACION (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monto_capital_retornado NUMERIC(12, 2) NOT NULL,
    monto_interes_pagado NUMERIC(12, 2) NOT NULL,
    tipo_operacion VARCHAR(50) NOT NULL,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deposito_plazo_fijo_id INT UNIQUE NOT NULL REFERENCES DEPOSITO_PLAZO_FIJO(id)
);

CREATE TABLE DECLARACION_JURADA_UIF (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    origen VARCHAR(255) NOT NULL,
    destino VARCHAR(255) NOT NULL
);

CREATE TABLE TRANSACCION (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo VARCHAR(50) NOT NULL,
    monto NUMERIC(12, 2) NOT NULL,
    canal VARCHAR(50) NOT NULL,
    fecha_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    control_caja_id INT REFERENCES CONTROL_CAJA(id),
    moneda_id INT NOT NULL REFERENCES MONEDA(id),
    cuenta_ahorro_id INT REFERENCES CUENTA_AHORRO(id),
    deposito_plazo_fijo_id INT REFERENCES DEPOSITO_PLAZO_FIJO(id),
    pago_cuota_id INT REFERENCES PAGO_CUOTA(id),
    liquidacion_id INT REFERENCES LIQUIDACION(id),
    declaracion_jurada_uif_id INT REFERENCES DECLARACION_JURADA_UIF(id),
    transaccion_reversion_id INT REFERENCES TRANSACCION(id)
);

CREATE TABLE PLAN_CUENTA (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo VARCHAR(30) UNIQUE NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    nivel INT NOT NULL,
    tipo VARCHAR(50) NOT NULL,
    plan_cuenta_padre_id INT REFERENCES PLAN_CUENTA(id)
);

CREATE TABLE COMPROBANTE_CONTABLE (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo VARCHAR(50) NOT NULL,
    glosa TEXT NOT NULL,
    es_automatico BOOLEAN DEFAULT TRUE,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    transaccion_id INT REFERENCES TRANSACCION(id)
);

CREATE TABLE DETALLE_ASIENTO (
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    debe NUMERIC(12, 2) DEFAULT 0.00,
    haber NUMERIC(12, 2) DEFAULT 0.00,
    comprobante_contable_id INT NOT NULL REFERENCES COMPROBANTE_CONTABLE(id) ON DELETE CASCADE,
    plan_cuenta_id INT NOT NULL REFERENCES PLAN_CUENTA(id)
);

CREATE INDEX idx_bitacora_usuario_fecha ON BITACORA(usuario_id, fecha_hora);
CREATE INDEX idx_reporte_usuario_fecha ON REPORTE(usuario_id, fecha_generacion);

CREATE INDEX idx_socio_ci ON SOCIO(ci);
CREATE INDEX idx_socio_estado ON SOCIO(estado);
CREATE INDEX idx_socio_nombres ON SOCIO(apellido, nombre);

CREATE INDEX idx_cuenta_socio ON CUENTA_AHORRO(socio_id);
CREATE INDEX idx_cuenta_numero ON CUENTA_AHORRO(numero);
CREATE INDEX idx_cuenta_estado ON CUENTA_AHORRO(estado);

CREATE INDEX idx_solicitud_socio ON SOLICITUD_CREDITO(socio_id);
CREATE INDEX idx_solicitud_estado ON SOLICITUD_CREDITO(estado);
CREATE INDEX idx_solicitud_usuario ON SOLICITUD_CREDITO(usuario_id);

CREATE INDEX idx_credito_estado ON CREDITO(estado);

CREATE INDEX idx_amortizacion_credito_pago ON TABLA_AMORTIZACION(credito_id, estado_pago);
CREATE INDEX idx_amortizacion_vencimiento ON TABLA_AMORTIZACION(fecha_vencimiento, estado_pago);

CREATE INDEX idx_pagocuota_amortizacion ON PAGO_CUOTA(tabla_amortizacion_id);
CREATE INDEX idx_pagocuota_fecha ON PAGO_CUOTA(fecha);

CREATE INDEX idx_morosidad_credito ON MOROSIDAD(credito_id);
CREATE INDEX idx_morosidad_estado ON MOROSIDAD(estado, dias_de_retaso);
CREATE INDEX idx_prediccion_credito ON PREDICCION_DE_MOROSIDAD(credito_id);
CREATE INDEX idx_prediccion_riesgo ON PREDICCION_DE_MOROSIDAD(nivel_de_riesgo);

CREATE INDEX idx_dpf_socio ON DEPOSITO_PLAZO_FIJO(socio_id);
CREATE INDEX idx_dpf_vencimiento ON DEPOSITO_PLAZO_FIJO(fecha_vencimiento, estado);
CREATE INDEX idx_certaportacion_socio ON CERTIFICADO_APORTACION(socio_id);

CREATE INDEX idx_controlcaja_caja ON CONTROL_CAJA(caja_id, estado);
CREATE INDEX idx_controlcaja_fecha ON CONTROL_CAJA(fecha_apertura, fecha_cierre);

CREATE INDEX idx_transaccion_controlcaja ON TRANSACCION(control_caja_id);
CREATE INDEX idx_transaccion_fecha ON TRANSACCION(fecha_hora);
CREATE INDEX idx_transaccion_monto ON TRANSACCION(monto);
CREATE INDEX idx_transaccion_cuenta ON TRANSACCION(cuenta_ahorro_id);
CREATE INDEX idx_transaccion_dpf ON TRANSACCION(deposito_plazo_fijo_id);

CREATE INDEX idx_plancuenta_codigo ON PLAN_CUENTA(codigo);
CREATE INDEX idx_plancuenta_padre ON PLAN_CUENTA(plan_cuenta_padre_id);

CREATE INDEX idx_comprobante_fecha ON COMPROBANTE_CONTABLE(fecha);
CREATE INDEX idx_comprobante_transaccion ON COMPROBANTE_CONTABLE(transaccion_id);

CREATE INDEX idx_detalleasiento_comprobante ON DETALLE_ASIENTO(comprobante_contable_id);
CREATE INDEX idx_detalleasiento_plancuenta ON DETALLE_ASIENTO(plan_cuenta_id);

-- seed de datos

INSERT INTO ROL (nombre, descripcion) VALUES
('SUPERADMIN', 'Super Administrador SaaS: gestiona cooperativas (tenants) de la plataforma'),
('ADMINISTRADOR', 'Administrador de la Cooperativa: acceso total dentro de su tenant'),
('CAJERO', 'Cajero / Ventanilla: gestión de operaciones de caja y atención al socio'),
('OFICIAL_CREDITO', 'Oficial de Crédito / Campo: evaluación y seguimiento de créditos en campo'),
('CONTADOR', 'Contador / Cumplimiento: contabilidad, reportes y control de cumplimiento'),
('SOCIO', 'Socio / Cliente: acceso limitado a su propia información dentro de la cooperativa');

INSERT INTO PERMISO (nombre, descripcion) VALUES
('SAAS_TENANT_MGMT', 'Crear, configurar y monitorear cooperativas/tenants'),
('INST_USER_MGMT', 'Gestionar usuarios, asignación de roles y permisos internos'),
('CAJA_OPERACIONES', 'Realizar depósitos, retiros, apertura y arqueo de caja'),
('CREDITO_EVALUACION', 'Registrar datos de campo, analizar scoring e ingresar solicitudes'),
('CONTABILIDAD_UIF', 'Generar libros diarios, balance de comprobación y reportes UIF/ASFI'),
('APP_SOCIO_READ', 'Consulta de saldos, extractos y solicitud de créditos en app móvil');

INSERT INTO ROL_PERMISO (rol_id, permiso_id) VALUES
(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6);

INSERT INTO USUARIO (nombre, contrasena, correo, estado, rol_id) VALUES
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'superadmin@saas-financial.com', 'ACTIVO', 1),
('Gerente Cooperativa', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'patustarqui@gmail.com', 'ACTIVO', 2),
('Carlos Cajero', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'ccajero@cooperativa.com', 'ACTIVO', 3),
('Ana Oficial Crédito', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'acredito@cooperativa.com', 'ACTIVO', 4),
('Roberto Contador', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'rcontador@cooperativa.com', 'ACTIVO', 5),
('Juan Pérez (Socio)', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'juan.perez@email.com', 'ACTIVO', 6);

INSERT INTO BITACORA (modulo, accion, descripcion, ip, usuario_id) VALUES
('SEGURIDAD', 'LOGIN', 'Inicio de sesión Super Admin SaaS', '192.168.1.10', 1),
('CAJA', 'APERTURA', 'Apertura de caja inicial ventanilla 1', '192.168.1.15', 3),
('CREDITO', 'EVALUACION', 'Evaluación de campo realizada a socio', '192.168.1.20', 4);


INSERT INTO REPORTE (tipo, formato, parametros, usuario_id) VALUES
('CARTERA_MORA', 'PDF', '{"fecha_cierre": "2026-08-01", "gestion": 2026}', 5);

INSERT INTO MONEDA (codigo_iso, nombre, simbolo, es_moneda_base) VALUES
('BOB', 'Boliviano', 'Bs.', TRUE),
('USD', 'Dólar Estadounidense', '$', FALSE);

INSERT INTO TIPO_DE_CAMBIO (tasa_compra, tasa_venta, moneda_id) VALUES
(6.8600, 6.9600, 2);

INSERT INTO SOCIO (ci, nombre, apellido, direccion, telefono, correo, estado, fecha_registro, usuario_id) VALUES
('1234567 LP', 'Juan', 'Pérez Gómez', 'Av. 6 de Agosto #123', '71234567', 'juan.perez@email.com', 'ACTIVO', '2025-01-10', 6),
('7654321 CB', 'Maria', 'Lopez Arce', 'Calle Jordán #456', '77654321', 'maria.lopez@email.com', 'ACTIVO', '2025-02-15', NULL);

INSERT INTO CERTIFICADO_APORTACION (monto, fecha_emision, estado, socio_id, moneda_id) VALUES
(1000.00, '2025-01-10', 'EMITIDO', 1, 1),
(1000.00, '2025-02-15', 'EMITIDO', 2, 1);

INSERT INTO EVALUACION_CAMPO (ingreso_mensual, egreso_mensual, capacidad_pago, fotografias_respaldo, coordenadas, fecha, resumen_cualitativo_ia, usuario_id) VALUES
(8000.00, 3000.00, 5000.00, 'tienda_foto1.jpg,inventario.jpg', '-16.5000,-68.1500', '2026-01-10', 'Negocio formal con flujo constante de caja. Excelente perfil de cumplimiento.', 4);

INSERT INTO SOLICITUD_CREDITO (monto, plazo_meses, tasa_interes, calificacion_asfi, tiene_deudas, estado, socio_id, usuario_id, evaluacion_campo_id) VALUES
(25000.00, 12, 14.50, 'A', FALSE, 'APROBADO', 1, 4, 1);

INSERT INTO CREDITO (monto_aprobado, saldo_pendiente, estado, solicitud_credito_id) VALUES
(25000.00, 20000.00, 'VIGENTE', 1);

INSERT INTO TABLA_AMORTIZACION (numero_cuota, fecha_vencimiento, monto_capital, monto_interes, monto_cuota_total, estado_pago, credito_id) VALUES
(1, '2026-02-10', '2000.00', '302.08', '2302.08', 'PAGADO', 1),
(2, '2026-03-10', '2024.16', '277.92', '2302.08', 'PENDIENTE', 1);

INSERT INTO PAGO_CUOTA (monto_capital, monto_interes_pagado, monto_mora, fecha, tabla_amortizacion_id) VALUES
(2000.00, 302.08, 0.00, '2026-02-08 10:30:00', 1);

INSERT INTO HISTORIAL_GESTION_DE_COBRANZA (tipo_contacto, resultado_gestion, fecha_de_compromiso_de_pago, credito_id) VALUES
('LLAMADA', 'Socio confirmó abono puntual de la siguiente cuota.', '2026-03-09', 1);

INSERT INTO MOROSIDAD (dias_de_retaso, monto_penalizado, estado, credito_id) VALUES
(0, 0.00, 'AL_DIA', 1);

INSERT INTO PREDICCION_DE_MOROSIDAD (probabilidad_de_incumplimiento, nivel_de_riesgo, credito_id) VALUES
(0.0380, 'BAJO', 1);

INSERT INTO CAJA (nombre, estado) VALUES
('Ventanilla 1 - Central', 'ABIERTA');

INSERT INTO CONTROL_CAJA (monto_apertura, monto_cierre, saldo_sistema, fecha_apertura, estado, caja_id, usuario_id) VALUES
(5000.00, NULL, 87302.08, '2026-08-23 08:00:00', 'ABIERTA', 1, 3);

INSERT INTO CUENTA_AHORRO (numero, saldo_disponible, saldo_bloqueado, estado, fecha_registro, socio_id, moneda_id) VALUES
('CA-BOB-1001', 15000.00, 0.00, 'ACTIVA', '2025-01-10', 1, 1),
('CA-USD-2001', 500.00, 0.00, 'ACTIVA', '2025-02-15', 2, 2);

INSERT INTO DEPOSITO_PLAZO_FIJO (monto, tasa_interes_anual, plazo_dias, fecha_inicio, fecha_vencimiento, interes_calculado, estado, socio_id, moneda_id) VALUES
(80000.00, 6.50, 360, '2025-08-01', '2026-07-27', 5200.00, 'LIQUIDADO', 2, 1);

INSERT INTO LIQUIDACION (monto_capital_retornado, monto_interes_pagado, tipo_operacion, fecha, deposito_plazo_fijo_id) VALUES
(80000.00, 5200.00, 'CANCELACION_DPF', '2026-07-27 11:00:00', 1);

INSERT INTO DECLARACION_JURADA_UIF (origen, destino) VALUES
('Ahorros provenientes de actividad comercial minorista', 'Inversión en Depósito a Plazo Fijo (DPF)');

INSERT INTO TRANSACCION (tipo, monto, canal, fecha_hora, control_caja_id, moneda_id, cuenta_ahorro_id, deposito_plazo_fijo_id, pago_cuota_id, liquidacion_id, declaracion_jurada_uif_id) VALUES
('PAGO_CUOTA', 2302.08, 'VENTANILLA', '2026-02-08 10:30:00', 1, 1, 1, NULL, 1, NULL, NULL),
('APERTURA_DPF', 80000.00, 'VENTANILLA', '2025-08-01 14:00:00', 1, 1, NULL, 1, NULL, NULL, 1);

INSERT INTO PLAN_CUENTA (codigo, nombre, nivel, tipo, plan_cuenta_padre_id) VALUES
('100-000', 'ACTIVO', 1, 'ACTIVO', NULL),
('110-000', 'DISPONIBILIDADES', 2, 'ACTIVO', 1),
('111-001', 'Caja Moneda Nacional', 3, 'ACTIVO', 2),
('130-000', 'CARTERA DE CRÉDITOS', 2, 'ACTIVO', 1),
('131-001', 'Créditos Vigentes MN', 3, 'ACTIVO', 4);

INSERT INTO COMPROBANTE_CONTABLE (tipo, glosa, es_automatico, transaccion_id) VALUES
('INGRESO', 'Comprobante de ingreso automático por amortización de cuota 1 crédito #1', TRUE, 1);

INSERT INTO DETALLE_ASIENTO (debe, haber, comprobante_contable_id, plan_cuenta_id) VALUES
(2302.08, 0.00, 1, 3), 
(0.00, 2000.00, 1, 5),
(0.00, 302.08, 1, 5); 