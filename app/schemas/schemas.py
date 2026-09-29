from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator



class LoginRequest(BaseModel):
    correo: EmailStr
    contrasena: str = Field(..., min_length=1)


class PasswordResetRequest(BaseModel):
    correo: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(..., min_length=1)
    nueva_contrasena: str = Field(..., min_length=6, max_length=128)


class MessageResponse(BaseModel):
    message: str


class PasswordResetRequestResponse(BaseModel):
    """Respuesta al solicitar recuperación.

    `delivered` indica si el correo se entregó a SMTP (no si el receptor lo
    recibió en bandeja — eso depende de filtros del proveedor destino).
    Si el servidor está sin SMTP configurado (modo desarrollo), `debug_token`
    contiene el token generado para que el equipo pueda probar el flujo
    end-to-end sin esperar al correo.
    """
    message: str
    delivered: bool
    debug_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class PermisoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str | None = None


class RolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str | None = None
    permisos: list[PermisoOut] = []


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    correo: str
    estado: str
    fecha_creacion: datetime
    rol: RolOut
    cooperativa_id: int | None = None


class UsuarioCreate(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100)
    correo: EmailStr
    contrasena: str = Field(..., min_length=8, max_length=128)
    rol: str = Field(..., min_length=2, max_length=50)
    cooperativa_id: int | None = None


class UsuarioUpdate(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=100)
    correo: EmailStr | None = None
    contrasena: str | None = Field(None, min_length=8, max_length=128)
    rol: str | None = Field(None, min_length=2, max_length=50)
    estado: str | None = Field(None, pattern="^(ACTIVO|INACTIVO|BLOQUEADO)$")


class RolCreate(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=50)
    descripcion: str | None = None
    permiso_ids: list[int] = []


class RolUpdate(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=50)
    descripcion: str | None = None
    permiso_ids: list[int] | None = None


class LogoutResponse(BaseModel):
    message: str


class BitacoraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    modulo: str
    accion: str
    descripcion: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    fecha_hora: datetime
    usuario_id: int
    usuario_nombre: str | None = None   # campo calculado, poblado en el endpoint


class AdminStatsOut(BaseModel):
    total_socios: int = 0
    socios_activos: int = 0
    cuentas_activas: int = 0
    total_usuarios: int = 0



class CooperativaBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=150)
    razon_social: str | None = Field(None, max_length=200)
    nit: str | None = Field(None, min_length=1, max_length=20)
    correo: EmailStr | None = None
    telefono: str | None = Field(None, max_length=20)
    direccion: str | None = None


class CooperativaCreate(CooperativaBase):
    pass


class CooperativaUpdate(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=150)
    razon_social: str | None = Field(None, max_length=200)
    nit: str | None = Field(None, min_length=1, max_length=20)
    correo: EmailStr | None = None
    telefono: str | None = Field(None, max_length=20)
    direccion: str | None = None


class CooperativaOut(CooperativaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: UUID
    estado: str
    fecha_creacion: datetime
    fecha_baja: datetime | None = None


# ── Módulo KYC / Socios ────────────────────────────────────────────────────

class SocioCreate(BaseModel):
    """Datos para registrar un nuevo Socio (formulario KYC)."""
    ci: str = Field(..., min_length=5, max_length=20, description="Cédula de identidad")
    nombre: str = Field(..., min_length=2, max_length=100)
    apellido: str = Field(..., min_length=2, max_length=100)
    direccion: str | None = Field(None, max_length=500)
    telefono: str | None = Field(None, max_length=20)
    correo: EmailStr | None = None


class SocioUpdate(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=100)
    apellido: str | None = Field(None, min_length=2, max_length=100)
    direccion: str | None = Field(None, max_length=500)
    telefono: str | None = Field(None, max_length=20)
    correo: EmailStr | None = None
    estado: str | None = Field(None, pattern="^(ACTIVO|INACTIVO)$")


class SocioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: UUID
    cooperativa_id: int | None = None
    ci: str
    nombre: str
    apellido: str
    direccion: str | None = None
    telefono: str | None = None
    correo: str | None = None
    estado: str
    fecha_registro: date


class SocioRegistroResponse(BaseModel):
    """Respuesta al registrar un socio exitosamente."""
    socio: SocioOut
    mensaje: str = "Socio registrado correctamente"
    bitacora_id: int | None = None


class MonedaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo_iso: str
    nombre: str
    simbolo: str


class TipoCambioCreate(BaseModel):
    moneda_id: int = Field(gt=0)
    fecha: date
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=5)
    fuente: str = Field(default="BCB", min_length=1, max_length=30)


class TipoCambioUpdate(BaseModel):
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=5)
    fuente: str | None = Field(default=None, min_length=1, max_length=30)


class ProductoCreditoCreate(BaseModel):
    codigo: str
    nombre: str
    descripcion: str | None = None
    moneda_id: int
    monto_min: Decimal
    monto_max: Decimal
    plazo_min_meses: int
    plazo_max_meses: int
    tasa_interes_anual: Decimal
    tipo_amortizacion: str
    dias_gracia_mora: int = 0
    tasa_mora_anual: Decimal = Decimal("0.00")
    relacion_cuota_ingreso_max: Decimal = Decimal("40.00")
    requiere_garantia: bool = False
    cobertura_minima_garantia: Decimal = Field(default=Decimal("100.00"), ge=0, le=Decimal("9999.99"))
    monto_aprobacion_directa: Decimal = Field(default=Decimal("0.00"), ge=0)
    tipo_credito_asfi: Literal[
        "MICROCREDITO", "MICROCREDITO_AGROPECUARIO", "CONSUMO",
        "VIVIENDA_HIPOTECARIA", "VIVIENDA_SIN_GARANTIA",
    ] | None = None
    sector_productivo: bool = False


class ProductoCreditoUpdate(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    moneda_id: int | None = None
    monto_min: Decimal | None = None
    monto_max: Decimal | None = None
    plazo_min_meses: int | None = None
    plazo_max_meses: int | None = None
    tasa_interes_anual: Decimal | None = None
    tipo_amortizacion: str | None = None
    dias_gracia_mora: int | None = None
    tasa_mora_anual: Decimal | None = None
    relacion_cuota_ingreso_max: Decimal | None = None
    requiere_garantia: bool | None = None
    cobertura_minima_garantia: Decimal | None = Field(None, ge=0, le=Decimal("9999.99"))
    monto_aprobacion_directa: Decimal | None = Field(None, ge=0)
    tipo_credito_asfi: Literal[
        "MICROCREDITO", "MICROCREDITO_AGROPECUARIO", "CONSUMO",
        "VIVIENDA_HIPOTECARIA", "VIVIENDA_SIN_GARANTIA",
    ] | None = None
    sector_productivo: bool | None = None


class ProductoCreditoEstadoUpdate(BaseModel):
    estado: str


class ProductoCreditoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    nombre: str
    descripcion: str | None
    moneda: MonedaOut
    monto_min: Decimal
    monto_max: Decimal
    plazo_min_meses: int
    plazo_max_meses: int
    tasa_interes_anual: Decimal
    tipo_amortizacion: str
    dias_gracia_mora: int
    tasa_mora_anual: Decimal
    relacion_cuota_ingreso_max: Decimal
    requiere_garantia: bool
    tipo_credito_asfi: str | None
    sector_productivo: bool
    cobertura_minima_garantia: Decimal
    monto_aprobacion_directa: Decimal
    estado: str
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class SocioResumen(BaseModel):
    id: int
    nombre_completo: str
    ci: str
    estado: str


class ProductoResumen(BaseModel):
    id: int
    codigo: str
    nombre: str
    tipo_amortizacion: str
    relacion_cuota_ingreso_max: Decimal


class EvaluacionCreditoCreate(BaseModel):
    ingreso_mensual: Decimal
    egreso_mensual: Decimal
    cuota_deudas_mensual: Decimal
    actividad_economica: str
    fuente_ingresos: str
    antiguedad_laboral_meses: int
    calificacion_asfi: str
    coordenadas: str | None = None
    observaciones: str | None = None


class EvaluacionCreditoUpdate(BaseModel):
    ingreso_mensual: Decimal | None = None
    egreso_mensual: Decimal | None = None
    cuota_deudas_mensual: Decimal | None = None
    actividad_economica: str | None = None
    fuente_ingresos: str | None = None
    antiguedad_laboral_meses: int | None = None
    calificacion_asfi: str | None = None
    coordenadas: str | None = None
    observaciones: str | None = None


class EvaluacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingreso_mensual: Decimal
    egreso_mensual: Decimal
    cuota_deudas_mensual: Decimal
    capacidad_pago: Decimal
    actividad_economica: str | None
    fuente_ingresos: str | None
    antiguedad_laboral_meses: int | None
    calificacion_asfi: str | None
    coordenadas: str | None
    observaciones: str | None
    fecha: date


class SolicitudCreditoCreate(BaseModel):
    socio_id: int
    producto_id: int
    monto: Decimal
    plazo_meses: int
    destino: str
    destino_detalle: str | None = None
    observaciones: str | None = None
    evaluacion: EvaluacionCreditoCreate


class SolicitudCreditoUpdate(BaseModel):
    producto_id: int | None = None
    monto: Decimal | None = None
    plazo_meses: int | None = None
    destino: str | None = None
    destino_detalle: str | None = None
    observaciones: str | None = None
    evaluacion: EvaluacionCreditoUpdate | None = None


class SolicitudAnulacionIn(BaseModel):
    motivo: str


class OficialResumen(BaseModel):
    id: int
    nombre: str


class FactorOut(BaseModel):
    codigo: str
    descripcion: str
    puntos: int
    maximo: int
    valor: str
    motivo: str


class KnockoutOut(BaseModel):
    codigo: str
    descripcion: str
    efecto: Literal["RECHAZADO", "REVISION_MANUAL"]


class UsuarioEvaluacionOut(BaseModel):
    id: int
    nombre: str


class ResolucionEvaluacionOut(BaseModel):
    decision: Literal["APROBADO", "RECHAZADO"]
    justificacion: str
    fecha: datetime
    usuario: UsuarioEvaluacionOut


class ResolucionSolicitudIn(BaseModel):
    decision: Literal["APROBADO", "RECHAZADO"]
    justificacion: str


class EvaluacionCrediticiaOut(BaseModel):
    id: int
    solicitud_id: int
    version_modelo: str
    score: int
    dictamen: Literal["APROBADO", "RECHAZADO", "REVISION_MANUAL"]
    factores: list[FactorOut]
    knockouts: list[KnockoutOut]
    explicacion: str
    cuota_estimada: Decimal | None
    relacion_cuota_ingreso: Decimal | None
    fecha: datetime
    usuario: UsuarioEvaluacionOut
    resolucion: ResolucionEvaluacionOut | None
    probabilidad_mora: Decimal | None = None
    nivel_riesgo: str | None = None
    version_modelo_mora: str | None = None


class UltimaEvaluacionOut(BaseModel):
    id: int
    score: int
    dictamen: Literal["APROBADO", "RECHAZADO", "REVISION_MANUAL"]
    fecha: datetime


class AvalistaOut(BaseModel):
    nombre: str
    ci: str
    ingreso_mensual: Decimal
    relacion: str
    telefono: str | None = None
    socio_id: int | None = None


class UsuarioGarantiaOut(BaseModel):
    id: int
    nombre: str


class GarantiaOut(BaseModel):
    id: int
    tipo: Literal["HIPOTECARIA", "PRENDARIA", "PERSONAL"]
    descripcion: str
    moneda: MonedaOut
    valor_comercial: Decimal | None
    valor_realizable: Decimal
    documento_referencia: str | None
    avalista: AvalistaOut | None
    estado: Literal["REGISTRADA", "VERIFICADA", "RECHAZADA", "LIBERADA"]
    observacion_verificacion: str | None
    usuario_registro: UsuarioGarantiaOut
    usuario_verificacion: UsuarioGarantiaOut | None
    fecha_registro: datetime
    fecha_verificacion: datetime | None
    fecha_liberacion: datetime | None


class AvalistaIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=150)
    ci: str = Field(min_length=2, max_length=20)
    ingreso_mensual: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    relacion: str = Field(min_length=2, max_length=60)
    telefono: str | None = Field(None, max_length=30)
    socio_id: int | None = None


class GarantiaCreateIn(BaseModel):
    tipo: Literal["HIPOTECARIA", "PRENDARIA", "PERSONAL"]
    descripcion: str = Field(min_length=2, max_length=5000)
    moneda_id: int | None = None
    valor_comercial: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    documento_referencia: str | None = Field(None, max_length=120)
    avalista: AvalistaIn | None = None

    @model_validator(mode="after")
    def validate_by_type(self):
        if self.tipo == "PERSONAL":
            if self.avalista is None or self.moneda_id is not None or self.valor_comercial is not None:
                raise ValueError("La garantía personal requiere los datos del avalista")
        elif self.avalista is not None or self.moneda_id is None or self.valor_comercial is None:
            raise ValueError("La garantía hipotecaria o prendaria requiere moneda y valor comercial")
        return self


class GarantiaUpdateIn(BaseModel):
    descripcion: str | None = Field(None, min_length=2, max_length=5000)
    moneda_id: int | None = None
    valor_comercial: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    documento_referencia: str | None = Field(None, max_length=120)
    avalista: AvalistaIn | None = None


class GarantiaVerificacionIn(BaseModel):
    decision: Literal["VERIFICADA", "RECHAZADA"]
    observacion: str = Field(min_length=5)


class CoberturaOut(BaseModel):
    requiere_garantia: bool
    cobertura_minima: Decimal
    monto_solicitado: Decimal
    valor_realizable_verificado: Decimal
    valor_realizable_pendiente: Decimal
    porcentaje_cobertura: Decimal
    cumple: bool


class SolicitudOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_solicitud: str | None
    fecha_solicitud: datetime
    fecha_actualizacion: datetime
    estado: str | None
    socio: SocioResumen
    producto: ProductoResumen | None
    moneda: MonedaOut | None
    monto: Decimal
    plazo_meses: int
    tasa_interes: Decimal
    destino: str | None
    destino_detalle: str | None
    observaciones: str | None
    motivo_anulacion: str | None
    tiene_deudas: bool | None
    cuota_estimada: Decimal | None
    relacion_cuota_ingreso: Decimal | None
    supera_relacion_maxima: bool | None
    evaluacion: EvaluacionOut | None
    oficial: OficialResumen
    ultima_evaluacion: UltimaEvaluacionOut | None = None
    ronda_comite: int = 0
    resultado_comite: str | None = None
    cobertura: CoberturaOut | None = None


class VotoComiteIn(BaseModel):
    voto: Literal["APROBAR", "RECHAZAR", "OBSERVAR"]
    comentario: str = Field(min_length=10)

    @field_validator("comentario")
    @classmethod
    def comentario_no_vacio(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("El comentario debe tener al menos 10 caracteres")
        return normalized


class VotoUsuarioOut(BaseModel):
    id: int
    nombre: str
    rol: str


class VotoOut(BaseModel):
    id: int
    ronda: int
    usuario: VotoUsuarioOut
    voto: Literal["APROBAR", "RECHAZAR", "OBSERVAR"]
    comentario: str
    fecha: datetime


class ComiteItemOut(BaseModel):
    solicitud: SolicitudOut
    evaluacion: EvaluacionCrediticiaOut | None
    ronda: int
    votos: list[VotoOut]
    votos_requeridos: int = 3
    puede_votar: bool
    motivo_no_puede_votar: str | None


class RondaActaOut(BaseModel):
    ronda: int
    votos: list[VotoOut]
    resultado: str | None
    fecha_resolucion: datetime | None


class ActaOut(BaseModel):
    solicitud: SolicitudOut
    rondas: list[RondaActaOut]
    evaluacion: EvaluacionCrediticiaOut | None
    cooperativa: dict[str, int | str]


class SolicitudSimulacionIn(BaseModel):
    producto_id: int
    monto: Decimal
    plazo_meses: int
    ingreso_mensual: Decimal | None = None
    egreso_mensual: Decimal | None = None
    cuota_deudas_mensual: Decimal | None = None


class SolicitudSimulacionOut(BaseModel):
    producto: ProductoResumen
    moneda: MonedaOut
    monto: Decimal
    plazo_meses: int
    tasa_interes: Decimal
    cuota_estimada: Decimal
    total_intereses_estimado: Decimal
    capacidad_pago: Decimal | None
    relacion_cuota_ingreso: Decimal | None
    relacion_maxima: Decimal
    supera_relacion_maxima: bool | None
    dentro_de_rangos: bool
    errores: list[str]


class CuotaOut(BaseModel):
    numero: int
    fecha_vencimiento: date
    saldo_inicial: Decimal | None
    capital: Decimal
    interes: Decimal
    cuota: Decimal
    saldo_final: Decimal | None
    estado_pago: str


class PlanPagosOut(BaseModel):
    tipo_amortizacion: str
    monto: Decimal
    tasa_interes: Decimal
    plazo_meses: int
    moneda: MonedaOut
    fecha_desembolso: date
    fecha_primer_vencimiento: date
    cuotas: list[CuotaOut]
    total_capital: Decimal
    total_interes: Decimal
    total_pagar: Decimal


class CreditoSocioOut(BaseModel):
    id: int
    nombre_completo: str
    ci: str


class CreditoProductoOut(BaseModel):
    id: int
    codigo: str
    nombre: str


class CuentaDesembolsoOut(BaseModel):
    id: int
    numero: str


class SolicitudCreditoResumenOut(BaseModel):
    id: int
    numero_solicitud: str | None


class UsuarioDesembolsoOut(BaseModel):
    id: int
    nombre: str


class CreditoOut(BaseModel):
    id: int
    numero_credito: str | None
    estado: str
    socio: CreditoSocioOut
    producto: CreditoProductoOut | None
    moneda: MonedaOut | None
    monto_aprobado: Decimal
    saldo_pendiente: Decimal
    tasa_interes: Decimal | None
    plazo_meses: int | None
    tipo_amortizacion: str | None
    fecha_desembolso: date | None
    modalidad_desembolso: str | None
    cuenta_desembolso: CuentaDesembolsoOut | None
    solicitud: SolicitudCreditoResumenOut
    proxima_cuota: CuotaOut | None
    cuotas_pagadas: int
    cuotas_totales: int
    estado_mora: Literal["AL_DIA", "EN_MORA"] | None = None
    dias_de_retaso: int | None = None


class CreditoDetalleOut(CreditoOut):
    cronograma: list[CuotaOut]
    transaccion_desembolso_id: int | None
    usuario: UsuarioDesembolsoOut | None


class DeudaCuotaOut(BaseModel):
    credito_id: int
    numero_credito: str | None
    cuota: CuotaOut
    dias_atraso: int
    dias_gracia: int
    en_mora: bool
    mora: Decimal
    total_a_pagar: Decimal
    moneda: MonedaOut | None


class PagoCreditoOut(BaseModel):
    id: int
    numero_credito: str | None


class PagoSocioOut(BaseModel):
    id: int
    nombre_completo: str
    ci: str


class PagoCuentaOut(BaseModel):
    id: int
    numero: str


class PagoOut(BaseModel):
    id: int
    numero_recibo: str | None
    fecha: datetime
    modalidad: Literal["EFECTIVO", "CUENTA"] | None
    credito: PagoCreditoOut
    socio: PagoSocioOut
    numero_cuota: int
    capital: Decimal
    interes: Decimal
    mora: Decimal
    total: Decimal
    dias_atraso: int
    moneda: MonedaOut | None
    saldo_pendiente_credito: Decimal
    credito_estado: str
    cuenta: PagoCuentaOut | None
    caja_nombre: str | None
    usuario: UsuarioDesembolsoOut | None
    declaracion_uif_id: int | None


class MoraCreditoOut(BaseModel):
    credito_id: int
    numero_credito: str | None
    socio: PagoSocioOut
    estado_mora: Literal["AL_DIA", "EN_MORA"]
    dias_de_retaso: int
    monto_penalizado: Decimal
    cuotas_vencidas: int
    monto_vencido: Decimal
    saldo_pendiente: Decimal
    moneda: MonedaOut | None
    fecha_actualizacion: datetime | None
    probabilidad_mora: Decimal | None = None
    nivel_riesgo: str | None = None


class GestionUsuarioOut(BaseModel):
    id: int
    nombre: str


class GestionOut(BaseModel):
    id: int
    tipo_contacto: str
    resultado_gestion: str
    fecha_compromiso_pago: date | None
    fecha: datetime
    usuario: GestionUsuarioOut | None
    alerta_id: int | None


class AlertaCreditoOut(BaseModel):
    id: int
    numero_credito: str | None


class AlertaSocioOut(BaseModel):
    id: int
    nombre_completo: str
    ci: str


class AlertaCuotaOut(BaseModel):
    numero: int
    fecha_vencimiento: date
    cuota: Decimal


class AlertaOut(BaseModel):
    id: int
    tipo: str
    severidad: str
    estado: str
    mensaje: str
    credito: AlertaCreditoOut
    socio: AlertaSocioOut
    cuota: AlertaCuotaOut | None
    datos: dict
    fecha_creacion: datetime
    fecha_cierre: datetime | None
    usuario_cierre: GestionUsuarioOut | None
    comentario_cierre: str | None
    gestion: GestionOut | None


class GestionCreateIn(BaseModel):
    tipo_contacto: Literal["LLAMADA", "VISITA", "MENSAJE", "OTRO"]
    resultado_gestion: str = Field(..., min_length=10)
    fecha_compromiso_pago: date | None = None

    @field_validator("fecha_compromiso_pago")
    @classmethod
    def fecha_promesa_no_pasada(cls, value):
        if value is not None and value < date.today():
            raise ValueError("La fecha compromiso debe ser hoy o futura")
        return value


class DescartarAlertaIn(BaseModel):
    comentario: str = Field(..., min_length=5)


class CajaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    estado: str
    monto_maximo_efectivo: Decimal


class SesionCajaOut(BaseModel):
    id: int
    caja_id: int
    caja_nombre: str
    monto_apertura: Decimal
    saldo_sistema: Decimal
    fecha_apertura: datetime
    estado: str


class AperturaCajaCreate(BaseModel):
    caja_id: int = Field(..., ge=1)
    monto_apertura: Decimal = Field(..., max_digits=12, decimal_places=2)


class TitularCajaOut(BaseModel):
    socio_id: int
    nombre_completo: str
    ci: str


class CuentaCajaOut(BaseModel):
    id: int
    numero: str
    estado: str
    saldo_disponible: Decimal
    moneda: MonedaOut
    titular: TitularCajaOut


class DeclaracionUifIn(BaseModel):
    origen: str = Field("", max_length=255)
    origen_detalle: str | None = None
    destino: str = Field("", max_length=255)
    destino_detalle: str | None = None
    actividad_economica: str = Field("", max_length=150)
    realizado_por: str = ""
    tercero_nombre: str | None = Field(None, max_length=150)
    tercero_ci: str | None = Field(None, max_length=20)
    tercero_parentesco: str | None = Field(None, max_length=50)
    declara_bajo_juramento: bool = False


class PagoCuotaIn(BaseModel):
    modalidad: Literal["EFECTIVO", "CUENTA"]
    cuenta_ahorro_id: int | None = Field(None, ge=1)
    declaracion_uif: DeclaracionUifIn | None = None


class DesembolsoCreditoIn(BaseModel):
    modalidad: Literal["CUENTA", "EFECTIVO"]
    cuenta_ahorro_id: int | None = Field(None, ge=1)
    fecha_primer_vencimiento: date | None = None
    declaracion_uif: DeclaracionUifIn | None = None


class DepositoVentanillaCreate(BaseModel):
    cuenta_id: int = Field(..., ge=1)
    monto: Decimal = Field(..., max_digits=12, decimal_places=2)
    depositante_nombre: str = Field(..., min_length=1, max_length=150)
    depositante_ci: str = Field(..., min_length=1, max_length=20)
    declaracion_uif: DeclaracionUifIn | None = None


class DepositoVentanillaOut(BaseModel):
    numero_operacion: int
    fecha_hora: datetime
    cuenta_numero: str
    titular: TitularCajaOut
    monto: Decimal
    moneda: MonedaOut
    saldo_actualizado: Decimal
    depositante_nombre: str
    depositante_ci: str
    caja_nombre: str
    declaracion_uif_id: int | None = None


class TransferenciaVentanillaCreate(BaseModel):
    cuenta_origen_id: int = Field(..., ge=1)
    cuenta_destino_id: int = Field(..., ge=1)
    monto: Decimal = Field(..., max_digits=12, decimal_places=2)
    glosa: str | None = Field(None, max_length=255)


class CuentaTransferenciaPreviewOut(BaseModel):
    cuenta_id: int
    numero: str
    titular: TitularCajaOut
    moneda: MonedaOut


class CuentaOrigenTransferenciaPreviewOut(CuentaTransferenciaPreviewOut):
    saldo_disponible: Decimal


class TransferenciaPreviewOut(BaseModel):
    origen: CuentaOrigenTransferenciaPreviewOut
    destino: CuentaTransferenciaPreviewOut


class CuentaTransferenciaReceiptOut(BaseModel):
    numero: str
    titular: TitularCajaOut


class TransferenciaVentanillaOut(BaseModel):
    numero_operacion: int
    fecha_hora: datetime
    origen: CuentaTransferenciaReceiptOut
    destino: CuentaTransferenciaReceiptOut
    monto: Decimal
    moneda: MonedaOut
    saldo_origen_actualizado: Decimal
    glosa: str | None = None


class DenominacionArqueoOut(BaseModel):
    tipo: Literal["BILLETE", "MONEDA"]
    valor: Decimal


class MonedaArqueoResumenOut(BaseModel):
    moneda: MonedaOut
    saldo_teorico: Decimal
    monto_apertura: Decimal
    total_depositos: Decimal
    total_retiros: Decimal
    cantidad_movimientos: int
    denominaciones: list[DenominacionArqueoOut]


class ArqueoResumenOut(BaseModel):
    control_caja_id: int
    caja_nombre: str
    fecha_apertura: datetime
    umbral_diferencia: Decimal
    monedas: list[MonedaArqueoResumenOut]


class DenominacionArqueoCreate(BaseModel):
    denominacion: Decimal = Field(..., max_digits=12, decimal_places=2)
    cantidad: int


class MonedaArqueoCreate(BaseModel):
    moneda_id: int = Field(..., ge=1)
    detalle: list[DenominacionArqueoCreate]


class SupervisorArqueoCreate(BaseModel):
    correo: EmailStr
    contrasena: str = Field(..., min_length=1)


class ArqueoCajaCreate(BaseModel):
    monedas: list[MonedaArqueoCreate]
    observacion: str | None = None
    cerrar_caja: bool = False
    supervisor: SupervisorArqueoCreate | None = None


class DetalleArqueoOut(BaseModel):
    tipo: Literal["BILLETE", "MONEDA"]
    denominacion: Decimal
    cantidad: int
    subtotal: Decimal


class MonedaArqueoOut(BaseModel):
    moneda: MonedaOut
    saldo_teorico: Decimal
    total_contado: Decimal
    diferencia: Decimal
    resultado: Literal["CUADRADO", "SOBRANTE", "FALTANTE"]
    detalle: list[DetalleArqueoOut]


class UsuarioArqueoOut(BaseModel):
    id: int
    nombre: str


class ArqueoCajaOut(BaseModel):
    id: int
    fecha: datetime
    caja_nombre: str
    cajero: UsuarioArqueoOut
    supervisor: UsuarioArqueoOut | None
    fecha_autorizacion: datetime | None
    requiere_supervisor: bool
    cierre: bool
    observacion: str | None
    monedas: list[MonedaArqueoOut]


class RetiranteCreate(BaseModel):
    tipo: Literal["TITULAR", "APODERADO"]
    nombre: str | None = None
    ci: str | None = None


class RetiroVentanillaCreate(BaseModel):
    cuenta_id: int = Field(..., ge=1)
    monto: Decimal
    retirante: RetiranteCreate
    declaracion_uif: DeclaracionUifIn | None = None


class RetiranteOut(BaseModel):
    tipo: Literal["TITULAR", "APODERADO"]
    nombre: str
    ci: str


class RetiroVentanillaOut(BaseModel):
    numero_operacion: int
    fecha_hora: datetime
    cuenta_numero: str
    titular: TitularCajaOut
    monto: Decimal
    moneda: MonedaOut
    saldo_actualizado: Decimal
    saldo_minimo: Decimal
    retirante: RetiranteOut
    caja_nombre: str
    efectivo_caja_restante: Decimal
    declaracion_uif_id: int | None = None


class MonedaCierreVerificacionOut(BaseModel):
    moneda: MonedaOut
    resultado: Literal["CUADRADO", "SOBRANTE", "FALTANTE"]
    diferencia: Decimal


class ArqueoCierreVerificacionOut(BaseModel):
    id: int
    fecha: datetime
    requiere_supervisor: bool
    supervisor: UsuarioArqueoOut | None
    monedas: list[MonedaCierreVerificacionOut]


class CierreVerificacionOut(BaseModel):
    puede_cerrar: bool
    alertas: list[str]
    arqueo: ArqueoCierreVerificacionOut | None


class CierreCajaCreate(BaseModel):
    observacion: str | None = None


class MonedaCierreCajaOut(BaseModel):
    moneda: MonedaOut
    monto_apertura: Decimal
    total_depositos: Decimal
    cantidad_depositos: int
    total_retiros: Decimal
    cantidad_retiros: int
    cantidad_transferencias: int
    saldo_teorico: Decimal
    total_contado: Decimal
    diferencia: Decimal
    traspaso_boveda: Decimal


class CierreCajaOut(BaseModel):
    id: int
    fecha: datetime
    caja_nombre: str
    cajero: UsuarioArqueoOut
    fecha_apertura: datetime
    fecha_cierre: datetime
    arqueo_id: int
    observacion: str | None
    monedas: list[MonedaCierreCajaOut]


class CuentaAhorroCreate(BaseModel):
    socio_id: int
    moneda_id: int
    tipo_producto: Literal["VISTA", "PROGRAMADO"] = "VISTA"
    monto_apertura: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)


class CuentaAhorroEstadoUpdate(BaseModel):
    estado: Literal["ACTIVA", "BLOQUEADA", "CANCELADA"]


class CuentaAhorroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero: str
    tipo_producto: str
    saldo_disponible: Decimal
    saldo_bloqueado: Decimal
    estado: str
    fecha_registro: date
    socio_id: int
    socio_nombre: str | None = None
    moneda: MonedaOut


class CertificadoAportacionCreate(BaseModel):
    socio_id: int
    moneda_id: int
    numero_titulos: int = Field(..., gt=0)
    valor_unitario: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    fecha_emision: date = Field(default_factory=date.today)


class CertificadoAportacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    correlativo: int
    numero_titulos: int
    valor_unitario: Decimal
    monto: Decimal
    fecha_emision: date
    estado: str
    socio_id: int
    moneda: MonedaOut


class MovimientoCuentaCreate(BaseModel):
    monto: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)


class MovimientoCuentaOut(BaseModel):
    cuenta_id: int
    tipo: str
    monto: Decimal
    saldo_disponible: Decimal
    moneda: MonedaOut


class TransferenciaCreate(BaseModel):
    cuenta_origen_id: int = Field(..., ge=1)
    cuenta_destino_id: int = Field(..., ge=1)
    monto: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    glosa: str | None = Field(None, max_length=255)


class TransferenciaOut(BaseModel):
    transaccion_salida_id: int
    transaccion_entrada_id: int
    cuenta_origen: CuentaAhorroOut
    cuenta_destino: CuentaAhorroOut
    monto: Decimal
    glosa: str | None = None
    fecha_hora: datetime


class DPFCreate(BaseModel):
    socio_id: int = Field(..., ge=1)
    monto: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    moneda_id: int = Field(..., ge=1)
    plazo_dias: int = Field(..., ge=30)
    modalidad_pago_interes: Literal["VENCIMIENTO", "MENSUAL"]
    origen_fondos: Literal["CUENTA", "EFECTIVO"]
    cuenta_origen_id: int | None = Field(None, ge=1)
    cuenta_abono_id: int = Field(..., ge=1)
    declaracion_uif: DeclaracionUifIn | None = None


class DPFSimulacionIn(BaseModel):
    monto: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    moneda_id: int = Field(..., ge=1)
    plazo_dias: int = Field(..., ge=1)
    modalidad_pago_interes: Literal["VENCIMIENTO", "MENSUAL"]


class DPFInteresReferenciaOut(BaseModel):
    id: int
    numero_certificado: str | None


class DPFSocioInteresOut(BaseModel):
    id: int
    nombre_completo: str
    ci: str


class DPFCuentaInteresOut(BaseModel):
    id: int
    numero: str


class PagoInteresOut(BaseModel):
    cronograma_id: int
    dpf: DPFInteresReferenciaOut
    socio: DPFSocioInteresOut
    numero: int
    fecha_pago: date
    fecha_pago_real: datetime | None
    dias: int
    interes_bruto: str
    retencion_rciva: str
    interes_neto: str
    moneda: str
    cuenta_abono: DPFCuentaInteresOut
    transaccion_id: int | None


class DPFInteresesProcesarIn(BaseModel):
    fecha: date | None = None


class DPFLiquidacionIn(BaseModel):
    tipo: Literal["LIQUIDACION", "CANCELACION", "RENOVACION"]
    capitalizar: bool = False
    plazo_dias: int | None = Field(None, ge=30)
    modalidad_pago_interes: Literal["VENCIMIENTO", "MENSUAL"] | None = None


class SocioOfertaOut(BaseModel):
    id: int
    nombre_completo: str
    ci: str


class CreditoOrigenOfertaOut(BaseModel):
    id: int
    numero_credito: str | None
    estado: str | None


class ProductoOfertaOut(BaseModel):
    id: int
    codigo: str
    nombre: str


class SolicitudGeneradaOfertaOut(BaseModel):
    id: int
    numero_solicitud: str | None


class OfertaOut(BaseModel):
    id: int
    socio: SocioOfertaOut
    credito_origen: CreditoOrigenOfertaOut
    producto: ProductoOfertaOut
    moneda: str | None
    monto_sugerido: Decimal
    plazo_meses: int
    tasa_interes: Decimal
    cuota_estimada: Decimal
    probabilidad_mora: Decimal | None
    nivel_riesgo: str | None
    motivos: list[str]
    estado: Literal["VIGENTE", "ACEPTADA", "DESCARTADA", "EXPIRADA"]
    fecha_generacion: datetime
    fecha_vencimiento: date
    solicitud_generada: SolicitudGeneradaOfertaOut | None


class GeneracionRecreditosOut(BaseModel):
    generadas: int
    omitidas: int
    ofertas: list[OfertaOut]


class OfertaAceptadaOut(BaseModel):
    oferta: OfertaOut
    solicitud: SolicitudOut


class DescartarRecreditoIn(BaseModel):
    motivo: str = Field(min_length=5)


class PlanCuentaAnaliticaCreate(BaseModel):
    padre_id: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=200)
    descripcion: str | None = Field(default=None, max_length=2000)


class PlanCuentaAnaliticaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    descripcion: str | None = Field(default=None, max_length=2000)


class PlanCuentaEstadoUpdate(BaseModel):
    estado: Literal["ACTIVA", "INACTIVA"]
    motivo: str = Field(min_length=5, max_length=1000)

    @field_validator("motivo")
    @classmethod
    def validate_motivo(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("El motivo debe contener al menos cinco caracteres no blancos")
        return value


class PlanCuentaOut(BaseModel):
    id: int
    codigo: str
    nombre: str
    nivel: int
    naturaleza: Literal["DEUDORA", "ACREEDORA"]
    es_regularizadora: bool
    es_oficial: bool
    estado: Literal["ACTIVA", "INACTIVA"]
    acepta_movimientos: bool
    padre_id: int | None = None
    padre_codigo: str | None = None
    tiene_analiticas: bool = False

    model_config = ConfigDict(from_attributes=True)


ComprobanteTipo = Literal["INGRESO", "EGRESO", "TRASPASO"]
ComprobanteEstado = Literal["REGISTRADO", "ANULADO"]
ComprobanteOrigen = Literal["MANUAL", "AUTOMATICO"]


class ParametroContableUpdate(BaseModel):
    plan_cuenta_id: int = Field(gt=0)


class ParametroContableOut(BaseModel):
    clave: str
    plan_cuenta_id: int
    codigo: str
    nombre: str


class ComprobanteLineaCreate(BaseModel):
    plan_cuenta_id: int = Field(gt=0)
    debe: Decimal = Field(default=Decimal("0.00"), ge=0)
    haber: Decimal = Field(default=Decimal("0.00"), ge=0)
    glosa: str | None = Field(default=None, max_length=1000)

    @field_validator("debe", "haber")
    @classmethod
    def round_amount_to_cents(cls, value: Decimal) -> Decimal:
        try:
            rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError("El importe no es válido") from exc
        if rounded < 0 or rounded > Decimal("9999999999.99"):
            raise ValueError("El importe debe estar entre cero y 9,999,999,999.99")
        return rounded

    @field_validator("glosa")
    @classmethod
    def normalize_line_glosa(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_one_side(self):
        if (self.debe > 0) == (self.haber > 0):
            raise ValueError("Cada línea debe tener exactamente uno de debe o haber mayor que cero")
        return self


class ComprobanteManualCreate(BaseModel):
    tipo: ComprobanteTipo
    fecha_contable: date
    glosa: str = Field(max_length=500)
    moneda_id: int = Field(gt=0)
    lineas: list[ComprobanteLineaCreate]

    @field_validator("glosa")
    @classmethod
    def normalize_header_glosa(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("La glosa debe contener al menos cinco caracteres")
        return value

    @model_validator(mode="after")
    def validate_balanced_lines(self):
        if len(self.lineas) < 2:
            raise ValueError("El comprobante debe contener al menos dos líneas")
        total_debe = sum((line.debe for line in self.lineas), Decimal("0.00"))
        total_haber = sum((line.haber for line in self.lineas), Decimal("0.00"))
        if total_debe <= 0 or total_haber <= 0 or total_debe != total_haber:
            raise ValueError("El debe y el haber deben ser iguales y mayores que cero")
        return self


class ComprobanteAnularCreate(BaseModel):
    motivo: str = Field(max_length=1000)

    @field_validator("motivo")
    @classmethod
    def normalize_motivo(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("El motivo debe contener al menos cinco caracteres no blancos")
        return value


class ComprobanteGenerarAutomaticos(BaseModel):
    desde: date | None = None
    hasta: date | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.desde and self.hasta and self.desde > self.hasta:
            raise ValueError("La fecha desde no puede ser posterior a hasta")
        return self
