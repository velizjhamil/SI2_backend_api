"""Socio self-service endpoints."""
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_current_socio, get_db
from app.models.models import CuentaAhorro, Credito, DepositoPlazoFijo, DPFCronograma, Moneda, OfertaRecredito, Socio, SolicitudCredito, TablaAmortizacion, Usuario
from app.schemas.schemas import DeudaCuotaOut, PagoCuotaIn, PagoOut

router = APIRouter()
CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def _sentido(tipo: str) -> str:
    return "DEBITO" if tipo.upper() in {"RETIRO", "PAGO_CUOTA", "TRANSFERENCIA_SALIDA", "DEBITO"} else "CREDITO"


@router.get("/cuentas/{cuenta_id}/extracto")
def extracto_cuenta(
    cuenta_id: int,
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    socio: Socio = Depends(get_current_socio),
    db: Session = Depends(get_db),
):
    end = hasta or date.today()
    start = desde or end - timedelta(days=30)
    if start > end:
        raise HTTPException(status_code=422, detail="desde no puede ser posterior a hasta")
    if (end - start).days > 366:
        raise HTTPException(status_code=422, detail="El rango no puede superar 366 días")
    cuenta = db.execute(
        select(CuentaAhorro).options(joinedload(CuentaAhorro.moneda)).where(
            CuentaAhorro.id == cuenta_id, CuentaAhorro.socio_id == socio.id
        )
    ).unique().scalar_one_or_none()
    if cuenta is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")

    rows = db.execute(text("""
        SELECT id, tipo, monto, canal, fecha_hora
        FROM transaccion
        WHERE cuenta_ahorro_id = :account_id
          AND fecha_hora >= :start_date AND fecha_hora < :end_exclusive
        ORDER BY fecha_hora ASC, id ASC
    """), {"account_id": cuenta.id, "start_date": start,
          "end_exclusive": end + timedelta(days=1)}).mappings().all()
    after_range_net = db.execute(text("""
        SELECT COALESCE(SUM(CASE WHEN tipo IN ('RETIRO','PAGO_CUOTA','TRANSFERENCIA_SALIDA','DEBITO')
                                 THEN -monto ELSE monto END), 0)
        FROM transaccion WHERE cuenta_ahorro_id = :account_id AND fecha_hora >= :end_exclusive
    """), {"account_id": cuenta.id, "end_exclusive": end + timedelta(days=1)}).scalar_one()
    closing = Decimal(cuenta.saldo_disponible) - Decimal(str(after_range_net))
    range_net = sum(
        (Decimal(row["monto"]) * (-1 if _sentido(row["tipo"]) == "DEBITO" else 1) for row in rows),
        Decimal("0.00"),
    )
    initial = closing - range_net
    balance = initial
    credit_total = Decimal("0.00")
    debit_total = Decimal("0.00")
    movements = []
    for row in rows:
        amount = Decimal(row["monto"])
        sense = _sentido(row["tipo"])
        if sense == "CREDITO":
            credit_total += amount
            balance += amount
        else:
            debit_total += amount
            balance -= amount
        movements.append({
            "transaccion_id": row["id"], "fecha": row["fecha_hora"].isoformat(),
            "tipo": row["tipo"], "descripcion": row["tipo"].replace("_", " ").title(),
            "canal": row["canal"], "monto": _money(amount), "sentido": sense,
            "saldo_resultante": _money(balance),
        })
    total = len(movements)
    movements = list(reversed(movements))[offset:offset + limit]
    return {
        "cuenta": {"id": cuenta.id, "numero_cuenta": cuenta.numero,
                   "moneda": cuenta.moneda.codigo_iso,
                   "saldo_disponible": _money(Decimal(cuenta.saldo_disponible))},
        "desde": start.isoformat(), "hasta": end.isoformat(),
        "saldo_inicial": _money(initial), "total_creditos": _money(credit_total),
        "total_debitos": _money(debit_total), "saldo_final": _money(closing),
        "total": total, "movimientos": movements,
    }


@router.get("/creditos")
def listar_creditos_socio(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Credito)
        .options(joinedload(Credito.producto), joinedload(Credito.moneda),
                 joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
                 joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
                 selectinload(Credito.cronograma))
        .where(Credito.socio_id == socio.id)
        .order_by(Credito.estado.notin_(("VIGENTE", "EN_MORA")), Credito.id)
    ).unique().scalars().all()
    output = []
    for credit in rows:
        schedule = sorted(credit.cronograma, key=lambda row: row.numero_cuota)
        next_due = next((row for row in schedule if (row.estado_pago or "PENDIENTE") not in {"PAGADA", "PAGADO"}), None)
        product = credit.producto or credit.solicitud.producto
        currency = credit.moneda or credit.solicitud.moneda
        output.append({
            "id": credit.id, "numero_credito": credit.numero_credito,
            "producto": None if product is None else product.nombre,
            "moneda": None if currency is None else currency.codigo_iso,
            "monto_desembolsado": _money(Decimal(credit.monto_aprobado)),
            "saldo_pendiente": _money(Decimal(credit.saldo_pendiente)),
            "estado": credit.estado,
            "proxima_cuota": None if next_due is None else {
                "numero": next_due.numero_cuota,
                "fecha_vencimiento": next_due.fecha_vencimiento.isoformat(),
                "monto_total": _money(Decimal(next_due.monto_cuota_total)),
            },
        })
    return output


@router.get("/creditos/{credito_id}/deuda", response_model=DeudaCuotaOut)
def deuda_credito_socio(credito_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    credit = db.execute(select(Credito).options(joinedload(Credito.socio), joinedload(Credito.producto),
        joinedload(Credito.moneda), joinedload(Credito.solicitud).joinedload(SolicitudCredito.socio),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda), selectinload(Credito.cronograma))
        .where(Credito.id == credito_id, Credito.socio_id == socio.id)).unique().scalar_one_or_none()
    if credit is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    from app.api.v1.endpoints.creditos import _deuda_cuota_out
    return _deuda_cuota_out(credit)


@router.post("/creditos/{credito_id}/pagos", response_model=PagoOut, status_code=status.HTTP_201_CREATED)
def pagar_cuota_socio(credito_id: int, body: dict, request: Request,
                      socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    account_id = body.get("cuenta_ahorro_id")
    if not isinstance(account_id, int) or account_id < 1:
        raise HTTPException(status_code=422, detail="Debe indicar una cuenta de ahorro")
    # The shared CU-W27 handler reads this server-set request context; a client
    # cannot supply it as a query parameter or change the staff endpoint's policy.
    request.state.mobile_socio_id = socio.id
    from app.api.v1.endpoints.creditos import cobrar_cuota
    return cobrar_cuota(credito_id, PagoCuotaIn(modalidad="CUENTA", cuenta_ahorro_id=account_id),
                        request, db.get(Usuario, socio.usuario_id), db)


@router.get("/creditos/{credito_id}/pagos", response_model=list[PagoOut])
def listar_pagos_socio(credito_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    from app.api.v1.endpoints.creditos import _pago_out
    from app.models.models import PagoCuota
    from sqlalchemy.orm import joinedload
    credit = db.execute(select(Credito).where(Credito.id == credito_id, Credito.socio_id == socio.id)).scalar_one_or_none()
    if credit is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    payments = db.execute(select(PagoCuota).join(TablaAmortizacion, PagoCuota.tabla_amortizacion_id == TablaAmortizacion.id)
        .options(joinedload(PagoCuota.cuota), joinedload(PagoCuota.credito), joinedload(PagoCuota.usuario))
        .where(PagoCuota.credito_id == credit.id).order_by(PagoCuota.fecha, PagoCuota.id)).unique().scalars().all()
    return [_pago_out(db, payment) for payment in payments]


@router.get("/dpf")
def listar_dpf_socio(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    rows = db.execute(select(DepositoPlazoFijo, Moneda).join(Moneda, Moneda.id == DepositoPlazoFijo.moneda_id)
                      .where(DepositoPlazoFijo.socio_id == socio.id)
                      .order_by(DepositoPlazoFijo.fecha_emision.desc(), DepositoPlazoFijo.id.desc())).all()
    return [{
        "id": dpf.id, "numero_certificado": dpf.numero_certificado,
        "moneda": currency.codigo_iso, "monto": _money(Decimal(dpf.monto)),
        "tasa_interes": _money(Decimal(dpf.tasa_interes_anual)), "plazo_dias": dpf.plazo_dias,
        "fecha_apertura": dpf.fecha_inicio.isoformat(),
        "fecha_vencimiento": dpf.fecha_vencimiento.isoformat(), "estado": dpf.estado,
        "frecuencia_pago": dpf.modalidad_pago_interes,
    } for dpf, currency in rows]


@router.get("/creditos/{credito_id}/cronograma")
def cronograma_credito_socio(credito_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    credit = db.execute(select(Credito).options(joinedload(Credito.producto), joinedload(Credito.moneda),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
        joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda)).where(
        Credito.id == credito_id, Credito.socio_id == socio.id)).unique().scalar_one_or_none()
    if credit is None:
        raise HTTPException(status_code=404, detail="Crédito no encontrado")
    schedule = db.execute(select(TablaAmortizacion).where(TablaAmortizacion.credito_id == credit.id)
                          .order_by(TablaAmortizacion.numero_cuota)).scalars().all()
    product = credit.producto or credit.solicitud.producto
    currency = credit.moneda or credit.solicitud.moneda
    return {"credito": {"id": credit.id, "numero_credito": credit.numero_credito,
            "moneda": None if currency is None else currency.codigo_iso,
            "estado": credit.estado, "saldo_pendiente": _money(Decimal(credit.saldo_pendiente))},
        "cuotas": [{"numero": row.numero_cuota, "fecha_vencimiento": row.fecha_vencimiento.isoformat(),
            "capital": _money(Decimal(row.monto_capital)), "interes": _money(Decimal(row.monto_interes)),
            "seguro": "0.00", "monto_total": _money(Decimal(row.monto_cuota_total)),
            "saldo_capital": _money(Decimal(row.saldo_final or 0)), "estado": row.estado_pago or "PENDIENTE",
            "fecha_pago": None if row.fecha_pago is None else row.fecha_pago.isoformat()} for row in schedule]}


@router.get("/dpf/{dpf_id}")
def detalle_dpf_socio(dpf_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    dpf = db.execute(select(DepositoPlazoFijo).where(
        DepositoPlazoFijo.id == dpf_id, DepositoPlazoFijo.socio_id == socio.id
    )).scalar_one_or_none()
    if dpf is None:
        raise HTTPException(status_code=404, detail="DPF no encontrado")
    from app.api.v1.endpoints.dpf import _certificado_out
    return _certificado_out(db, dpf)


@router.get("/recreditos")
def listar_recreditos_socio(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    offers = db.execute(select(OfertaRecredito).where(
        OfertaRecredito.socio_id == socio.id,
        OfertaRecredito.estado == "VIGENTE",
        OfertaRecredito.fecha_vencimiento >= date.today(),
    ).order_by(OfertaRecredito.fecha_generacion.desc(), OfertaRecredito.id.desc())).scalars().all()
    from app.api.v1.endpoints.creditos import _oferta_out
    return [_oferta_out(db, offer) for offer in offers]


@router.post("/recreditos/{oferta_id}/aceptar", status_code=status.HTTP_201_CREATED)
def aceptar_recredito_socio(oferta_id: int, request: Request, body: dict | None = None,
                            socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    offer = db.execute(select(OfertaRecredito).where(
        OfertaRecredito.id == oferta_id, OfertaRecredito.socio_id == socio.id
    ).with_for_update()).scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    term = None if body is None else body.get("plazo_meses")
    if term is not None and (not isinstance(term, int) or term < 1):
        raise HTTPException(status_code=422, detail="El plazo debe ser un entero positivo")
    from app.api.v1.endpoints.creditos import aceptar_oferta_core
    return aceptar_oferta_core(db, offer, db.get(Usuario, socio.usuario_id), socio.cooperativa_id,
                               request, plazo_meses=term, inactive_status=422)


@router.post("/recreditos/{oferta_id}/descartar")
def descartar_recredito_socio(oferta_id: int, request: Request,
                              socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    offer = db.execute(select(OfertaRecredito).where(
        OfertaRecredito.id == oferta_id, OfertaRecredito.socio_id == socio.id
    ).with_for_update()).scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    if offer.estado == "VIGENTE" and offer.fecha_vencimiento < date.today():
        raise HTTPException(status_code=422, detail="La oferta no está vigente")
    from app.api.v1.endpoints.creditos import descartar_oferta_core
    return descartar_oferta_core(db, offer, "Descartada por el socio", db.get(Usuario, socio.usuario_id),
                                 socio.cooperativa_id, request, inactive_status=422)
