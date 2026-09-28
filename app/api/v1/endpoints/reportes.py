"""Read-only financial and management reports (CU-W33)."""

import csv
import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import Date, cast, func, select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_current_user, get_db
from app.api.v1.endpoints.creditos import _configuracion_mora
from app.api.v1.endpoints.estados_financieros import _balance_data, _income_data
from app.models.models import Credito, SolicitudCredito, Usuario
from app.services.cobro_cuotas import resumir_morosidad

router = APIRouter()
ROLES = {"ADMINISTRADOR", "CONTADOR", "OFICIAL_CREDITO"}
CENT = Decimal("0.01")
CREDIT_EFFECTIVE_DATE_SQL = "COALESCE(c.fecha_desembolso, s.fecha_resolucion_comite::date, s.fecha_solicitud::date, c.fecha_creacion::date)"
CREDIT_EFFECTIVE_CURRENCY_SQL = "COALESCE(c.moneda_id, s.moneda_id)"


def _credit_effective_date():
    return func.coalesce(Credito.fecha_desembolso, cast(SolicitudCredito.fecha_resolucion_comite, Date),
                         cast(SolicitudCredito.fecha_solicitud, Date), cast(Credito.fecha_creacion, Date))


def _credit_effective_currency():
    return func.coalesce(Credito.moneda_id, SolicitudCredito.moneda_id)


def _tenant(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES:
        raise HTTPException(403, "Operación reservada a ADMINISTRADOR, CONTADOR u OFICIAL_CREDITO")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _filters(db: Session, cutoff: date, currency_id: int, tenant: int):
    if cutoff > date.today():
        raise HTTPException(422, "La fecha de corte no puede ser futura")
    currency = db.execute(text("SELECT id FROM moneda WHERE id=:id"), {"id": currency_id}).scalar_one_or_none()
    if currency is None:
        raise HTTPException(422, "La moneda indicada no existe")
    return {"tenant": tenant, "currency": currency_id}


def _money(value) -> str:
    return f"{Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def _csv(filename: str, headers: list[str], rows: list[list]):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _unclassified_credit_warnings(db: Session, tenant: int, cutoff: date):
    row = db.execute(text("""
        SELECT COUNT(*) AS count, COALESCE(SUM(c.saldo_pendiente),0) AS amount
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        WHERE c.cooperativa_id=:tenant AND c.estado='VIGENTE'
          AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff
          AND {CREDIT_EFFECTIVE_CURRENCY_SQL} IS NULL
    """.format(CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL,
               CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL)),
        {"tenant": tenant, "cutoff": cutoff}).mappings().one()
    if not row["count"]:
        return []
    return [{"codigo": "CREDITOS_SIN_MONEDA", "creditos": row["count"],
             "saldo": _money(row["amount"]),
             "mensaje": "Hay créditos vigentes sin moneda identificable; se excluyen de los totales por moneda."}]


def _portfolio(db: Session, tenant: int, cutoff: date, currency_id: int):
    params = _filters(db, cutoff, currency_id, tenant)
    warnings = _unclassified_credit_warnings(db, tenant, cutoff)
    credits = db.execute(select(Credito).join(SolicitudCredito, SolicitudCredito.id == Credito.solicitud_credito_id).options(
        joinedload(Credito.producto), joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
        selectinload(Credito.cronograma)
    ).where(Credito.cooperativa_id == tenant, _credit_effective_currency() == currency_id,
            Credito.estado == "VIGENTE", _credit_effective_date() <= cutoff)).unique().scalars().all()
    arrears = {}
    for credit in credits:
        rate, grace = _configuracion_mora(credit)
        summary = resumir_morosidad(cuotas=credit.cronograma, fecha=cutoff,
                                    tasa_mora_anual=rate, dias_gracia_mora=grace)
        arrears[credit.id] = summary["dias_de_retaso"] if summary["estado"] == "EN_MORA" else 0
    ids = list(arrears)
    mora_ids = [credit_id for credit_id, days in arrears.items() if days > 0]
    totals = db.execute(text("""
        SELECT COALESCE(SUM(c.saldo_pendiente),0) AS gross, COUNT(*) AS credits,
               COALESCE(SUM(c.saldo_pendiente) FILTER (WHERE c.id=ANY(:mora_ids)),0) AS overdue
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        WHERE c.cooperativa_id=:tenant AND {CREDIT_EFFECTIVE_CURRENCY_SQL}=:currency AND c.estado='VIGENTE'
          AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff
    """.format(CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL,
               CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL)), {**params, "cutoff": cutoff, "mora_ids": mora_ids}).mappings().one()
    state_rows = db.execute(text("""
        SELECT c.estado AS key, COUNT(*) AS count, COALESCE(SUM(c.saldo_pendiente),0) AS amount
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        WHERE c.cooperativa_id=:tenant AND {CREDIT_EFFECTIVE_CURRENCY_SQL}=:currency AND c.estado='VIGENTE'
          AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff GROUP BY c.estado ORDER BY c.estado
    """.format(CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL,
               CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL)), {**params, "cutoff": cutoff}).mappings().all()
    product_rows = db.execute(text("""
        SELECT COALESCE(p.nombre,'Sin producto') AS key, COUNT(*) AS count,
               COALESCE(SUM(c.saldo_pendiente),0) AS amount
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        LEFT JOIN producto_credito p ON p.id=c.producto_credito_id
        WHERE c.cooperativa_id=:tenant AND {CREDIT_EFFECTIVE_CURRENCY_SQL}=:currency AND c.estado='VIGENTE'
          AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff GROUP BY p.nombre ORDER BY p.nombre
    """.format(CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL,
               CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL)), {**params, "cutoff": cutoff}).mappings().all()
    destination_rows = db.execute(text("""
        SELECT COALESCE(NULLIF(s.destino_detalle,''), s.destino, 'Sin destino') AS key,
               COUNT(*) AS count, COALESCE(SUM(c.saldo_pendiente),0) AS amount
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        WHERE c.cooperativa_id=:tenant AND {CREDIT_EFFECTIVE_CURRENCY_SQL}=:currency AND c.estado='VIGENTE'
          AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff GROUP BY key ORDER BY key
    """.format(CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL,
               CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL)), {**params, "cutoff": cutoff}).mappings().all()
    ranges = [("0",0,0),("1-30",1,30),("31-90",31,90),("91-180",91,180),(">180",181,None)]
    mora_rows = []
    for label, minimum, maximum in ranges:
        selected = [credit_id for credit_id, days in arrears.items()
                    if (days == 0 if label == "0" else days >= minimum and (maximum is None or days <= maximum))]
        row = db.execute(text("""
            SELECT COUNT(*) AS count, COALESCE(SUM(saldo_pendiente),0) AS amount FROM credito
            WHERE id=ANY(:ids)
        """), {"ids": selected}).mappings().one()
        mora_rows.append({"rango": label, "saldo": _money(row["amount"]), "creditos": row["count"]})
    month_number = cutoff.month - 11
    month_year = cutoff.year
    if month_number <= 0:
        month_number += 12
        month_year -= 1
    month_start = date(month_year, month_number, 1)
    months = db.execute(text("""
        SELECT date_trunc('month', {CREDIT_EFFECTIVE_DATE_SQL})::date AS month, COUNT(*) AS count,
               COALESCE(SUM(c.monto_aprobado),0) AS amount
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        WHERE c.cooperativa_id=:tenant AND {CREDIT_EFFECTIVE_CURRENCY_SQL}=:currency
          AND {CREDIT_EFFECTIVE_DATE_SQL}>=:start AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff
        GROUP BY 1 ORDER BY 1
    """.format(CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL,
               CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL)), {**params, "cutoff": cutoff, "start": month_start}).mappings().all()
    gross = Decimal(str(totals["gross"] or 0))
    overdue = Decimal(str(totals["overdue"] or 0))
    return {"definicion": "Cartera bruta: suma del saldo pendiente de créditos VIGENTE; mora: criterio de cuotas impagas y días de gracia de CU-W28 evaluado a fecha_corte.",
        "advertencias": warnings,
        "fecha_corte": cutoff.isoformat(), "moneda_id": currency_id,
        "totales": {"cartera_bruta": _money(gross), "cartera_en_mora": _money(overdue),
                    "indice_mora": _money(overdue/gross*100) if gross else None,
                    **({"motivo_indice_mora": "La cartera bruta es cero para la fecha de corte."} if not gross else {}),
                    "creditos": totals["credits"]},
        "por_estado": [{"estado": r["key"], "creditos": r["count"], "saldo": _money(r["amount"])} for r in state_rows],
        "por_producto": [{"producto": r["key"], "creditos": r["count"], "saldo": _money(r["amount"])} for r in product_rows],
        "por_destino": [{"destino": r["key"], "creditos": r["count"], "saldo": _money(r["amount"])} for r in destination_rows],
        "por_rango_mora": mora_rows,
        "colocaciones_mes": [{"mes": r["month"].isoformat()[:7], "monto": _money(r["amount"]), "creditos": r["count"]} for r in months]}


@router.get("/cartera")
def cartera(fecha_corte: date = Query(default_factory=date.today), moneda_id: int = Query(1, ge=1),
            user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return _portfolio(db, _tenant(user), fecha_corte, moneda_id)


@router.get("/cartera/export")
def export_cartera(fecha_corte: date = Query(default_factory=date.today), moneda_id: int = Query(1, ge=1),
                   user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    body = _portfolio(db, _tenant(user), fecha_corte, moneda_id)
    rows = [["Total", key, value] for key, value in body["totales"].items()]
    rows += [["Advertencia", item["codigo"], item["creditos"], item["saldo"], item["mensaje"]]
             for item in body["advertencias"]]
    return _csv(f"cartera-{fecha_corte.isoformat()}.csv",
                ["Sección", "Concepto", "Valor", "Saldo", "Mensaje"], rows)


def _deposits(db: Session, tenant: int, cutoff: date, currency_id: int):
    params = _filters(db, cutoff, currency_id, tenant)
    savings = db.execute(text("""
        SELECT a.tipo_producto AS type, COUNT(*) AS count,
               COALESCE(SUM(a.saldo_disponible+a.saldo_bloqueado),0) AS amount
        FROM cuenta_ahorro a JOIN socio s ON s.id=a.socio_id
        WHERE s.cooperativa_id=:tenant AND a.moneda_id=:currency AND a.estado='ACTIVA'
          AND a.fecha_registro<=:cutoff_end GROUP BY a.tipo_producto ORDER BY a.tipo_producto
    """), {**params, "cutoff_end": datetime.combine(cutoff, time.max)}).mappings().all()
    terms = db.execute(text("""
        SELECT CASE WHEN plazo_dias<=30 THEN '30' WHEN plazo_dias<=60 THEN '31-60'
          WHEN plazo_dias<=90 THEN '61-90' WHEN plazo_dias<=180 THEN '91-180'
          WHEN plazo_dias<=360 THEN '181-360' WHEN plazo_dias<=720 THEN '361-720'
          WHEN plazo_dias<=1080 THEN '721-1080' ELSE '>1080' END AS band,
          COUNT(*) AS count, COALESCE(SUM(monto),0) AS amount
        FROM deposito_plazo_fijo d JOIN socio s ON s.id=d.socio_id
        WHERE s.cooperativa_id=:tenant AND d.moneda_id=:currency AND d.estado='VIGENTE'
          AND d.fecha_inicio<=:cutoff GROUP BY band
    """), {**params, "cutoff": cutoff}).mappings().all()
    term_map = {r["band"]: r for r in terms}
    labels = ["30", "31-60", "61-90", "91-180", "181-360", "361-720", "721-1080", ">1080"]
    expiring = db.execute(text("""
        SELECT COUNT(*) AS count, COALESCE(SUM(monto),0) AS amount
        FROM deposito_plazo_fijo d JOIN socio s ON s.id=d.socio_id
        WHERE s.cooperativa_id=:tenant AND d.moneda_id=:currency AND d.estado='VIGENTE'
          AND d.fecha_inicio<=:cutoff AND d.fecha_vencimiento>:cutoff
          AND d.fecha_vencimiento<=:next_cutoff
    """), {**params, "cutoff": cutoff, "next_cutoff": cutoff + timedelta(days=30)}).mappings().one()
    savings_total = db.execute(text("""
        SELECT COUNT(*) AS count, COALESCE(SUM(a.saldo_disponible+a.saldo_bloqueado),0) AS amount
        FROM cuenta_ahorro a JOIN socio s ON s.id=a.socio_id
        WHERE s.cooperativa_id=:tenant AND a.moneda_id=:currency AND a.estado='ACTIVA'
          AND a.fecha_registro<=:cutoff_end
    """), {**params, "cutoff_end": datetime.combine(cutoff, time.max)}).mappings().one()
    dpf_total = db.execute(text("""
        SELECT COUNT(*) AS count, COALESCE(SUM(d.monto),0) AS amount
        FROM deposito_plazo_fijo d JOIN socio s ON s.id=d.socio_id
        WHERE s.cooperativa_id=:tenant AND d.moneda_id=:currency AND d.estado='VIGENTE'
          AND d.fecha_inicio<=:cutoff
    """), {**params, "cutoff": cutoff}).mappings().one()
    combined_total = db.execute(text("SELECT CAST(:savings AS numeric)+CAST(:dpf AS numeric)"),
                                {"savings": savings_total["amount"], "dpf": dpf_total["amount"]}).scalar_one()
    return {"definicion": "Captaciones: saldos disponibles y bloqueados de cuentas ACTIVA más capital de DPF VIGENTE a fecha_corte; plazo según días contractuales.",
        "fecha_corte": cutoff.isoformat(), "moneda_id": currency_id,
        "ahorros_por_tipo": [{"tipo": r["type"], "cuentas": r["count"], "saldo": _money(r["amount"])} for r in savings],
        "dpf_por_plazo": [{"rango": label, "certificados": term_map.get(label, {}).get("count", 0),
                           "capital": _money(term_map.get(label, {}).get("amount", 0))} for label in labels],
        "dpf_vencimientos_proximos": {"certificados": expiring["count"], "capital": _money(expiring["amount"])},
        "totales": {"cuentas_ahorro": savings_total["count"], "saldo_ahorro": _money(savings_total["amount"]),
                    "certificados_dpf": dpf_total["count"], "capital_dpf": _money(dpf_total["amount"]),
                    "captaciones": _money(combined_total)}}


@router.get("/captaciones")
def captaciones(fecha_corte: date = Query(default_factory=date.today), moneda_id: int = Query(1, ge=1),
                user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return _deposits(db, _tenant(user), fecha_corte, moneda_id)


@router.get("/captaciones/export")
def export_captaciones(fecha_corte: date = Query(default_factory=date.today), moneda_id: int = Query(1, ge=1),
                       user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    body = _deposits(db, _tenant(user), fecha_corte, moneda_id)
    rows = [["Ahorros", r["tipo"], r["saldo"]] for r in body["ahorros_por_tipo"]]
    rows += [["DPF", r["rango"], r["capital"]] for r in body["dpf_por_plazo"]]
    return _csv(f"captaciones-{fecha_corte.isoformat()}.csv", ["Sección", "Concepto", "Monto"], rows)


def _group_amount(balance, prefixes):
    amount = Decimal("0")
    for section in balance["secciones"].values():
        for code, group in section.items():
            if any(code.startswith(prefix) for prefix in prefixes):
                amount += Decimal(str(group["monto"]))
            for account in group.get("cuentas", []):
                if any(account["codigo"].startswith(prefix) for prefix in prefixes):
                    amount += Decimal(str(account["monto"]))
    return amount


def _has_foreign_postings(db: Session, tenant: int, base_id: int | None, cutoff: date,
                          start: date | None = None) -> bool:
    if base_id is None:
        return False
    lower_clause = "AND cc.fecha_contable>=:start" if start is not None else ""
    params = {"tenant": tenant, "base": base_id, "cutoff": cutoff}
    if start is not None:
        params["start"] = start
    return bool(db.execute(text(f"""
        SELECT EXISTS (SELECT 1 FROM comprobante_contable cc
          JOIN detalle_asiento d ON d.comprobante_contable_id=cc.id
          WHERE cc.cooperativa_id=:tenant AND cc.moneda_id<>:base
            AND cc.fecha_contable<=:cutoff {lower_clause})
    """), params).scalar())


def _portfolio_overdue_bob(db: Session, tenant: int, cutoff: date):
    base_id = db.execute(text("SELECT id FROM moneda WHERE es_moneda_base IS TRUE ORDER BY id LIMIT 1")).scalar_one_or_none()
    if base_id is None:
        return None, "No está configurada la moneda base BOB."
    currencies = db.execute(text("""
        SELECT DISTINCT {CREDIT_EFFECTIVE_CURRENCY_SQL} AS currency_id
        FROM credito c JOIN solicitud_credito s ON s.id=c.solicitud_credito_id
        WHERE c.cooperativa_id=:tenant AND c.estado='VIGENTE'
          AND {CREDIT_EFFECTIVE_DATE_SQL}<=:cutoff AND {CREDIT_EFFECTIVE_CURRENCY_SQL} IS NOT NULL
        ORDER BY currency_id
    """.format(CREDIT_EFFECTIVE_CURRENCY_SQL=CREDIT_EFFECTIVE_CURRENCY_SQL,
               CREDIT_EFFECTIVE_DATE_SQL=CREDIT_EFFECTIVE_DATE_SQL)), {"tenant": tenant, "cutoff": cutoff}).scalars().all()
    total = Decimal("0")
    for currency_id in currencies:
        portfolio = _portfolio(db, tenant, cutoff, currency_id)
        overdue = Decimal(portfolio["totales"]["cartera_en_mora"])
        if currency_id == base_id:
            total += overdue
            continue
        if not overdue:
            continue
        rate = db.execute(text("""
            SELECT valor FROM tipo_cambio WHERE cooperativa_id=:tenant AND moneda_id=:currency
              AND fecha<=:cutoff ORDER BY fecha DESC LIMIT 1
        """), {"tenant": tenant, "currency": currency_id, "cutoff": cutoff}).scalar_one_or_none()
        if rate is None:
            currency_name = db.execute(text("SELECT codigo_iso FROM moneda WHERE id=:id"), {"id": currency_id}).scalar_one()
            return None, f"No existe tipo de cambio de {currency_name} vigente al {cutoff.isoformat()} para convertir la cartera en mora a BOB."
        total += overdue * Decimal(str(rate))
    return total, None


def _indicators(db: Session, tenant: int, cutoff: date):
    if cutoff > date.today():
        raise HTTPException(422, "La fecha de corte no puede ser futura")
    cutoff = min(cutoff, date.today())
    balance = income = None
    balance_error = income_error = None
    base_id = db.execute(text("SELECT id FROM moneda WHERE es_moneda_base IS TRUE ORDER BY id LIMIT 1")).scalar_one_or_none()
    year_start = date(cutoff.year, 1, 1)
    has_foreign_balance_postings = _has_foreign_postings(db, tenant, base_id, cutoff)
    has_foreign_income_postings = _has_foreign_postings(db, tenant, base_id, cutoff, year_start)
    try:
        balance = _balance_data(db, tenant, cutoff, "CONSOLIDADO", 3)
    except HTTPException as exc:
        balance_error = str(exc.detail)
        if not has_foreign_balance_postings:
            try:
                balance = _balance_data(db, tenant, cutoff, "BOB", 3)
                balance_error = None
            except HTTPException as fallback_exc:
                balance_error = str(fallback_exc.detail)
    try:
        income = _income_data(db, tenant, year_start, cutoff, "CONSOLIDADO")
    except HTTPException as exc:
        income_error = str(exc.detail)
        if not has_foreign_income_postings:
            try:
                income = _income_data(db, tenant, year_start, cutoff, "BOB")
                income_error = None
            except HTTPException as fallback_exc:
                income_error = str(fallback_exc.detail)
    overdue_bob, portfolio_error = _portfolio_overdue_bob(db, tenant, cutoff)
    items = []

    def add(key, name, numerator, denominator, definition, reason=None):
        if reason:
            value = None
        elif denominator is None or denominator == 0:
            value, reason = None, "El denominador es cero o no está disponible para la fecha de corte."
        else:
            value = _money(Decimal(numerator) / Decimal(denominator) * Decimal("100"))
        items.append({"clave": key, "nombre": name, "valor": value, "unidad": "%",
                      **({"motivo": reason} if reason else {}), "definicion": definition})

    if balance is None:
        add("liquidez", "Liquidez", 0, None, "Disponibilidades (110) / obligaciones a la vista y de ahorro (211+212) × 100.", balance_error)
        add("cobertura_previsiones", "Cobertura de previsiones", 0, None, "|Previsión para incobrabilidad de cartera (139)| / cartera en mora convertida a BOB × 100.", portfolio_error or balance_error)
        add("roa", "ROA", 0, None, "Resultado de gestión YTD anualizado / activo total × 100.", balance_error)
        add("roe", "ROE", 0, None, "Resultado de gestión YTD anualizado / patrimonio × 100.", balance_error)
        return {"tipo": "indicadores de gestión", "fecha_corte": cutoff.isoformat(), "indicadores": items,
                "tipo_cambio": None}
    liquid_assets = _group_amount(balance, ("110",))
    liabilities = _group_amount(balance, ("211", "212"))
    provisions = abs(_group_amount(balance, ("139",)))
    add("liquidez", "Liquidez", liquid_assets, liabilities,
        "Disponibilidades (110) / obligaciones a la vista y de ahorro (211+212) × 100.")
    add("cobertura_previsiones", "Cobertura de previsiones", provisions, overdue_bob,
        "|Previsión para incobrabilidad de cartera (139)| / cartera en mora convertida a BOB × 100.", portfolio_error)
    annualized = None
    income_reason = income_error
    if income is not None:
        days = (cutoff - year_start).days + 1
        annualized = Decimal(income["resultado_neto_gestion"]) * Decimal("365") / Decimal(days)
    add("roa", "ROA", annualized or 0, Decimal(balance["total_activo"]),
        "Resultado de gestión YTD × 365 / días transcurridos del año / activo total × 100.", income_reason)
    add("roe", "ROE", annualized or 0, Decimal(balance["total_patrimonio"]),
        "Resultado de gestión YTD × 365 / días transcurridos del año / patrimonio × 100.", income_reason)
    return {"tipo": "indicadores de gestión", "fecha_corte": cutoff.isoformat(), "indicadores": items,
            "tipo_cambio": balance.get("tipo_cambio")}


@router.get("/indicadores")
def indicadores(fecha_corte: date = Query(default_factory=date.today), user: Usuario = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return _indicators(db, _tenant(user), fecha_corte)


@router.get("/indicadores/export")
def export_indicadores(fecha_corte: date = Query(default_factory=date.today), user: Usuario = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    body = _indicators(db, _tenant(user), fecha_corte)
    rows = [[i["clave"], i["nombre"], i["valor"], i.get("motivo", ""), i["definicion"]]
            for i in body["indicadores"]]
    return _csv(f"indicadores-{fecha_corte.isoformat()}.csv", ["Clave", "Indicador", "Valor", "Motivo", "Definición"], rows)


@router.get("/resumen-ejecutivo")
def resumen_ejecutivo(fecha_corte: date = Query(default_factory=date.today), moneda_id: int = Query(1, ge=1),
                      user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant(user)
    portfolio = _portfolio(db, tenant, fecha_corte, moneda_id)
    return {"definicion": "Resumen gerencial de cartera, captaciones e indicadores de gestión al corte y moneda solicitados.",
            "advertencias": portfolio["advertencias"],
            "fecha_corte": fecha_corte.isoformat(), "cartera": portfolio["totales"],
            "captaciones": _deposits(db, tenant, fecha_corte, moneda_id)["totales"],
            "indicadores": _indicators(db, tenant, fecha_corte)["indicadores"]}
