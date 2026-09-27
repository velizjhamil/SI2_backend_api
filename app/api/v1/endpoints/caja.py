"""Operaciones de caja de ventanilla."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_db, require_operaciones
from app.api.v1.endpoints.ahorros import MONTO_MINIMO_APERTURA, _registrar_transferencia
from app.core.bitacora import registrar_accion
from app.core.security import verify_password
from app.services.uif import exigir_declaracion_si_corresponde, registrar_declaracion
from app.models.models import (
    ArqueoCaja,
    ArqueoCajaDetalle,
    ArqueoCajaMoneda,
    Caja,
    CierreCaja,
    CierreCajaMoneda,
    ControlCaja,
    CuentaAhorro,
    Moneda,
    Rol,
    Socio,
    Usuario,
)
from app.schemas.schemas import (
    AperturaCajaCreate,
    ArqueoCajaCreate,
    ArqueoCajaOut,
    ArqueoCierreVerificacionOut,
    CierreCajaCreate,
    CierreCajaOut,
    CierreVerificacionOut,
    DetalleArqueoOut,
    MonedaArqueoOut,
    MonedaCierreCajaOut,
    MonedaCierreVerificacionOut,
    CajaOut,
    CuentaOrigenTransferenciaPreviewOut,
    CuentaCajaOut,
    CuentaTransferenciaPreviewOut,
    DepositoVentanillaCreate,
    DepositoVentanillaOut,
    RetiroVentanillaCreate,
    RetiroVentanillaOut,
    RetiranteOut,
    MonedaOut,
    ArqueoResumenOut,
    MonedaArqueoResumenOut,
    DenominacionArqueoOut,
    SesionCajaOut,
    TitularCajaOut,
    UsuarioArqueoOut,
    TransferenciaPreviewOut,
    TransferenciaVentanillaCreate,
    TransferenciaVentanillaOut,
)

router = APIRouter()

DENOMINACIONES_ARQUEO = {
    "BOB": (
        ("BILLETE", Decimal("200.00")),
        ("BILLETE", Decimal("100.00")),
        ("BILLETE", Decimal("50.00")),
        ("BILLETE", Decimal("20.00")),
        ("BILLETE", Decimal("10.00")),
        ("MONEDA", Decimal("5.00")),
        ("MONEDA", Decimal("2.00")),
        ("MONEDA", Decimal("1.00")),
        ("MONEDA", Decimal("0.50")),
        ("MONEDA", Decimal("0.20")),
        ("MONEDA", Decimal("0.10")),
    ),
    "USD": (
        ("BILLETE", Decimal("100.00")),
        ("BILLETE", Decimal("50.00")),
        ("BILLETE", Decimal("20.00")),
        ("BILLETE", Decimal("10.00")),
        ("BILLETE", Decimal("5.00")),
        ("BILLETE", Decimal("1.00")),
    ),
}


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


@router.get("/arqueos/resumen", response_model=ArqueoResumenOut)
def obtener_resumen_arqueo(
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

    return _resumen_sesion_arqueo(db, control)


def _resumen_sesion_arqueo(
    db: Session, control: ControlCaja
) -> ArqueoResumenOut:
    """Return one session's theoretical cash balance, grouped by currency."""
    rows = db.execute(
        text("""
            SELECT moneda_id,
                   COALESCE(SUM(CASE WHEN tipo IN ('DEPOSITO', 'PAGO_CUOTA') THEN monto ELSE 0 END), 0) AS depositos,
                   COALESCE(SUM(CASE WHEN tipo = 'RETIRO' THEN monto ELSE 0 END), 0) AS retiros,
                   COUNT(*) AS cantidad
            FROM transaccion
            WHERE control_caja_id = :control_caja_id
              AND canal = 'VENTANILLA'
              AND tipo IN ('DEPOSITO', 'RETIRO', 'PAGO_CUOTA')
            GROUP BY moneda_id
        """),
        {"control_caja_id": control.id},
    ).all()
    movimientos = {
        moneda_id: (Decimal(depositos), Decimal(retiros), int(cantidad))
        for moneda_id, depositos, retiros, cantidad in rows
    }
    moneda_bob = db.execute(
        select(Moneda).where(Moneda.codigo_iso == "BOB")
    ).scalar_one_or_none()
    if moneda_bob is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se encontró la moneda BOB",
        )

    moneda_ids = set(movimientos)
    moneda_ids.add(moneda_bob.id)
    monedas = db.execute(
        select(Moneda).where(Moneda.id.in_(moneda_ids)).order_by(Moneda.id)
    ).scalars().all()
    resumen_monedas = []
    for moneda in monedas:
        depositos, retiros, cantidad = movimientos.get(
            moneda.id, (Decimal("0.00"), Decimal("0.00"), 0)
        )
        monto_apertura = (
            control.monto_apertura
            if moneda.codigo_iso == "BOB"
            else Decimal("0.00")
        )
        resumen_monedas.append(
            MonedaArqueoResumenOut(
                moneda=MonedaOut.model_validate(moneda),
                saldo_teorico=monto_apertura + depositos - retiros,
                monto_apertura=monto_apertura,
                total_depositos=depositos,
                total_retiros=retiros,
                cantidad_movimientos=cantidad,
                denominaciones=[
                    DenominacionArqueoOut(tipo=tipo, valor=valor)
                    for tipo, valor in DENOMINACIONES_ARQUEO.get(
                        moneda.codigo_iso, ()
                    )
                ],
            )
        )
    return ArqueoResumenOut(
        control_caja_id=control.id,
        caja_nombre=control.caja.nombre,
        fecha_apertura=control.fecha_apertura,
        umbral_diferencia=control.caja.umbral_diferencia_arqueo,
        monedas=resumen_monedas,
    )


@router.post(
    "/retiros",
    response_model=RetiroVentanillaOut,
    status_code=status.HTTP_201_CREATED,
)
def registrar_retiro(
    body: RetiroVentanillaCreate,
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

    saldo_minimo = MONTO_MINIMO_APERTURA[cuenta.tipo_producto]
    if cuenta.saldo_disponible - body.monto < saldo_minimo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Saldo insuficiente: debe mantener un saldo mínimo de "
                f"{saldo_minimo:.2f}"
            ),
        )
    if body.retirante.tipo == "TITULAR":
        if body.retirante.ci != cuenta.socio.ci:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El CI del titular no coincide con la cuenta",
            )
        retirante_nombre = body.retirante.nombre or (
            f"{cuenta.socio.nombre} {cuenta.socio.apellido}".strip()
        )
        retirante_ci = body.retirante.ci
    else:
        retirante_nombre = (body.retirante.nombre or "").strip()
        retirante_ci = (body.retirante.ci or "").strip()
        if not retirante_nombre or not retirante_ci:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El nombre y CI del apoderado son obligatorios",
            )

    fraccionada = exigir_declaracion_si_corresponde(
        db,
        declaracion=body.declaracion_uif,
        socio_id=cuenta.socio_id,
        moneda_id=cuenta.moneda_id,
        moneda_iso=cuenta.moneda.codigo_iso,
        monto=body.monto,
    )
    declaracion_id = registrar_declaracion(
        db,
        declaracion=body.declaracion_uif,
        tipo_operacion="RETIRO",
        monto=body.monto,
        moneda_id=cuenta.moneda_id,
        socio_id=cuenta.socio_id,
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        fraccionada=fraccionada,
    )

    resumen = _resumen_sesion_arqueo(db, control)
    efectivo = next(
        (
            moneda.saldo_teorico
            for moneda in resumen.monedas
            if moneda.moneda.id == cuenta.moneda_id
        ),
        Decimal("0.00"),
    )
    if body.monto > efectivo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Efectivo insuficiente en caja",
        )

    cuenta.saldo_disponible -= body.monto
    control.saldo_sistema -= body.monto
    transaccion = db.execute(
        text("""
            INSERT INTO transaccion (
                tipo, monto, canal, control_caja_id, moneda_id,
                cuenta_ahorro_id, retirante_tipo, retirante_nombre, retirante_ci,
                declaracion_jurada_uif_id
            )
            VALUES (
                'RETIRO', :monto, 'VENTANILLA', :control_caja_id, :moneda_id,
                :cuenta_id, :retirante_tipo, :retirante_nombre, :retirante_ci,
                :declaracion_id
            )
            RETURNING id, fecha_hora
        """),
        {
            "monto": body.monto,
            "control_caja_id": control.id,
            "moneda_id": cuenta.moneda_id,
            "cuenta_id": cuenta.id,
            "retirante_tipo": body.retirante.tipo,
            "retirante_nombre": retirante_nombre,
            "retirante_ci": retirante_ci,
            "declaracion_id": declaracion_id,
        },
    ).one()
    registrar_accion(
        db,
        accion="RETIRO",
        modulo="CAJA",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Retiro de {body.monto} en cuenta {cuenta.numero} por ventanilla",
        request=request,
    )
    db.commit()
    return RetiroVentanillaOut(
        numero_operacion=transaccion.id,
        fecha_hora=transaccion.fecha_hora,
        cuenta_numero=cuenta.numero,
        titular=_titular_out(cuenta.socio),
        monto=body.monto,
        moneda=MonedaOut.model_validate(cuenta.moneda),
        saldo_actualizado=cuenta.saldo_disponible,
        saldo_minimo=saldo_minimo,
        retirante=RetiranteOut(
            tipo=body.retirante.tipo,
            nombre=retirante_nombre,
            ci=retirante_ci,
        ),
        caja_nombre=control.caja.nombre,
        efectivo_caja_restante=efectivo - body.monto,
        declaracion_uif_id=declaracion_id,
    )


@router.post(
    "/arqueos",
    response_model=ArqueoCajaOut,
    status_code=status.HTTP_201_CREATED,
)
def registrar_arqueo(
    body: ArqueoCajaCreate,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    if body.cerrar_caja:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El cierre de caja se realiza desde el cierre de turno",
        )
    control = _sesion_abierta(db, usuario, bloquear=True)
    resumen = obtener_resumen_arqueo(usuario=usuario, db=db)
    esperadas = {moneda.moneda.id: moneda for moneda in resumen.monedas}

    ids_recibidos = [moneda.moneda_id for moneda in body.monedas]
    if len(ids_recibidos) != len(set(ids_recibidos)) or set(ids_recibidos) != set(esperadas):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe incluir exactamente todas las monedas del resumen",
        )

    diferencias = []
    for moneda_body in body.monedas:
        moneda_resumen = esperadas[moneda_body.moneda_id]
        codigo = moneda_resumen.moneda.codigo_iso
        definidas = set(DENOMINACIONES_ARQUEO.get(codigo, ()))
        detalle_ids = [
            (tipo, Decimal(detalle.denominacion))
            for detalle in moneda_body.detalle
            for tipo, valor in definidas
            if valor == Decimal(detalle.denominacion)
        ]
        if any(detalle.cantidad < 0 for detalle in moneda_body.detalle):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La cantidad de cada denominación no puede ser negativa",
            )
        if len(detalle_ids) != len(moneda_body.detalle):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Denominación no válida para {codigo}",
            )
        total_contado = sum(
            (
                Decimal(detalle.denominacion) * detalle.cantidad
                for detalle in moneda_body.detalle
            ),
            Decimal("0.00"),
        )
        diferencia = total_contado - moneda_resumen.saldo_teorico
        resultado = (
            "CUADRADO"
            if diferencia == 0
            else "SOBRANTE"
            if diferencia > 0
            else "FALTANTE"
        )
        diferencias.append(
            (moneda_body, moneda_resumen, total_contado, diferencia, resultado)
        )

    requiere_supervisor = any(
        abs(diferencia) > control.caja.umbral_diferencia_arqueo
        for _, _, _, diferencia, _ in diferencias
    )
    if requiere_supervisor and body.supervisor is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diferencia significativa: se requiere autorización del supervisor",
        )

    supervisor = None
    if body.supervisor is not None:
        supervisor = db.execute(
            select(Usuario)
            .join(Rol, Usuario.rol_id == Rol.id)
            .where(
                Usuario.correo == body.supervisor.correo,
                Usuario.estado == "ACTIVO",
                Usuario.cooperativa_id == cooperativa_id,
                Usuario.id != usuario.id,
                Rol.nombre == "ADMINISTRADOR",
            )
        ).scalar_one_or_none()
        if supervisor is None or not verify_password(
            body.supervisor.contrasena, supervisor.contrasena
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Las credenciales del supervisor no son válidas",
            )

    arqueo = ArqueoCaja(
        control_caja_id=control.id,
        usuario_id=usuario.id,
        supervisor_id=supervisor.id if supervisor else None,
        fecha_autorizacion=datetime.now().astimezone() if supervisor else None,
        cierre=body.cerrar_caja,
        requiere_supervisor=requiere_supervisor,
        observacion=body.observacion,
    )
    db.add(arqueo)
    db.flush()
    monedas_out = []
    for moneda_body, resumen_moneda, total, diferencia, resultado in diferencias:
        arqueo_moneda = ArqueoCajaMoneda(
            arqueo_id=arqueo.id,
            moneda_id=moneda_body.moneda_id,
            saldo_teorico=resumen_moneda.saldo_teorico,
            total_contado=total,
            diferencia=diferencia,
            resultado=resultado,
        )
        db.add(arqueo_moneda)
        db.flush()
        detalle_out = []
        for detalle in moneda_body.detalle:
            tipo = next(
                tipo
                for tipo, valor in DENOMINACIONES_ARQUEO[resumen_moneda.moneda.codigo_iso]
                if valor == Decimal(detalle.denominacion)
            )
            subtotal = Decimal(detalle.denominacion) * detalle.cantidad
            db.add(
                ArqueoCajaDetalle(
                    arqueo_moneda_id=arqueo_moneda.id,
                    tipo=tipo,
                    denominacion=detalle.denominacion,
                    cantidad=detalle.cantidad,
                    subtotal=subtotal,
                )
            )
            detalle_out.append(
                DetalleArqueoOut(
                    tipo=tipo,
                    denominacion=detalle.denominacion,
                    cantidad=detalle.cantidad,
                    subtotal=subtotal,
                )
            )
        monedas_out.append(
            MonedaArqueoOut(
                moneda=resumen_moneda.moneda,
                saldo_teorico=resumen_moneda.saldo_teorico,
                total_contado=total,
                diferencia=diferencia,
                resultado=resultado,
                detalle=detalle_out,
            )
        )

    nombre_usuario = usuario.nombre
    supervisor_out = (
        UsuarioArqueoOut(id=supervisor.id, nombre=supervisor.nombre)
        if supervisor
        else None
    )
    fecha = arqueo.fecha or datetime.now().astimezone()
    response = ArqueoCajaOut(
        id=arqueo.id,
        fecha=fecha,
        caja_nombre=control.caja.nombre,
        cajero=UsuarioArqueoOut(id=usuario.id, nombre=nombre_usuario),
        supervisor=supervisor_out,
        fecha_autorizacion=arqueo.fecha_autorizacion,
        requiere_supervisor=requiere_supervisor,
        cierre=body.cerrar_caja,
        observacion=body.observacion,
        monedas=monedas_out,
    )
    registrar_accion(
        db,
        accion="ARQUEO",
        modulo="CAJA",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Arqueo de caja {control.caja.nombre}",
        request=request,
    )
    db.commit()
    return response


@router.get("/arqueos/{arqueo_id}", response_model=ArqueoCajaOut)
def obtener_arqueo(
    arqueo_id: int,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    arqueo = db.execute(
        select(ArqueoCaja)
        .join(ControlCaja, ArqueoCaja.control_caja_id == ControlCaja.id)
        .join(Caja, ControlCaja.caja_id == Caja.id)
        .options(
            joinedload(ArqueoCaja.control_caja).joinedload(ControlCaja.caja),
            joinedload(ArqueoCaja.usuario),
            joinedload(ArqueoCaja.supervisor),
            selectinload(ArqueoCaja.monedas).joinedload(ArqueoCajaMoneda.moneda),
            selectinload(ArqueoCaja.monedas)
            .selectinload(ArqueoCajaMoneda.detalle),
        )
        .where(
            ArqueoCaja.id == arqueo_id,
            Caja.cooperativa_id == cooperativa_id,
        )
    ).unique().scalar_one_or_none()
    if arqueo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arqueo no encontrado",
        )

    return ArqueoCajaOut(
        id=arqueo.id,
        fecha=arqueo.fecha,
        caja_nombre=arqueo.control_caja.caja.nombre,
        cajero=UsuarioArqueoOut(
            id=arqueo.usuario.id,
            nombre=arqueo.usuario.nombre,
        ),
        supervisor=(
            UsuarioArqueoOut(
                id=arqueo.supervisor.id,
                nombre=arqueo.supervisor.nombre,
            )
            if arqueo.supervisor
            else None
        ),
        fecha_autorizacion=arqueo.fecha_autorizacion,
        requiere_supervisor=arqueo.requiere_supervisor,
        cierre=arqueo.cierre,
        observacion=arqueo.observacion,
        monedas=[
            MonedaArqueoOut(
                moneda=MonedaOut.model_validate(moneda_arqueo.moneda),
                saldo_teorico=moneda_arqueo.saldo_teorico,
                total_contado=moneda_arqueo.total_contado,
                diferencia=moneda_arqueo.diferencia,
                resultado=moneda_arqueo.resultado,
                detalle=[
                    DetalleArqueoOut(
                        tipo=detalle.tipo,
                        denominacion=detalle.denominacion,
                        cantidad=detalle.cantidad,
                        subtotal=detalle.subtotal,
                    )
                    for detalle in moneda_arqueo.detalle
                ],
            )
            for moneda_arqueo in arqueo.monedas
        ],
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


def _verificar_cierre(db: Session, control: ControlCaja):
    arqueo = db.execute(
        select(ArqueoCaja)
        .options(
            joinedload(ArqueoCaja.supervisor),
            selectinload(ArqueoCaja.monedas).joinedload(ArqueoCajaMoneda.moneda),
        )
        .where(ArqueoCaja.control_caja_id == control.id)
        .order_by(ArqueoCaja.fecha.desc(), ArqueoCaja.id.desc())
        .limit(1)
    ).unique().scalar_one_or_none()
    if arqueo is None:
        return CierreVerificacionOut(
            puede_cerrar=False,
            alertas=["No hay un arqueo registrado para esta sesión"],
            arqueo=None,
        ), None

    alertas = []
    tiene_diferencia_no_autorizada = any(
        moneda.resultado != "CUADRADO" for moneda in arqueo.monedas
    ) and arqueo.supervisor_id is None
    if tiene_diferencia_no_autorizada:
        alertas.append(
            "El último arqueo tiene diferencias sin autorización del supervisor"
        )
    hay_movimiento_posterior = db.execute(
        text("""
            SELECT EXISTS (
                SELECT 1
                FROM transaccion
                WHERE control_caja_id = :control_caja_id
                  AND canal = 'VENTANILLA'
                  AND fecha_hora > :fecha_arqueo
            )
        """),
        {"control_caja_id": control.id, "fecha_arqueo": arqueo.fecha},
    ).scalar_one()
    if hay_movimiento_posterior:
        alertas.append("Hay transacciones posteriores al último arqueo")

    response = CierreVerificacionOut(
        puede_cerrar=not alertas,
        alertas=alertas,
        arqueo=ArqueoCierreVerificacionOut(
            id=arqueo.id,
            fecha=arqueo.fecha,
            requiere_supervisor=arqueo.requiere_supervisor,
            supervisor=(
                UsuarioArqueoOut(id=arqueo.supervisor.id, nombre=arqueo.supervisor.nombre)
                if arqueo.supervisor
                else None
            ),
            monedas=[
                MonedaCierreVerificacionOut(
                    moneda=MonedaOut.model_validate(item.moneda),
                    resultado=item.resultado,
                    diferencia=item.diferencia,
                )
                for item in arqueo.monedas
            ],
        ),
    )
    return response, arqueo


@router.get("/cierres/verificacion", response_model=CierreVerificacionOut)
def verificar_cierre_turno(
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
    response, _ = _verificar_cierre(db, control)
    return response


@router.post(
    "/cierres",
    response_model=CierreCajaOut,
    status_code=status.HTTP_201_CREATED,
)
def cerrar_turno(
    body: CierreCajaCreate,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    control = _sesion_abierta(db, usuario, bloquear=True)
    verificacion, arqueo = _verificar_cierre(db, control)
    if not verificacion.puede_cerrar:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=verificacion.alertas[0],
        )
    assert arqueo is not None

    resumen = _resumen_sesion_arqueo(db, control)
    movimientos = db.execute(
        text("""
            SELECT moneda_id,
                   COUNT(*) FILTER (WHERE tipo = 'DEPOSITO') AS cantidad_depositos,
                   COUNT(*) FILTER (WHERE tipo = 'RETIRO') AS cantidad_retiros,
                   COUNT(*) FILTER (WHERE tipo = 'TRANSFERENCIA_SALIDA') AS cantidad_transferencias
            FROM transaccion
            WHERE control_caja_id = :control_caja_id
              AND canal = 'VENTANILLA'
            GROUP BY moneda_id
        """),
        {"control_caja_id": control.id},
    ).all()
    conteos = {
        moneda_id: (int(depositos), int(retiros), int(transferencias))
        for moneda_id, depositos, retiros, transferencias in movimientos
    }
    contados = {item.moneda_id: item for item in arqueo.monedas}

    cierre = CierreCaja(
        control_caja_id=control.id,
        arqueo_id=arqueo.id,
        usuario_id=usuario.id,
        observacion=body.observacion,
    )
    db.add(cierre)
    db.flush()
    monedas_out = []
    for resumen_moneda in resumen.monedas:
        arqueo_moneda = contados[resumen_moneda.moneda.id]
        cantidades = conteos.get(resumen_moneda.moneda.id, (0, 0, 0))
        values = {
            "cierre_id": cierre.id,
            "moneda_id": resumen_moneda.moneda.id,
            "monto_apertura": resumen_moneda.monto_apertura,
            "total_depositos": resumen_moneda.total_depositos,
            "cantidad_depositos": cantidades[0],
            "total_retiros": resumen_moneda.total_retiros,
            "cantidad_retiros": cantidades[1],
            "cantidad_transferencias": cantidades[2],
            "saldo_teorico": resumen_moneda.saldo_teorico,
            "total_contado": arqueo_moneda.total_contado,
            "diferencia": arqueo_moneda.diferencia,
            "traspaso_boveda": arqueo_moneda.total_contado,
        }
        db.add(CierreCajaMoneda(**values))
        monedas_out.append(
            MonedaCierreCajaOut(
                moneda=resumen_moneda.moneda,
                monto_apertura=values["monto_apertura"],
                total_depositos=values["total_depositos"],
                cantidad_depositos=values["cantidad_depositos"],
                total_retiros=values["total_retiros"],
                cantidad_retiros=values["cantidad_retiros"],
                cantidad_transferencias=values["cantidad_transferencias"],
                saldo_teorico=values["saldo_teorico"],
                total_contado=values["total_contado"],
                diferencia=values["diferencia"],
                traspaso_boveda=values["traspaso_boveda"],
            )
        )

    fecha_cierre = datetime.now()
    control.estado = "CERRADA"
    control.fecha_cierre = fecha_cierre
    control.monto_cierre = contados[
        next(item.moneda.id for item in resumen.monedas if item.moneda.codigo_iso == "BOB")
    ].total_contado
    control.caja.estado = "CERRADA"
    registrar_accion(
        db,
        accion="CIERRE",
        modulo="CAJA",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Cierre de turno de caja {control.caja.nombre}",
        request=request,
    )
    response = CierreCajaOut(
        id=cierre.id,
        fecha=cierre.fecha,
        caja_nombre=control.caja.nombre,
        cajero=UsuarioArqueoOut(id=usuario.id, nombre=usuario.nombre),
        fecha_apertura=control.fecha_apertura,
        fecha_cierre=fecha_cierre,
        arqueo_id=arqueo.id,
        observacion=body.observacion,
        monedas=monedas_out,
    )
    db.commit()
    return response


@router.get("/cierres/{cierre_id}", response_model=CierreCajaOut)
def obtener_cierre_turno(
    cierre_id: int,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_operador(usuario)
    cierre = db.execute(
        select(CierreCaja)
        .join(ControlCaja, CierreCaja.control_caja_id == ControlCaja.id)
        .join(Caja, ControlCaja.caja_id == Caja.id)
        .options(
            joinedload(CierreCaja.control_caja).joinedload(ControlCaja.caja),
            joinedload(CierreCaja.usuario),
            selectinload(CierreCaja.monedas).joinedload(CierreCajaMoneda.moneda),
        )
        .where(
            CierreCaja.id == cierre_id,
            Caja.cooperativa_id == cooperativa_id,
        )
    ).unique().scalar_one_or_none()
    if cierre is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cierre no encontrado",
        )
    return CierreCajaOut(
        id=cierre.id,
        fecha=cierre.fecha,
        caja_nombre=cierre.control_caja.caja.nombre,
        cajero=UsuarioArqueoOut(
            id=cierre.usuario.id,
            nombre=cierre.usuario.nombre,
        ),
        fecha_apertura=cierre.control_caja.fecha_apertura,
        fecha_cierre=cierre.control_caja.fecha_cierre,
        arqueo_id=cierre.arqueo_id,
        observacion=cierre.observacion,
        monedas=[
            MonedaCierreCajaOut(
                moneda=MonedaOut.model_validate(item.moneda),
                monto_apertura=item.monto_apertura,
                total_depositos=item.total_depositos,
                cantidad_depositos=item.cantidad_depositos,
                total_retiros=item.total_retiros,
                cantidad_retiros=item.cantidad_retiros,
                cantidad_transferencias=item.cantidad_transferencias,
                saldo_teorico=item.saldo_teorico,
                total_contado=item.total_contado,
                diferencia=item.diferencia,
                traspaso_boveda=item.traspaso_boveda,
            )
            for item in cierre.monedas
        ],
    )


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

    fraccionada = exigir_declaracion_si_corresponde(
        db,
        declaracion=body.declaracion_uif,
        socio_id=cuenta.socio_id,
        moneda_id=cuenta.moneda_id,
        moneda_iso=cuenta.moneda.codigo_iso,
        monto=body.monto,
    )
    declaracion_id = registrar_declaracion(
        db,
        declaracion=body.declaracion_uif,
        tipo_operacion="DEPOSITO",
        monto=body.monto,
        moneda_id=cuenta.moneda_id,
        socio_id=cuenta.socio_id,
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        fraccionada=fraccionada,
    )

    cuenta.saldo_disponible += body.monto
    control.saldo_sistema += body.monto
    transaccion = db.execute(
        text("""
            INSERT INTO transaccion (
                tipo, monto, canal, control_caja_id, moneda_id,
                cuenta_ahorro_id, depositante_nombre, depositante_ci,
                declaracion_jurada_uif_id
            )
            VALUES (
                'DEPOSITO', :monto, 'VENTANILLA', :control_caja_id, :moneda_id,
                :cuenta_id, :depositante_nombre, :depositante_ci, :declaracion_id
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
            "declaracion_id": declaracion_id,
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
        declaracion_uif_id=declaracion_id,
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
    control = _sesion_abierta(db, usuario, bloquear=True)
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
