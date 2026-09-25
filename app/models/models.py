from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Numeric,
    ForeignKey,
    Integer,
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
    cooperativa_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cooperativa.id"), nullable=True
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

    Cada socio pertenece a una cooperativa; los registros legacy sin tenant
    solo son visibles para SUPERADMIN hasta ser asignados.
    """

    __tablename__ = "socio"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=func.gen_random_uuid()
    )
    cooperativa_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cooperativa.id"), nullable=True
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
    usuario_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("ci", name="uq_socio_ci"),
        CheckConstraint(
            "estado IN ('ACTIVO','INACTIVO')",
            name="chk_socio_estado",
        ),
    )

    cuentas_ahorro: Mapped[list["CuentaAhorro"]] = relationship(back_populates="socio")
    certificados_aportacion: Mapped[list["CertificadoAportacion"]] = relationship(back_populates="socio")


class Moneda(Base):
    __tablename__ = "moneda"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    codigo_iso: Mapped[str] = mapped_column(String(3), nullable=False, unique=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False)
    simbolo: Mapped[str] = mapped_column(String(5), nullable=False)
    es_moneda_base: Mapped[bool] = mapped_column(default=False)


class CuentaAhorro(Base):
    __tablename__ = "cuenta_ahorro"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    tipo_producto: Mapped[str] = mapped_column(String(20), nullable=False, default="VISTA")
    saldo_disponible = mapped_column(Numeric(12, 2), nullable=False, default=0)
    saldo_bloqueado = mapped_column(Numeric(12, 2), nullable=False, default=0)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVA")
    fecha_registro = mapped_column(Date, nullable=False, server_default=func.current_date())
    socio_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("socio.id"), nullable=False)
    moneda_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("moneda.id"), nullable=False)

    socio: Mapped["Socio"] = relationship(back_populates="cuentas_ahorro")
    moneda: Mapped["Moneda"] = relationship()


class Caja(Base):
    __tablename__ = "caja"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="CERRADA")
    cooperativa_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cooperativa.id"), nullable=True
    )
    monto_maximo_efectivo = mapped_column(
        Numeric(12, 2), nullable=False, default=50000.00, server_default="50000.00"
    )
    umbral_diferencia_arqueo = mapped_column(
        Numeric(12, 2), nullable=False, default=50.00, server_default="50.00"
    )

    cooperativa: Mapped["Cooperativa | None"] = relationship()
    controles: Mapped[list["ControlCaja"]] = relationship(back_populates="caja")


class ControlCaja(Base):
    __tablename__ = "control_caja"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monto_apertura = mapped_column(Numeric(12, 2), nullable=False)
    monto_cierre = mapped_column(Numeric(12, 2))
    saldo_sistema = mapped_column(Numeric(12, 2), nullable=False)
    fecha_apertura = mapped_column(TIMESTAMP(timezone=False), nullable=False)
    fecha_cierre = mapped_column(TIMESTAMP(timezone=False))
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ABIERTA")
    caja_id: Mapped[int] = mapped_column(Integer, ForeignKey("caja.id"), nullable=False)
    usuario_id: Mapped[int] = mapped_column(Integer, ForeignKey("usuario.id"), nullable=False)

    caja: Mapped["Caja"] = relationship(back_populates="controles")
    usuario: Mapped["Usuario"] = relationship()


class ArqueoCaja(Base):
    __tablename__ = "arqueo_caja"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    control_caja_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("control_caja.id"), nullable=False
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id"), nullable=False
    )
    supervisor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id"), nullable=True
    )
    fecha = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    fecha_autorizacion = mapped_column(TIMESTAMP(timezone=True))
    cierre: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    requiere_supervisor: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observacion: Mapped[str | None] = mapped_column(Text)

    control_caja: Mapped["ControlCaja"] = relationship()
    usuario: Mapped["Usuario"] = relationship(foreign_keys=[usuario_id])
    supervisor: Mapped["Usuario | None"] = relationship(foreign_keys=[supervisor_id])
    monedas: Mapped[list["ArqueoCajaMoneda"]] = relationship(
        back_populates="arqueo", cascade="all, delete-orphan"
    )


class ArqueoCajaMoneda(Base):
    __tablename__ = "arqueo_caja_moneda"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    arqueo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("arqueo_caja.id", ondelete="CASCADE"), nullable=False
    )
    moneda_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("moneda.id"), nullable=False
    )
    saldo_teorico = mapped_column(Numeric(12, 2), nullable=False)
    total_contado = mapped_column(Numeric(12, 2), nullable=False)
    diferencia = mapped_column(Numeric(12, 2), nullable=False)
    resultado: Mapped[str] = mapped_column(String(10), nullable=False)

    arqueo: Mapped["ArqueoCaja"] = relationship(back_populates="monedas")
    moneda: Mapped["Moneda"] = relationship()
    detalle: Mapped[list["ArqueoCajaDetalle"]] = relationship(
        back_populates="arqueo_moneda", cascade="all, delete-orphan"
    )


class ArqueoCajaDetalle(Base):
    __tablename__ = "arqueo_caja_detalle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    arqueo_moneda_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("arqueo_caja_moneda.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    denominacion = mapped_column(Numeric(12, 2), nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal = mapped_column(Numeric(12, 2), nullable=False)

    arqueo_moneda: Mapped["ArqueoCajaMoneda"] = relationship(
        back_populates="detalle"
    )


class CertificadoAportacion(Base):
    __tablename__ = "certificado_aportacion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    correlativo: Mapped[int] = mapped_column(nullable=False)
    numero_titulos: Mapped[int] = mapped_column(nullable=False, default=1)
    valor_unitario = mapped_column(Numeric(12, 2), nullable=False, default=0)
    monto = mapped_column(Numeric(12, 2), nullable=False)
    fecha_emision = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="EMITIDO")
    socio_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("socio.id"), nullable=False)
    moneda_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("moneda.id"), nullable=False)

    socio: Mapped["Socio"] = relationship(back_populates="certificados_aportacion")
    moneda: Mapped["Moneda"] = relationship()
