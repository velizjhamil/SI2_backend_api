"""Cuentas de ahorro y certificados de aportacion."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fpdf import FPDF
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_socio, get_current_user, get_db, require_operaciones
from app.core.bitacora import registrar_accion
from app.models.models import CertificadoAportacion, CuentaAhorro, Moneda, Socio, Usuario
from app.schemas.schemas import (
    CertificadoAportacionCreate,
    CertificadoAportacionOut,
    CuentaAhorroCreate,
    CuentaAhorroEstadoUpdate,
    CuentaAhorroOut,
    MovimientoCuentaCreate,
    MovimientoCuentaOut,
    MonedaOut,
    TransferenciaCreate,
    TransferenciaOut,
)

router = APIRouter()

# Monto minimo de apertura segun el producto (regla de negocio parametrizada por tipo).
MONTO_MINIMO_APERTURA: dict[str, Decimal] = {
    "VISTA": Decimal("10.00"),
    "PROGRAMADO": Decimal("50.00"),
}


def _socio_visible(admin: Usuario, socio: Socio) -> bool:
    return admin.rol.nombre == "SUPERADMIN" or socio.cooperativa_id == admin.cooperativa_id


def _get_socio(db: Session, admin: Usuario, socio_id: int) -> Socio:
    socio = db.get(Socio, socio_id)
    if socio is None or not _socio_visible(admin, socio):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Socio no encontrado")
    if socio.estado != "ACTIVO":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El socio no está activo")
    return socio


def _get_moneda(db: Session, moneda_id: int) -> Moneda:
    moneda = db.get(Moneda, moneda_id)
    if moneda is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Moneda no encontrada")
    return moneda


def _get_cuenta_visible(db: Session, admin: Usuario, cuenta_id: int, *, requiere_activa: bool = True) -> CuentaAhorro:
    cuenta = db.execute(
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(joinedload(CuentaAhorro.moneda, innerjoin=True))
        .where(CuentaAhorro.id == cuenta_id)
        .with_for_update()
    ).unique().scalar_one_or_none()
    if cuenta is None or not _socio_visible(admin, cuenta.socio):
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    if requiere_activa and cuenta.estado != "ACTIVA":
        raise HTTPException(status_code=400, detail="La cuenta no está activa")
    return cuenta


def _get_certificado_visible(db: Session, admin: Usuario, certificado_id: int) -> CertificadoAportacion:
    certificado = db.execute(
        select(CertificadoAportacion)
        .join(Socio, CertificadoAportacion.socio_id == Socio.id)
        .options(
            joinedload(CertificadoAportacion.moneda, innerjoin=True),
            joinedload(CertificadoAportacion.socio, innerjoin=True),
        )
        .where(CertificadoAportacion.id == certificado_id)
    ).unique().scalar_one_or_none()
    if certificado is None or not _socio_visible(admin, certificado.socio):
        raise HTTPException(status_code=404, detail="Certificado no encontrado")
    return certificado


def _registrar_movimiento(db: Session, cuenta: CuentaAhorro, tipo: str, monto, admin: Usuario, request: Request):
    db.execute(
        text("""
            INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id)
            VALUES (:tipo, :monto, 'WEB', :moneda_id, :cuenta_id)
        """),
        {"tipo": tipo, "monto": monto, "moneda_id": cuenta.moneda_id, "cuenta_id": cuenta.id},
    )
    registrar_accion(
        db,
        accion=tipo,
        modulo="AHORROS",
        usuario_id=admin.id,
        cooperativa_id=cuenta.socio.cooperativa_id,
        descripcion=f"{tipo} de {monto} en cuenta {cuenta.numero}",
        request=request,
    )


def _siguiente_secuencia(db: Session, cooperativa_id: int | None, tipo: str) -> int:
    """Incrementa atómicamente el correlativo parametrizado (por cooperativa y tipo de documento)."""
    tenant = cooperativa_id or 0
    return db.execute(
        text("""
            INSERT INTO secuencia_documento (cooperativa_id, tipo, siguiente)
            VALUES (:coop, :tipo, 2)
            ON CONFLICT (cooperativa_id, tipo)
            DO UPDATE SET siguiente = secuencia_documento.siguiente + 1
            RETURNING siguiente - 1
        """),
        {"coop": tenant, "tipo": tipo},
    ).scalar_one()


def _numero_cuenta(db: Session, cooperativa_id: int | None) -> str:
    tenant = cooperativa_id or 0
    correlativo = _siguiente_secuencia(db, cooperativa_id, "CUENTA_AHORRO")
    return f"CA-{tenant:03d}-{correlativo:06d}"


@router.get("/monedas", response_model=list[MonedaOut], summary="Listar monedas")
def listar_monedas(_: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.execute(select(Moneda).order_by(Moneda.codigo_iso)).scalars())


@router.get("/cuentas", response_model=list[CuentaAhorroOut], summary="Listar cuentas de ahorro")
def listar_cuentas(
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
    socio_id: int | None = Query(None, ge=1),
    estado: str | None = Query(None, pattern="^(ACTIVA|BLOQUEADA|CANCELADA)$"),
    limite: int | None = Query(None, ge=1, le=200),
):
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(joinedload(CuentaAhorro.moneda), joinedload(CuentaAhorro.socio))
        .order_by(CuentaAhorro.id.desc())
    )
    if admin.rol.nombre != "SUPERADMIN":
        query = query.where(Socio.cooperativa_id == admin.cooperativa_id)
    if socio_id:
        query = query.where(CuentaAhorro.socio_id == socio_id)
    if estado:
        query = query.where(CuentaAhorro.estado == estado)
    if limite:
        query = query.limit(limite)
    cuentas = list(db.execute(query).unique().scalars())
    return [
        CuentaAhorroOut.model_validate(c).model_copy(
            update={"socio_nombre": f"{c.socio.nombre} {c.socio.apellido}"}
        )
        for c in cuentas
    ]


@router.post("/cuentas", response_model=CuentaAhorroOut, status_code=status.HTTP_201_CREATED, summary="Abrir cuenta de ahorro")
def abrir_cuenta(
    body: CuentaAhorroCreate,
    request: Request,
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    socio = _get_socio(db, admin, body.socio_id)
    moneda = _get_moneda(db, body.moneda_id)

    minimo = MONTO_MINIMO_APERTURA[body.tipo_producto]
    if body.monto_apertura < minimo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El monto mínimo de apertura para cuentas {body.tipo_producto.lower()} es {minimo}",
        )

    cuenta = CuentaAhorro(
        numero=_numero_cuenta(db, socio.cooperativa_id),
        tipo_producto=body.tipo_producto,
        fecha_registro=date.today(),
        socio_id=socio.id,
        moneda_id=moneda.id,
        estado="ACTIVA",
        saldo_disponible=body.monto_apertura,
    )
    db.add(cuenta)
    db.flush()
    _registrar_movimiento(db, cuenta, "APERTURA", body.monto_apertura, admin, request)
    db.commit()
    db.refresh(cuenta)
    return db.execute(
        select(CuentaAhorro).options(joinedload(CuentaAhorro.moneda)).where(CuentaAhorro.id == cuenta.id)
    ).scalar_one()


@router.patch("/cuentas/{cuenta_id}/estado", response_model=CuentaAhorroOut, summary="Cambiar estado de una cuenta")
def cambiar_estado_cuenta(
    cuenta_id: int,
    body: CuentaAhorroEstadoUpdate,
    request: Request,
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cuenta = _get_cuenta_visible(db, admin, cuenta_id, requiere_activa=False)
    if cuenta.estado == body.estado:
        return cuenta
    estado_anterior = cuenta.estado
    cuenta.estado = body.estado
    registrar_accion(
        db,
        accion="CAMBIO_ESTADO",
        modulo="AHORROS",
        usuario_id=admin.id,
        cooperativa_id=cuenta.socio.cooperativa_id,
        descripcion=f"Cuenta {cuenta.numero}: {estado_anterior} → {body.estado}",
        request=request,
    )
    db.commit()
    db.refresh(cuenta)
    return db.execute(
        select(CuentaAhorro).options(joinedload(CuentaAhorro.moneda)).where(CuentaAhorro.id == cuenta.id)
    ).scalar_one()


@router.post("/cuentas/{cuenta_id}/depositos", response_model=MovimientoCuentaOut, summary="Depositar en cuenta")
def depositar(
    cuenta_id: int,
    body: MovimientoCuentaCreate,
    request: Request,
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cuenta = _get_cuenta_visible(db, admin, cuenta_id)
    cuenta.saldo_disponible = cuenta.saldo_disponible + body.monto
    _registrar_movimiento(db, cuenta, "DEPOSITO", body.monto, admin, request)
    db.commit()
    db.refresh(cuenta)
    return MovimientoCuentaOut(cuenta_id=cuenta.id, tipo="DEPOSITO", monto=body.monto, saldo_disponible=cuenta.saldo_disponible, moneda=cuenta.moneda)


@router.post("/cuentas/{cuenta_id}/retiros", response_model=MovimientoCuentaOut, summary="Retirar de cuenta")
def retirar(
    cuenta_id: int,
    body: MovimientoCuentaCreate,
    request: Request,
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cuenta = _get_cuenta_visible(db, admin, cuenta_id)
    if cuenta.saldo_disponible < body.monto:
        raise HTTPException(status_code=400, detail="Saldo insuficiente")
    cuenta.saldo_disponible = cuenta.saldo_disponible - body.monto
    _registrar_movimiento(db, cuenta, "RETIRO", body.monto, admin, request)
    db.commit()
    db.refresh(cuenta)
    return MovimientoCuentaOut(cuenta_id=cuenta.id, tipo="RETIRO", monto=body.monto, saldo_disponible=cuenta.saldo_disponible, moneda=cuenta.moneda)


@router.get("/certificados", response_model=list[CertificadoAportacionOut], summary="Listar certificados")
def listar_certificados(
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
    socio_id: int | None = Query(None, ge=1),
):
    query = (
        select(CertificadoAportacion)
        .join(Socio, CertificadoAportacion.socio_id == Socio.id)
        .options(joinedload(CertificadoAportacion.moneda))
        .order_by(CertificadoAportacion.id.desc())
    )
    if admin.rol.nombre != "SUPERADMIN":
        query = query.where(Socio.cooperativa_id == admin.cooperativa_id)
    if socio_id:
        query = query.where(CertificadoAportacion.socio_id == socio_id)
    return list(db.execute(query).unique().scalars())


@router.post("/certificados", response_model=CertificadoAportacionOut, status_code=status.HTTP_201_CREATED, summary="Emitir certificado de aportación")
def emitir_certificado(
    body: CertificadoAportacionCreate,
    request: Request,
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    socio = _get_socio(db, admin, body.socio_id)
    moneda = _get_moneda(db, body.moneda_id)
    correlativo = _siguiente_secuencia(db, socio.cooperativa_id, "CERTIFICADO_APORTACION")
    monto = body.numero_titulos * body.valor_unitario
    certificado = CertificadoAportacion(
        correlativo=correlativo,
        numero_titulos=body.numero_titulos,
        valor_unitario=body.valor_unitario,
        monto=monto,
        fecha_emision=body.fecha_emision,
        estado="EMITIDO",
        socio_id=socio.id,
        moneda_id=moneda.id,
    )
    db.add(certificado)
    db.flush()
    registrar_accion(
        db,
        accion="EMISION",
        modulo="APORTES",
        usuario_id=admin.id,
        cooperativa_id=socio.cooperativa_id,
        descripcion=f"Certificado #{correlativo} ({body.numero_titulos} títulos) emitido para socio {socio.id}",
        request=request,
    )
    db.commit()
    db.refresh(certificado)
    return db.execute(
        select(CertificadoAportacion)
        .options(joinedload(CertificadoAportacion.moneda))
        .where(CertificadoAportacion.id == certificado.id)
    ).scalar_one()


@router.get("/certificados/{certificado_id}/pdf", summary="Comprobante en PDF del certificado")
def comprobante_certificado_pdf(
    certificado_id: int,
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    certificado = _get_certificado_visible(db, admin, certificado_id)

    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Certificado de Aportación", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 8, "Comprobante de emisión", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(8)

    filas = [
        ("N.º de certificado", f"{certificado.correlativo:06d}"),
        ("Socio", f"{certificado.socio.nombre} {certificado.socio.apellido}"),
        ("CI", certificado.socio.ci),
        ("Fecha de emisión", certificado.fecha_emision.strftime("%d/%m/%Y")),
        ("Número de títulos", str(certificado.numero_titulos)),
        ("Valor unitario", f"{certificado.moneda.simbolo} {certificado.valor_unitario:.2f}"),
        ("Monto total", f"{certificado.moneda.simbolo} {certificado.monto:.2f}"),
        ("Estado", certificado.estado),
    ]
    for etiqueta, valor in filas:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(55, 9, f"{etiqueta}:")
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 9, valor, new_x="LMARGIN", new_y="NEXT")

    pdf_bytes = bytes(pdf.output())
    nombre_archivo = f"certificado_{certificado.correlativo:06d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


@router.get("/mis-cuentas", response_model=list[CuentaAhorroOut], summary="Consultar mis cuentas")
def mis_cuentas(usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(joinedload(CuentaAhorro.moneda))
        .where(Socio.usuario_id == usuario.id)
        .order_by(CuentaAhorro.id.desc())
    )
    return list(db.execute(query).unique().scalars())


@router.get("/mis-certificados", response_model=list[CertificadoAportacionOut], summary="Consultar mis certificados")
def mis_certificados(usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(CertificadoAportacion)
        .join(Socio, CertificadoAportacion.socio_id == Socio.id)
        .options(joinedload(CertificadoAportacion.moneda))
        .where(Socio.usuario_id == usuario.id)
        .order_by(CertificadoAportacion.id.desc())
    )
    return list(db.execute(query).unique().scalars())


def _lock_cuentas_propias(
    db: Session, socio: Socio, cuenta_origen_id: int, cuenta_destino_id: int
) -> tuple[CuentaAhorro, CuentaAhorro]:
    """Bloquea (`FOR UPDATE`) las dos cuentas de una transferencia, en orden
    ascendente por `id`, para evitar deadlocks entre transferencias
    concurrentes que involucren el mismo par de cuentas en cualquier orden.

    La propiedad se valida en el propio `WHERE` (`socio_id == socio.id`):
    cuenta inexistente y cuenta ajena producen el mismo `None` → 404, sin
    distinguir los dos casos. No usa `joinedload`: `moneda` se carga después
    del lock, fuera de esta función. No reutiliza `_get_cuenta_visible()`
    (autorización de staff, con 3 llamadores que no deben cambiar).
    """
    cuentas: dict[int, CuentaAhorro] = {}
    for cuenta_id in sorted([cuenta_origen_id, cuenta_destino_id]):
        cuenta = db.execute(
            select(CuentaAhorro)
            .where(CuentaAhorro.id == cuenta_id, CuentaAhorro.socio_id == socio.id)
            .with_for_update(of=CuentaAhorro)
        ).scalar_one_or_none()
        if cuenta is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")
        cuentas[cuenta_id] = cuenta
    return cuentas[cuenta_origen_id], cuentas[cuenta_destino_id]


def _registrar_transferencia(
    db: Session,
    *,
    cuenta_origen: CuentaAhorro,
    cuenta_destino: CuentaAhorro,
    monto: Decimal,
    glosa: str | None,
    usuario: Usuario,
    request: Request,
) -> tuple[int, int, object]:
    """Registra las dos patas de la transferencia en `transaccion`, vinculadas
    por `transaccion_contraparte_id`, más una entrada de bitácora. Todo dentro
    de la misma transacción del llamador (sin `commit` propio).
    """
    salida = db.execute(
        text("""
            INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id)
            VALUES ('TRANSFERENCIA_SALIDA', :monto, 'MOVIL', :moneda_id, :cuenta_id)
            RETURNING id, fecha_hora
        """),
        {"monto": monto, "moneda_id": cuenta_origen.moneda_id, "cuenta_id": cuenta_origen.id},
    ).one()
    salida_id, fecha_hora = salida

    entrada_id = db.execute(
        text("""
            INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id, transaccion_contraparte_id)
            VALUES ('TRANSFERENCIA_ENTRADA', :monto, 'MOVIL', :moneda_id, :cuenta_id, :contraparte)
            RETURNING id
        """),
        {
            "monto": monto,
            "moneda_id": cuenta_destino.moneda_id,
            "cuenta_id": cuenta_destino.id,
            "contraparte": salida_id,
        },
    ).scalar_one()

    db.execute(
        text("UPDATE transaccion SET transaccion_contraparte_id = :entrada WHERE id = :salida"),
        {"entrada": entrada_id, "salida": salida_id},
    )

    descripcion = f"Transferencia de {monto} de cuenta {cuenta_origen.numero} a {cuenta_destino.numero}"
    if glosa:
        descripcion += f" — {glosa}"
    registrar_accion(
        db,
        accion="TRANSFERENCIA",
        modulo="AHORROS",
        usuario_id=usuario.id,
        cooperativa_id=cuenta_origen.socio.cooperativa_id,
        descripcion=descripcion,
        request=request,
    )
    return salida_id, entrada_id, fecha_hora


@router.post(
    "/transferencias",
    response_model=TransferenciaOut,
    status_code=status.HTTP_201_CREATED,
    summary="Transferir saldo entre cuentas propias",
)
def transferir_entre_cuentas_propias(
    body: TransferenciaCreate,
    request: Request,
    usuario: Usuario = Depends(get_current_user),
    socio: Socio = Depends(get_current_socio),
    db: Session = Depends(get_db),
):
    """Autoservicio: el socio autenticado mueve saldo entre dos cuentas
    propias, sin comisión ni conversión de moneda en v1."""
    if body.cuenta_origen_id == body.cuenta_destino_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta de origen y la de destino deben ser distintas",
        )

    cuenta_origen, cuenta_destino = _lock_cuentas_propias(
        db, socio, body.cuenta_origen_id, body.cuenta_destino_id
    )

    if cuenta_origen.estado != "ACTIVA" or cuenta_destino.estado != "ACTIVA":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ambas cuentas deben estar activas")
    if cuenta_origen.moneda_id != cuenta_destino.moneda_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Las cuentas deben tener la misma moneda; no se realiza conversión",
        )
    if body.monto > cuenta_origen.saldo_disponible:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Saldo insuficiente")

    cuenta_origen.saldo_disponible = cuenta_origen.saldo_disponible - body.monto
    cuenta_destino.saldo_disponible = cuenta_destino.saldo_disponible + body.monto

    salida_id, entrada_id, fecha_hora = _registrar_transferencia(
        db,
        cuenta_origen=cuenta_origen,
        cuenta_destino=cuenta_destino,
        monto=body.monto,
        glosa=body.glosa,
        usuario=usuario,
        request=request,
    )
    db.commit()

    cuenta_origen_out = db.execute(
        select(CuentaAhorro).options(joinedload(CuentaAhorro.moneda)).where(CuentaAhorro.id == cuenta_origen.id)
    ).scalar_one()
    cuenta_destino_out = db.execute(
        select(CuentaAhorro).options(joinedload(CuentaAhorro.moneda)).where(CuentaAhorro.id == cuenta_destino.id)
    ).scalar_one()

    return TransferenciaOut(
        transaccion_salida_id=salida_id,
        transaccion_entrada_id=entrada_id,
        cuenta_origen=CuentaAhorroOut.model_validate(cuenta_origen_out),
        cuenta_destino=CuentaAhorroOut.model_validate(cuenta_destino_out),
        monto=body.monto,
        glosa=body.glosa,
        fecha_hora=fecha_hora,
    )
