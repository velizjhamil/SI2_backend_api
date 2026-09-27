from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Numeric,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Index,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
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
    dpf_permite_cancelacion_anticipada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    dpf_tasa_penalizacion = mapped_column(
        Numeric(5, 2), nullable=False, default=1.00, server_default="1.00"
    )

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
        UniqueConstraint("id", "cooperativa_id", name="uq_usuario_id_cooperativa"),
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


class ProductoCredito(Base):
    __tablename__ = "producto_credito"
    __table_args__ = (
        UniqueConstraint("cooperativa_id", "codigo", name="uq_producto_credito_cooperativa_codigo"),
        CheckConstraint("tipo_amortizacion IN ('FRANCES','ALEMAN')", name="chk_producto_credito_amortizacion"),
        CheckConstraint("estado IN ('ACTIVO','INACTIVO')", name="chk_producto_credito_estado"),
        CheckConstraint("monto_min > 0 AND monto_min <= monto_max", name="chk_producto_credito_montos"),
        CheckConstraint("plazo_min_meses > 0 AND plazo_min_meses <= plazo_max_meses", name="chk_producto_credito_plazos"),
        CheckConstraint("tasa_interes_anual >= 0 AND tasa_interes_anual <= 100", name="chk_producto_credito_tasa"),
        CheckConstraint("tasa_mora_anual >= 0 AND tasa_mora_anual <= 100", name="chk_producto_credito_tasa_mora"),
        CheckConstraint("relacion_cuota_ingreso_max > 0 AND relacion_cuota_ingreso_max <= 100", name="chk_producto_credito_ratio"),
        CheckConstraint("dias_gracia_mora >= 0", name="chk_producto_credito_gracia"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cooperativa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cooperativa.id"), nullable=False)
    codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    moneda_id: Mapped[int] = mapped_column(Integer, ForeignKey("moneda.id"), nullable=False)
    monto_min = mapped_column(Numeric(14, 2), nullable=False)
    monto_max = mapped_column(Numeric(14, 2), nullable=False)
    plazo_min_meses: Mapped[int] = mapped_column(Integer, nullable=False)
    plazo_max_meses: Mapped[int] = mapped_column(Integer, nullable=False)
    tasa_interes_anual = mapped_column(Numeric(5, 2), nullable=False)
    tipo_amortizacion: Mapped[str] = mapped_column(String(10), nullable=False)
    dias_gracia_mora: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tasa_mora_anual = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    relacion_cuota_ingreso_max = mapped_column(Numeric(5, 2), nullable=False, default=40, server_default="40.00")
    requiere_garantia: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    cobertura_minima_garantia = mapped_column(Numeric(6, 2), nullable=False, default=100, server_default="100.00")
    monto_aprobacion_directa = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0.00")
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVO", server_default="ACTIVO")
    fecha_creacion = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    fecha_actualizacion = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    moneda: Mapped["Moneda"] = relationship()


class EvaluacionCampo(Base):
    __tablename__ = "evaluacion_campo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingreso_mensual = mapped_column(Numeric(12, 2), nullable=False)
    egreso_mensual = mapped_column(Numeric(12, 2), nullable=False)
    capacidad_pago = mapped_column(Numeric(12, 2), nullable=False)
    fotografias_respaldo: Mapped[str | None] = mapped_column(Text)
    coordenadas: Mapped[str | None] = mapped_column(String(100))
    fecha = mapped_column(Date, nullable=False, server_default=func.current_date())
    resumen_cualitativo_ia: Mapped[str | None] = mapped_column(Text)
    usuario_id: Mapped[int] = mapped_column(Integer, ForeignKey("usuario.id"), nullable=False)
    socio_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("socio.id"))
    cuota_deudas_mensual = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    actividad_economica: Mapped[str | None] = mapped_column(String(150))
    fuente_ingresos: Mapped[str | None] = mapped_column(String(20))
    antiguedad_laboral_meses: Mapped[int | None] = mapped_column(Integer)
    calificacion_asfi: Mapped[str | None] = mapped_column(String(1))
    observaciones: Mapped[str | None] = mapped_column(Text)

    socio: Mapped["Socio | None"] = relationship()
    usuario: Mapped["Usuario"] = relationship()


class SolicitudCredito(Base):
    __tablename__ = "solicitud_credito"
    __table_args__ = (
        UniqueConstraint("cooperativa_id", "numero_solicitud", name="uq_solicitud_credito_coop_numero"),
        UniqueConstraint("id", "cooperativa_id", name="uq_solicitud_credito_id_cooperativa"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monto = mapped_column(Numeric(12, 2), nullable=False)
    plazo_meses: Mapped[int] = mapped_column(Integer, nullable=False)
    tasa_interes = mapped_column(Numeric(5, 2), nullable=False)
    calificacion_asfi: Mapped[str | None] = mapped_column(String(10))
    tiene_deudas: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default="false")
    estado: Mapped[str | None] = mapped_column(String(20), default="PENDIENTE", server_default="PENDIENTE")
    socio_id: Mapped[int] = mapped_column(Integer, ForeignKey("socio.id"), nullable=False)
    usuario_id: Mapped[int] = mapped_column(Integer, ForeignKey("usuario.id"), nullable=False)
    evaluacion_campo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("evaluacion_campo.id"))
    producto_credito_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("producto_credito.id"))
    numero_solicitud: Mapped[str | None] = mapped_column(String(20))
    destino: Mapped[str | None] = mapped_column(String(20))
    destino_detalle: Mapped[str | None] = mapped_column(Text)
    observaciones: Mapped[str | None] = mapped_column(Text)
    motivo_anulacion: Mapped[str | None] = mapped_column(Text)
    fecha_solicitud = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    fecha_actualizacion = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    moneda_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("moneda.id"))
    cooperativa_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cooperativa.id"))
    ronda_comite: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    resultado_comite: Mapped[str | None] = mapped_column(String(12))
    fecha_resolucion_comite = mapped_column(TIMESTAMP(timezone=True))

    socio: Mapped["Socio"] = relationship()
    producto: Mapped["ProductoCredito | None"] = relationship()
    moneda: Mapped["Moneda | None"] = relationship()
    evaluacion: Mapped["EvaluacionCampo | None"] = relationship()
    oficial: Mapped["Usuario"] = relationship()
    cooperativa: Mapped["Cooperativa | None"] = relationship()
    evaluaciones_crediticias: Mapped[list["EvaluacionCrediticia"]] = relationship(
        back_populates="solicitud", order_by="EvaluacionCrediticia.fecha.desc()"
    )
    garantias: Mapped[list["Garantia"]] = relationship(back_populates="solicitud", lazy="selectin")


class Garantia(Base):
    __tablename__ = "garantia"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cooperativa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cooperativa.id"), nullable=False)
    solicitud_credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("solicitud_credito.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    moneda_id: Mapped[int] = mapped_column(Integer, ForeignKey("moneda.id"), nullable=False)
    valor_comercial = mapped_column(Numeric(14, 2))
    valor_realizable = mapped_column(Numeric(14, 2), nullable=False)
    documento_referencia: Mapped[str | None] = mapped_column(String(120))
    avalista_nombre: Mapped[str | None] = mapped_column(String(150))
    avalista_ci: Mapped[str | None] = mapped_column(String(20))
    avalista_ingreso_mensual = mapped_column(Numeric(14, 2))
    avalista_relacion: Mapped[str | None] = mapped_column(String(60))
    avalista_telefono: Mapped[str | None] = mapped_column(String(30))
    socio_avalista_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("socio.id"))
    estado: Mapped[str] = mapped_column(String(12), nullable=False, default="REGISTRADA", server_default="REGISTRADA")
    observacion_verificacion: Mapped[str | None] = mapped_column(Text)
    usuario_registro_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuario.id"), nullable=False)
    usuario_verificacion_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    fecha_registro = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    fecha_verificacion = mapped_column(TIMESTAMP(timezone=True))
    fecha_liberacion = mapped_column(TIMESTAMP(timezone=True))

    solicitud: Mapped["SolicitudCredito"] = relationship(back_populates="garantias")
    moneda: Mapped["Moneda"] = relationship()
    usuario_registro: Mapped["Usuario"] = relationship(foreign_keys=[usuario_registro_id])
    usuario_verificacion: Mapped["Usuario | None"] = relationship(foreign_keys=[usuario_verificacion_id])


class EvaluacionCrediticia(Base):
    __tablename__ = "evaluacion_crediticia"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 1000", name="ck_evaluacion_crediticia_score"),
        CheckConstraint(
            "dictamen IN ('APROBADO', 'RECHAZADO', 'REVISION_MANUAL')",
            name="ck_evaluacion_crediticia_dictamen",
        ),
        CheckConstraint(
            "resolucion IS NULL OR resolucion IN ('APROBADO', 'RECHAZADO')",
            name="ck_evaluacion_crediticia_resolucion",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    solicitud_credito_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("solicitud_credito.id"), nullable=False
    )
    cooperativa_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cooperativa.id"), nullable=False
    )
    version_modelo: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    dictamen: Mapped[str] = mapped_column(String(20), nullable=False)
    factores = mapped_column(JSONB, nullable=False)
    knockouts = mapped_column(JSONB, nullable=False)
    explicacion: Mapped[str] = mapped_column(Text, nullable=False)
    cuota_estimada = mapped_column(Numeric(14, 2))
    relacion_cuota_ingreso = mapped_column(Numeric(7, 2))
    usuario_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuario.id"), nullable=False)
    fecha = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    resolucion: Mapped[str | None] = mapped_column(String(20))
    resolucion_justificacion: Mapped[str | None] = mapped_column(Text)
    resolucion_usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    resolucion_fecha = mapped_column(TIMESTAMP(timezone=True))
    probabilidad_mora = mapped_column(Numeric(5, 4))
    nivel_riesgo: Mapped[str | None] = mapped_column(String(10))
    version_modelo_mora: Mapped[str | None] = mapped_column(String(20))

    solicitud: Mapped["SolicitudCredito"] = relationship(back_populates="evaluaciones_crediticias")
    cooperativa: Mapped["Cooperativa"] = relationship()
    usuario: Mapped["Usuario"] = relationship(foreign_keys=[usuario_id])
    resolucion_usuario: Mapped["Usuario | None"] = relationship(foreign_keys=[resolucion_usuario_id])


class VotoComite(Base):
    __tablename__ = "voto_comite"
    __table_args__ = (
        UniqueConstraint("solicitud_credito_id", "ronda", "usuario_id", name="uq_voto_comite_solicitud_ronda_usuario"),
        ForeignKeyConstraint(
            ["solicitud_credito_id", "cooperativa_id"],
            ["solicitud_credito.id", "solicitud_credito.cooperativa_id"],
            name="fk_voto_comite_solicitud_cooperativa",
        ),
        ForeignKeyConstraint(
            ["usuario_id", "cooperativa_id"],
            ["usuario.id", "usuario.cooperativa_id"],
            name="fk_voto_comite_usuario_cooperativa",
        ),
        CheckConstraint("ronda > 0", name="ck_voto_comite_ronda"),
        CheckConstraint("voto IN ('APROBAR','RECHAZAR','OBSERVAR')", name="ck_voto_comite_voto"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    solicitud_credito_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cooperativa_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ronda: Mapped[int] = mapped_column(Integer, nullable=False)
    usuario_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voto: Mapped[str] = mapped_column(String(10), nullable=False)
    comentario: Mapped[str] = mapped_column(Text, nullable=False)
    fecha = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    usuario: Mapped["Usuario"] = relationship()


class Credito(Base):
    __tablename__ = "credito"
    __table_args__ = (
        UniqueConstraint("cooperativa_id", "numero_credito", name="uq_credito_cooperativa_numero"),
        CheckConstraint(
            "modalidad_desembolso IS NULL OR modalidad_desembolso IN ('CUENTA','EFECTIVO')",
            name="ck_credito_modalidad_desembolso",
        ),
        CheckConstraint(
            "tipo_amortizacion IS NULL OR tipo_amortizacion IN ('FRANCES','ALEMAN')",
            name="ck_credito_tipo_amortizacion",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monto_aprobado = mapped_column(Numeric(12, 2), nullable=False)
    saldo_pendiente = mapped_column(Numeric(12, 2), nullable=False)
    estado: Mapped[str | None] = mapped_column(String(20), default="VIGENTE", server_default="VIGENTE")
    solicitud_credito_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("solicitud_credito.id"), nullable=False, unique=True
    )
    numero_credito: Mapped[str | None] = mapped_column(String(20))
    cooperativa_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cooperativa.id"))
    socio_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("socio.id"))
    producto_credito_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("producto_credito.id"))
    moneda_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("moneda.id"))
    tasa_interes = mapped_column(Numeric(5, 2))
    plazo_meses: Mapped[int | None] = mapped_column(Integer)
    tipo_amortizacion: Mapped[str | None] = mapped_column(String(10))
    fecha_desembolso = mapped_column(Date)
    modalidad_desembolso: Mapped[str | None] = mapped_column(String(10))
    cuenta_desembolso_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("cuenta_ahorro.id"))
    # Database FK is installed by migration 017; no Transaccion ORM exists in this project.
    transaccion_desembolso_id: Mapped[int | None] = mapped_column(Integer)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    fecha_creacion = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    solicitud: Mapped["SolicitudCredito"] = relationship()
    cooperativa: Mapped["Cooperativa | None"] = relationship()
    socio: Mapped["Socio | None"] = relationship()
    producto: Mapped["ProductoCredito | None"] = relationship()
    moneda: Mapped["Moneda | None"] = relationship()
    cuenta_desembolso: Mapped["CuentaAhorro | None"] = relationship()
    usuario: Mapped["Usuario | None"] = relationship()
    cronograma: Mapped[list["TablaAmortizacion"]] = relationship(
        back_populates="credito", order_by="TablaAmortizacion.numero_cuota"
    )
    mora: Mapped["Morosidad | None"] = relationship(back_populates="credito")
    pagos: Mapped[list["PagoCuota"]] = relationship(back_populates="credito")


class TablaAmortizacion(Base):
    __tablename__ = "tabla_amortizacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero_cuota: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_vencimiento = mapped_column(Date, nullable=False)
    monto_capital = mapped_column(Numeric(12, 2), nullable=False)
    monto_interes = mapped_column(Numeric(12, 2), nullable=False)
    monto_cuota_total = mapped_column(Numeric(12, 2), nullable=False)
    estado_pago: Mapped[str | None] = mapped_column(String(20), default="PENDIENTE", server_default="PENDIENTE")
    credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("credito.id"), nullable=False)
    saldo_inicial = mapped_column(Numeric(14, 2))
    saldo_final = mapped_column(Numeric(14, 2))
    monto_pagado = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0.00")
    fecha_pago = mapped_column(TIMESTAMP(timezone=True))

    credito: Mapped["Credito"] = relationship(back_populates="cronograma")
    pagos: Mapped[list["PagoCuota"]] = relationship(
        back_populates="cuota", order_by="PagoCuota.id"
    )


class PagoCuota(Base):
    __tablename__ = "pago_cuota"
    __table_args__ = (
        UniqueConstraint(
            "cooperativa_id", "numero_recibo",
            name="uq_pago_cuota_cooperativa_recibo",
        ),
        CheckConstraint(
            "modalidad IS NULL OR modalidad IN ('EFECTIVO', 'CUENTA')",
            name="ck_pago_cuota_modalidad",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monto_capital = mapped_column(Numeric(12, 2), nullable=False)
    monto_interes_pagado = mapped_column(Numeric(12, 2), nullable=False)
    monto_mora = mapped_column(Numeric(12, 2), default=0, server_default="0.00")
    fecha = mapped_column(TIMESTAMP(timezone=False), server_default=func.now())
    tabla_amortizacion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tabla_amortizacion.id"), nullable=False
    )
    credito_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("credito.id"))
    cooperativa_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cooperativa.id")
    )
    numero_recibo: Mapped[str | None] = mapped_column(String(20))
    modalidad: Mapped[str | None] = mapped_column(String(10))
    monto_total = mapped_column(Numeric(14, 2))
    dias_atraso: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The transaction table is SQL-only in this project; the FK is installed by migration 018.
    transaccion_id: Mapped[int | None] = mapped_column(Integer)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))

    cuota: Mapped["TablaAmortizacion"] = relationship(back_populates="pagos")
    credito: Mapped["Credito | None"] = relationship(back_populates="pagos")
    cooperativa: Mapped["Cooperativa | None"] = relationship()
    usuario: Mapped["Usuario | None"] = relationship()


class Morosidad(Base):
    __tablename__ = "morosidad"
    __table_args__ = (
        UniqueConstraint("credito_id", name="uq_morosidad_credito"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dias_de_retaso: Mapped[int] = mapped_column(Integer, nullable=False)
    monto_penalizado = mapped_column(Numeric(12, 2), default=0, server_default="0.00")
    estado: Mapped[str | None] = mapped_column(String(20), default="EN_MORA", server_default="EN_MORA")
    credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("credito.id"), nullable=False)
    fecha_actualizacion = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    credito: Mapped["Credito"] = relationship(back_populates="mora")


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


class CierreCaja(Base):
    __tablename__ = "cierre_caja"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    control_caja_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("control_caja.id"), nullable=False, unique=True
    )
    arqueo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("arqueo_caja.id"), nullable=False
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id"), nullable=False
    )
    fecha = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    observacion: Mapped[str | None] = mapped_column(Text)

    control_caja: Mapped["ControlCaja"] = relationship()
    arqueo: Mapped["ArqueoCaja"] = relationship()
    usuario: Mapped["Usuario"] = relationship()
    monedas: Mapped[list["CierreCajaMoneda"]] = relationship(
        back_populates="cierre", cascade="all, delete-orphan"
    )


class CierreCajaMoneda(Base):
    __tablename__ = "cierre_caja_moneda"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cierre_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cierre_caja.id", ondelete="CASCADE"), nullable=False
    )
    moneda_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("moneda.id"), nullable=False
    )
    monto_apertura = mapped_column(Numeric(12, 2), nullable=False)
    total_depositos = mapped_column(Numeric(12, 2), nullable=False)
    cantidad_depositos: Mapped[int] = mapped_column(Integer, nullable=False)
    total_retiros = mapped_column(Numeric(12, 2), nullable=False)
    cantidad_retiros: Mapped[int] = mapped_column(Integer, nullable=False)
    cantidad_transferencias: Mapped[int] = mapped_column(Integer, nullable=False)
    saldo_teorico = mapped_column(Numeric(12, 2), nullable=False)
    total_contado = mapped_column(Numeric(12, 2), nullable=False)
    diferencia = mapped_column(Numeric(12, 2), nullable=False)
    traspaso_boveda = mapped_column(Numeric(12, 2), nullable=False)

    cierre: Mapped["CierreCaja"] = relationship(back_populates="monedas")
    moneda: Mapped["Moneda"] = relationship()


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


class DeclaracionJuradaUIF(Base):
    __tablename__ = "declaracion_jurada_uif"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origen: Mapped[str] = mapped_column(String(255), nullable=False)
    destino: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo_operacion: Mapped[str | None] = mapped_column(String(30))
    monto = mapped_column(Numeric(14, 2))
    moneda_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("moneda.id"))
    socio_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("socio.id"))
    realizado_por: Mapped[str | None] = mapped_column(String(10))
    tercero_nombre: Mapped[str | None] = mapped_column(String(150))
    tercero_ci: Mapped[str | None] = mapped_column(String(20))
    tercero_parentesco: Mapped[str | None] = mapped_column(String(50))
    actividad_economica: Mapped[str | None] = mapped_column(String(150))
    origen_detalle: Mapped[str | None] = mapped_column(Text)
    destino_detalle: Mapped[str | None] = mapped_column(Text)
    declara_bajo_juramento: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fraccionada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    cooperativa_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cooperativa.id"))
    fecha = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class DepositoPlazoFijo(Base):
    __tablename__ = "deposito_plazo_fijo"
    __table_args__ = (Index(
        "uq_dpf_coop_numero_certificado", "cooperativa_id", "numero_certificado", unique=True,
        postgresql_where=sql_text("numero_certificado IS NOT NULL"),
    ),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monto = mapped_column(Numeric(12, 2), nullable=False)
    tasa_interes_anual = mapped_column(Numeric(5, 2), nullable=False)
    plazo_dias: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_inicio = mapped_column(Date, nullable=False)
    fecha_vencimiento = mapped_column(Date, nullable=False)
    interes_calculado = mapped_column(Numeric(12, 2), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="VIGENTE")
    socio_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("socio.id"), nullable=False)
    moneda_id: Mapped[int] = mapped_column(Integer, ForeignKey("moneda.id"), nullable=False)
    numero_certificado: Mapped[str | None] = mapped_column(String(30))
    modalidad_pago_interes: Mapped[str | None] = mapped_column(String(12), default="VENCIMIENTO")
    interes_bruto = mapped_column(Numeric(12, 2))
    retencion_rciva = mapped_column(Numeric(12, 2))
    interes_neto = mapped_column(Numeric(12, 2))
    origen_fondos: Mapped[str | None] = mapped_column(String(10))
    cuenta_origen_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cuenta_ahorro.id"))
    cuenta_abono_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cuenta_ahorro.id"))
    codigo_verificacion: Mapped[str | None] = mapped_column(String(16))
    dpf_origen_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("deposito_plazo_fijo.id"))
    declaracion_jurada_uif_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("declaracion_jurada_uif.id"))
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    cooperativa_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cooperativa.id"))
    fecha_emision = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class TasaDPF(Base):
    __tablename__ = "tasa_dpf"
    __table_args__ = (UniqueConstraint("cooperativa_id", "moneda_id", "plazo_min_dias", name="uq_tasa_dpf_banda"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cooperativa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cooperativa.id"), nullable=False)
    moneda_id: Mapped[int] = mapped_column(Integer, ForeignKey("moneda.id"), nullable=False)
    plazo_min_dias: Mapped[int] = mapped_column(Integer, nullable=False)
    plazo_max_dias: Mapped[int | None] = mapped_column(Integer)
    tna = mapped_column(Numeric(5, 2), nullable=False)


class DPFCronograma(Base):
    __tablename__ = "dpf_cronograma"
    __table_args__ = (UniqueConstraint("deposito_plazo_fijo_id", "numero", name="uq_dpf_cronograma_numero"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deposito_plazo_fijo_id: Mapped[int] = mapped_column(Integer, ForeignKey("deposito_plazo_fijo.id", ondelete="CASCADE"), nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_pago = mapped_column(Date, nullable=False)
    dias: Mapped[int] = mapped_column(Integer, nullable=False)
    interes_bruto = mapped_column(Numeric(12, 2), nullable=False)
    retencion_rciva = mapped_column(Numeric(12, 2), nullable=False)
    interes_neto = mapped_column(Numeric(12, 2), nullable=False)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDIENTE")
    fecha_pago_real = mapped_column(TIMESTAMP(timezone=True))
    transaccion_id: Mapped[int | None] = mapped_column(Integer)


class Liquidacion(Base):
    __tablename__ = "liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monto_capital_retornado = mapped_column(Numeric(12, 2), nullable=False)
    monto_interes_pagado = mapped_column(Numeric(12, 2), nullable=False)
    tipo_operacion: Mapped[str] = mapped_column(String(50), nullable=False)
    fecha = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    deposito_plazo_fijo_id: Mapped[int] = mapped_column(Integer, ForeignKey("deposito_plazo_fijo.id"), nullable=False, unique=True)
    tipo: Mapped[str | None] = mapped_column(String(20))
    retencion_rciva = mapped_column(Numeric(12, 2))
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    cuenta_abono_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cuenta_ahorro.id"))
    dpf_renovado_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("deposito_plazo_fijo.id"))
    interes_ya_pagado = mapped_column(Numeric(12, 2), nullable=False, default=0)
    descuento_capital = mapped_column(Numeric(12, 2), nullable=False, default=0)


class PrediccionDeMorosidad(Base):
    __tablename__ = "prediccion_de_morosidad"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("credito.id", ondelete="CASCADE"), nullable=False, unique=True)
    probabilidad_mora = mapped_column(Numeric(5, 4), nullable=False)
    nivel_riesgo: Mapped[str] = mapped_column(String(10), nullable=False)
    version_modelo: Mapped[str | None] = mapped_column(String(20))
    fecha = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class GestionCobranza(Base):
    """Maps both legacy collection rows and W28 audited collection actions."""
    __tablename__ = "historial_gestion_de_cobranza"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo_contacto: Mapped[str] = mapped_column(String(50), nullable=False)
    resultado_gestion: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_compromiso_pago = mapped_column("fecha_de_compromiso_de_pago", Date)
    credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("credito.id"), nullable=False)
    cooperativa_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cooperativa.id"))
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    fecha = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    alerta_id: Mapped[int | None] = mapped_column(BigInteger)
    usuario: Mapped["Usuario | None"] = relationship()


class AlertaCredito(Base):
    __tablename__ = "alerta_credito"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cooperativa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cooperativa.id"), nullable=False)
    credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("credito.id"), nullable=False)
    tabla_amortizacion_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tabla_amortizacion.id"))
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    severidad: Mapped[str] = mapped_column(String(12), nullable=False)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    datos = mapped_column(JSONB, nullable=False, server_default="{}")
    estado: Mapped[str] = mapped_column(String(12), nullable=False, server_default="ACTIVA")
    fecha_creacion = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    fecha_cierre = mapped_column(TIMESTAMP(timezone=True))
    usuario_cierre_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    comentario_cierre: Mapped[str | None] = mapped_column(Text)
    gestion_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("historial_gestion_de_cobranza.id"))
    credito: Mapped["Credito"] = relationship()
    cuota: Mapped["TablaAmortizacion | None"] = relationship()
    usuario_cierre: Mapped["Usuario | None"] = relationship(foreign_keys=[usuario_cierre_id])
    gestion: Mapped["GestionCobranza | None"] = relationship(foreign_keys=[gestion_id])


class OfertaRecredito(Base):
    __tablename__ = "oferta_recredito"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cooperativa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cooperativa.id"), nullable=False)
    socio_id: Mapped[int] = mapped_column(Integer, ForeignKey("socio.id"), nullable=False)
    credito_origen_id: Mapped[int] = mapped_column(Integer, ForeignKey("credito.id"), nullable=False)
    producto_credito_id: Mapped[int] = mapped_column(Integer, ForeignKey("producto_credito.id"), nullable=False)
    monto_sugerido = mapped_column(Numeric(14, 2), nullable=False)
    plazo_meses: Mapped[int] = mapped_column(Integer, nullable=False)
    tasa_interes = mapped_column(Numeric(5, 2), nullable=False)
    cuota_estimada = mapped_column(Numeric(14, 2), nullable=False)
    probabilidad_mora = mapped_column(Numeric(5, 4))
    nivel_riesgo: Mapped[str | None] = mapped_column(String(10))
    motivos = mapped_column(JSONB, nullable=False)
    estado: Mapped[str] = mapped_column(String(12), nullable=False, server_default="VIGENTE")
    fecha_generacion = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    fecha_vencimiento = mapped_column(Date, nullable=False)
    solicitud_generada_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("solicitud_credito.id"))
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    motivo_descarte: Mapped[str | None] = mapped_column(Text)
