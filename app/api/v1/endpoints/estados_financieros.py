"""Exchange rates and financial statements over posted accounting vouchers."""

import csv
import io
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_db
from app.api.v1.endpoints.accounting_sql import nature_balance_delta_sql
from app.core.bitacora import registrar_accion
from app.models.models import TipoCambio, Usuario
from app.schemas.schemas import TipoCambioCreate, TipoCambioUpdate

router = APIRouter()
ROLES = {"CONTADOR", "ADMINISTRADOR"}
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _tenant(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES:
        raise HTTPException(403, "Operación reservada a CONTADOR o ADMINISTRADOR")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _money(value) -> str:
    return f"{Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def _rate_public(row):
    return {"id": row["id"], "cooperativa_id": row["cooperativa_id"],
            "moneda_id": row["moneda_id"], "moneda": row["codigo_iso"],
            "fecha": row["fecha"], "valor": f"{Decimal(row['valor']):.5f}",
            "fuente": row["fuente"], "usuario_id": row["usuario_id"],
            "fecha_registro": row["fecha_registro"]}


def _rate_rows(db, tenant, moneda_id=None, desde=None, hasta=None):
    where = ["tc.cooperativa_id=:tenant"]
    params = {"tenant": tenant}
    if moneda_id is not None:
        where.append("tc.moneda_id=:moneda_id")
        params["moneda_id"] = moneda_id
    if desde is not None:
        where.append("tc.fecha>=:desde")
        params["desde"] = desde
    if hasta is not None:
        where.append("tc.fecha<=:hasta")
        params["hasta"] = hasta
    return db.execute(text(f"""
        SELECT tc.*,m.codigo_iso FROM tipo_cambio tc JOIN moneda m ON m.id=tc.moneda_id
        WHERE {' AND '.join(where)} ORDER BY tc.fecha DESC,m.codigo_iso
    """), params).mappings().all()


@router.get("/tipos-cambio")
def list_exchange_rates(moneda_id: int | None = Query(None, ge=1), desde: date | None = None,
                        hasta: date | None = None, user: Usuario = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    tenant = _tenant(user)
    if desde and hasta and desde > hasta:
        raise HTTPException(422, "La fecha desde no puede ser posterior a hasta")
    if moneda_id is not None and db.execute(text("SELECT 1 FROM moneda WHERE id=:id"), {"id": moneda_id}).scalar_one_or_none() is None:
        raise HTTPException(422, "La moneda indicada no existe")
    return [_rate_public(row) for row in _rate_rows(db, tenant, moneda_id, desde, hasta)]


@router.post("/tipos-cambio", status_code=status.HTTP_201_CREATED)
def create_exchange_rate(payload: TipoCambioCreate, request: Request, user: Usuario = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    tenant = _tenant(user)
    if payload.fecha > date.today():
        raise HTTPException(422, "La fecha del tipo de cambio no puede ser futura")
    currency = db.execute(text("SELECT id,codigo_iso,es_moneda_base FROM moneda WHERE id=:id"),
                          {"id": payload.moneda_id}).mappings().one_or_none()
    if currency is None:
        raise HTTPException(422, "La moneda indicada no existe")
    if currency["es_moneda_base"]:
        raise HTTPException(422, "No se registra tipo de cambio para la moneda base")
    entity = TipoCambio(cooperativa_id=tenant, moneda_id=payload.moneda_id, fecha=payload.fecha,
                        valor=payload.valor, fuente=payload.fuente.strip(), usuario_id=user.id)
    db.add(entity)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Ya existe un tipo de cambio para esa moneda y fecha") from exc
    registrar_accion(db, accion="TIPO_CAMBIO_CREAR", modulo="CONTABILIDAD", usuario_id=user.id,
                     cooperativa_id=tenant, descripcion=f"Registró tipo de cambio {currency['codigo_iso']} {payload.fecha}", request=request)
    db.commit()
    return _rate_public(_rate_rows(db, tenant, payload.moneda_id, payload.fecha, payload.fecha)[0])


@router.put("/tipos-cambio/{rate_id}")
def update_exchange_rate(rate_id: int, payload: TipoCambioUpdate, request: Request,
                         user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant(user)
    entity = db.query(TipoCambio).filter_by(id=rate_id, cooperativa_id=tenant).with_for_update().one_or_none()
    if entity is None:
        raise HTTPException(404, "Tipo de cambio no encontrado")
    entity.valor = payload.valor
    if payload.fuente is not None:
        entity.fuente = payload.fuente.strip()
    registrar_accion(db, accion="TIPO_CAMBIO_ACTUALIZAR", modulo="CONTABILIDAD", usuario_id=user.id,
                     cooperativa_id=tenant, descripcion=f"Corrigió tipo de cambio {entity.moneda_id} {entity.fecha}", request=request)
    db.commit()
    return _rate_public(_rate_rows(db, tenant, entity.moneda_id, entity.fecha, entity.fecha)[0])


def _currency(db, tenant, mode, cutoff):
    if mode == "BOB":
        row = db.execute(text("SELECT id,codigo_iso FROM moneda WHERE es_moneda_base IS TRUE ORDER BY id LIMIT 1")).mappings().one_or_none()
        if row is None:
            raise HTTPException(422, "No está configurada la moneda base")
        return {"ids": [row["id"]], "rate": None, "mode": mode}
    if mode == "USD":
        row = db.execute(text("SELECT id,codigo_iso FROM moneda WHERE codigo_iso='USD'")).mappings().one_or_none()
        if row is None:
            raise HTTPException(422, "No está configurada la moneda USD")
        return {"ids": [row["id"]], "rate": None, "mode": mode}
    base = db.execute(text("SELECT id,codigo_iso FROM moneda WHERE es_moneda_base IS TRUE ORDER BY id LIMIT 1")).mappings().one_or_none()
    if base is None:
        raise HTTPException(422, "No está configurada la moneda base")
    rates = {}
    for curr in db.execute(text("SELECT id,codigo_iso FROM moneda WHERE id<>:base ORDER BY id"), {"base": base["id"]}).mappings():
        rate = db.execute(text("""SELECT valor,fecha FROM tipo_cambio WHERE cooperativa_id=:tenant
                                 AND moneda_id=:currency AND fecha<=:cutoff ORDER BY fecha DESC LIMIT 1"""),
                          {"tenant": tenant, "currency": curr["id"], "cutoff": cutoff}).mappings().one_or_none()
        if rate is None:
            raise HTTPException(422, f"Registre el tipo de cambio de {curr['codigo_iso']} vigente al {cutoff}")
        rates[curr["id"]] = {"valor": Decimal(rate["valor"]), "fecha": rate["fecha"], "codigo": curr["codigo_iso"]}
    return {"ids": [base["id"], *rates.keys()], "base_id": base["id"], "rates": rates,
            "rate": rates.get(next(iter(rates), None)), "mode": mode}


def _posted_lines(db, tenant, start, end, currency_ids, classes, *, cumulative=False):
    # Deliberately include all headers: ANULADO source vouchers and their linked
    # reversal both remain in the ledger and therefore cancel each other.
    balance_delta = nature_balance_delta_sql("pc.naturaleza", "SUM(d.debe)", "SUM(d.haber)")
    return db.execute(text(f"""
        SELECT pc.id account_id,pc.codigo,pc.nombre,pc.naturaleza,pc.es_regularizadora,
               pc.plan_cuenta_padre_id parent_id,pc.nivel,cc.moneda_id,
               SUM(d.debe) debe,SUM(d.haber) haber,{balance_delta} naturaleza_saldo
        FROM comprobante_contable cc JOIN detalle_asiento d ON d.comprobante_contable_id=cc.id
        JOIN plan_cuenta pc ON pc.id=d.plan_cuenta_id
        WHERE cc.cooperativa_id=:tenant AND cc.moneda_id=ANY(:currencies)
          AND cc.fecha_contable<=:end AND (:cumulative OR cc.fecha_contable>=:start)
          AND left(pc.codigo,1)=ANY(:classes)
        GROUP BY pc.id,pc.codigo,pc.nombre,pc.naturaleza,pc.es_regularizadora,
                 pc.plan_cuenta_padre_id,pc.nivel,cc.moneda_id
    """), {"tenant": tenant, "start": start, "end": end, "currencies": currency_ids,
           "classes": classes, "cumulative": cumulative}).mappings().all()


def _rolled_accounts(db, rows, tenant, currencies, conversion, *, group_by="section"):
    # Roll posting rows to the actual MCEF levels (2=group, 3=subaccount).
    rolled = {}
    for row in rows:
        amount = Decimal(row["naturaleza_saldo"] or 0)
        if row["es_regularizadora"]:
            amount = -abs(amount)
        if amount == 0:
            continue
        path = [row["account_id"]]
        parent_id = row["parent_id"]
        while parent_id:
            path.append(parent_id)
            parent_id = db.execute(text("SELECT plan_cuenta_padre_id FROM plan_cuenta WHERE id=:id AND (cooperativa_id IS NULL OR cooperativa_id=:tenant)"),
                                   {"id": parent_id, "tenant": tenant}).scalar_one_or_none()
        ancestor_rows = db.execute(text("SELECT id,codigo,nombre,nivel FROM plan_cuenta WHERE id=ANY(:ids)"), {"ids": path}).mappings().all()
        ancestors = {a["nivel"]: a for a in ancestor_rows}
        group, sub = ancestors.get(2), ancestors.get(3)
        if not group or not sub:
            continue
        key = (group["id"], sub["id"])
        entry = rolled.setdefault(key, {"group": group, "account": sub, "currency_amounts": {}})
        entry["currency_amounts"][row["moneda_id"]] = entry["currency_amounts"].get(row["moneda_id"], ZERO) + amount
    # Currency components belong to one displayed level-3 account line. Convert
    # and round that combined line once, rather than rounding each analytic leaf.
    for entry in rolled.values():
        amount = ZERO
        for currency_id, currency_amount in entry["currency_amounts"].items():
            if conversion["mode"] == "CONSOLIDADO" and currency_id != conversion["base_id"]:
                currency_amount *= conversion["rates"][currency_id]["valor"]
            amount += currency_amount
        entry["amount"] = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    return [entry for entry in rolled.values() if entry["amount"]]


def _conversion_meta(conversion):
    if conversion["mode"] != "CONSOLIDADO":
        return None
    rate = conversion.get("rate")
    return {"moneda": rate["codigo"] if rate else "BOB", "fecha": rate["fecha"] if rate else None,
            "valor": f"{rate['valor']:.5f}" if rate else "1.00000"}


def _balance_data(db, tenant, cutoff, mode, nivel):
    conv = _currency(db, tenant, mode, cutoff)
    rows = _posted_lines(db, tenant, date(cutoff.year, 1, 1), cutoff, conv["ids"], ["1", "2", "3", "4", "5"], cumulative=True)
    account_rows = _rolled_accounts(db, rows, tenant, conv["ids"], conv)
    sections = {"ACTIVO": {}, "PASIVO": {}, "PATRIMONIO": {}}
    totals = {"ACTIVO": ZERO, "PASIVO": ZERO, "PATRIMONIO": ZERO}
    for item in account_rows:
        cls = item["account"]["codigo"][0]
        if cls not in "123":
            continue
        section = {"1": "ACTIVO", "2": "PASIVO", "3": "PATRIMONIO"}[cls]
        group = item["group"]
        group_out = sections[section].setdefault(group["codigo"], {"codigo": group["codigo"], "nombre": group["nombre"], "monto": ZERO, "cuentas": {}})
        group_out["monto"] += item["amount"]
        group_out["cuentas"].setdefault(item["account"]["codigo"], {"codigo": item["account"]["codigo"], "nombre": item["account"]["nombre"], "monto": ZERO})["monto"] += item["amount"]
        totals[section] += item["amount"]
    # Current-year operating income less expenses belongs to equity until closing entries exist.
    pl_rows = _posted_lines(db, tenant, date(cutoff.year, 1, 1), cutoff, conv["ids"], ["4", "5"])
    pl_accounts = _rolled_accounts(db, pl_rows, tenant, conv["ids"], conv)
    result = sum((item["amount"] * (Decimal(-1) if item["account"]["codigo"].startswith("4") else Decimal(1))
                  for item in pl_accounts), ZERO).quantize(CENT, rounding=ROUND_HALF_UP)
    if result:
        sections["PATRIMONIO"]["resultado_gestion"] = {"codigo": "RESULTADO_GESTION", "nombre": "Resultado de la gestión", "monto": result, "cuentas": {}}
        totals["PATRIMONIO"] += result
    for values in sections.values():
        for group in values.values():
            group["monto"] = _money(group["monto"])
            group["cuentas"] = [dict(a, monto=_money(a["monto"])) for a in group["cuentas"].values() if a["monto"]]
            if nivel == 2:
                group["cuentas"] = []
            if not group["cuentas"] and group["codigo"] != "RESULTADO_GESTION":
                group["_zero"] = True
        for code in [code for code, group in values.items() if group.get("_zero")]:
            del values[code]
    asset = totals["ACTIVO"].quantize(CENT, rounding=ROUND_HALF_UP)
    liability = totals["PASIVO"].quantize(CENT, rounding=ROUND_HALF_UP)
    equity = totals["PATRIMONIO"].quantize(CENT, rounding=ROUND_HALF_UP)
    return {"fecha_corte": cutoff, "moneda": mode, "nivel": nivel,
            "criterio_conversion": "BOB más USD convertido al tipo vigente a la fecha de corte; redondeo HALF_UP por cuenta y suma posterior." if mode == "CONSOLIDADO" else "Importes expresados en la moneda seleccionada, sin conversión.",
            "secciones": sections, "resultado_gestion": _money(result), "total_activo": _money(asset),
            "total_pasivo": _money(liability), "total_patrimonio": _money(equity),
            "cuadra": asset == liability + equity, "tipo_cambio": _conversion_meta(conv)}


@router.get("/balance-general")
def balance_general(fecha_corte: date = Query(...), nivel: int = Query(3, ge=2, le=3),
                    moneda: str = Query("CONSOLIDADO", pattern="^(CONSOLIDADO|BOB|USD)$"),
                    user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return _balance_data(db, _tenant(user), fecha_corte, moneda, nivel)


RESULT_GROUPS = [
    ("510", "Ingresos financieros", 1), ("410", "Gastos financieros", -1),
    ("540", "Otros ingresos operativos", 1), ("440", "Otros gastos operativos", -1),
    ("530", "Recuperaciones de activos financieros", 1), ("430", "Cargos por incobrabilidad y desvalorización", -1),
    ("450", "Gastos de administración", -1), ("520", "Abonos por diferencia de cambio", 1),
    ("420", "Cargos por diferencia de cambio", -1), ("570", "Ingresos extraordinarios", 1),
    ("470", "Gastos extraordinarios", -1), ("580", "Ingresos de gestiones anteriores", 1),
    ("480", "Gastos de gestiones anteriores", -1), ("590", "Abonos por ajuste por inflación", 1),
    ("490", "Cargos por ajuste por inflación", -1), ("460", "Impuesto sobre las utilidades de las empresas", -1),
]
SUBTOTALS = [
    "Resultado financiero bruto", "Resultado de operación bruto", "Resultado de operación después de incobrables",
    "Resultado de operación neto", "Resultado después de ajuste por diferencia de cambio y mantenimiento de valor",
    "Resultado neto del ejercicio antes de ajustes de gestiones anteriores",
    "Resultado antes de impuestos y ajuste contable por efecto de la inflación", "Resultado antes de impuestos",
    "Resultado neto de la gestión",
]


def _income_data(db, tenant, desde, hasta, mode):
    if desde.year != hasta.year:
        raise HTTPException(422, "Las fechas desde y hasta deben pertenecer a la misma gestión")
    if desde > hasta:
        raise HTTPException(422, "La fecha desde no puede ser posterior a hasta")
    conv = _currency(db, tenant, mode, hasta)
    rows = _posted_lines(db, tenant, desde, hasta, conv["ids"], ["4", "5"])
    account_rows = _rolled_accounts(db, rows, tenant, conv["ids"], conv)
    group_map = {}
    for row in account_rows:
        # MCEF group is level 2, while detail is the level-3 account.
        code = row["group"]["codigo"]
        group_map.setdefault(code, {"codigo": code, "nombre": row["group"]["nombre"], "cuentas": {}, "monto": ZERO})
        group = group_map[code]
        group["cuentas"].setdefault(row["account"]["codigo"], {"codigo": row["account"]["codigo"], "nombre": row["account"]["nombre"], "monto": ZERO})["monto"] += row["amount"]
    for group in group_map.values():
        group["monto"] = sum((a["monto"] for a in group["cuentas"].values()), ZERO)
    lines = []
    running = ZERO
    index = 0
    # Keep the prescribed nine subtotal steps; map code roots to actual groups.
    steps = [(("510", 1), ("410", -1)), (("540", 1), ("440", -1)),
             (("530", 1), ("430", -1)), (("450", -1),), (("520", 1), ("420", -1)),
             (("570", 1), ("470", -1)), (("580", 1), ("480", -1)),
             (("590", 1), ("490", -1)), (("460", -1),)]
    for step, subtotal_name in zip(steps, SUBTOTALS):
        components = []
        for root, sign in step:
            matched = [g for code, g in group_map.items() if code.startswith(root)]
            amount = sum((g["monto"] for g in matched), ZERO)
            for group in matched:
                components.append({"codigo": group["codigo"], "nombre": group["nombre"],
                                   "monto": _money(group["monto"] * sign),
                                   "cuentas": [dict(a, monto=_money(a["monto"] * sign)) for a in group["cuentas"].values() if a["monto"]]})
            running += amount * sign
        index += 1
        lines.append({"componentes": components, "subtotal": subtotal_name, "monto": _money(running)})
    return {"desde": desde, "hasta": hasta, "moneda": mode, "formato": "MCEF grupos (Título II)",
            "criterio_conversion": "Conversión simplificada al tipo de cambio vigente en la fecha hasta; redondeo HALF_UP por cuenta y suma posterior." if mode == "CONSOLIDADO" else "Importes expresados en la moneda seleccionada, sin conversión.",
            "lineas": lines, "resultado_neto_gestion": _money(running), "tipo_cambio": _conversion_meta(conv)}


@router.get("/estado-resultados")
def income_statement(desde: date = Query(...), hasta: date = Query(...),
                     moneda: str = Query("CONSOLIDADO", pattern="^(CONSOLIDADO|BOB|USD)$"),
                     user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return _income_data(db, _tenant(user), desde, hasta, moneda)


def _csv(body, filename, headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/balance-general/export")
def export_balance(fecha_corte: date = Query(...), nivel: int = Query(3, ge=2, le=3),
                   moneda: str = Query("CONSOLIDADO", pattern="^(CONSOLIDADO|BOB|USD)$"),
                   user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    body = _balance_data(db, _tenant(user), fecha_corte, moneda, nivel)
    rows = []
    for section, groups in body["secciones"].items():
        for group in groups.values():
            rows.append([section, group["codigo"], group["nombre"], group["monto"]])
            if nivel >= 3:
                rows.extend([[section, account["codigo"], account["nombre"], account["monto"]] for account in group["cuentas"]])
    return _csv(body, f"balance-general-{fecha_corte.isoformat()}.csv", ["Sección", "Código", "Cuenta", "Saldo"], rows)


@router.get("/estado-resultados/export")
def export_income_statement(desde: date = Query(...), hasta: date = Query(...),
                            moneda: str = Query("CONSOLIDADO", pattern="^(CONSOLIDADO|BOB|USD)$"),
                            user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    body = _income_data(db, _tenant(user), desde, hasta, moneda)
    rows = []
    for line in body["lineas"]:
        rows.extend([[group["codigo"], group["nombre"], group["monto"]] for group in line["componentes"]])
        rows.append(["", line["subtotal"], line["monto"]])
    return _csv(body, f"estado-resultados-{desde.isoformat()}-{hasta.isoformat()}.csv", ["Código", "Concepto", "Monto"], rows)
