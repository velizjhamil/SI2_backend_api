create table rol (
    id int generated always as identity primary key,
    nombre varchar(50) not null,
    descripcion varchar(255)
);

create table permiso (
    id int generated always as identity primary key,
    nombre varchar(100) not null,
    descripcion varchar(255)
);

create table rol_permiso (
    rol_id int not null references rol(id) on delete cascade,
    permiso_id int not null references permiso(id) on delete cascade,
    primary key (rol_id, permiso_id)
);

create table cooperativa (
    id bigserial primary key,
    uuid uuid not null default gen_random_uuid(),
    nombre varchar(150) not null,
    razon_social varchar(200),
    nit varchar(20) unique,
    correo varchar(150),
    telefono varchar(20),
    direccion text,
    estado varchar(20) not null default 'ACTIVO',
    fecha_creacion timestamptz not null default now(),
    fecha_baja timestamptz,
    constraint uq_cooperativa_uuid unique (uuid),
    constraint chk_cooperativa_estado check (estado in ('ACTIVO','INACTIVO'))
);

create index idx_cooperativa_estado on cooperativa(estado);
create index idx_cooperativa_nombre on cooperativa(nombre);

create table usuario (
    id bigserial primary key,
    uuid uuid not null default gen_random_uuid(),
    rol_id smallint not null references rol(id),
    cooperativa_id bigint references cooperativa(id),
    nombre varchar(100) not null,
    contrasena varchar(255) not null,
    correo varchar(150) unique not null,
    estado varchar(20) not null default 'ACTIVO',
    fecha_creacion timestamptz not null default now(),
    fecha_baja timestamptz,
    constraint uq_usuario_uuid unique (uuid),
    constraint chk_usuario_estado check (estado in ('ACTIVO','INACTIVO','BLOQUEADO')),
    constraint chk_usuario_correo check (correo ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
);

create index idx_usuario_estado on usuario(estado);
create index idx_usuario_cooperativa on usuario(cooperativa_id);

CREATE TABLE BITACORA (
    id BIGSERIAL PRIMARY KEY,
    usuario_id BIGINT NOT NULL REFERENCES USUARIO(id),
    cooperativa_id BIGINT REFERENCES COOPERATIVA(id),
    modulo VARCHAR(50) NOT NULL,
    accion VARCHAR(100) NOT NULL,
    descripcion TEXT,
    ip VARCHAR(45),
    user_agent TEXT,
    fecha_hora TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

create table reporte (
    id int generated always as identity primary key,
    tipo varchar(50) not null,
    formato varchar(10) not null,
    parametros text,
    fecha_generacion timestamp default current_timestamp,
    usuario_id int not null references usuario(id)
);

create table moneda (
    id int generated always as identity primary key,
    codigo_iso varchar(3) unique not null,
    nombre varchar(50) not null,
    simbolo varchar(5) not null,
    es_moneda_base boolean default false
);

create table tipo_de_cambio (
    id int generated always as identity primary key,
    tasa_compra numeric(10,4) not null,
    tasa_venta numeric(10,4) not null,
    fecha_registro timestamp default current_timestamp,
    moneda_id int not null references moneda(id)
);

CREATE TABLE SOCIO (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid(),
    cooperativa_id BIGINT REFERENCES COOPERATIVA(id),
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

create table certificado_aportacion (
    id int generated always as identity primary key,
    monto numeric(12,2) not null,
    fecha_emision date not null,
    estado varchar(20) default 'EMITIDO',
    socio_id int not null references socio(id),
    moneda_id int not null references moneda(id)
);

create table evaluacion_campo (
    id int generated always as identity primary key,
    ingreso_mensual numeric(12,2) not null,
    egreso_mensual numeric(12,2) not null,
    capacidad_pago numeric(12,2) not null,
    fotografias_respaldo text,
    coordenadas varchar(100),
    fecha date not null,
    resumen_cualitativo_ia text,
    usuario_id int not null references usuario(id)
);

create table solicitud_credito (
    id int generated always as identity primary key,
    monto numeric(12,2) not null,
    plazo_meses int not null,
    tasa_interes numeric(5,2) not null,
    calificacion_asfi varchar(10),
    tiene_deudas boolean default false,
    estado varchar(20) default 'PENDIENTE',
    socio_id int not null references socio(id),
    usuario_id int not null references usuario(id),
    evaluacion_campo_id int references evaluacion_campo(id)
);

create table credito (
    id int generated always as identity primary key,
    monto_aprobado numeric(12,2) not null,
    saldo_pendiente numeric(12,2) not null,
    estado varchar(20) default 'VIGENTE',
    solicitud_credito_id int unique not null references solicitud_credito(id)
);

create table tabla_amortizacion (
    id int generated always as identity primary key,
    numero_cuota int not null,
    fecha_vencimiento date not null,
    monto_capital numeric(12,2) not null,
    monto_interes numeric(12,2) not null,
    monto_cuota_total numeric(12,2) not null,
    estado_pago varchar(20) default 'PENDIENTE',
    credito_id int not null references credito(id)
);

create table pago_cuota (
    id int generated always as identity primary key,
    monto_capital numeric(12,2) not null,
    monto_interes_pagado numeric(12,2) not null,
    monto_mora numeric(12,2) default 0.00,
    fecha timestamp default current_timestamp,
    tabla_amortizacion_id int not null references tabla_amortizacion(id)
);

create table historial_gestion_de_cobranza (
    id int generated always as identity primary key,
    tipo_contacto varchar(50) not null,
    resultado_gestion text not null,
    fecha_de_compromiso_de_pago date,
    credito_id int not null references credito(id)
);

create table morosidad (
    id int generated always as identity primary key,
    dias_de_retaso int not null,
    monto_penalizado numeric(12,2) default 0.00,
    estado varchar(20) default 'EN_MORA',
    credito_id int not null references credito(id)
);

create table prediccion_de_morosidad (
    id int generated always as identity primary key,
    probabilidad_de_incumplimiento numeric(5,4) not null,
    nivel_de_riesgo varchar(20) not null,
    credito_id int not null references credito(id)
);

create table caja (
    id int generated always as identity primary key,
    nombre varchar(50) not null,
    estado varchar(20) default 'CERRADA'
);

create table control_caja (
    id int generated always as identity primary key,
    monto_apertura numeric(12,2) not null,
    monto_cierre numeric(12,2),
    saldo_sistema numeric(12,2) not null,
    fecha_apertura timestamp not null,
    fecha_cierre timestamp,
    estado varchar(20) default 'ABIERTA',
    caja_id int not null references caja(id),
    usuario_id int not null references usuario(id)
);

create table cuenta_ahorro (
    id int generated always as identity primary key,
    numero varchar(30) unique not null,
    saldo_disponible numeric(12,2) default 0.00,
    saldo_bloqueado numeric(12,2) default 0.00,
    estado varchar(20) default 'ACTIVA',
    fecha_registro date not null,
    socio_id int not null references socio(id),
    moneda_id int not null references moneda(id)
);

create table deposito_plazo_fijo (
    id int generated always as identity primary key,
    monto numeric(12,2) not null,
    tasa_interes_anual numeric(5,2) not null,
    plazo_dias int not null,
    fecha_inicio date not null,
    fecha_vencimiento date not null,
    interes_calculado numeric(12,2) not null,
    estado varchar(20) default 'VIGENTE',
    socio_id int not null references socio(id),
    moneda_id int not null references moneda(id)
);

create table liquidacion (
    id int generated always as identity primary key,
    monto_capital_retornado numeric(12,2) not null,
    monto_interes_pagado numeric(12,2) not null,
    tipo_operacion varchar(50) not null,
    fecha timestamp default current_timestamp,
    deposito_plazo_fijo_id int unique not null references deposito_plazo_fijo(id)
);

create table declaracion_jurada_uif (
    id int generated always as identity primary key,
    origen varchar(255) not null,
    destino varchar(255) not null
);

create table transaccion (
    id int generated always as identity primary key,
    tipo varchar(50) not null,
    monto numeric(12,2) not null,
    canal varchar(50) not null,
    fecha_hora timestamp default current_timestamp,
    control_caja_id int references control_caja(id),
    moneda_id int not null references moneda(id),
    cuenta_ahorro_id int references cuenta_ahorro(id),
    deposito_plazo_fijo_id int references deposito_plazo_fijo(id),
    pago_cuota_id int references pago_cuota(id),
    liquidacion_id int references liquidacion(id),
    declaracion_jurada_uif_id int references declaracion_jurada_uif(id),
    transaccion_reversion_id int references transaccion(id)
);

create table plan_cuenta (
    id int generated always as identity primary key,
    codigo varchar(30) unique not null,
    nombre varchar(100) not null,
    nivel int not null,
    tipo varchar(50) not null,
    plan_cuenta_padre_id int references plan_cuenta(id)
);

create table comprobante_contable (
    id int generated always as identity primary key,
    tipo varchar(50) not null,
    glosa text not null,
    es_automatico boolean default true,
    fecha timestamp default current_timestamp,
    transaccion_id int references transaccion(id)
);

create table detalle_asiento (
    id int generated always as identity primary key,
    debe numeric(12,2) default 0.00,
    haber numeric(12,2) default 0.00,
    comprobante_contable_id int not null references comprobante_contable(id) on delete cascade,
    plan_cuenta_id int not null references plan_cuenta(id)
);

-- Índices adicionales
create index idx_bitacora_usuario_fecha on bitacora(usuario_id, fecha_hora);
create index idx_reporte_usuario_fecha on reporte(usuario_id, fecha_generacion);

CREATE INDEX idx_socio_ci ON SOCIO(ci);
CREATE INDEX idx_socio_cooperativa ON SOCIO(cooperativa_id);
CREATE INDEX idx_socio_usuario ON SOCIO(usuario_id);
CREATE INDEX idx_socio_estado ON SOCIO(estado);
CREATE INDEX idx_socio_nombres ON SOCIO(apellido, nombre);

create index idx_cuenta_socio on cuenta_ahorro(socio_id);
create index idx_cuenta_numero on cuenta_ahorro(numero);
create index idx_cuenta_estado on cuenta_ahorro(estado);

create index idx_solicitud_socio on solicitud_credito(socio_id);
create index idx_solicitud_estado on solicitud_credito(estado);
create index idx_solicitud_usuario on solicitud_credito(usuario_id);

create index idx_credito_estado on credito(estado);

create index idx_amortizacion_credito_pago on tabla_amortizacion(credito_id, estado_pago);
create index idx_amortizacion_vencimiento on tabla_amortizacion(fecha_vencimiento, estado_pago);

create index idx_pagocuota_amortizacion on pago_cuota(tabla_amortizacion_id);
create index idx_pagocuota_fecha on pago_cuota(fecha);

create index idx_morosidad_credito on morosidad(credito_id);
create index idx_morosidad_estado on morosidad(estado, dias_de_retaso);
create index idx_prediccion_credito on prediccion_de_morosidad(credito_id);
create index idx_prediccion_riesgo on prediccion_de_morosidad(nivel_de_riesgo);

create index idx_dpf_socio on deposito_plazo_fijo(socio_id);
create index idx_dpf_vencimiento on deposito_plazo_fijo(fecha_vencimiento, estado);
create index idx_certaportacion_socio on certificado_aportacion(socio_id);

create index idx_controlcaja_caja on control_caja(caja_id, estado);
create index idx_controlcaja_fecha on control_caja(fecha_apertura, fecha_cierre);

create index idx_transaccion_controlcaja on transaccion(control_caja_id);
create index idx_transaccion_fecha on transaccion(fecha_hora);
create index idx_transaccion_monto on transaccion(monto);
create index idx_transaccion_cuenta on transaccion(cuenta_ahorro_id);
create index idx_transaccion_dpf on transaccion(deposito_plazo_fijo_id);

create index idx_plancuenta_codigo on plan_cuenta(codigo);
create index idx_plancuenta_padre on plan_cuenta(plan_cuenta_padre_id);

create index idx_comprobante_fecha on comprobante_contable(fecha);
create index idx_comprobante_transaccion on comprobante_contable(transaccion_id);

create index idx_detalleasiento_comprobante on detalle_asiento(comprobante_contable_id);
create index idx_detalleasiento_plancuenta on detalle_asiento(plan_cuenta_id);

-- seed de datos

insert into rol (nombre, descripcion) values
('SUPERADMIN', 'Super Administrador SaaS: gestiona cooperativas (tenants) de la plataforma'),
('ADMINISTRADOR', 'Administrador de la Cooperativa: acceso total dentro de su tenant'),
('CAJERO', 'Cajero / Ventanilla: gestión de operaciones de caja y atención al socio'),
('OFICIAL_CREDITO', 'Oficial de Crédito / Campo: evaluación y seguimiento de créditos en campo'),
('CONTADOR', 'Contador / Cumplimiento: contabilidad, reportes y control de cumplimiento'),
('SOCIO', 'Socio / Cliente: acceso limitado a su propia información dentro de la cooperativa');

insert into permiso (nombre, descripcion) values
('SAAS_TENANT_MGMT', 'Crear, configurar y monitorear cooperativas/tenants'),
('INST_USER_MGMT', 'Gestionar usuarios, asignación de roles y permisos internos'),
('CAJA_OPERACIONES', 'Realizar depósitos, retiros, apertura y arqueo de caja'),
('CREDITO_EVALUACION', 'Registrar datos de campo, analizar scoring e ingresar solicitudes'),
('CONTABILIDAD_UIF', 'Generar libros diarios, balance de comprobación y reportes UIF/ASFI'),
('APP_SOCIO_READ', 'Consulta de saldos, extractos y solicitud de créditos en app móvil');

insert into rol_permiso (rol_id, permiso_id) values
(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6);

insert into usuario (nombre, contrasena, correo, estado, rol_id) values
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'yimyt771@gmail.com', 'ACTIVO', 1),
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'nicolasrevolloroman@gmail.com', 'ACTIVO', 1),
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'anjunishizawa103@gmail.com', 'ACTIVO', 1),
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', '123dio404@gmail.com', 'ACTIVO', 1),
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'aaxel532@gmail.com', 'ACTIVO', 1),
('Admin SaaS Global', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'jhamil.veliz1@gmail.com', 'ACTIVO', 1),
('Gerente Cooperativa', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'admin@gmail.com', 'ACTIVO', 2),
('Carlos Cajero', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'ccajero@cooperativa.com', 'ACTIVO', 3),
('Ana Oficial Crédito', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'acredito@cooperativa.com', 'ACTIVO', 4),
('Roberto Contador', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'rcontador@cooperativa.com', 'ACTIVO', 5),
('Juan Pérez (Socio)', '$2b$12$XtO2CMCiiQIhv/eT8FTFuu0XfV8qGZNqa/UhNJvQpnF4d5BEW6Ok2', 'juan.perez@email.com', 'ACTIVO', 6);

insert into bitacora (modulo, accion, descripcion, ip, usuario_id) values
('SEGURIDAD', 'LOGIN', 'Inicio de sesión Super Admin SaaS', '192.168.1.10', 1),
('CAJA', 'APERTURA', 'Apertura de caja inicial ventanilla 1', '192.168.1.15', 3),
('CREDITO', 'EVALUACION', 'Evaluación de campo realizada a socio', '192.168.1.20', 4);

insert into reporte (tipo, formato, parametros, usuario_id) values
('CARTERA_MORA', 'PDF', '{"fecha_cierre": "2026-08-01", "gestion": 2026}', 5);

insert into moneda (codigo_iso, nombre, simbolo, es_moneda_base) values
('BOB', 'Boliviano', 'Bs.', true),
('USD', 'Dólar Estadounidense', '$', false);

insert into tipo_de_cambio (tasa_compra, tasa_venta, moneda_id) values
(6.8600, 6.9600, 2);

insert into socio (ci, nombre, apellido, direccion, telefono, correo, estado, fecha_registro, usuario_id) values
('1234567 LP', 'Juan', 'Pérez Gómez', 'Av. 6 de Agosto #123', '71234567', 'juan.perez@email.com', 'ACTIVO', '2025-01-10', 6),
('7654321 CB', 'Maria', 'Lopez Arce', 'Calle Jordán #456', '77654321', 'maria.lopez@email.com', 'ACTIVO', '2025-02-15', null);

insert into certificado_aportacion (monto, fecha_emision, estado, socio_id, moneda_id) values
(1000.00, '2025-01-10', 'EMITIDO', 1, 1),
(1000.00, '2025-02-15', 'EMITIDO', 2, 1);

insert into evaluacion_campo (ingreso_mensual, egreso_mensual, capacidad_pago, fotografias_respaldo, coordenadas, fecha, resumen_cualitativo_ia, usuario_id) values
(8000.00, 3000.00, 5000.00, 'tienda_foto1.jpg,inventario.jpg', '-16.5000,-68.1500', '2026-01-10', 'Negocio formal con flujo constante de caja. Excelente perfil de cumplimiento.', 4);

insert into solicitud_credito (monto, plazo_meses, tasa_interes, calificacion_asfi, tiene_deudas, estado, socio_id, usuario_id, evaluacion_campo_id) values
(25000.00, 12, 14.50, 'A', false, 'APROBADO', 1, 4, 1);

insert into credito (monto_aprobado, saldo_pendiente, estado, solicitud_credito_id) values
(25000.00, 20000.00, 'VIGENTE', 1);

insert into tabla_amortizacion (numero_cuota, fecha_vencimiento, monto_capital, monto_interes, monto_cuota_total, estado_pago, credito_id) values
(1, '2026-02-10', '2000.00', '302.08', '2302.08', 'PAGADO', 1),
(2, '2026-03-10', '2024.16', '277.92', '2302.08', 'PENDIENTE', 1);

insert into pago_cuota (monto_capital, monto_interes_pagado, monto_mora, fecha, tabla_amortizacion_id) values
(2000.00, 302.08, 0.00, '2026-02-08 10:30:00', 1);

insert into historial_gestion_de_cobranza (tipo_contacto, resultado_gestion, fecha_de_compromiso_de_pago, credito_id) values
('LLAMADA', 'Socio confirmó abono puntual de la siguiente cuota.', '2026-03-09', 1);

insert into morosidad (dias_de_retaso, monto_penalizado, estado, credito_id) values
(0, 0.00, 'AL_DIA', 1);

insert into prediccion_de_morosidad (probabilidad_de_incumplimiento, nivel_de_riesgo, credito_id) values
(0.0380, 'BAJO', 1);

insert into caja (nombre, estado) values
('Ventanilla 1 - Central', 'ABIERTA');

insert into control_caja (monto_apertura, monto_cierre, saldo_sistema, fecha_apertura, estado, caja_id, usuario_id) values
(5000.00, null, 87302.08, '2026-08-23 08:00:00', 'ABIERTA', 1, 3);

insert into cuenta_ahorro (numero, saldo_disponible, saldo_bloqueado, estado, fecha_registro, socio_id, moneda_id) values
('CA-BOB-1001', 15000.00, 0.00, 'ACTIVA', '2025-01-10', 1, 1),
('CA-USD-2001', 500.00, 0.00, 'ACTIVA', '2025-02-15', 2, 2);

insert into deposito_plazo_fijo (monto, tasa_interes_anual, plazo_dias, fecha_inicio, fecha_vencimiento, interes_calculado, estado, socio_id, moneda_id) values
(80000.00, 6.50, 360, '2025-08-01', '2026-07-27', 5200.00, 'LIQUIDADO', 2, 1);

insert into liquidacion (monto_capital_retornado, monto_interes_pagado, tipo_operacion, fecha, deposito_plazo_fijo_id) values
(80000.00, 5200.00, 'CANCELACION_DPF', '2026-07-27 11:00:00', 1);

insert into declaracion_jurada_uif (origen, destino) values
('Ahorros provenientes de actividad comercial minorista', 'Inversión en Depósito a Plazo Fijo (DPF)');

insert into transaccion (tipo, monto, canal, fecha_hora, control_caja_id, moneda_id, cuenta_ahorro_id, deposito_plazo_fijo_id, pago_cuota_id, liquidacion_id, declaracion_jurada_uif_id) values
('PAGO_CUOTA', 2302.08, 'VENTANILLA', '2026-02-08 10:30:00', 1, 1, 1, null, 1, null, null),
('APERTURA_DPF', 80000.00, 'VENTANILLA', '2025-08-01 14:00:00', 1, 1, null, 1, null, null, 1);

insert into plan_cuenta (codigo, nombre, nivel, tipo, plan_cuenta_padre_id) values
('100-000', 'ACTIVO', 1, 'ACTIVO', null),
('110-000', 'DISPONIBILIDADES', 2, 'ACTIVO', 1),
('111-001', 'Caja Moneda Nacional', 3, 'ACTIVO', 2),
('130-000', 'CARTERA DE CRÉDITOS', 2, 'ACTIVO', 1),
('131-001', 'Créditos Vigentes MN', 3, 'ACTIVO', 4);

insert into comprobante_contable (tipo, glosa, es_automatico, transaccion_id) values
('INGRESO', 'Comprobante de ingreso automático por amortización de cuota 1 crédito #1', true, 1);

insert into detalle_asiento (debe, haber, comprobante_contable_id, plan_cuenta_id) values
(2302.08, 0.00, 1, 3), 
(0.00, 2000.00, 1, 5),
(0.00, 302.08, 1, 5);
