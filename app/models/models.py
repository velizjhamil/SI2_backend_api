from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
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

    @property
    def esta_activo(self) -> bool:
        return self.estado == "ACTIVO"