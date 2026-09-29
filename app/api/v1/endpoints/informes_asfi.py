"""Tenant-scoped ASFI previews and report snapshots (CU-W34)."""

import csv
import hashlib
import io
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_current_user, get_db
from app.api.v1.endpoints.creditos import _configuracion_mora
from app.api.v1.endpoints.reportes import _unclassified_credit_warnings
from app.core.bitacora import registrar_accion
from app.models.models import Credito, Moneda, Reporte, SolicitudCredito, Usuario
from app.services.cobro_cuotas import resumir_morosidad
from app.services.informes_asfi import (
    base_prevision_hipotecaria,
    categoria_por_mora,
    monto_prevision,
    porcentaje_prevision_ciclica,
    porcentaje_prevision_especifica,
)

router = APIRouter()
ROLES = {"ADMINISTRADOR", "CONTADOR"}
CENT = Decimal("0.01")
ASFI_CREDIT_URL = "https://servdmzw.asfi.gob.bo/circular/Textos/L03T02.pdf"
ASFI_HOME_URL = "https://www.asfi.gob.bo/"


class GenerarReporte(BaseModel):
    tipo: Literal["CALIFICACION_CARTERA", "ESTADOS_FINANCIEROS", "CARTERA_DEUDORES"]
    fecha_corte: date


def _tenant(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES:
        raise HTTPException(403, "Operación reservada a ADMINISTRADOR o CONTADOR")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _money(value) -> str:
    return f"{Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def _effective_date():
    return func.coalesce(Credito.fecha_desembolso, cast(SolicitudCredito.fecha_resolucion_comite, Date),
                         cast(SolicitudCredito.fecha_solicitud, Date), cast(Credito.fecha_creacion, Date))


def _effective_currency():
    return func.coalesce(Credito.moneda_id, SolicitudCredito.moneda_id)


def _get_prevision_contable(db: Session, tenant: int, cutoff: date) -> dict[str, Decimal]:
    # Reuse CU-W32's ledger rolling logic so 139.xx subaccounts reconcile identically.
    from app.api.v1.endpoints.estados_financieros import _posted_lines, _rolled_accounts

    currencies = db.execute(select(Moneda.id).order_by(Moneda.id)).scalars().all()
    if not currencies:
        return {}
    rows = _posted_lines(db, tenant, date(cutoff.year, 1, 1), cutoff, currencies, ["1"], cumulative=True)
    amounts: dict[str, Decimal] = {}
    codes = dict(db.execute(select(Moneda.id, Moneda.codigo_iso).where(Moneda.id.in_(currencies))).all())
    for currency_id in currencies:
        conversion = {"mode": "BOB", "ids": [currency_id]}
        accounts = _rolled_accounts(db, [row for row in rows if row["moneda_id"] == currency_id], tenant,
                                    [currency_id], conversion)
        amount = sum((Decimal(item["amount"]) for item in accounts
                      if item["account"]["codigo"].startswith("139")), Decimal("0"))
        amounts[codes[currency_id]] = abs(amount).quantize(CENT)
    return amounts


@router.get("/calificacion-cartera")
def calificacion_cartera(fecha_corte: date = Query(...), user: Usuario = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    tenant = _tenant(user)
    if fecha_corte > date.today():
        raise HTTPException(422, "La fecha de corte no puede ser futura")
    credits = db.execute(select(Credito).join(SolicitudCredito, SolicitudCredito.id == Credito.solicitud_credito_id)
        .options(joinedload(Credito.socio), joinedload(Credito.producto), joinedload(Credito.moneda),
                 joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
                 joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
                 selectinload(Credito.cronograma),
                 joinedload(Credito.solicitud).selectinload(SolicitudCredito.garantias))
        .where(Credito.cooperativa_id == tenant, Credito.estado == "VIGENTE",
               _effective_date() <= fecha_corte).order_by(Credito.id)).unique().scalars().all()
    advertencias = _unclassified_credit_warnings(db, tenant, fecha_corte)
    rows = []
    totals: dict[tuple[str, str], dict] = {}
    reserve_totals: dict[str, Decimal] = {}
    cyclic_totals: dict[str, Decimal] = {}
    untyped = []
    for credit in credits:
        request = credit.solicitud
        product = credit.producto or request.producto
        tipo = product.tipo_credito_asfi if product else None
        currency = credit.moneda or request.moneda
        if currency is None:
            continue
        if tipo is None:
            untyped.append({"numero_credito": credit.numero_credito, "producto": product.nombre if product else None,
                            "motivo": "Producto sin tipo_credito_asfi; crédito excluido de la calificación."})
            continue
        rate, _grace = _configuracion_mora(credit)
        summary = resumir_morosidad(cuotas=credit.cronograma, fecha=fecha_corte,
                                    tasa_mora_anual=rate, dias_gracia_mora=0)
        days = summary["dias_de_retaso"] if summary["estado"] == "EN_MORA" else 0
        category = categoria_por_mora(tipo, days)
        balance = Decimal(str(credit.saldo_pendiente or 0))
        rate_pct = porcentaje_prevision_especifica(category, tipo, currency.codigo_iso,
                                                  bool(product.sector_productivo))
        mortgage = next((g for g in request.garantias if g.tipo == "HIPOTECARIA" and
                         g.estado == "VERIFICADA" and g.fecha_liberacion is None and
                         g.valor_comercial is not None), None)
        mortgage_applied = tipo == "VIVIENDA_HIPOTECARIA" and mortgage is not None
        base = base_prevision_hipotecaria(balance, Decimal(mortgage.valor_comercial)) if mortgage_applied else balance
        specific = monto_prevision(base, rate_pct)
        cyclic_pct = porcentaje_prevision_ciclica(category, tipo, currency.codigo_iso)
        cyclic = monto_prevision(balance, cyclic_pct)
        currency_code = currency.codigo_iso
        rows.append({
            "numero_credito": credit.numero_credito,
            "socio": {"ci": credit.socio.ci if credit.socio else None,
                      "nombre": f"{credit.socio.nombre} {credit.socio.apellido}" if credit.socio else None},
            "producto": product.nombre if product else None,
            "tipo_credito_asfi": tipo,
            "sector_productivo": bool(product.sector_productivo),
            "moneda": currency_code,
            "saldo_capital": _money(balance), "dias_mora": days, "categoria": category,
            "porcentaje": _money(rate_pct), "base_prevision": _money(base),
            "prevision_especifica": _money(specific), "prevision_ciclica": _money(cyclic),
            "garantia_hipotecaria_aplicada": mortgage_applied,
        })
        key = (category, currency_code)
        group = totals.setdefault(key, {"categoria": category, "moneda": currency_code,
            "creditos": 0, "saldo_capital": Decimal("0"), "prevision_especifica": Decimal("0"),
            "prevision_ciclica": Decimal("0")})
        group["creditos"] += 1
        group["saldo_capital"] += balance
        group["prevision_especifica"] += specific
        group["prevision_ciclica"] += cyclic
        reserve_totals[currency_code] = reserve_totals.get(currency_code, Decimal("0")) + specific
        cyclic_totals[currency_code] = cyclic_totals.get(currency_code, Decimal("0")) + cyclic
    if untyped:
        advertencias.append({"codigo": "PRODUCTOS_SIN_TIPO_ASFI", "creditos": untyped})
    contable = _get_prevision_contable(db, tenant, fecha_corte)
    currencies = set(reserve_totals) | set(cyclic_totals) | set(contable)
    difference = {code: _money(reserve_totals.get(code, Decimal("0")) + cyclic_totals.get(code, Decimal("0")) - contable.get(code, Decimal("0")))
                  for code in sorted(currencies)}
    return {
        "fecha_corte": fecha_corte.isoformat(), "creditos": rows,
        "por_categoria": [{key: _money(value) if isinstance(value, Decimal) else value
                           for key, value in group.items()} for group in totals.values()],
        "total_prevision_especifica": {code: _money(value) for code, value in sorted(reserve_totals.items())},
        "total_prevision_ciclica": {code: _money(value) for code, value in sorted(cyclic_totals.items())},
        "prevision_contable": {code: _money(value) for code, value in sorted(contable.items())},
        "diferencia": difference,
        "criterio": ("RNSF Libro 3° Título II Capítulo IV Sección 2 Arts. 7 y 8 y Sección 3 Arts. 1 y 8; "
                     "días desde la cuota impaga más antigua. Los créditos de consumo "
                     "usan la tabla de microcrédito Art. 8.1 como interpretación, porque Art. 8 agrupa ambos productos y "
                     "solo publica esa tabla. Previsión específica y cíclica requeridas; no incluye cronograma de constitución."),
        "advertencias": advertencias,
    }


def _validate_cutoff(cutoff: date):
    if cutoff > date.today():
        raise HTTPException(422, "La fecha de corte no puede ser futura")


def _financial_package(db: Session, tenant: int, cutoff: date):
    from app.api.v1.endpoints.estados_financieros import _balance_data, _income_data

    _validate_cutoff(cutoff)
    return {
        "fecha_corte": cutoff.isoformat(),
        "moneda": "BOB",
        "balance_general": _balance_data(db, tenant, cutoff, "CONSOLIDADO", 3),
        "estado_resultados": _income_data(db, tenant, date(cutoff.year, 1, 1), cutoff, "CONSOLIDADO"),
    }


def _debtors_data(db: Session, tenant: int, cutoff: date):
    _validate_cutoff(cutoff)
    credits = db.execute(select(Credito).join(SolicitudCredito, SolicitudCredito.id == Credito.solicitud_credito_id)
        .options(joinedload(Credito.socio), joinedload(Credito.producto), joinedload(Credito.moneda),
                 joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
                 joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
                 selectinload(Credito.cronograma),
                 joinedload(Credito.solicitud).selectinload(SolicitudCredito.garantias))
        .where(Credito.cooperativa_id == tenant, Credito.estado == "VIGENTE",
               _effective_date() <= cutoff).order_by(Credito.id)).unique().scalars().all()
    rows = []
    for credit in credits:
        request = credit.solicitud
        product = credit.producto or request.producto
        tipo = product.tipo_credito_asfi if product else None
        currency = credit.moneda or request.moneda
        rate, _grace = _configuracion_mora(credit)
        summary = resumir_morosidad(cuotas=credit.cronograma, fecha=cutoff,
                                    tasa_mora_anual=rate, dias_gracia_mora=0)
        days = summary["dias_de_retaso"] if summary["estado"] == "EN_MORA" else 0
        balance = Decimal(str(credit.saldo_pendiente or 0))
        category = reserve = None
        if tipo and currency:
            category = categoria_por_mora(tipo, days)
            pct = porcentaje_prevision_especifica(category, tipo, currency.codigo_iso,
                                                  bool(product.sector_productivo))
            guarantee = next((g for g in request.garantias if g.tipo == "HIPOTECARIA" and
                              g.estado == "VERIFICADA" and g.fecha_liberacion is None and
                              g.valor_comercial is not None), None)
            base = base_prevision_hipotecaria(balance, Decimal(guarantee.valor_comercial)) \
                if tipo == "VIVIENDA_HIPOTECARIA" and guarantee else balance
            reserve = _money(monto_prevision(base, pct))
        installments = credit.cronograma or []
        rows.append({
            "ci": credit.socio.ci if credit.socio else None,
            "nombre": f"{credit.socio.nombre} {credit.socio.apellido}" if credit.socio else None,
            "numero_credito": credit.numero_credito,
            "tipo_credito_asfi": tipo,
            "moneda": currency.codigo_iso if currency else None,
            "monto_desembolsado": _money(credit.monto_aprobado),
            "saldo": _money(balance),
            "fecha_desembolso": credit.fecha_desembolso.isoformat() if credit.fecha_desembolso else None,
            "fecha_vencimiento_final": max((i.fecha_vencimiento for i in installments), default=None).isoformat()
                if installments else None,
            "dias_mora": days,
            "categoria": category,
            "prevision": reserve,
        })
    return {"fecha_corte": cutoff.isoformat(),
            "etiqueta": "Base para reporte a la CIC — formato interno; el layout oficial SCIP/CIC no está publicado en fuente verificable",
            "cartera_deudores": rows,
            "advertencias": _unclassified_credit_warnings(db, tenant, cutoff)}


@router.get("/estados-financieros")
def estados_financieros(fecha_corte: date = Query(...), user: Usuario = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    return _financial_package(db, _tenant(user), fecha_corte)


@router.get("/cartera-deudores")
def cartera_deudores(fecha_corte: date = Query(...), user: Usuario = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    return _debtors_data(db, _tenant(user), fecha_corte)


def _payload(tipo: str, cutoff: date, user: Usuario, db: Session):
    if tipo == "CALIFICACION_CARTERA":
        return calificacion_cartera(cutoff, user, db)
    if tipo == "ESTADOS_FINANCIEROS":
        return _financial_package(db, _tenant(user), cutoff)
    return _debtors_data(db, _tenant(user), cutoff)


def _canonical_json(value) -> str:
    return json.dumps(jsonable_encoder(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@router.post("/generar", status_code=status.HTTP_201_CREATED)
def generar_reporte(body: GenerarReporte, request: Request, user: Usuario = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    tenant = _tenant(user)
    content = jsonable_encoder(_payload(body.tipo, body.fecha_corte, user, db))
    canonical = _canonical_json(content)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    previous = db.execute(select(Reporte).where(Reporte.cooperativa_id == tenant,
        Reporte.tipo == body.tipo, Reporte.periodo == body.fecha_corte, Reporte.estado == "GENERADO")).scalars().all()
    for report in previous:
        report.estado = "REEMPLAZADO"
    summary = {"fecha_corte": body.fecha_corte.isoformat(), "tipo": body.tipo}
    entity = Reporte(tipo=body.tipo, formato="JSON", parametros=json.dumps({"fecha_corte": body.fecha_corte.isoformat()}),
        usuario_id=user.id, cooperativa_id=tenant, periodo=body.fecha_corte, estado="GENERADO",
        contenido=content, resumen=summary, hash_contenido=digest)
    db.add(entity)
    registrar_accion(db, accion="GENERAR_INFORME_ASFI", modulo="INFORMES_ASFI", usuario_id=user.id,
        cooperativa_id=tenant, descripcion=f"Generó {body.tipo} al {body.fecha_corte}", request=request)
    db.commit()
    db.refresh(entity)
    return {"id": entity.id, "tipo": entity.tipo, "periodo": entity.periodo.isoformat(),
            "estado": entity.estado, "hash_contenido": entity.hash_contenido, "resumen": entity.resumen}


def _report_public(report: Reporte):
    return {"id": report.id, "tipo": report.tipo, "periodo": report.periodo.isoformat() if report.periodo else None,
        "estado": report.estado, "fecha_generacion": report.fecha_generacion.isoformat() if report.fecha_generacion else None,
        "hash_contenido": report.hash_contenido, "resumen": report.resumen}


@router.get("/reportes")
def listar_reportes(tipo: str | None = None, desde: date | None = None, hasta: date | None = None,
                    user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant(user)
    query = select(Reporte).where(Reporte.cooperativa_id == tenant)
    if tipo:
        query = query.where(Reporte.tipo == tipo)
    if desde:
        query = query.where(Reporte.periodo >= desde)
    if hasta:
        query = query.where(Reporte.periodo <= hasta)
    rows = db.execute(query.order_by(Reporte.periodo.desc(), Reporte.id.desc())).scalars().all()
    return [_report_public(report) for report in rows]


def _get_tenant_report(db: Session, tenant: int, report_id: int):
    report = db.execute(select(Reporte).where(Reporte.id == report_id,
                            Reporte.cooperativa_id == tenant)).scalar_one_or_none()
    if report is None:
        raise HTTPException(404, "Reporte no encontrado")
    return report


def _integrity(report: Reporte) -> bool:
    return bool(report.hash_contenido and hashlib.sha256(
        _canonical_json(report.contenido).encode("utf-8")).hexdigest() == report.hash_contenido)


@router.get("/reportes/{reporte_id}")
def obtener_reporte(reporte_id: int, user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    report = _get_tenant_report(db, _tenant(user), reporte_id)
    return {**_report_public(report), "contenido": report.contenido, "integridad_ok": _integrity(report)}


@router.get("/reportes/{reporte_id}/export")
def exportar_reporte(reporte_id: int, user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    report = _get_tenant_report(db, _tenant(user), reporte_id)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    payload = report.contenido if isinstance(report.contenido, dict) else {}
    if report.tipo == "CALIFICACION_CARTERA":
        headers = ["tipo_fila", "numero_credito", "socio_ci", "socio_nombre", "producto", "tipo_credito_asfi",
                   "sector_productivo", "moneda", "saldo_capital", "dias_mora", "categoria", "porcentaje",
                   "base_prevision", "prevision_especifica", "prevision_ciclica",
                   "garantia_hipotecaria_aplicada"]
        writer.writerow(headers)
        for row in payload.get("creditos", []):
            socio = row.get("socio") or {}
            writer.writerow(["CREDITO", row.get("numero_credito"), socio.get("ci"), socio.get("nombre"),
                             row.get("producto"), row.get("tipo_credito_asfi"), row.get("sector_productivo"),
                             row.get("moneda"), row.get("saldo_capital"), row.get("dias_mora"), row.get("categoria"),
                             row.get("porcentaje"), row.get("base_prevision"), row.get("prevision_especifica"),
                             row.get("prevision_ciclica"), row.get("garantia_hipotecaria_aplicada")])
        for row in payload.get("por_categoria", []):
            writer.writerow(["TOTAL", "", "", "", "", "", "", row.get("moneda"), row.get("saldo_capital"), "",
                             row.get("categoria"), "", "", row.get("prevision_especifica"),
                             row.get("prevision_ciclica"), ""])
    elif report.tipo == "CARTERA_DEUDORES":
        headers = ["ci", "nombre", "numero_credito", "tipo_credito_asfi", "moneda", "monto_desembolsado",
                   "saldo", "fecha_desembolso", "fecha_vencimiento_final", "dias_mora", "categoria", "prevision"]
        writer.writerow(headers)
        for row in payload.get("cartera_deudores", []):
            writer.writerow([row.get(key) for key in headers])
    elif report.tipo == "ESTADOS_FINANCIEROS":
        writer.writerow(["seccion", "codigo", "nombre", "monto"])
        for section, data in (("balance_general", payload.get("balance_general", {})),
                              ("estado_resultados", payload.get("estado_resultados", {}))):
            for code, item in (data.get("secciones", {}) or {}).items():
                for group in item.values():
                    writer.writerow([section, group.get("codigo", code), group.get("nombre"), group.get("monto")])
                    for account in group.get("cuentas", []):
                        writer.writerow([section, account.get("codigo"), account.get("nombre"), account.get("monto")])
            for line in data.get("lineas", []):
                for component in line.get("componentes", []):
                    writer.writerow([section, component.get("codigo"), component.get("nombre"), component.get("monto")])
                if line.get("subtotal") is not None:
                    writer.writerow([section, "", line.get("subtotal"), line.get("monto")])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="informe-asfi-{report.id}.csv"'})


@router.get("/catalogo")
def catalogo(user: Usuario = Depends(get_current_user)):
    _tenant(user)
    return [
        {"codigo": "CALIFICACION_CARTERA", "nombre": "Calificación y previsión de cartera", "implementado": True,
         "motivo": None, "fuente_url": ASFI_CREDIT_URL},
        {"codigo": "ESTADOS_FINANCIEROS", "nombre": "Estados financieros mensuales", "implementado": True,
         "motivo": None, "fuente_url": ASFI_CREDIT_URL},
        {"codigo": "CARTERA_DEUDORES_INTERNA", "nombre": "Base interna de cartera de deudores", "implementado": True,
         "motivo": "Formato interno; no sustituye el layout oficial SCIP/CIC.", "fuente_url": ASFI_HOME_URL},
        {"codigo": "CAP", "nombre": "Coeficiente de adecuación patrimonial", "implementado": False,
         "motivo": "Factores de ponderación de riesgo no verificados en fuente primaria.", "fuente_url": ASFI_HOME_URL},
        {"codigo": "ENCAJE_LEGAL", "nombre": "Encaje legal", "implementado": False,
         "motivo": "Base de cálculo y tasas vigentes no verificadas en texto primario.", "fuente_url": ASFI_HOME_URL},
        {"codigo": "LIMITES_CONCENTRACION", "nombre": "Límites de concentración", "implementado": False,
         "motivo": "Umbrales vigentes no verificados en fuente primaria.", "fuente_url": ASFI_HOME_URL},
        {"codigo": "CIC_OFICIAL", "nombre": "Reporte oficial SCIP/CIC", "implementado": False,
         "motivo": "El layout oficial SCIP/CIC no está publicado en una fuente verificable.", "fuente_url": ASFI_HOME_URL},
        {"codigo": "UIF", "nombre": "Reportes UIF", "implementado": False,
         "motivo": "Las declaraciones UIF están cubiertas por CU-W14.", "fuente_url": ASFI_HOME_URL},
    ]
