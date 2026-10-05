"""Fixed-term deposit API (CU-W17 and CU-W19)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.api.v1.endpoints.ahorros import MONTO_MINIMO_APERTURA, _siguiente_secuencia
from app.api.v1.endpoints.caja import _resumen_sesion_arqueo, _sesion_abierta
from app.api.v1.deps import get_current_user, get_db, require_operaciones
from app.core.bitacora import registrar_accion
from app.models.models import Cooperativa, CuentaAhorro, DepositoPlazoFijo, DPFCronograma, Liquidacion, Moneda, Socio, TasaDPF, Usuario
from app.schemas.schemas import DPFCreate, DPFInteresesProcesarIn, DPFLiquidacionIn, DPFSimulacionIn, MonedaOut
from app.services.uif import exigir_declaracion_si_corresponde, registrar_declaracion, requiere_declaracion, validar_declaracion

router = APIRouter()
CENT = Decimal("0.01")
TAX_RATE = Decimal("0.13")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _nullable_money(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _obtener_tasa(db: Session, usuario: Usuario, moneda_id: int, plazo_dias: int):
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    return db.execute(
        select(TasaDPF, Moneda)
        .join(Moneda, Moneda.id == TasaDPF.moneda_id)
        .where(
            TasaDPF.cooperativa_id == usuario.cooperativa_id,
            TasaDPF.moneda_id == moneda_id,
            TasaDPF.plazo_min_dias <= plazo_dias,
            (TasaDPF.plazo_max_dias.is_(None) | (TasaDPF.plazo_max_dias >= plazo_dias)),
        )
        .order_by(TasaDPF.plazo_min_dias.desc())
    ).first()


def _calcular_simulacion(db: Session, usuario: Usuario, monto: Decimal, moneda_id: int, plazo_dias: int, modalidad: str):
    if monto <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero")
    if plazo_dias < 30:
        raise HTTPException(status_code=400, detail="El plazo mínimo para un DPF es 30 días")
    result = _obtener_tasa(db, usuario, moneda_id, plazo_dias)
    if result is None:
        raise HTTPException(status_code=400, detail="No existe una tasa para el plazo y moneda seleccionados")
    rate, moneda = result
    start = date.today()
    maturity = start + timedelta(days=plazo_dias)
    gross_total = _money(monto * rate.tna * Decimal(plazo_dias) / Decimal("36000"))
    exempt = moneda.codigo_iso == "BOB" and plazo_dias >= 30
    tax_total = Decimal("0.00") if exempt else _money(gross_total * TAX_RATE)

    periods = [plazo_dias] if modalidad == "VENCIMIENTO" else [30] * (plazo_dias // 30)
    if modalidad == "MENSUAL":
        remainder = plazo_dias % 30
        if remainder:
            periods.append(remainder)
    gross_parts = []
    for days in periods[:-1]:
        gross_parts.append(_money(monto * rate.tna * Decimal(days) / Decimal("36000")))
    gross_parts.append(gross_total - sum(gross_parts, Decimal("0.00")))
    tax_parts = []
    for part in gross_parts[:-1]:
        tax_parts.append(Decimal("0.00") if exempt else _money(part * TAX_RATE))
    tax_parts.append(tax_total - sum(tax_parts, Decimal("0.00")))
    schedule = []
    cumulative = 0
    for index, (days, gross, tax) in enumerate(zip(periods, gross_parts, tax_parts), start=1):
        cumulative += days
        schedule.append({
            "numero": index,
            "fecha_pago": start + timedelta(days=cumulative),
            "dias": days,
            "interes_bruto": f"{gross:.2f}",
            "retencion_rciva": f"{tax:.2f}",
            "interes_neto": f"{gross - tax:.2f}",
        })
    return {
        "monto": f"{monto:.2f}",
        "moneda": {"id": moneda.id, "codigo_iso": moneda.codigo_iso, "nombre": moneda.nombre, "simbolo": moneda.simbolo},
        "plazo_dias": plazo_dias,
        "tna": f"{rate.tna:.2f}",
        "fecha_inicio": start,
        "fecha_vencimiento": maturity,
        "interes_bruto": f"{gross_total:.2f}",
        "exento_rciva": exempt,
        "retencion_rciva": f"{tax_total:.2f}",
        "interes_neto": f"{gross_total - tax_total:.2f}",
        "total_a_recibir": f"{monto + gross_total - tax_total:.2f}",
        "cronograma": schedule,
        "rate_value": rate.tna,
        "gross_value": gross_total,
        "tax_value": tax_total,
        "net_value": gross_total - tax_total,
    }


@router.get("/tarifario")
def obtener_tarifario(usuario: Usuario = Depends(require_operaciones), db: Session = Depends(get_db)):
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    rows = db.execute(
        select(TasaDPF, Moneda)
        .join(Moneda, Moneda.id == TasaDPF.moneda_id)
        .where(TasaDPF.cooperativa_id == usuario.cooperativa_id)
        .order_by(Moneda.codigo_iso, TasaDPF.plazo_min_dias)
    ).all()
    return [{
        "moneda": {"id": moneda.id, "codigo_iso": moneda.codigo_iso, "nombre": moneda.nombre, "simbolo": moneda.simbolo},
        "plazo_min_dias": tasa.plazo_min_dias,
        "plazo_max_dias": tasa.plazo_max_dias,
        "tna": f"{tasa.tna:.2f}",
    } for tasa, moneda in rows]


@router.post("/simulacion")
def simular_dpf(body: DPFSimulacionIn, usuario: Usuario = Depends(require_operaciones), db: Session = Depends(get_db)):
    try:
        result = _calcular_simulacion(db, usuario, body.monto, body.moneda_id, body.plazo_dias, body.modalidad_pago_interes)
    except HTTPException:
        raise
    for key in ("rate_value", "gross_value", "tax_value", "net_value"):
        result.pop(key, None)
    return result


def _certificado_out(db: Session, dpf: DepositoPlazoFijo):
    socio = db.get(Socio, dpf.socio_id)
    moneda = db.get(Moneda, dpf.moneda_id)
    abono = db.get(CuentaAhorro, dpf.cuenta_abono_id)
    origen = db.get(CuentaAhorro, dpf.cuenta_origen_id) if dpf.cuenta_origen_id else None
    actor = db.get(Usuario, dpf.usuario_id)
    cronograma = db.execute(
        select(DPFCronograma).where(DPFCronograma.deposito_plazo_fijo_id == dpf.id).order_by(DPFCronograma.numero)
    ).scalars().all()
    return {
        "id": dpf.id,
        "numero_certificado": dpf.numero_certificado,
        "codigo_verificacion": dpf.codigo_verificacion,
        "socio": {"id": socio.id, "nombre_completo": f"{socio.nombre} {socio.apellido}".strip(), "ci": socio.ci},
        "monto": f"{dpf.monto:.2f}",
        "moneda": MonedaOut.model_validate(moneda).model_dump(),
        "tna": f"{dpf.tasa_interes_anual:.2f}",
        "plazo_dias": dpf.plazo_dias,
        "modalidad_pago_interes": dpf.modalidad_pago_interes,
        "fecha_emision": dpf.fecha_emision,
        "fecha_inicio": dpf.fecha_inicio,
        "fecha_vencimiento": dpf.fecha_vencimiento,
        "interes_bruto": f"{dpf.interes_bruto:.2f}",
        "exento_rciva": moneda.codigo_iso == "BOB" and dpf.plazo_dias >= 30,
        "retencion_rciva": f"{dpf.retencion_rciva:.2f}",
        "interes_neto": _nullable_money(dpf.interes_neto),
        "origen_fondos": dpf.origen_fondos,
        "cuenta_origen": {"id": origen.id, "numero": origen.numero} if origen else None,
        "cuenta_abono": {"id": abono.id, "numero": abono.numero},
        "estado": dpf.estado,
        "cronograma": [
            {"numero": item.numero, "fecha_pago": item.fecha_pago, "dias": item.dias,
             "interes_bruto": f"{item.interes_bruto:.2f}", "retencion_rciva": f"{item.retencion_rciva:.2f}",
             "interes_neto": f"{item.interes_neto:.2f}", "estado": item.estado, "fecha_pago_real": item.fecha_pago_real}
            for item in cronograma
        ],
        "declaracion_uif_id": dpf.declaracion_jurada_uif_id,
        "dpf_origen_id": dpf.dpf_origen_id,
        "usuario": {"id": actor.id, "nombre": actor.nombre} if actor else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def emitir_dpf(
    body: DPFCreate,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    socio = db.execute(
        select(Socio).where(Socio.id == body.socio_id, Socio.cooperativa_id == usuario.cooperativa_id).with_for_update()
    ).scalar_one_or_none()
    if socio is None:
        raise HTTPException(status_code=404, detail="Socio no encontrado")
    if socio.estado != "ACTIVO":
        raise HTTPException(status_code=400, detail="El socio no está activo")
    moneda = db.get(Moneda, body.moneda_id)
    if moneda is None:
        raise HTTPException(status_code=400, detail="Moneda no encontrada")
    simulation = _calcular_simulacion(db, usuario, body.monto, body.moneda_id, body.plazo_dias, body.modalidad_pago_interes)
    minimum_source = MONTO_MINIMO_APERTURA["VISTA"]
    if body.origen_fondos == "CUENTA":
        if body.cuenta_origen_id is None:
            raise HTTPException(status_code=400, detail="La cuenta de origen es obligatoria")
        account_ids = [body.cuenta_origen_id, body.cuenta_abono_id]
        accounts = db.execute(
            select(CuentaAhorro).where(CuentaAhorro.id.in_(account_ids)).with_for_update()
        ).scalars().all()
        accounts_by_id = {account.id: account for account in accounts}
        source = accounts_by_id.get(body.cuenta_origen_id)
        abono = accounts_by_id.get(body.cuenta_abono_id)
        for account in (source, abono):
            if account is None or account.socio_id != socio.id or account.moneda_id != moneda.id or account.estado != "ACTIVA":
                raise HTTPException(status_code=400, detail="Las cuentas deben pertenecer al socio, moneda y estar activas")
        minimum_source = MONTO_MINIMO_APERTURA[source.tipo_producto]
        minimum_abono = MONTO_MINIMO_APERTURA[abono.tipo_producto]
        if source.id == abono.id:
            if source.saldo_disponible - body.monto < minimum_source:
                raise HTTPException(status_code=400, detail="Saldo insuficiente para mantener el saldo mínimo")
        elif abono.saldo_disponible < minimum_abono:
            raise HTTPException(status_code=400, detail="La cuenta de abono debe mantener su saldo mínimo")
        if source.saldo_disponible - body.monto < minimum_source:
            raise HTTPException(status_code=400, detail="Saldo insuficiente para mantener el saldo mínimo")
        control = None
    else:
        abono = db.execute(
            select(CuentaAhorro).where(CuentaAhorro.id == body.cuenta_abono_id).with_for_update()
        ).scalar_one_or_none()
        if abono is None or abono.socio_id != socio.id or abono.moneda_id != moneda.id or abono.estado != "ACTIVA":
            raise HTTPException(status_code=400, detail="La cuenta de abono debe pertenecer al socio y moneda y estar activa")
        if abono.saldo_disponible < MONTO_MINIMO_APERTURA[abono.tipo_producto]:
            raise HTTPException(status_code=400, detail="La cuenta de abono debe mantener su saldo mínimo")
        control = _sesion_abierta(db, usuario, bloquear=True)
        from app.services.uif import umbral_para
        threshold = umbral_para(moneda.codigo_iso)
        accumulated, fraction = requiere_declaracion(db, socio_id=socio.id, moneda_id=moneda.id, moneda_iso=moneda.codigo_iso, monto=body.monto)
        required = (threshold is not None and body.monto >= threshold) or accumulated
        if required and body.declaracion_uif is None:
            raise HTTPException(status_code=428, detail="Se requiere declaración jurada UIF para esta operación")
        validar_declaracion(body.declaracion_uif)
        fraction = fraction
    if body.origen_fondos == "CUENTA":
        threshold = None
        accumulated = False
        fraction = False
        from app.services.uif import umbral_para
        threshold = umbral_para(moneda.codigo_iso)
        required = threshold is not None and body.monto >= threshold
        if required and body.declaracion_uif is None:
            raise HTTPException(status_code=428, detail="Se requiere declaración jurada UIF para esta operación")
        validar_declaracion(body.declaracion_uif)

    declaration_id = registrar_declaracion(
        db, declaracion=body.declaracion_uif, tipo_operacion="DPF", monto=body.monto,
        moneda_id=moneda.id, socio_id=socio.id, usuario_id=usuario.id,
        cooperativa_id=usuario.cooperativa_id, fraccionada=fraction,
    )
    numero = f"DPF-{_siguiente_secuencia(db, usuario.cooperativa_id, 'DPF'):06d}"
    verification_text = "|".join((numero, socio.ci, f"{body.monto:.2f}", moneda.codigo_iso,
                                   simulation["fecha_inicio"].isoformat(), simulation["fecha_vencimiento"].isoformat(),
                                   f"{simulation['rate_value']:.2f}"))
    codigo = hashlib.sha256(verification_text.encode("utf-8")).hexdigest()[:16]
    dpf = DepositoPlazoFijo(
        monto=body.monto, tasa_interes_anual=simulation["rate_value"], plazo_dias=body.plazo_dias,
        fecha_inicio=simulation["fecha_inicio"], fecha_vencimiento=simulation["fecha_vencimiento"],
        interes_calculado=simulation["gross_value"], estado="VIGENTE", socio_id=socio.id,
        moneda_id=moneda.id, numero_certificado=numero, modalidad_pago_interes=body.modalidad_pago_interes,
        interes_bruto=simulation["gross_value"], retencion_rciva=simulation["tax_value"],
        interes_neto=simulation["net_value"], origen_fondos=body.origen_fondos,
        cuenta_origen_id=body.cuenta_origen_id if body.origen_fondos == "CUENTA" else None,
        cuenta_abono_id=abono.id, codigo_verificacion=codigo, declaracion_jurada_uif_id=declaration_id,
        usuario_id=usuario.id, cooperativa_id=usuario.cooperativa_id,
    )
    db.add(dpf)
    db.flush()
    for installment in simulation["cronograma"]:
        db.add(DPFCronograma(
            deposito_plazo_fijo_id=dpf.id, numero=installment["numero"], fecha_pago=installment["fecha_pago"],
            dias=installment["dias"], interes_bruto=Decimal(installment["interes_bruto"]),
            retencion_rciva=Decimal(installment["retencion_rciva"]), interes_neto=Decimal(installment["interes_neto"]),
            estado="PENDIENTE",
        ))
    if body.origen_fondos == "CUENTA":
        source.saldo_disponible -= body.monto
        tx_type, channel, control_id, cuenta_id = "RETIRO", "WEB", None, source.id
    else:
        if moneda.codigo_iso == "BOB":
            control.saldo_sistema += body.monto
        tx_type, channel, control_id, cuenta_id = "DEPOSITO", "VENTANILLA", control.id, None
    db.execute(text("""
        INSERT INTO transaccion (tipo, monto, canal, control_caja_id, moneda_id, cuenta_ahorro_id,
                                 deposito_plazo_fijo_id, declaracion_jurada_uif_id)
        VALUES (:tipo, :monto, :canal, :control_id, :moneda_id, :cuenta_id, :dpf_id, :declaration_id)
    """), {"tipo": tx_type, "monto": body.monto, "canal": channel, "control_id": control_id,
           "moneda_id": moneda.id, "cuenta_id": cuenta_id, "dpf_id": dpf.id, "declaration_id": declaration_id})
    registrar_accion(db, accion="EMISION", modulo="DPF", usuario_id=usuario.id,
                     cooperativa_id=usuario.cooperativa_id,
                     descripcion=f"Emisión DPF {numero} para socio {socio.ci}", request=request)
    db.commit()
    return _certificado_out(db, dpf)


@router.get("")
def listar_dpf(
    estado: str | None = Query(None),
    vence_en_dias: int | None = Query(None),
    socio_ci: str | None = Query(None),
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    query = (
        select(DepositoPlazoFijo, Socio, Moneda)
        .join(Socio, Socio.id == DepositoPlazoFijo.socio_id)
        .join(Moneda, Moneda.id == DepositoPlazoFijo.moneda_id)
        .where(DepositoPlazoFijo.cooperativa_id == usuario.cooperativa_id)
    )
    if vence_en_dias is not None:
        query = query.where(
            DepositoPlazoFijo.estado == "VIGENTE",
            DepositoPlazoFijo.fecha_vencimiento <= date.today() + timedelta(days=vence_en_dias),
        )
    elif estado is not None:
        query = query.where(DepositoPlazoFijo.estado == estado)
    if socio_ci:
        query = query.where(Socio.ci == socio_ci)
    rows = db.execute(query.order_by(DepositoPlazoFijo.fecha_vencimiento, DepositoPlazoFijo.id)).all()
    return [{
        "id": dpf.id, "numero_certificado": dpf.numero_certificado,
        "socio": {"id": socio.id, "nombre_completo": f"{socio.nombre} {socio.apellido}".strip(), "ci": socio.ci},
        "monto": f"{dpf.monto:.2f}",
        "moneda": {"id": moneda.id, "codigo_iso": moneda.codigo_iso, "nombre": moneda.nombre, "simbolo": moneda.simbolo},
        "tna": f"{dpf.tasa_interes_anual:.2f}", "plazo_dias": dpf.plazo_dias,
        "fecha_inicio": dpf.fecha_inicio, "fecha_vencimiento": dpf.fecha_vencimiento,
        "interes_neto": _nullable_money(dpf.interes_neto), "estado": dpf.estado,
        "dias_para_vencer": (dpf.fecha_vencimiento - date.today()).days,
    } for dpf, socio, moneda in rows]



def _pago_interes_out(db: Session, row: DPFCronograma, dpf: DepositoPlazoFijo):
    socio = db.get(Socio, dpf.socio_id)
    cuenta = db.get(CuentaAhorro, dpf.cuenta_abono_id)
    return {
        "cronograma_id": row.id,
        "dpf": {"id": dpf.id, "numero_certificado": dpf.numero_certificado},
        "socio": {"id": socio.id, "nombre_completo": f"{socio.nombre} {socio.apellido}".strip(), "ci": socio.ci},
        "numero": row.numero, "fecha_pago": row.fecha_pago, "fecha_pago_real": row.fecha_pago_real,
        "dias": row.dias, "interes_bruto": f"{row.interes_bruto:.2f}",
        "retencion_rciva": f"{row.retencion_rciva:.2f}", "interes_neto": f"{row.interes_neto:.2f}",
        "moneda": db.get(Moneda, dpf.moneda_id).codigo_iso,
        "cuenta_abono": {"id": cuenta.id, "numero": cuenta.numero}, "transaccion_id": row.transaccion_id,
    }


@router.post("/intereses/procesar")
def procesar_intereses_dpf(
    body: DPFInteresesProcesarIn | None = None,
    request: Request = None,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None or usuario.rol.nombre not in {"OFICIAL_CREDITO", "ADMINISTRADOR"}:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    fecha = (body.fecha if body else None) or date.today()
    if fecha > date.today():
        raise HTTPException(status_code=400, detail="La fecha no puede ser futura")
    due = db.execute(
        select(DPFCronograma, DepositoPlazoFijo)
        .join(DepositoPlazoFijo, DepositoPlazoFijo.id == DPFCronograma.deposito_plazo_fijo_id)
        .where(DepositoPlazoFijo.cooperativa_id == usuario.cooperativa_id,
               DepositoPlazoFijo.estado == "VIGENTE", DepositoPlazoFijo.modalidad_pago_interes == "MENSUAL",
               DPFCronograma.estado == "PENDIENTE", DPFCronograma.fecha_pago <= fecha)
        .order_by(DPFCronograma.fecha_pago, DPFCronograma.id).with_for_update()
    ).all()
    payments, skipped = [], []
    for row, dpf in due:
        account = db.execute(select(CuentaAhorro).where(CuentaAhorro.id == dpf.cuenta_abono_id).with_for_update()).scalar_one_or_none()
        if account is None or account.estado != "ACTIVA":
            skipped.append({"dpf_id": dpf.id, "numero_certificado": dpf.numero_certificado, "motivo": "La cuenta de abono no está activa"})
            continue
        tx_id = db.execute(text("""INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id, deposito_plazo_fijo_id)
            VALUES ('PAGO_INTERES_DPF', :monto, 'WEB', :moneda, :cuenta, :dpf) RETURNING id"""),
            {"monto": row.interes_neto, "moneda": dpf.moneda_id, "cuenta": account.id, "dpf": dpf.id}).scalar_one()
        account.saldo_disponible += row.interes_neto
        row.estado = "PAGADO"
        row.fecha_pago_real = datetime.now().astimezone()
        row.transaccion_id = tx_id
        payments.append(_pago_interes_out(db, row, dpf))
        registrar_accion(db, accion="PAGO_INTERES", modulo="DPF", usuario_id=usuario.id,
                         cooperativa_id=usuario.cooperativa_id,
                         descripcion=f"Pago de interés DPF {dpf.numero_certificado}", request=request)
    db.commit()
    totals = {}
    for payment in payments:
        totals[payment["moneda"]] = totals.get(payment["moneda"], Decimal("0.00")) + Decimal(payment["interes_neto"])
    return {"fecha": fecha, "dpf_procesados": len({p["dpf"]["id"] for p in payments}),
            "cuotas_pagadas": len(payments), "total_neto_por_moneda": [
                {"moneda": currency, "total": f"{amount:.2f}"} for currency, amount in sorted(totals.items())],
            "pagos": payments, "omitidos": skipped}


@router.get("/intereses/pagos")
def listar_pagos_interes_dpf(
    desde: date | None = Query(None), hasta: date | None = Query(None),
    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None or usuario.rol.nombre not in {"OFICIAL_CREDITO", "ADMINISTRADOR", "CONTADOR"}:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    stmt = select(DPFCronograma, DepositoPlazoFijo).join(DepositoPlazoFijo).where(
        DepositoPlazoFijo.cooperativa_id == usuario.cooperativa_id, DPFCronograma.estado == "PAGADO")
    if desde: stmt = stmt.where(DPFCronograma.fecha_pago_real >= datetime.combine(desde, datetime.min.time(), tzinfo=timezone.utc))
    if hasta: stmt = stmt.where(DPFCronograma.fecha_pago_real < datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    rows = db.execute(stmt.order_by(DPFCronograma.fecha_pago_real.desc(), DPFCronograma.id.desc())).all()
    return [_pago_interes_out(db, row, dpf) for row, dpf in rows]


@router.get("/{dpf_id}/intereses")
def listar_intereses_dpf(dpf_id: int, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    if usuario.cooperativa_id is None or usuario.rol.nombre not in {"OFICIAL_CREDITO", "ADMINISTRADOR", "CONTADOR"}:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    dpf = db.execute(select(DepositoPlazoFijo).where(DepositoPlazoFijo.id == dpf_id,
        DepositoPlazoFijo.cooperativa_id == usuario.cooperativa_id)).scalar_one_or_none()
    if dpf is None: raise HTTPException(status_code=404, detail="DPF no encontrado")
    rows = db.execute(select(DPFCronograma).where(DPFCronograma.deposito_plazo_fijo_id == dpf_id,
        DPFCronograma.estado == "PAGADO").order_by(DPFCronograma.fecha_pago_real.desc())).scalars().all()
    return [_pago_interes_out(db, row, dpf) for row in rows]


@router.get("/{dpf_id}")
def obtener_dpf(
    dpf_id: int,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    dpf = db.execute(
        select(DepositoPlazoFijo).where(
            DepositoPlazoFijo.id == dpf_id,
            DepositoPlazoFijo.cooperativa_id == usuario.cooperativa_id,
        )
    ).scalar_one_or_none()
    if dpf is None:
        raise HTTPException(status_code=404, detail="DPF no encontrado")
    return _certificado_out(db, dpf)


TIPOS_LIQUIDACION = {"LIQUIDACION", "CANCELACION", "RENOVACION"}


def _obtener_dpf_tenant(db: Session, usuario: Usuario, dpf_id: int, *, bloquear=False):
    if usuario.cooperativa_id is None or usuario.rol.nombre == "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    query = select(DepositoPlazoFijo).where(
        DepositoPlazoFijo.id == dpf_id,
        DepositoPlazoFijo.cooperativa_id == usuario.cooperativa_id,
    )
    if bloquear:
        query = query.with_for_update()
    dpf = db.execute(query).scalar_one_or_none()
    if dpf is None:
        raise HTTPException(status_code=404, detail="DPF no encontrado")
    if dpf.estado != "VIGENTE":
        raise HTTPException(status_code=400, detail="El DPF no está vigente")
    return dpf


def _preview_liquidacion(db: Session, usuario: Usuario, dpf: DepositoPlazoFijo, tipo: str):
    if tipo not in TIPOS_LIQUIDACION:
        raise HTTPException(status_code=400, detail="Tipo de liquidación inválido")
    today = date.today()
    elapsed = max(0, (today - dpf.fecha_inicio).days)
    early = today < dpf.fecha_vencimiento
    coop = db.get(Cooperativa, usuario.cooperativa_id)
    moneda = db.get(Moneda, dpf.moneda_id)
    allowed, reason = True, None
    applied_rate = dpf.tasa_interes_anual
    paid_rows = db.execute(select(DPFCronograma).where(
        DPFCronograma.deposito_plazo_fijo_id == dpf.id, DPFCronograma.estado == "PAGADO")).scalars().all()
    paid_net = sum((item.interes_neto for item in paid_rows), Decimal("0.00"))
    pending_rows = db.execute(select(DPFCronograma).where(
        DPFCronograma.deposito_plazo_fijo_id == dpf.id, DPFCronograma.estado == "PENDIENTE")).scalars().all()
    if tipo == "CANCELACION":
        if not early:
            allowed, reason = False, "La cancelación solo se permite antes del vencimiento"
        elif not coop.dpf_permite_cancelacion_anticipada:
            allowed, reason = False, "La cooperativa no permite cancelaciones anticipadas"
        applied_rate = coop.dpf_tasa_penalizacion
        gross = _money(dpf.monto * applied_rate * Decimal(elapsed) / Decimal("36000"))
        exempt = moneda.codigo_iso == "BOB" and elapsed >= 30
        withholding = Decimal("0.00") if exempt else _money(gross * TAX_RATE)
        penalty_net = gross - withholding
        net = max(Decimal("0.00"), penalty_net - paid_net)
        capital_discount = max(Decimal("0.00"), paid_net - penalty_net)
        capital = dpf.monto - capital_discount
    else:
        if early:
            allowed, reason = False, "La liquidación o renovación solo se permite al vencimiento"
        gross = sum((item.interes_bruto for item in pending_rows), Decimal("0.00"))
        withholding = sum((item.retencion_rciva for item in pending_rows), Decimal("0.00"))
        net = sum((item.interes_neto for item in pending_rows), Decimal("0.00"))
        capital_discount = Decimal("0.00")
        capital = dpf.monto
    total = capital + net
    abono = db.get(CuentaAhorro, dpf.cuenta_abono_id)
    return {
        "tipo": tipo, "anticipada": early, "dias_transcurridos": elapsed,
        "tasa_aplicada": f"{applied_rate:.2f}", "interes_bruto": f"{gross:.2f}",
        "retencion_rciva": f"{withholding:.2f}", "interes_neto": f"{net:.2f}",
        "interes_ya_pagado": f"{paid_net:.2f}", "descuento_capital": f"{capital_discount:.2f}",
        "capital": f"{capital:.2f}", "total_a_abonar": f"{total:.2f}",
        "cuenta_abono": {"id": abono.id, "numero": abono.numero},
        "permitido": allowed, "motivo": reason, "gross_value": gross,
        "withholding_value": withholding, "net_value": net, "capital_value": capital,
        "interes_ya_pagado_value": paid_net, "descuento_capital_value": capital_discount,
    }


@router.get("/{dpf_id}/liquidacion/preview")
def previsualizar_liquidacion(
    dpf_id: int,
    tipo: str = Query(...),
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    dpf = _obtener_dpf_tenant(db, usuario, dpf_id)
    preview = _preview_liquidacion(db, usuario, dpf, tipo)
    for key in ("gross_value", "withholding_value", "net_value", "capital_value", "interes_ya_pagado_value", "descuento_capital_value"):
        preview.pop(key)
    return preview


@router.post("/{dpf_id}/liquidacion", status_code=status.HTTP_201_CREATED)
def liquidar_dpf(
    dpf_id: int,
    body: DPFLiquidacionIn,
    request: Request,
    usuario: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
):
    dpf = _obtener_dpf_tenant(db, usuario, dpf_id, bloquear=True)
    preview = _preview_liquidacion(db, usuario, dpf, body.tipo)
    if not preview["permitido"]:
        raise HTTPException(status_code=400, detail=preview["motivo"])
    abono = db.execute(select(CuentaAhorro).where(CuentaAhorro.id == dpf.cuenta_abono_id).with_for_update()).scalar_one()
    if abono.estado != "ACTIVA":
        raise HTTPException(status_code=400, detail="La cuenta de abono no está activa")
    if abono.saldo_disponible < MONTO_MINIMO_APERTURA[abono.tipo_producto]:
        raise HTTPException(status_code=400, detail="La cuenta de abono debe mantener su saldo mínimo")

    new_dpf = None
    roll_amount = Decimal("0.00")
    if body.tipo == "RENOVACION":
        term = body.plazo_dias or dpf.plazo_dias
        modality = body.modalidad_pago_interes or dpf.modalidad_pago_interes
        roll_amount = dpf.monto + (preview["net_value"] if body.capitalizar else Decimal("0.00"))
        simulation = _calcular_simulacion(db, usuario, roll_amount, dpf.moneda_id, term, modality)
        socio = db.get(Socio, dpf.socio_id)
        number = f"DPF-{_siguiente_secuencia(db, usuario.cooperativa_id, 'DPF'):06d}"
        signature = "|".join((number, socio.ci, f"{roll_amount:.2f}", db.get(Moneda, dpf.moneda_id).codigo_iso,
                               simulation["fecha_inicio"].isoformat(), simulation["fecha_vencimiento"].isoformat(),
                               f"{simulation['rate_value']:.2f}"))
        new_dpf = DepositoPlazoFijo(
            monto=roll_amount, tasa_interes_anual=simulation["rate_value"], plazo_dias=term,
            fecha_inicio=simulation["fecha_inicio"], fecha_vencimiento=simulation["fecha_vencimiento"],
            interes_calculado=simulation["gross_value"], estado="VIGENTE", socio_id=dpf.socio_id,
            moneda_id=dpf.moneda_id, numero_certificado=number, modalidad_pago_interes=modality,
            interes_bruto=simulation["gross_value"], retencion_rciva=simulation["tax_value"],
            interes_neto=simulation["net_value"], origen_fondos="CUENTA", cuenta_origen_id=abono.id,
            cuenta_abono_id=abono.id, codigo_verificacion=hashlib.sha256(signature.encode()).hexdigest()[:16],
            dpf_origen_id=dpf.id, usuario_id=usuario.id, cooperativa_id=usuario.cooperativa_id,
        )
        db.add(new_dpf)
        db.flush()
        for installment in simulation["cronograma"]:
            db.add(DPFCronograma(
                deposito_plazo_fijo_id=new_dpf.id, numero=installment["numero"], fecha_pago=installment["fecha_pago"],
                dias=installment["dias"], interes_bruto=Decimal(installment["interes_bruto"]),
                retencion_rciva=Decimal(installment["retencion_rciva"]), interes_neto=Decimal(installment["interes_neto"]),
                estado="PENDIENTE",
            ))

    liquidation = Liquidacion(
        monto_capital_retornado=preview["capital_value"],
        monto_interes_pagado=preview["net_value"],
        tipo_operacion=body.tipo,
        deposito_plazo_fijo_id=dpf.id,
        tipo=body.tipo,
        retencion_rciva=preview["withholding_value"],
        usuario_id=usuario.id,
        cuenta_abono_id=abono.id,
        dpf_renovado_id=new_dpf.id if new_dpf else None,
        interes_ya_pagado=preview["interes_ya_pagado_value"],
        descuento_capital=preview["descuento_capital_value"],
    )
    db.add(liquidation)
    db.flush()
    proceeds = preview["capital_value"] + preview["net_value"]
    new_amount = roll_amount if new_dpf else Decimal("0.00")
    account_credit = proceeds - new_amount
    if account_credit:
        abono.saldo_disponible += account_credit
    db.execute(text("""
        INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id, deposito_plazo_fijo_id, liquidacion_id)
        VALUES ('DEPOSITO', :monto, 'WEB', :moneda, :cuenta, :dpf, :liquidacion)
    """), {"monto": proceeds, "moneda": dpf.moneda_id, "cuenta": abono.id, "dpf": dpf.id, "liquidacion": liquidation.id})
    if new_dpf:
        db.execute(text("""
            INSERT INTO transaccion (tipo, monto, canal, moneda_id, cuenta_ahorro_id, deposito_plazo_fijo_id, liquidacion_id)
            VALUES ('RETIRO', :monto, 'WEB', :moneda, :cuenta, :dpf, :liquidacion)
        """), {"monto": new_amount, "moneda": dpf.moneda_id, "cuenta": abono.id, "dpf": new_dpf.id, "liquidacion": liquidation.id})
    dpf.estado = {"LIQUIDACION": "LIQUIDADO", "CANCELACION": "CANCELADO", "RENOVACION": "RENOVADO"}[body.tipo]
    registrar_accion(db, accion=body.tipo, modulo="DPF", usuario_id=usuario.id,
                     cooperativa_id=usuario.cooperativa_id,
                     descripcion=f"{body.tipo} DPF {dpf.numero_certificado}", request=request)
    db.commit()
    return {
        "liquidacion_id": liquidation.id,
        "tipo": body.tipo,
        "dpf": {"id": dpf.id, "numero_certificado": dpf.numero_certificado, "estado": dpf.estado},
        "capital": f"{preview['capital_value']:.2f}",
        "interes_bruto": f"{preview['gross_value']:.2f}",
        "retencion_rciva": f"{preview['withholding_value']:.2f}",
        "interes_neto": f"{preview['net_value']:.2f}",
        "interes_ya_pagado": f"{preview['interes_ya_pagado_value']:.2f}",
        "descuento_capital": f"{preview['descuento_capital_value']:.2f}",
        "total_abonado": f"{account_credit:.2f}",
        "cuenta_abono": {"id": abono.id, "numero": abono.numero},
        "nuevo_dpf": _certificado_out(db, new_dpf) if new_dpf else None,
    }
