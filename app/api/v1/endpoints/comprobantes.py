"""Manual accounting voucher endpoints for CU-W30."""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_db
from app.core.bitacora import registrar_accion
from app.models.models import (
    ComprobanteContable,
    Cooperativa,
    DetalleAsiento,
    Moneda,
    PlanCuenta,
    ParametroContable,
    Usuario,
)
from app.schemas.schemas import (
    ComprobanteAnularCreate,
    ComprobanteManualCreate,
    ComprobanteOrigen,
    ComprobanteEstado,
    ComprobanteTipo,
    ComprobanteGenerarAutomaticos,
    ParametroContableUpdate,
    ParametroContableOut,
)

router = APIRouter()
READ_ROLES = {"CONTADOR", "ADMINISTRADOR"}
NUMBER_PREFIX = {"INGRESO": "I", "EGRESO": "E", "TRASPASO": "T"}
PARAMETER_DEFAULTS = {
    "CAJA": "111.01", "AHORRO_VISTA": "212.01", "AHORRO_PROGRAMADO": "212.01",
    "CARTERA_VIGENTE": "131.05", "INTERES_CARTERA": "513.05", "INTERES_PENAL": "515.03",
    "DPF_30": "213.01", "DPF_31_60": "213.02", "DPF_61_90": "213.03",
    "DPF_91_180": "213.04", "DPF_181_360": "213.05", "DPF_361_720": "213.06",
    "DPF_721_1080": "213.07", "DPF_MAS_1080": "213.08", "INTERES_DPF": "411.04",
    "RETENCION_RCIVA": "242.03", "CERTIFICADOS_APORTACION": "311.02", "COMISIONES": "541.99",
}


def _amount(value) -> str:
    return f"{Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _validate_body(schema, payload):
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        detail = "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        raise HTTPException(status_code=422, detail=detail) from exc


def _tenant(user: Usuario) -> int:
    role = user.rol.nombre if user.rol else None
    if role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Operación reservada a CONTADOR o ADMINISTRADOR")
    if user.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _next_number(db: Session, cooperative_id: int, tipo: str, gestion: int) -> str:
    # Locking the cooperative row serializes number allocation for this tenant.
    # The lock is held until the voucher and its details commit together.
    locked_id = db.execute(
        select(Cooperativa.id).where(Cooperativa.id == cooperative_id).with_for_update()
    ).scalar_one_or_none()
    if locked_id is None:
        raise HTTPException(status_code=403, detail="El usuario no pertenece a una cooperativa activa")
    prefix = NUMBER_PREFIX[tipo]
    pattern = f"^{prefix}-{gestion}-[0-9]{{6}}$"
    last_number = db.execute(
        text("""
            SELECT COALESCE(MAX(CASE WHEN numero ~ :pattern
                                     THEN split_part(numero, '-', 3)::integer
                                     ELSE 0 END), 0)
            FROM comprobante_contable
            WHERE cooperativa_id = :cooperativa_id AND tipo = :tipo AND gestion = :gestion
        """),
        {"pattern": pattern, "cooperativa_id": cooperative_id, "tipo": tipo, "gestion": gestion},
    ).scalar_one()
    next_value = last_number + 1
    if next_value > 999999:
        raise HTTPException(status_code=409, detail="Se agotó la numeración de comprobantes para esta gestión")
    return f"{prefix}-{gestion}-{next_value:06d}"


def _default_parameter_account(db: Session, cooperative_id: int, key: str):
    code = PARAMETER_DEFAULTS[key]
    official = db.execute(select(PlanCuenta).where(PlanCuenta.codigo == code, PlanCuenta.cooperativa_id.is_(None))).scalar_one_or_none()
    if official is None:
        raise HTTPException(status_code=500, detail=f"Cuenta MCEF predeterminada {code} no encontrada")
    analytic = db.execute(
        select(PlanCuenta).where(
            PlanCuenta.cooperativa_id == cooperative_id,
            PlanCuenta.plan_cuenta_padre_id == official.id,
            PlanCuenta.nivel == 5,
        ).order_by(PlanCuenta.id).limit(1)
    ).scalar_one_or_none()
    return official, analytic


def _ensure_parameter_defaults(db: Session, cooperative_id: int, request: Request, user: Usuario) -> bool:
    db.execute(select(Cooperativa.id).where(Cooperativa.id == cooperative_id).with_for_update()).scalar_one()
    changed = False
    for key in PARAMETER_DEFAULTS:
        official, analytic = _default_parameter_account(db, cooperative_id, key)
        desired = analytic or official
        parameter = db.execute(
            select(ParametroContable).where(
                ParametroContable.cooperativa_id == cooperative_id,
                ParametroContable.clave == key,
            ).with_for_update()
        ).scalar_one_or_none()
        if parameter is None:
            parameter = ParametroContable(cooperativa_id=cooperative_id, clave=key, plan_cuenta_id=desired.id)
            db.add(parameter)
            changed = True
        else:
            current = db.get(PlanCuenta, parameter.plan_cuenta_id)
            valid_target = current is not None and current.estado == "ACTIVA" and current.acepta_movimientos and (
                current.id == official.id if analytic is None else
                current.cooperativa_id == cooperative_id and current.plan_cuenta_padre_id == official.id and current.nivel == 5
            )
            if not valid_target:
                parameter.plan_cuenta_id = desired.id
                changed = True
    if changed:
        registrar_accion(
            db, accion="PARAMETROS_CONTABLES_INICIALIZAR", modulo="CONTABILIDAD",
            usuario_id=user.id, cooperativa_id=cooperative_id,
            descripcion="Inicializó o corrigió parámetros contables predeterminados", request=request,
        )
        db.commit()
    return changed


def _header(db: Session, voucher_id: int, cooperative_id: int):
    return db.execute(
        text("""
            SELECT c.id, c.numero, c.tipo, c.fecha_contable, c.glosa, c.origen, c.estado,
                   c.moneda_id, m.codigo_iso AS moneda_codigo, m.nombre AS moneda_nombre,
                   m.simbolo AS moneda_simbolo, c.transaccion_id,
                   COALESCE(sum(d.debe), 0) AS total_debe,
                   COALESCE(sum(d.haber), 0) AS total_haber,
                   c.usuario_id, c.gestion, c.fecha_registro,
                   c.comprobante_reversion_id, c.revierte_a_id, c.motivo_anulacion
            FROM comprobante_contable c
            JOIN moneda m ON m.id = c.moneda_id
            LEFT JOIN detalle_asiento d ON d.comprobante_contable_id = c.id
            WHERE c.id = :voucher_id AND c.cooperativa_id = :cooperativa_id
            GROUP BY c.id, m.id
        """),
        {"voucher_id": voucher_id, "cooperativa_id": cooperative_id},
    ).mappings().one_or_none()


def _public_header(row):
    return {
        "id": row["id"],
        "numero": row["numero"],
        "tipo": row["tipo"],
        "fecha_contable": row["fecha_contable"],
        "glosa": row["glosa"],
        "origen": row["origen"],
        "estado": row["estado"],
        "moneda": {
            "id": row["moneda_id"],
            "codigo": row["moneda_codigo"],
            "nombre": row["moneda_nombre"],
            "simbolo": row["moneda_simbolo"],
        },
        "total_debe": _amount(row["total_debe"]),
        "total_haber": _amount(row["total_haber"]),
        "transaccion_id": row["transaccion_id"],
        "comprobante_reversion_id": row["comprobante_reversion_id"],
        "revierte_a_id": row["revierte_a_id"],
        "motivo_anulacion": row["motivo_anulacion"],
    }


def _detail(db: Session, voucher_id: int, cooperative_id: int):
    row = _header(db, voucher_id, cooperative_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    lines = db.execute(
        text("""
            SELECT d.orden, pc.id AS cuenta_id, pc.codigo, pc.nombre,
                   d.glosa, d.debe, d.haber
            FROM detalle_asiento d
            JOIN plan_cuenta pc ON pc.id = d.plan_cuenta_id
            WHERE d.comprobante_contable_id = :voucher_id
            ORDER BY COALESCE(d.orden, 2147483647), d.id
        """),
        {"voucher_id": voucher_id},
    ).mappings().all()
    return {
        **_public_header(row),
        "gestion": row["gestion"],
        "fecha_registro": row["fecha_registro"],
        "lineas": [
            {
                "orden": line["orden"],
                "cuenta": {"id": line["cuenta_id"], "codigo": line["codigo"], "nombre": line["nombre"]},
                "glosa": line["glosa"],
                "debe": _amount(line["debe"]),
                "haber": _amount(line["haber"]),
            }
            for line in lines
        ],
    }


@router.get("/comprobantes")
def listar_comprobantes(
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    tipo: ComprobanteTipo | None = Query(None),
    origen: ComprobanteOrigen | None = Query(None),
    estado: ComprobanteEstado | None = Query(None),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    if desde and hasta and desde > hasta:
        raise HTTPException(status_code=422, detail="La fecha desde no puede ser posterior a hasta")
    filters = ["c.cooperativa_id = :cooperativa_id"]
    params = {"cooperativa_id": cooperative_id, "limit": limit, "offset": offset}
    if desde:
        filters.append("c.fecha_contable >= :desde")
        params["desde"] = desde
    if hasta:
        filters.append("c.fecha_contable <= :hasta")
        params["hasta"] = hasta
    if tipo:
        filters.append("c.tipo = :tipo")
        params["tipo"] = tipo
    if origen:
        filters.append("c.origen = :origen")
        params["origen"] = origen
    if estado:
        filters.append("c.estado = :estado")
        params["estado"] = estado
    if q:
        filters.append("(c.numero ILIKE :q OR c.glosa ILIKE :q)")
        params["q"] = f"%{q}%"
    where = " AND ".join(filters)
    total = db.execute(
        text(f"SELECT count(*) FROM comprobante_contable c WHERE {where}"), params
    ).scalar_one()
    rows = db.execute(
        text(f"""
            SELECT c.id, c.numero, c.tipo, c.fecha_contable, c.glosa, c.origen, c.estado,
                   c.transaccion_id, c.moneda_id, m.codigo_iso AS moneda_codigo,
                   m.nombre AS moneda_nombre, m.simbolo AS moneda_simbolo,
                   c.comprobante_reversion_id, c.revierte_a_id, c.motivo_anulacion,
                   COALESCE(sum(d.debe), 0) AS total_debe,
                   COALESCE(sum(d.haber), 0) AS total_haber
            FROM comprobante_contable c
            JOIN moneda m ON m.id = c.moneda_id
            LEFT JOIN detalle_asiento d ON d.comprobante_contable_id = c.id
            WHERE {where}
            GROUP BY c.id, m.id
            ORDER BY c.fecha_contable DESC, c.id DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()
    items = []
    for row in rows:
        public = _public_header(row)
        items.append({key: public[key] for key in (
            "id", "numero", "tipo", "fecha_contable", "glosa", "origen", "estado", "moneda", "total_debe", "total_haber", "transaccion_id"
        )})
    return {"total": total, "items": items}


@router.get("/comprobantes/{voucher_id}")
def obtener_comprobante(
    voucher_id: int,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _detail(db, voucher_id, _tenant(user))


@router.post("/comprobantes", status_code=status.HTTP_201_CREATED)
def crear_comprobante(
    request: Request,
    payload: Any = Body(...),
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = _validate_body(ComprobanteManualCreate, payload)
    cooperative_id = _tenant(user)
    if body.fecha_contable > date.today():
        raise HTTPException(status_code=422, detail="La fecha contable no puede ser futura")
    if db.get(Moneda, body.moneda_id) is None:
        raise HTTPException(status_code=422, detail="La moneda seleccionada no existe")

    accounts = {}
    for line in body.lineas:
        if line.plan_cuenta_id in accounts:
            continue
        account = db.execute(
            select(PlanCuenta).where(
                PlanCuenta.id == line.plan_cuenta_id,
                (PlanCuenta.cooperativa_id.is_(None) | (PlanCuenta.cooperativa_id == cooperative_id)),
            )
        ).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail="Cuenta contable no encontrada")
        if not account.acepta_movimientos:
            raise HTTPException(status_code=422, detail=f"La cuenta {account.codigo} no acepta movimientos")
        if account.estado != "ACTIVA":
            raise HTTPException(status_code=422, detail=f"La cuenta {account.codigo} no está activa")
        has_analytics = db.execute(
            select(PlanCuenta.id).where(
                PlanCuenta.cooperativa_id == cooperative_id,
                PlanCuenta.plan_cuenta_padre_id == account.id,
                PlanCuenta.nivel == 5,
            ).limit(1)
        ).scalar_one_or_none()
        if has_analytics is not None:
            raise HTTPException(
                status_code=422,
                detail=f"Debe utilizar una cuenta analítica para la subcuenta {account.codigo}",
            )
        accounts[line.plan_cuenta_id] = account

    try:
        numero = _next_number(db, cooperative_id, body.tipo, body.fecha_contable.year)
        voucher = ComprobanteContable(
            tipo=body.tipo,
            glosa=body.glosa,
            es_automatico=False,
            fecha=datetime.now(),
            transaccion_id=None,
            cooperativa_id=cooperative_id,
            numero=numero,
            gestion=body.fecha_contable.year,
            fecha_contable=body.fecha_contable,
            moneda_id=body.moneda_id,
            estado="REGISTRADO",
            usuario_id=user.id,
            origen="MANUAL",
        )
        db.add(voucher)
        db.flush()
        for order, line in enumerate(body.lineas, start=1):
            db.add(
                DetalleAsiento(
                    debe=line.debe,
                    haber=line.haber,
                    comprobante_contable_id=voucher.id,
                    plan_cuenta_id=line.plan_cuenta_id,
                    glosa=line.glosa,
                    orden=order,
                )
            )
        registrar_accion(
            db,
            accion="COMPROBANTE_CREAR_MANUAL",
            modulo="CONTABILIDAD",
            usuario_id=user.id,
            cooperativa_id=cooperative_id,
            descripcion=f"Creó comprobante manual {numero}",
            request=request,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return _detail(db, voucher.id, cooperative_id)


@router.post("/comprobantes/{voucher_id}/anular", status_code=status.HTTP_201_CREATED)
def anular_comprobante(
    voucher_id: int,
    request: Request,
    payload: Any = Body(...),
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = _validate_body(ComprobanteAnularCreate, payload)
    cooperative_id = _tenant(user)
    voucher = db.execute(
        select(ComprobanteContable)
        .where(
            ComprobanteContable.id == voucher_id,
            ComprobanteContable.cooperativa_id == cooperative_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if voucher is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    if voucher.origen == "AUTOMATICO":
        raise HTTPException(
            status_code=409,
            detail="Los comprobantes automáticos se revierten con la operación de origen",
        )
    if voucher.estado != "REGISTRADO":
        raise HTTPException(status_code=409, detail="Solo se pueden anular comprobantes registrados")

    try:
        today = date.today()
        numero = _next_number(db, cooperative_id, voucher.tipo, today.year)
        original_lines = db.execute(
            select(DetalleAsiento)
            .where(DetalleAsiento.comprobante_contable_id == voucher.id)
            .order_by(DetalleAsiento.orden, DetalleAsiento.id)
        ).scalars().all()
        reversal = ComprobanteContable(
            tipo=voucher.tipo,
            glosa=f"Anulación de {voucher.numero}: {body.motivo}",
            es_automatico=False,
            fecha=datetime.now(),
            transaccion_id=voucher.transaccion_id,
            cooperativa_id=cooperative_id,
            numero=numero,
            gestion=today.year,
            fecha_contable=today,
            moneda_id=voucher.moneda_id,
            estado="REGISTRADO",
            usuario_id=user.id,
            origen="MANUAL",
            revierte_a_id=voucher.id,
        )
        db.add(reversal)
        db.flush()
        for order, line in enumerate(original_lines, start=1):
            db.add(
                DetalleAsiento(
                    debe=line.haber,
                    haber=line.debe,
                    comprobante_contable_id=reversal.id,
                    plan_cuenta_id=line.plan_cuenta_id,
                    glosa=line.glosa,
                    orden=order,
                )
            )
        voucher.estado = "ANULADO"
        voucher.comprobante_reversion_id = reversal.id
        voucher.motivo_anulacion = body.motivo
        registrar_accion(
            db,
            accion="COMPROBANTE_ANULAR",
            modulo="CONTABILIDAD",
            usuario_id=user.id,
            cooperativa_id=cooperative_id,
            descripcion=f"Anuló comprobante {voucher.numero} mediante {numero}",
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _detail(db, reversal.id, cooperative_id)


@router.post("/comprobantes/generar-automaticos")
def generar_comprobantes_automaticos(
    request: Request,
    payload: Any = Body(...),
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Report transaction sources that cannot yet be safely resolved.

    B3's posting engine is deliberately tenant-derived; transaction has no
    cooperative_id and is queried as SQL because it has no ORM model.
    """
    body = _validate_body(ComprobanteGenerarAutomaticos, payload)
    cooperative_id = _tenant(user)
    filters = ["t.fecha_hora::date >= COALESCE(:desde, '-infinity'::date)",
               "t.fecha_hora::date <= COALESCE(:hasta, 'infinity'::date)"]
    params = {"cooperativa_id": cooperative_id, "desde": body.desde, "hasta": body.hasta}
    # Serialize generation per tenant before the pending-source snapshot.
    db.execute(select(Cooperativa.id).where(Cooperativa.id == cooperative_id).with_for_update()).scalar_one()
    rows = db.execute(text(f"""
        SELECT t.id, t.tipo, t.monto, t.fecha_hora, t.moneda_id,
               t.control_caja_id, t.cuenta_ahorro_id, t.credito_id,
               t.deposito_plazo_fijo_id, t.pago_cuota_id, t.liquidacion_id,
               t.transaccion_contraparte_id, a.tipo_producto,
               (SELECT c.cooperativa_id FROM caja c JOIN control_caja cc ON cc.caja_id=c.id WHERE cc.id=t.control_caja_id) AS cash_coop,
               (SELECT s.cooperativa_id FROM cuenta_ahorro ac JOIN socio s ON s.id=ac.socio_id WHERE ac.id=t.cuenta_ahorro_id) AS account_coop,
               (SELECT cr.cooperativa_id FROM credito cr WHERE cr.id=t.credito_id) AS credit_coop,
               (SELECT pc.cooperativa_id FROM pago_cuota pc WHERE pc.id=t.pago_cuota_id) AS payment_coop,
               (SELECT d.cooperativa_id FROM deposito_plazo_fijo d WHERE d.id=t.deposito_plazo_fijo_id) AS dpf_coop,
               (SELECT d.cooperativa_id FROM liquidacion li JOIN deposito_plazo_fijo d ON d.id=li.deposito_plazo_fijo_id WHERE li.id=t.liquidacion_id) AS liquidation_coop,
               p.monto_capital, p.monto_interes_pagado, p.monto_mora, p.monto_total, p.modalidad,
               dp.plazo_dias, dp.monto AS dpf_monto,
               l.monto_capital_retornado, l.monto_interes_pagado AS liquidacion_interes,
               l.retencion_rciva AS liquidacion_rciva, l.interes_ya_pagado, l.descuento_capital,
               l.dpf_renovado_id, l.deposito_plazo_fijo_id AS liquidacion_dpf_id,
               crono.interes_bruto, crono.retencion_rciva AS cronograma_rciva, crono.interes_neto,
               crono.linked_count AS cronograma_count,
               cp.id AS contraparte_id, cp.tipo AS contraparte_tipo,
               cp.transaccion_contraparte_id AS contraparte_ref,
               cp.monto AS contraparte_monto, cp.moneda_id AS contraparte_moneda_id,
               cp.cuenta_ahorro_id AS contraparte_cuenta_id,
               (SELECT tipo_producto FROM cuenta_ahorro ca WHERE ca.id=cp.cuenta_ahorro_id) AS contraparte_producto,
               (SELECT cs.cooperativa_id FROM cuenta_ahorro ca JOIN socio cs ON cs.id=ca.socio_id WHERE ca.id=cp.cuenta_ahorro_id) AS contraparte_coop,
               COALESCE(
                 (SELECT c.cooperativa_id FROM caja c JOIN control_caja cc ON cc.caja_id=c.id WHERE cc.id=t.control_caja_id),
                 (SELECT s.cooperativa_id FROM cuenta_ahorro a JOIN socio s ON s.id=a.socio_id WHERE a.id=t.cuenta_ahorro_id),
                 (SELECT cr.cooperativa_id FROM credito cr WHERE cr.id=t.credito_id),
                 (SELECT pc.cooperativa_id FROM pago_cuota pc WHERE pc.id=t.pago_cuota_id),
                 (SELECT dp.cooperativa_id FROM deposito_plazo_fijo dp WHERE dp.id=t.deposito_plazo_fijo_id),
                 (SELECT dp.cooperativa_id FROM liquidacion l JOIN deposito_plazo_fijo dp ON dp.id=l.deposito_plazo_fijo_id WHERE l.id=t.liquidacion_id)
               ) AS cooperative_id
        FROM transaccion t
        LEFT JOIN cuenta_ahorro a ON a.id=t.cuenta_ahorro_id
        LEFT JOIN pago_cuota p ON p.id=t.pago_cuota_id
        LEFT JOIN transaccion cp ON cp.id=t.transaccion_contraparte_id
        LEFT JOIN deposito_plazo_fijo dp ON dp.id=t.deposito_plazo_fijo_id
        LEFT JOIN liquidacion l ON l.id=t.liquidacion_id
        LEFT JOIN LATERAL (
            SELECT dc.interes_bruto, dc.retencion_rciva, dc.interes_neto,
                   count(*) OVER () AS linked_count
            FROM dpf_cronograma dc WHERE dc.transaccion_id=t.id
            ORDER BY dc.id LIMIT 1
        ) crono ON TRUE
        WHERE {" AND ".join(filters)}
          AND NOT EXISTS (SELECT 1 FROM comprobante_contable v WHERE v.transaccion_id=t.id AND v.origen='AUTOMATICO' AND v.revierte_a_id IS NULL)
          AND COALESCE(
                 (SELECT c.cooperativa_id FROM caja c JOIN control_caja cc ON cc.caja_id=c.id WHERE cc.id=t.control_caja_id),
                 (SELECT s.cooperativa_id FROM cuenta_ahorro a JOIN socio s ON s.id=a.socio_id WHERE a.id=t.cuenta_ahorro_id),
                 (SELECT cr.cooperativa_id FROM credito cr WHERE cr.id=t.credito_id),
                 (SELECT pc.cooperativa_id FROM pago_cuota pc WHERE pc.id=t.pago_cuota_id),
                 (SELECT dp.cooperativa_id FROM deposito_plazo_fijo dp WHERE dp.id=t.deposito_plazo_fijo_id),
                 (SELECT dp.cooperativa_id FROM liquidacion l JOIN deposito_plazo_fijo dp ON dp.id=l.deposito_plazo_fijo_id WHERE l.id=t.liquidacion_id)
               ) = :cooperativa_id
        ORDER BY t.id
        FOR UPDATE OF t
    """), params).mappings().all()
    omitted = []
    generated = 0
    by_type = {}

    def account_for(key):
        row = db.execute(text("""
            SELECT pc.id, pc.codigo, pc.estado, pc.acepta_movimientos,
                   EXISTS(SELECT 1 FROM plan_cuenta child WHERE child.cooperativa_id=:coop AND child.plan_cuenta_padre_id=pc.id AND child.nivel=5) AS has_children
            FROM parametro_contable p JOIN plan_cuenta pc ON pc.id=p.plan_cuenta_id
            WHERE p.cooperativa_id=:coop AND p.clave=:key
              AND (pc.cooperativa_id IS NULL OR pc.cooperativa_id=:coop)
        """), {"coop": cooperative_id, "key": key}).mappings().one_or_none()
        if row is None or row["estado"] != "ACTIVA" or not row["acepta_movimientos"] or row["has_children"]:
            raise ValueError(f"Parámetro contable {key} ausente o no es una cuenta activa de movimiento analítica")
        return row["id"]

    def add_line(voucher_id, order, account_id, debit, credit, label):
        db.add(DetalleAsiento(
            debe=debit, haber=credit, comprobante_contable_id=voucher_id,
            plan_cuenta_id=account_id, glosa=label, orden=order,
        ))

    for row in rows:
        kind = row["tipo"]
        try:
            source_tenants = (row["cash_coop"], row["account_coop"], row["credit_coop"], row["payment_coop"], row["dpf_coop"], row["liquidation_coop"])
            if any(source_tenant is not None and source_tenant != cooperative_id for source_tenant in source_tenants):
                raise ValueError("Los enlaces de origen pertenecen a cooperativas distintas")
            if kind == "TRANSFERENCIA_ENTRADA":
                raise ValueError("Entrada de transferencia cubierta por la salida contraparte; no se duplica")
            if kind == "APERTURA_DPF":
                raise ValueError("APERTURA_DPF solo aparece en datos legacy y no tiene escritor runtime")
            if kind in {"APERTURA", "DEPOSITO", "RETIRO"} and not row["control_caja_id"] and not row["credito_id"] and not row["liquidacion_id"] and not row["deposito_plazo_fijo_id"]:
                raise ValueError("Movimiento de cuenta sin fuente de caja explícita; no se infiere una contrapartida")
            amount = Decimal(str(row["monto"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            postings = []
            cash_debit = False
            cash_credit = False
            if kind == "RETIRO" and row["credito_id"] and row["control_caja_id"]:
                postings = [(account_for("CARTERA_VIGENTE"), amount, Decimal("0")), (account_for("CAJA"), Decimal("0"), amount)]
                cash_credit = True
            elif kind in {"DEPOSITO", "RETIRO"} and row["control_caja_id"] and row["cuenta_ahorro_id"] and not row["credito_id"]:
                cash, saving = account_for("CAJA"), account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(cash, amount, Decimal("0")), (saving, Decimal("0"), amount)] if kind == "DEPOSITO" else [(saving, amount, Decimal("0")), (cash, Decimal("0"), amount)]
                cash_debit = kind == "DEPOSITO"
                cash_credit = kind == "RETIRO"
            elif kind == "DESEMBOLSO_CREDITO" and row["cuenta_ahorro_id"] and not row["control_caja_id"]:
                saving = account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(account_for("CARTERA_VIGENTE"), amount, Decimal("0")), (saving, Decimal("0"), amount)]
            elif kind == "DEPOSITO" and row["deposito_plazo_fijo_id"] and not row["liquidacion_id"]:
                if not row["control_caja_id"] and not row["cuenta_ahorro_id"]:
                    raise ValueError("Emisión DPF sin fuente de fondos explícita")
                if Decimal(str(row["dpf_monto"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != amount:
                    raise ValueError("El monto de emisión no concilia con el principal persistido del DPF")
                plazo = int(row["plazo_dias"] or 0)
                dpf_key = ("DPF_30" if plazo <= 30 else "DPF_31_60" if plazo <= 60 else "DPF_61_90" if plazo <= 90 else "DPF_91_180" if plazo <= 180 else "DPF_181_360" if plazo <= 360 else "DPF_361_720" if plazo <= 720 else "DPF_721_1080" if plazo <= 1080 else "DPF_MAS_1080")
                source = account_for("CAJA") if row["control_caja_id"] else account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(source, amount, Decimal("0")),(account_for(dpf_key),Decimal("0"),amount)]
                cash_debit = bool(row["control_caja_id"])
            elif kind == "PAGO_CUOTA" and row["pago_cuota_id"]:
                capital, interest, mora, total = (Decimal(str(row[name] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for name in ("monto_capital", "monto_interes_pagado", "monto_mora", "monto_total"))
                if total <= 0 or capital + interest + mora != total or amount != total:
                    raise ValueError("Componentes persistidos de pago no concilian con total de la transacción")
                if row["modalidad"] == "EFECTIVO" and not row["control_caja_id"]:
                    raise ValueError("Pago marcado EFECTIVO sin control de caja persistido")
                if row["modalidad"] == "CUENTA" and not row["cuenta_ahorro_id"]:
                    raise ValueError("Pago marcado CUENTA sin cuenta de débito persistida")
                if row["modalidad"] not in {"EFECTIVO", "CUENTA"}:
                    raise ValueError("Modalidad de pago ausente o no reconocida")
                if row["modalidad"] == "EFECTIVO":
                    debit_account = account_for("CAJA"); cash_debit = True
                elif row["cuenta_ahorro_id"]:
                    debit_account = account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                else:
                    raise ValueError("Pago sin cuenta de débito persistida")
                postings = [(debit_account,total,Decimal("0"))]
                for key, value in (("CARTERA_VIGENTE",capital),("INTERES_CARTERA",interest),("INTERES_PENAL",mora)):
                    if value:
                        postings.append((account_for(key),Decimal("0"),value))
            elif kind == "PAGO_INTERES_DPF" and row["interes_bruto"] is not None and row["cronograma_rciva"] is not None and row["interes_neto"] is not None:
                if row["cronograma_count"] != 1:
                    raise ValueError("Se esperaba exactamente un cronograma DPF enlazado con la transacción")
                gross, retention, net = (Decimal(str(row[name])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for name in ("interes_bruto", "cronograma_rciva", "interes_neto"))
                if net != amount or gross != net + retention:
                    raise ValueError("Componentes persistidos del cronograma DPF no concilian con el neto pagado")
                if not row["cuenta_ahorro_id"]:
                    raise ValueError("Pago de interés DPF sin cuenta de abono persistida")
                saving = account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(account_for("INTERES_DPF"),gross,Decimal("0")),(saving,Decimal("0"),net)]
                if retention:
                    postings.append((account_for("RETENCION_RCIVA"),Decimal("0"),retention))
            elif kind == "DEPOSITO" and row["liquidacion_id"]:
                if row["liquidacion_dpf_id"] != row["deposito_plazo_fijo_id"]:
                    raise ValueError("La transacción de liquidación no referencia el DPF liquidado")
                capital = Decimal(str(row["monto_capital_retornado"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                net_interest = Decimal(str(row["liquidacion_interes"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                retention = Decimal(str(row["liquidacion_rciva"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                paid = Decimal(str(row["interes_ya_pagado"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                discount = Decimal(str(row["descuento_capital"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if paid or discount:
                    raise ValueError("Liquidación contiene interés previamente pagado o descuento de capital no separable sin inferencia")
                if capital + net_interest != amount:
                    raise ValueError("Componentes persistidos de liquidación no concilian con la transacción")
                if not row["cuenta_ahorro_id"]:
                    raise ValueError("Liquidación sin cuenta de abono persistida")
                plazo = int(row["plazo_dias"] or 0)
                dpf_key = ("DPF_30" if plazo <= 30 else "DPF_31_60" if plazo <= 60 else "DPF_61_90" if plazo <= 90 else "DPF_91_180" if plazo <= 180 else "DPF_181_360" if plazo <= 360 else "DPF_361_720" if plazo <= 720 else "DPF_721_1080" if plazo <= 1080 else "DPF_MAS_1080")
                saving = account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(account_for(dpf_key),capital,Decimal("0")),(account_for("INTERES_DPF"),net_interest+retention,Decimal("0")),(saving,Decimal("0"),amount)]
                if retention:
                    postings.append((account_for("RETENCION_RCIVA"),Decimal("0"),retention))
            elif kind == "RETIRO" and row["liquidacion_id"] and row["dpf_renovado_id"] == row["deposito_plazo_fijo_id"] and row["cuenta_ahorro_id"]:
                if Decimal(str(row["dpf_monto"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != amount:
                    raise ValueError("El retiro de renovación no concilia con el principal persistido del nuevo DPF")
                plazo = int(row["plazo_dias"] or 0)
                dpf_key = ("DPF_30" if plazo <= 30 else "DPF_31_60" if plazo <= 60 else "DPF_61_90" if plazo <= 90 else "DPF_91_180" if plazo <= 180 else "DPF_181_360" if plazo <= 360 else "DPF_361_720" if plazo <= 720 else "DPF_721_1080" if plazo <= 1080 else "DPF_MAS_1080")
                saving = account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(saving,amount,Decimal("0")),(account_for(dpf_key),Decimal("0"),amount)]
            elif kind == "TRANSFERENCIA_SALIDA" and row["cuenta_ahorro_id"] and row["contraparte_id"] and row["contraparte_cuenta_id"]:
                if row["contraparte_tipo"] != "TRANSFERENCIA_ENTRADA" or row["contraparte_ref"] != row["id"] or row["contraparte_coop"] != cooperative_id or Decimal(str(row["contraparte_monto"])) != Decimal(str(row["monto"])) or row["contraparte_moneda_id"] != row["moneda_id"]:
                    raise ValueError("La contraparte de transferencia no es recíproca o sus importes, moneda o tenant difieren")
                from_account = account_for("AHORRO_PROGRAMADO" if row["tipo_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                to_account = account_for("AHORRO_PROGRAMADO" if row["contraparte_producto"] == "PROGRAMADO" else "AHORRO_VISTA")
                postings = [(from_account,amount,Decimal("0")),(to_account,Decimal("0"),amount)]
            else:
                raise ValueError("Fuente ambigua o tipo sin componentes contables persistidos suficientes")
            if sum((p[1] for p in postings), Decimal("0")) != sum((p[2] for p in postings), Decimal("0")):
                raise ValueError("Asiento generado no balancea")
            voucher_type = "INGRESO" if cash_debit else ("EGRESO" if cash_credit else "TRASPASO")
            date_value = row["fecha_hora"].date() if row["fecha_hora"] else date.today()
            number = _next_number(db, cooperative_id, voucher_type, date_value.year)
            voucher = ComprobanteContable(tipo=voucher_type, glosa=f"Generado por transacción {row['id']}", es_automatico=True,
                transaccion_id=row["id"], cooperativa_id=cooperative_id, numero=number, gestion=date_value.year,
                fecha_contable=date_value, moneda_id=row["moneda_id"], estado="REGISTRADO", usuario_id=user.id, origen="AUTOMATICO")
            db.add(voucher); db.flush()
            for order, (account_id, debit, credit) in enumerate(postings, 1):
                add_line(voucher.id, order, account_id, debit, credit, f"Transacción {row['id']}")
            generated += 1
            by_type[voucher_type] = by_type.get(voucher_type, 0) + 1
        except ValueError as exc:
            omitted.append({"transaccion_id": row["id"], "tipo": kind, "motivo": str(exc)})
    result = {"procesadas": len(rows), "generados": generated, "omitidas": omitted, "por_tipo": by_type}
    registrar_accion(
        db, accion="COMPROBANTES_GENERAR_AUTOMATICOS", modulo="CONTABILIDAD",
        usuario_id=user.id, cooperativa_id=cooperative_id,
        descripcion=f"Generación automática: {generated} comprobantes, {len(omitted)} omitidos", request=request,
    )
    db.commit()
    return result


@router.get("/parametros-contables", response_model=list[ParametroContableOut])
def listar_parametros_contables(
    request: Request,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    _ensure_parameter_defaults(db, cooperative_id, request, user)
    rows = db.execute(text("""
        SELECT p.clave, p.plan_cuenta_id, pc.codigo, pc.nombre
        FROM parametro_contable p JOIN plan_cuenta pc ON pc.id=p.plan_cuenta_id
        WHERE p.cooperativa_id=:cooperativa_id
        ORDER BY p.clave
    """), {"cooperativa_id": cooperative_id}).mappings().all()
    return [dict(row) for row in rows]


@router.put("/parametros-contables/{clave}", response_model=ParametroContableOut)
def actualizar_parametro_contable(
    clave: str,
    body: ParametroContableUpdate,
    request: Request,
    user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperative_id = _tenant(user)
    if clave not in PARAMETER_DEFAULTS:
        raise HTTPException(status_code=404, detail="Parámetro contable no encontrado")
    _ensure_parameter_defaults(db, cooperative_id, request, user)
    official, analytic = _default_parameter_account(db, cooperative_id, clave)
    account = db.execute(
        select(PlanCuenta).where(
            PlanCuenta.id == body.plan_cuenta_id,
            (PlanCuenta.cooperativa_id.is_(None) | (PlanCuenta.cooperativa_id == cooperative_id)),
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Cuenta contable no encontrada")
    if account.estado != "ACTIVA":
        raise HTTPException(status_code=422, detail=f"La cuenta {account.codigo} no está activa")
    if not account.acepta_movimientos:
        raise HTTPException(status_code=422, detail=f"La cuenta {account.codigo} no acepta movimientos")
    if analytic is not None:
        valid = account.cooperativa_id == cooperative_id and account.plan_cuenta_padre_id == official.id and account.nivel == 5
        if not valid:
            raise HTTPException(status_code=422, detail=f"Debe utilizar una cuenta analítica de {official.codigo} para el parámetro {clave}")
    elif account.id != official.id:
        raise HTTPException(status_code=422, detail=f"Sin cuentas analíticas, el parámetro {clave} debe utilizar la cuenta MCEF {official.codigo}")

    parameter = db.execute(
        select(ParametroContable).where(
            ParametroContable.cooperativa_id == cooperative_id,
            ParametroContable.clave == clave,
        ).with_for_update()
    ).scalar_one()
    parameter.plan_cuenta_id = account.id
    registrar_accion(
        db, accion="PARAMETRO_CONTABLE_ACTUALIZAR", modulo="CONTABILIDAD",
        usuario_id=user.id, cooperativa_id=cooperative_id,
        descripcion=f"Actualizó parámetro contable {clave} a la cuenta {account.codigo}", request=request,
    )
    db.commit()
    return {"clave": clave, "plan_cuenta_id": account.id, "codigo": account.codigo, "nombre": account.nombre}
