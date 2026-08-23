from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field



class LoginRequest(BaseModel):
    correo: EmailStr
    contrasena: str = Field(..., min_length=1)


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


class SocioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: UUID
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