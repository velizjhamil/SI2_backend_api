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