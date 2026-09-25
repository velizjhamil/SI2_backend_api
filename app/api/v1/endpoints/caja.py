"""Operaciones de caja de ventanilla."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_db, require_operaciones
from app.api.v1.endpoints.ahorros import _registrar_transferencia
from app.core.bitacora import registrar_accion
from app.models.models import Caja, ControlCaja, CuentaAhorro, Socio, Usuario
from app.schemas.schemas import (
    AperturaCajaCreate,
    CajaOut,
    CuentaOrigenTransferenciaPreviewOut,
    CuentaCajaOut,
    CuentaTransferenciaPreviewOut,
    DepositoVentanillaCreate,
    DepositoVentanillaOut,
    MonedaOut,
    SesionCajaOut,
    TitularCajaOut,
    TransferenciaPreviewOut,
    TransferenciaVentanillaCreate,
    TransferenciaVentanillaOut,
)

router = APIRouter()


def _validar_operador(usuario: Usuario) -> int:
    if usuario.rol.nombre == "SUPERADMIN" or usuario.cooperativa_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operación no disponible para este usuario",
        )
    return usuario.cooperativa_id


def _sesion_out(control: ControlCaja) -> SesionCajaOut:
    return SesionCajaOut(
        id=control.id,
        caja_id=control.caja_id,
        caja_nombre=control.caja.nombre,
        monto_apertura=control.monto_apertura,
        saldo_sistema=control.saldo_sistema,
        fecha_apertura=control.fecha_apertura,
        estado=control.estado,
    )


def _titular_out(socio: Socio) -> TitularCajaOut:
    return TitularCajaOut(
        socio_id=socio.id,
        nombre_completo=f"{socio.nombre} {socio.apellido}".strip(),
        ci=socio.ci,
    )


def _cuenta_out(cuenta: CuentaAhorro) -> CuentaCajaOut:
    return CuentaCajaOut(
        id=cuenta.id,
        numero=cuenta.numero,
        estado=cuenta.estado,
        saldo_disponible=cuenta.saldo_disponible,
        moneda=MonedaOut.model_validate(cuenta.moneda),
        titular=_titular_out(cuenta.socio),
    )


def _cuenta_transferencia_out(cuenta: CuentaAhorro) -> CuentaTransferenciaPreviewOut:
    return CuentaTransferenciaPreviewOut(
        cuenta_id=cuenta.id,
        numero=cuenta.numero,
        titular=_titular_out(cuenta.socio),
        moneda=MonedaOut.model_validate(cuenta.moneda),
    )


def _validar_cuentas_transferencia(
    cuenta_origen: CuentaAhorro, cuenta_destino: CuentaAhorro
) -> None:
    if cuenta_origen.id == cuenta_destino.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta de origen y la de destino deben ser distintas",
        )
    if cuenta_origen.estado != "ACTIVA" or cuenta_destino.estado != "ACTIVA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ambas cuentas deben estar activas",
        )
    if cuenta_origen.moneda_id != cuenta_destino.moneda_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Las cuentas deben tener la misma moneda; no se realiza conversión",
        )


def _obtener_cuenta_por_id(
    db: Session, cuenta_id: int, cooperativa_id: int, *, bloquear: bool
) -> CuentaAhorro:
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(
            joinedload(CuentaAhorro.moneda),
            joinedload(CuentaAhorro.socio),
        )
        .where(
            CuentaAhorro.id == cuenta_id,
            Socio.cooperativa_id == cooperativa_id,
        )
    )
    if bloquear:
        query = query.with_for_update(of=CuentaAhorro)
    cuenta = db.execute(query).unique().scalar_one_or_none()
    if cuenta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cuenta no encontrada",
        )
    return cuenta


def _obtener_cuentas_por_id(
    db: Session,
    cooperativa_id: int,
    cuenta_origen_id: int,
    cuenta_destino_id: int,
) -> tuple[CuentaAhorro, CuentaAhorro]:
    cuentas: dict[int, CuentaAhorro] = {}
    for cuenta_id in sorted((cuenta_origen_id, cuenta_destino_id)):
        cuentas[cuenta_id] = _obtener_cuenta_por_id(
            db, cuenta_id, cooperativa_id, bloquear=True
        )
    return cuentas[cuenta_origen_id], cuentas[cuenta_destino_id]


def _obtener_cuenta_por_numero(
    db: Session, numero: str, cooperativa_id: int
) -> CuentaAhorro:
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(
            joinedload(CuentaAhorro.moneda),
            joinedload(CuentaAhorro.socio),
        )
        .where(
            CuentaAhorro.numero == numero,
            Socio.cooperativa_id == cooperativa_id,
        )
    )
    cuenta = db.execute(query).unique().scalar_one_or_none()
    if cuenta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cuenta no encontrada",
        )
    return cuenta


@router.get("/cajas", response_model=list[CajaOut])
def listar_cajas(
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    query = (
        select(Caja)
        .where(Caja.cooperativa_id == cooperativa_id)
        .order_by(Caja.id)
    )
    return list(db.execute(query).scalars())


@router.get("/sesion-actual", response_model=SesionCajaOut)
def obtener_sesion_actual(
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    _validar_operador(usuario)
    control = db.execute(
        select(ControlCaja)
        .options(joinedload(ControlCaja.caja))
        .where(
            ControlCaja.usuario_id == usuario.id,
            ControlCaja.estado == "ABIERTA",
        )
    ).unique().scalar_one_or_none()
    if control is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay una sesión de caja abierta",
        )
    return _sesion_out(control)


@router.post(
    "/aperturas",
    response_model=SesionCajaOut,
    status_code=status.HTTP_201_CREATED,
)
def abrir_caja(
    body: AperturaCajaCreate,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    if db.execute(
        select(ControlCaja.id).where(
            ControlCaja.usuario_id == usuario.id,
            ControlCaja.estado == "ABIERTA",
        )
    ).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario ya tiene una caja abierta",
        )

    caja = db.execute(
        select(Caja)
        .where(
            Caja.id == body.caja_id,
            Caja.cooperativa_id == cooperativa_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if caja is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Caja no encontrada",
        )
    if body.monto_apertura <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El monto de apertura debe ser mayor a cero",
        )
    if body.monto_apertura > caja.monto_maximo_efectivo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El monto de apertura supera el máximo permitido para la caja",
        )
    if db.execute(
        select(ControlCaja.id).where(
            ControlCaja.caja_id == caja.id,
            ControlCaja.estado == "ABIERTA",
        )
    ).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La caja ya está abierta por otro usuario",
        )

    caja.estado = "ABIERTA"
    control = ControlCaja(
        monto_apertura=body.monto_apertura,
        saldo_sistema=body.monto_apertura,
        fecha_apertura=datetime.now(),
        estado="ABIERTA",
        caja=caja,
        usuario_id=usuario.id,
    )
    db.add(control)
    registrar_accion(
        db,
        accion="APERTURA",
        modulo="CAJA",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Apertura de caja {caja.nombre} con saldo inicial {body.monto_apertura}",
        request=request,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario o la caja ya tiene una sesión abierta",
        )
    db.refresh(control)
    return _sesion_out(control)


@router.get("/cuentas/buscar", response_model=list[CuentaCajaOut])
def buscar_cuentas(
    q: str = Query(..., min_length=1),
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(
            joinedload(CuentaAhorro.moneda),
            joinedload(CuentaAhorro.socio),
        )
        .where(
            Socio.cooperativa_id == cooperativa_id,
            or_(CuentaAhorro.numero == q, Socio.ci == q),
        )
        .order_by(CuentaAhorro.id)
    )
    return [_cuenta_out(cuenta) for cuenta in db.execute(query).unique().scalars()]


def _sesion_abierta(
    db: Session, usuario: Usuario, *, bloquear: bool = False
) -> ControlCaja:
    query = (
        select(ControlCaja)
        .options(joinedload(ControlCaja.caja))
        .where(
            ControlCaja.usuario_id == usuario.id,
            ControlCaja.estado == "ABIERTA",
        )
    )
    if bloquear:
        query = query.with_for_update(of=ControlCaja)
    control = db.execute(query).unique().scalar_one_or_none()
    if control is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario no tiene una sesión de caja abierta",
        )
    return control


@router.post(
    "/depositos",
    response_model=DepositoVentanillaOut,
    status_code=status.HTTP_201_CREATED,
)
def registrar_deposito(
    body: DepositoVentanillaCreate,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    control = _sesion_abierta(db, usuario, bloquear=True)
    cuenta = db.execute(
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(
            joinedload(CuentaAhorro.moneda),
            joinedload(CuentaAhorro.socio),
        )
        .where(
            CuentaAhorro.id == body.cuenta_id,
            Socio.cooperativa_id == cooperativa_id,
        )
        .with_for_update(of=CuentaAhorro)
    ).unique().scalar_one_or_none()
    if cuenta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cuenta no encontrada",
        )
    if cuenta.estado != "ACTIVA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta no está activa",
        )
    if body.monto <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El monto debe ser mayor a cero",
        )

    cuenta.saldo_disponible += body.monto
    control.saldo_sistema += body.monto
    transaccion = db.execute(
        text("""
            INSERT INTO transaccion (
                tipo, monto, canal, control_caja_id, moneda_id,
                cuenta_ahorro_id, depositante_nombre, depositante_ci
            )
            VALUES (
                'DEPOSITO', :monto, 'VENTANILLA', :control_caja_id, :moneda_id,
                :cuenta_id, :depositante_nombre, :depositante_ci
            )
            RETURNING id, fecha_hora
        """),
        {
            "monto": body.monto,
            "control_caja_id": control.id,
            "moneda_id": cuenta.moneda_id,
            "cuenta_id": cuenta.id,
            "depositante_nombre": body.depositante_nombre,
            "depositante_ci": body.depositante_ci,
        },
    ).one()
    registrar_accion(
        db,
        accion="DEPOSITO",
        modulo="CAJA",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Depósito de {body.monto} en cuenta {cuenta.numero} por ventanilla",
        request=request,
    )
    db.commit()
    return DepositoVentanillaOut(
        numero_operacion=transaccion.id,
        fecha_hora=transaccion.fecha_hora,
        cuenta_numero=cuenta.numero,
        titular=_titular_out(cuenta.socio),
        monto=body.monto,
        moneda=MonedaOut.model_validate(cuenta.moneda),
        saldo_actualizado=cuenta.saldo_disponible,
        depositante_nombre=body.depositante_nombre,
        depositante_ci=body.depositante_ci,
        caja_nombre=control.caja.nombre,
    )


@router.get("/transferencias/preview", response_model=TransferenciaPreviewOut)
def previsualizar_transferencia(
    origen: str = Query(..., min_length=1),
    destino: str = Query(..., min_length=1),
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    _sesion_abierta(db, usuario)
    if origen == destino:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta de origen y la de destino deben ser distintas",
        )
    cuenta_origen = _obtener_cuenta_por_numero(db, origen, cooperativa_id)
    cuenta_destino = _obtener_cuenta_por_numero(db, destino, cooperativa_id)
    _validar_cuentas_transferencia(cuenta_origen, cuenta_destino)
    origen_out = CuentaOrigenTransferenciaPreviewOut(
        **_cuenta_transferencia_out(cuenta_origen).model_dump(),
        saldo_disponible=cuenta_origen.saldo_disponible,
    )
    return TransferenciaPreviewOut(
        origen=origen_out,
        destino=_cuenta_transferencia_out(cuenta_destino),
    )


@router.post(
    "/transferencias",
    response_model=TransferenciaVentanillaOut,
    status_code=status.HTTP_201_CREATED,
)
def transferir_en_ventanilla(
    body: TransferenciaVentanillaCreate,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    control = _sesion_abierta(db, usuario)
    if body.cuenta_origen_id == body.cuenta_destino_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta de origen y la de destino deben ser distintas",
        )
    cuenta_origen, cuenta_destino = _obtener_cuentas_por_id(
        db,
        cooperativa_id,
        body.cuenta_origen_id,
        body.cuenta_destino_id,
    )
    _validar_cuentas_transferencia(cuenta_origen, cuenta_destino)
    if body.monto <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El monto debe ser mayor a cero",
        )
    if body.monto > cuenta_origen.saldo_disponible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Saldo insuficiente",
        )

    cuenta_origen.saldo_disponible -= body.monto
    cuenta_destino.saldo_disponible += body.monto
    salida_id, _, fecha_hora = _registrar_transferencia(
        db,
        cuenta_origen=cuenta_origen,
        cuenta_destino=cuenta_destino,
        monto=body.monto,
        glosa=body.glosa,
        usuario=usuario,
        request=request,
        canal="VENTANILLA",
        control_caja_id=control.id,
        modulo="CAJA",
    )
    db.commit()
    return TransferenciaVentanillaOut(
        numero_operacion=salida_id,
        fecha_hora=fecha_hora,
        origen={
            "numero": cuenta_origen.numero,
            "titular": _titular_out(cuenta_origen.socio),
        },
        destino={
            "numero": cuenta_destino.numero,
            "titular": _titular_out(cuenta_destino.socio),
        },
        monto=body.monto,
        moneda=MonedaOut.model_validate(cuenta_origen.moneda),
        saldo_origen_actualizado=cuenta_origen.saldo_disponible,
        glosa=body.glosa,
    )
