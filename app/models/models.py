from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

rol_permiso = Table(
    "rol_permiso",
    Base.metadata,
    Column("rol_id", SmallInteger, ForeignKey("rol.id", ondelete="CASCADE"), primary_key=True),
    Column("permiso_id", SmallInteger, ForeignKey("permiso.id", ondelete="CASCADE"), primary_key=True),
)


class Cooperativa(Base):
    __tablename__ = "cooperativa"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=func.gen_random_uuid()
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    razon_social: Mapped[str | None] = mapped_column(String(200))
    nit: Mapped[str | None] = mapped_column(String(20))
    correo: Mapped[str | None] = mapped_column(String(150))
    telefono: Mapped[str | None] = mapped_column(String(20))
    direccion: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVO")
    fecha_creacion = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    fecha_baja = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_cooperativa_uuid"),
        UniqueConstraint("nit", name="uq_cooperativa_nit"),
        CheckConstraint(
            "estado IN ('ACTIVO','INACTIVO')",
            name="chk_cooperativa_estado",
        ),
    )

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="cooperativa")

    @property
    def esta_activa(self) -> bool:
        return self.estado == "ACTIVO"


class Permiso(Base):
    __tablename__ = "permiso"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    descripcion: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("nombre", name="uq_permiso_nombre"),
    )

    roles: Mapped[list["Rol"]] = relationship(
        secondary=rol_permiso, back_populates="permisos"
    )


class Rol(Base):
    __tablename__ = "rol"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    descripcion: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("nombre", name="uq_rol_nombre"),
    )

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="rol")
    permisos: Mapped[list["Permiso"]] = relationship(
        secondary=rol_permiso, back_populates="roles"
    )


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=func.gen_random_uuid()
    )
    rol_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("rol.id"), nullable=False
    )
    cooperativa_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cooperativa.id"), nullable=True
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    contrasena: Mapped[str] = mapped_column(String(255), nullable=False)
    correo: Mapped[str] = mapped_column(String(150), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVO")
    fecha_creacion = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    fecha_baja = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("correo", name="uq_usuario_correo"),
        UniqueConstraint("uuid", name="uq_usuario_uuid"),
        CheckConstraint(
            "estado IN ('ACTIVO','INACTIVO','BLOQUEADO')",
            name="chk_usuario_estado",
        ),
        CheckConstraint(
            "correo ~* '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'",
            name="chk_usuario_correo",
        ),
    )

    rol: Mapped["Rol"] = relationship(back_populates="usuarios")
    cooperativa: Mapped["Cooperativa"] = relationship(back_populates="usuarios")
    bitacoras: Mapped[list["Bitacora"]] = relationship(back_populates="usuario")

    @property
    def esta_activo(self) -> bool:
        return self.estado == "ACTIVO"


class Bitacora(Base):
    """Registro de auditoría de acciones relevantes del sistema.

    Columnas reales en la BD (Sprint 0):
      id, usuario_id, modulo, accion, descripcion, ip, user_agent, fecha_hora
    """

    __tablename__ = "bitacora"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id"), nullable=False
    )
    modulo: Mapped[str] = mapped_column(String(50), nullable=False)
    accion: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(45))     # tipo inet en BD
    user_agent: Mapped[str | None] = mapped_column(Text)
    fecha_hora = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    usuario: Mapped["Usuario"] = relationship(back_populates="bitacoras")


class Socio(Base):
    """
    Persona natural afiliada a la cooperativa (tabla KYC real en la BD).

    Nota: No tiene cooperativa_id en la BD actual. El tenant se gestiona
    a nivel de la sesión del administrador que registra al socio.
    """

    __tablename__ = "socio"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=func.gen_random_uuid()
    )
    ci: Mapped[str] = mapped_column(String(20), nullable=False)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apellido: Mapped[str] = mapped_column(String(100), nullable=False)
    direccion: Mapped[str | None] = mapped_column(Text)
    telefono: Mapped[str | None] = mapped_column(String(20))
    correo: Mapped[str | None] = mapped_column(String(150))
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVO")
    fecha_registro = mapped_column(Date, nullable=False, server_default=func.current_date())
    fecha_baja = mapped_column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint("ci", name="uq_socio_ci"),
        CheckConstraint(
            "estado IN ('ACTIVO','INACTIVO')",
            name="chk_socio_estado",
        ),
    )