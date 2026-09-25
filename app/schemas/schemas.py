from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field



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


class DepositoVentanillaCreate(BaseModel):
    cuenta_id: int = Field(..., ge=1)
    monto: Decimal = Field(..., max_digits=12, decimal_places=2)
    depositante_nombre: str = Field(..., min_length=1, max_length=150)
    depositante_ci: str = Field(..., min_length=1, max_length=20)


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
