"""Socio self-service endpoints."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_current_socio, get_db
from app.core.config import settings
from app.api.v1.endpoints.ahorros import _siguiente_secuencia
from app.core.bitacora import registrar_accion
from app.services.amortizacion import generar_plan_pagos
from app.services.credit_request_rules import (
    cancel_credit_request,
    has_in_progress_request,
    requires_field_evaluation,
    validate_base_credit_request,
)
from app.models.models import CuentaAhorro, Credito, DepositoPlazoFijo, DPFCronograma, Moneda, OfertaRecredito, ProductoCredito, Socio, SolicitudCredito, TablaAmortizacion, Usuario
from app.models.models import ComprobanteTransaccion
from app.schemas.schemas import DeudaCuotaOut, PagoCuotaIn, PagoOut

router = APIRouter()
CENT = Decimal("0.01")


class DatosDeclarados(BaseModel):
    ingreso_mensual: Decimal = Field(gt=0)
    egreso_mensual: Decimal = Field(ge=0)
    actividad_economica: str = Field(min_length=1, max_length=150)
    fuente_ingresos: str

    @field_validator("fuente_ingresos")
    @classmethod
    def validate_source(cls, value):
        if value not in {"DEPENDIENTE", "INDEPENDIENTE", "MIXTO"}:
            raise ValueError("La fuente de ingresos no es válida")
        return value


class SolicitudMovilCreate(BaseModel):
    producto_id: int
    monto: Decimal = Field(gt=0)
    plazo_meses: int = Field(gt=0)
    destino: str
    destino_detalle: str | None = None
    datos_declarados: DatosDeclarados


class SolicitudMovilCancel(BaseModel):
    motivo: str


@router.post("/solicitudes", status_code=status.HTTP_201_CREATED)
def crear_solicitud_movil(
    body: SolicitudMovilCreate, request: Request,
    socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db),
):
    product = db.execute(select(ProductoCredito).options(joinedload(ProductoCredito.moneda)).where(
        ProductoCredito.id == body.producto_id,
        ProductoCredito.cooperativa_id == socio.cooperativa_id,
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto crediticio no encontrado")
    validate_base_credit_request(product, monto=body.monto, plazo_meses=body.plazo_meses,
        destino=body.destino, destino_detalle=body.destino_detalle)
    if has_in_progress_request(db, socio.id):
        raise HTTPException(status_code=409, detail="El socio ya tiene una solicitud en curso")
    number = _siguiente_secuencia(db, socio.cooperativa_id, "SOLICITUD_CREDITO")
    row = SolicitudCredito(monto=body.monto, plazo_meses=body.plazo_meses,
        tasa_interes=product.tasa_interes_anual, estado="PENDIENTE", socio_id=socio.id,
        usuario_id=socio.usuario_id, producto_credito_id=product.id, moneda_id=product.moneda_id,
        numero_solicitud=f"SOL-{number:06d}", destino=body.destino,
        destino_detalle=(body.destino_detalle or "").strip() or None,
        cooperativa_id=socio.cooperativa_id, canal_origen="MOVIL",
        datos_declarados=body.datos_declarados.model_dump(mode="json"))
    db.add(row); db.flush()
    registrar_accion(db, accion="REGISTRAR_SOLICITUD", modulo="CREDITOS",
        usuario_id=socio.usuario_id, cooperativa_id=socio.cooperativa_id,
        descripcion=f"Solicitud móvil registrada: {row.id} ({row.numero_solicitud})", request=request)
    db.commit(); db.refresh(row)
    return _solicitud_movil_out(row)


def _solicitud_movil_out(row: SolicitudCredito):
    return {"id": row.id, "numero_solicitud": row.numero_solicitud,
        "producto": row.producto.nombre if row.producto else None,
        "monto": _money(row.monto), "plazo_meses": row.plazo_meses, "destino": row.destino,
        "estado": row.estado, "canal_origen": row.canal_origen,
        "fecha_solicitud": row.fecha_solicitud.isoformat(),
        "requiere_evaluacion": requires_field_evaluation(row),
        "motivo": row.motivo_anulacion or row.observaciones}


@router.get("/solicitudes")
def listar_solicitudes_movil(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    rows = db.execute(select(SolicitudCredito).options(joinedload(SolicitudCredito.producto)).where(
        SolicitudCredito.socio_id == socio.id,
        SolicitudCredito.cooperativa_id == socio.cooperativa_id,
    ).order_by(SolicitudCredito.fecha_solicitud.desc(), SolicitudCredito.id.desc())).scalars().all()
    return [_solicitud_movil_out(row) for row in rows]


@router.get("/solicitudes/{solicitud_id}")
def detalle_solicitud_movil(solicitud_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    row = db.execute(select(SolicitudCredito).options(joinedload(SolicitudCredito.producto)).where(
        SolicitudCredito.id == solicitud_id, SolicitudCredito.socio_id == socio.id,
        SolicitudCredito.cooperativa_id == socio.cooperativa_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Solicitud de crédito no encontrada")
    result = _solicitud_movil_out(row)
    result["linea_tiempo"] = [{"estado": row.estado, "fecha": row.fecha_actualizacion.isoformat(),
        "motivo": row.motivo_anulacion or row.observaciones}]
    return result


@router.post("/solicitudes/{solicitud_id}/cancelar")
def cancelar_solicitud_movil(solicitud_id: int, body: SolicitudMovilCancel, request: Request,
    socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    row = db.execute(select(SolicitudCredito).where(
        SolicitudCredito.id == solicitud_id, SolicitudCredito.socio_id == socio.id,
        SolicitudCredito.cooperativa_id == socio.cooperativa_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Solicitud de crédito no encontrada")
    cancel_credit_request(db, row, motivo=body.motivo, request=request, user_id=socio.usuario_id,
        cooperativa_id=socio.cooperativa_id, require_mobile_eligibility=True)
    db.commit(); db.refresh(row)
    return _solicitud_movil_out(row)


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


@router.get("/productos-credito")
def listar_productos_credito_socio(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    products = db.execute(
        select(ProductoCredito).options(joinedload(ProductoCredito.moneda)).where(
            ProductoCredito.cooperativa_id == socio.cooperativa_id,
            ProductoCredito.estado == "ACTIVO",
        ).order_by(ProductoCredito.nombre, ProductoCredito.id)
    ).scalars().all()
    return [{
        "id": item.id, "codigo": item.codigo, "nombre": item.nombre,
        "moneda": item.moneda.codigo_iso, "monto_min": _money(item.monto_min),
        "monto_max": _money(item.monto_max), "plazo_min_meses": item.plazo_min_meses,
        "plazo_max_meses": item.plazo_max_meses,
        "tasa_interes_anual": _money(item.tasa_interes_anual),
        "tipo_amortizacion": item.tipo_amortizacion,
        "requiere_garantia": item.requiere_garantia,
    } for item in products]


@router.get("/creditos/simulacion")
def simular_credito_socio(
    producto_id: int, monto: Decimal = Query(..., gt=0), plazo_meses: int = Query(..., gt=0),
    socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db),
):
    product = db.execute(select(ProductoCredito).where(
        ProductoCredito.id == producto_id,
        ProductoCredito.cooperativa_id == socio.cooperativa_id,
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto crediticio no encontrado")
    if product.estado != "ACTIVO":
        raise HTTPException(status_code=422, detail="El producto crediticio no está activo")
    if monto < product.monto_min or monto > product.monto_max:
        raise HTTPException(status_code=422, detail="El monto está fuera del rango permitido para el producto")
    if plazo_meses < product.plazo_min_meses or plazo_meses > product.plazo_max_meses:
        raise HTTPException(status_code=422, detail="El plazo está fuera del rango permitido para el producto")
    plan = generar_plan_pagos(monto=monto, tasa_anual=product.tasa_interes_anual,
        plazo_meses=plazo_meses, tipo_amortizacion=product.tipo_amortizacion, fecha_desembolso=date.today())
    return {
        "cuota_estimada": _money(plan["cuotas"][0]["cuota"]),
        "total_intereses": _money(plan["total_interes"]),
        "total_a_pagar": _money(plan["total_pagar"]),
        "cronograma": [{"numero": row["numero"], "capital": _money(row["capital"]),
            "interes": _money(row["interes"]), "cuota": _money(row["cuota"]),
            "saldo": _money(row["saldo_final"])} for row in plan["cuotas"]],
    }


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
            "saldo_resultante": _money(balance), "comprobante_id": None,
        })
    transaction_ids = [movement["transaccion_id"] for movement in movements]
    if transaction_ids:
        receipts = db.execute(select(ComprobanteTransaccion).where(
            ComprobanteTransaccion.socio_id == socio.id,
            (ComprobanteTransaccion.transaccion_salida_id.in_(transaction_ids)
             | ComprobanteTransaccion.transaccion_entrada_id.in_(transaction_ids)),
        )).scalars().all()
        receipt_by_transaction = {}
        for receipt in receipts:
            if receipt.transaccion_salida_id is not None:
                receipt_by_transaction[receipt.transaccion_salida_id] = receipt.id
            if receipt.transaccion_entrada_id is not None:
                receipt_by_transaction[receipt.transaccion_entrada_id] = receipt.id
        for movement in movements:
            movement["comprobante_id"] = receipt_by_transaction.get(movement["transaccion_id"])
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


@router.post("/creditos/{credito_id}/pagos", response_model=PagoOut, response_model_exclude_unset=True, status_code=status.HTTP_201_CREATED)
def pagar_cuota_socio(credito_id: int, body: dict, request: Request,
                      socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    account_id = body.get("cuenta_ahorro_id")
    if not isinstance(account_id, int) or account_id < 1:
        raise HTTPException(status_code=422, detail="Debe indicar una cuenta de ahorro")
    # The shared CU-W27 handler reads this server-set request context; a client
    # cannot supply it as a query parameter or change the staff endpoint's policy.
    request.state.mobile_socio_id = socio.id
    from app.api.v1.endpoints.creditos import cobrar_cuota
    from app.services.comprobantes_movil import issue_receipt, receipt_payload

    usuario = db.get(Usuario, socio.usuario_id)
    if usuario is None:
        raise HTTPException(status_code=403, detail="Usuario del socio no disponible")

    def issue_mobile_receipt(db_session, payment, payload, credit, installment, account, transaction_id):
        currency = credit.moneda or credit.solicitud.moneda
        receipt = issue_receipt(
            db_session, receipt_type="PAGO_CUOTA", cooperative_id=socio.cooperativa_id,
            member_id=socio.id, user_id=usuario.id, amount=payment.monto_total,
            currency=currency.codigo_iso, source_account_id=account.id,
            credit_id=credit.id, installment_number=installment.numero_cuota,
            outgoing_transaction_id=transaction_id, payment_id=payment.id,
            request=request,
        )
        payload["comprobante"] = receipt_payload(receipt, source_number=account.numero)
        payload["comprobante_id"] = receipt.id

    return cobrar_cuota(credito_id, PagoCuotaIn(modalidad="CUENTA", cuenta_ahorro_id=account_id),
                        request, usuario, db, before_commit_callback=issue_mobile_receipt)


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
    results = []
    for payment in payments:
        item = _pago_out(db, payment)
        receipt_id = db.execute(select(ComprobanteTransaccion.id).where(
            ComprobanteTransaccion.socio_id == socio.id,
            ComprobanteTransaccion.pago_cuota_id == payment.id,
        )).scalar_one_or_none()
        item["comprobante_id"] = receipt_id
        results.append(item)
    return results


@router.get("/comprobantes")
def listar_comprobantes_socio(
    tipo: str | None = Query(None), desde: date | None = Query(None), hasta: date | None = Query(None),
    socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db),
):
    from app.services.comprobantes_movil import receipt_payload

    query = select(ComprobanteTransaccion).where(ComprobanteTransaccion.socio_id == socio.id)
    if tipo:
        query = query.where(ComprobanteTransaccion.tipo == tipo)
    if desde:
        query = query.where(ComprobanteTransaccion.emitido_en >= datetime.combine(desde, datetime.min.time(), tzinfo=timezone.utc))
    if hasta:
        query = query.where(ComprobanteTransaccion.emitido_en < datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    rows = db.execute(query.order_by(ComprobanteTransaccion.emitido_en.desc(), ComprobanteTransaccion.id.desc())).scalars().all()
    return [_receipt_output(db, receipt, receipt_payload) for receipt in rows]


def _receipt_output(db, receipt, payload_builder):
    source = db.get(CuentaAhorro, receipt.cuenta_origen_id)
    destination = db.get(CuentaAhorro, receipt.cuenta_destino_id) if receipt.cuenta_destino_id else None
    return payload_builder(receipt, source_number=source.numero if source else "", destination_number=destination.numero if destination else None)


@router.get("/comprobantes/{receipt_id}")
def obtener_comprobante_socio(receipt_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    from app.services.comprobantes_movil import receipt_payload

    receipt = db.execute(select(ComprobanteTransaccion).where(
        ComprobanteTransaccion.id == receipt_id, ComprobanteTransaccion.socio_id == socio.id,
    )).scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    return _receipt_output(db, receipt, receipt_payload)


@router.get("/comprobantes/{receipt_id}/pdf")
def descargar_comprobante_socio(receipt_id: int, socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    from io import BytesIO
    from fastapi.responses import Response
    from fpdf import FPDF
    import segno

    receipt = db.execute(select(ComprobanteTransaccion).where(
        ComprobanteTransaccion.id == receipt_id, ComprobanteTransaccion.socio_id == socio.id,
    )).scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    source = db.get(CuentaAhorro, receipt.cuenta_origen_id)
    destination = db.get(CuentaAhorro, receipt.cuenta_destino_id) if receipt.cuenta_destino_id else None
    from app.services.comprobantes_movil import mask_account
    url = f"{settings.PUBLIC_API_URL.rstrip('/')}/verificacion/comprobantes/{receipt.codigo_verificacion}"
    qr = segno.make(url)
    qr_buffer = BytesIO()
    qr.save(qr_buffer, kind="png", scale=4)
    qr_buffer.seek(0)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=15)
    pdf.cell(0, 12, "Mobile transaction receipt", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    for label, value in (
        ("Number", receipt.numero), ("Type", receipt.tipo), ("Date", receipt.emitido_en.isoformat()),
        ("Amount", f"{receipt.monto} {receipt.moneda}"),
        ("Source account", mask_account(source.numero if source else None) or "Unavailable"),
        ("Destination account", mask_account(destination.numero if destination else None) or "—"),
        ("Verification code", receipt.codigo_verificacion),
    ):
        pdf.cell(0, 8, f"{label}: {value}", new_x="LMARGIN", new_y="NEXT")
    pdf.image(qr_buffer, x=10, y=pdf.get_y() + 4, w=30, h=30)
    return Response(bytes(pdf.output()), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{receipt.numero}.pdf"'})


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
