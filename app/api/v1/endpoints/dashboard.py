"""Role dashboards: a structured cooperative summary for staff and a financial summary for socios."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_socio, get_current_user, get_db
from app.api.v1.endpoints.feedback import _avg, _round1
from app.api.v1.endpoints.reportes import _money, _portfolio
from app.api.v1.endpoints.socio import listar_creditos_socio
from app.models.models import Bitacora, EncuestaSatisfaccion, NotificacionDestinatario, Socio, Usuario

router = APIRouter()
ROLES_STAFF = {"ADMINISTRADOR", "OFICIAL_CREDITO", "ASESOR_CREDITO", "CAJERO", "CONTADOR"}
NIVELES_RIESGO = ("BAJO", "MEDIO", "ALTO")
ACTIVIDAD_RECIENTE = 8


def _tenant(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES_STAFF:
        raise HTTPException(403, "Operación reservada al personal de la cooperativa")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _last_months(today: date, count: int = 12) -> list[str]:
    """The last ``count`` months as YYYY-MM, oldest first, ending with the current month."""
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


@router.get("/resumen")
def resumen_staff(moneda_id: int = Query(1, ge=1), user: Usuario = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Cooperative KPIs grouped by area: members, deposits, portfolio, operations, risk, satisfaction, activity."""
    tenant = _tenant(user)
    today = date.today()
    month_start = today.replace(day=1)
    months = _last_months(today)
    year_start = date.fromisoformat(f"{months[0]}-01")
    params = {"tenant": tenant, "currency": moneda_id, "month_start": month_start, "year_start": year_start}

    socios = db.execute(text("""
        SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE estado='ACTIVO') AS activos,
               COUNT(*) FILTER (WHERE fecha_registro >= :month_start) AS nuevos_mes
        FROM socio WHERE cooperativa_id=:tenant
    """), params).mappings().one()
    savings = db.execute(text("""
        SELECT COUNT(*) AS cuentas, COALESCE(SUM(a.saldo_disponible + a.saldo_bloqueado), 0) AS saldo
        FROM cuenta_ahorro a JOIN socio s ON s.id=a.socio_id
        WHERE s.cooperativa_id=:tenant AND a.estado='ACTIVA' AND a.moneda_id=:currency
    """), params).mappings().one()
    dpf = db.execute(text("""
        SELECT COUNT(*) AS certificados, COALESCE(SUM(d.monto), 0) AS capital
        FROM deposito_plazo_fijo d JOIN socio s ON s.id=d.socio_id
        WHERE s.cooperativa_id=:tenant AND d.estado='VIGENTE' AND d.moneda_id=:currency
    """), params).mappings().one()

    portfolio = _portfolio(db, tenant, today, moneda_id)
    placements = {row["mes"]: row for row in portfolio["colocaciones_mes"]}

    disbursed = db.execute(text("""
        SELECT COUNT(*) AS count, COALESCE(SUM(monto_aprobado), 0) AS amount FROM credito
        WHERE cooperativa_id=:tenant AND fecha_desembolso >= :month_start
    """), params).mappings().one()
    collections = db.execute(text("""
        SELECT to_char(date_trunc('month', p.fecha), 'YYYY-MM') AS mes, COUNT(*) AS pagos,
               COALESCE(SUM(p.monto_capital + p.monto_interes_pagado + COALESCE(p.monto_seguro, 0)
                            + COALESCE(p.monto_mora, 0)), 0) AS monto
        FROM pago_cuota p WHERE p.cooperativa_id=:tenant AND p.fecha >= :year_start
        GROUP BY 1
    """), params).mappings().all()
    collections_by_month = {row["mes"]: row for row in collections}
    this_month = collections_by_month.get(months[-1])

    requests = db.execute(text("""
        SELECT COALESCE(estado, 'PENDIENTE') AS estado, COUNT(*) AS count FROM solicitud_credito
        WHERE cooperativa_id=:tenant GROUP BY 1 ORDER BY 1
    """), params).mappings().all()
    risk = db.execute(text("""
        SELECT COALESCE(p.nivel_riesgo, 'SIN_PREDICCION') AS nivel, COUNT(*) AS count
        FROM credito c LEFT JOIN prediccion_de_morosidad p ON p.credito_id=c.id
        WHERE c.cooperativa_id=:tenant AND c.estado='VIGENTE' GROUP BY 1
    """), params).mappings().all()
    risk_counts = {row["nivel"]: row["count"] for row in risk}
    alerts = db.execute(text("SELECT COUNT(*) FROM alerta_credito WHERE cooperativa_id=:tenant AND estado='ACTIVA'"),
                        params).scalar_one()

    feedback = db.execute(select(EncuestaSatisfaccion.calificacion, EncuestaSatisfaccion.recomendaria).where(
        EncuestaSatisfaccion.cooperativa_id == tenant, EncuestaSatisfaccion.fecha_creacion >= year_start)).all()
    nps_answers = [row.recomendaria for row in feedback if row.recomendaria is not None]
    nps = None
    if nps_answers:
        promoters = sum(1 for value in nps_answers if value >= 9)
        detractors = sum(1 for value in nps_answers if value <= 6)
        nps = _round1(Decimal(promoters - detractors) * 100 / len(nps_answers))

    activity = db.execute(
        select(Bitacora.fecha_hora, Bitacora.modulo, Bitacora.accion, Bitacora.descripcion, Usuario.nombre)
        .join(Usuario, Usuario.id == Bitacora.usuario_id)
        .where(Bitacora.cooperativa_id == tenant)
        .order_by(Bitacora.fecha_hora.desc(), Bitacora.id.desc()).limit(ACTIVIDAD_RECIENTE)
    ).all()

    return {
        "fecha": today.isoformat(),
        "moneda_id": moneda_id,
        "socios": {"total": socios["total"], "activos": socios["activos"], "nuevos_mes": socios["nuevos_mes"]},
        "captaciones": {"cuentas_activas": savings["cuentas"], "saldo_ahorro": _money(savings["saldo"]),
                        "dpf_vigentes": dpf["certificados"], "capital_dpf": _money(dpf["capital"]),
                        "total": _money(Decimal(str(savings["saldo"])) + Decimal(str(dpf["capital"])))},
        "cartera": {"creditos_vigentes": portfolio["totales"]["creditos"],
                    "cartera_bruta": portfolio["totales"]["cartera_bruta"],
                    "cartera_en_mora": portfolio["totales"]["cartera_en_mora"],
                    "indice_mora": portfolio["totales"]["indice_mora"],
                    "por_producto": portfolio["por_producto"], "por_rango_mora": portfolio["por_rango_mora"]},
        "operaciones_mes": {"desembolsos": disbursed["count"], "monto_desembolsado": _money(disbursed["amount"]),
                            "pagos": this_month["pagos"] if this_month else 0,
                            "monto_cobrado": _money(this_month["monto"] if this_month else 0)},
        "solicitudes_por_estado": {row["estado"]: row["count"] for row in requests},
        "riesgo_ml": {level: risk_counts.get(level, 0) for level in (*NIVELES_RIESGO, "SIN_PREDICCION")},
        "alertas_activas": alerts,
        "satisfaccion": {"total": len(feedback), "promedio": _avg([row.calificacion for row in feedback]),
                         "nps": nps},
        "colocaciones_12m": [{"mes": month, "creditos": placements[month]["creditos"] if month in placements else 0,
                              "monto": placements[month]["monto"] if month in placements else "0.00"}
                             for month in months],
        "cobranza_12m": [{"mes": month,
                          "pagos": collections_by_month[month]["pagos"] if month in collections_by_month else 0,
                          "monto": _money(collections_by_month[month]["monto"] if month in collections_by_month else 0)}
                         for month in months],
        "actividad_reciente": [{"fecha": row.fecha_hora.isoformat() if row.fecha_hora else None,
                                "usuario": row.nombre, "modulo": row.modulo, "accion": row.accion,
                                "descripcion": row.descripcion} for row in activity],
    }


@router.get("/socio")
def resumen_socio(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    """The socio's own savings, credits (with type and next installment), payments, DPF and pending notices."""
    params = {"socio": socio.id}
    savings = db.execute(text("""
        SELECT COUNT(*) AS cuentas, COALESCE(SUM(saldo_disponible), 0) AS saldo
        FROM cuenta_ahorro WHERE socio_id=:socio AND estado='ACTIVA'
    """), params).mappings().one()
    paid = db.execute(text("""
        SELECT COALESCE(SUM(p.monto_capital), 0) AS capital, COALESCE(SUM(p.monto_interes_pagado), 0) AS interes,
               COALESCE(SUM(p.monto_seguro), 0) AS seguro, COALESCE(SUM(p.monto_mora), 0) AS mora
        FROM pago_cuota p JOIN credito c ON c.id=p.credito_id WHERE c.socio_id=:socio
    """), params).mappings().one()
    dpf = db.execute(text("""
        SELECT COUNT(*) AS certificados, COALESCE(SUM(monto), 0) AS capital
        FROM deposito_plazo_fijo WHERE socio_id=:socio AND estado='VIGENTE'
    """), params).mappings().one()
    unread = 0
    if socio.usuario_id is not None:
        unread = db.execute(select(func.count(NotificacionDestinatario.id)).where(
            NotificacionDestinatario.usuario_id == socio.usuario_id,
            NotificacionDestinatario.leida_en.is_(None))).scalar_one()
    ratings = db.execute(select(func.count(EncuestaSatisfaccion.id)).where(
        EncuestaSatisfaccion.socio_id == socio.id)).scalar_one()
    paid_parts = {key: Decimal(str(paid[key])) for key in ("capital", "interes", "seguro", "mora")}
    return {
        "socio": {"id": socio.id, "nombre": f"{socio.nombre} {socio.apellido}".strip()},
        "ahorros": {"cuentas": savings["cuentas"], "saldo_total": _money(savings["saldo"])},
        "creditos": listar_creditos_socio(socio, db),
        "pagado": {**{key: _money(value) for key, value in paid_parts.items()},
                   "total": _money(sum(paid_parts.values(), Decimal("0")))},
        "dpf": {"certificados": dpf["certificados"], "capital": _money(dpf["capital"])},
        "notificaciones_sin_leer": unread,
        "calificaciones_enviadas": ratings,
    }
