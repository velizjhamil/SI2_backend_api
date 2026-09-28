"""Read-only accounting books over manual and automatic vouchers."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_db
from app.models.models import Usuario

router = APIRouter()
ROLES = {"CONTADOR", "ADMINISTRADOR"}
ZERO = Decimal("0.00")


def _money(value):
    return f"{Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _context(user, db, desde, hasta, moneda_id):
    if user.rol is None or user.rol.nombre not in ROLES:
        raise HTTPException(403, "Operación reservada a CONTADOR o ADMINISTRADOR")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    today = date.today()
    desde = desde or today.replace(day=1)
    hasta = hasta or today
    if desde > hasta:
        raise HTTPException(422, "La fecha desde no puede ser posterior a hasta")
    if hasta - desde > timedelta(days=366):
        raise HTTPException(422, "El rango de fechas no puede exceder 366 días")
    if db.execute(text("SELECT 1 FROM moneda WHERE id=:id"), {"id": moneda_id}).scalar_one_or_none() is None:
        raise HTTPException(422, "La moneda indicada no existe")
    return user.cooperativa_id, desde, hasta


def _voucher_lines(db, voucher_ids):
    if not voucher_ids:
        return {}
    rows = db.execute(text("""
        SELECT d.comprobante_contable_id, d.orden, pc.codigo, pc.nombre,
               d.glosa, d.debe, d.haber, d.id
        FROM detalle_asiento d JOIN plan_cuenta pc ON pc.id=d.plan_cuenta_id
        WHERE d.comprobante_contable_id = ANY(:ids)
        ORDER BY d.comprobante_contable_id, COALESCE(d.orden,2147483647),d.id
    """), {"ids": voucher_ids}).mappings().all()
    result = {}
    for r in rows:
        result.setdefault(r["comprobante_contable_id"], []).append({
            "orden": r["orden"], "cuenta": {"codigo": r["codigo"], "nombre": r["nombre"]},
            "glosa": r["glosa"], "debe": _money(r["debe"]), "haber": _money(r["haber"]),
        })
    return result


def _diario(db, tenant, desde, hasta, moneda_id, limit=None, offset=0):
    where = "c.cooperativa_id=:tenant AND c.fecha_contable BETWEEN :desde AND :hasta AND c.moneda_id=:moneda"
    params = {"tenant": tenant, "desde": desde, "hasta": hasta, "moneda": moneda_id}
    total = db.execute(text(f"SELECT count(*) FROM comprobante_contable c WHERE {where}"), params).scalar_one()
    sums = db.execute(text(f"""
        SELECT COALESCE(sum(d.debe),0) debe, COALESCE(sum(d.haber),0) haber
        FROM comprobante_contable c LEFT JOIN detalle_asiento d ON d.comprobante_contable_id=c.id
        WHERE {where}
    """), params).mappings().one()
    paging = " LIMIT :lim OFFSET :off" if limit is not None else ""
    if limit is not None:
        params.update(lim=limit, off=offset)
    rows = db.execute(text(f"""
        SELECT c.id,c.numero,c.tipo,c.fecha_contable,c.glosa,c.origen,c.estado,
               c.revierte_a_id,c.comprobante_reversion_id,
               old.numero AS revierte_a_numero, rev.numero AS anulado_por_numero
        FROM comprobante_contable c
        LEFT JOIN comprobante_contable old ON old.id=c.revierte_a_id AND old.cooperativa_id=c.cooperativa_id
        LEFT JOIN comprobante_contable rev ON rev.id=c.comprobante_reversion_id AND rev.cooperativa_id=c.cooperativa_id
        WHERE {where} ORDER BY c.fecha_contable,c.numero{paging}
    """), params).mappings().all()
    line_map = _voucher_lines(db, [r["id"] for r in rows])
    items = [{
        "id": r["id"], "numero": r["numero"], "tipo": r["tipo"], "fecha_contable": r["fecha_contable"],
        "glosa": r["glosa"], "origen": r["origen"], "estado": r["estado"],
        "revierte_a_numero": r["revierte_a_numero"], "anulado_por_numero": r["anulado_por_numero"],
        "lineas": line_map.get(r["id"], []),
    } for r in rows]
    return {"items": items, "total": total, "totales_periodo": {"debe": _money(sums["debe"]), "haber": _money(sums["haber"])}}


@router.get("/libro-diario")
def libro_diario(desde: date | None = None, hasta: date | None = None, moneda_id: int = Query(1, ge=1),
                 limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                 user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant, desde, hasta = _context(user, db, desde, hasta, moneda_id)
    return _diario(db, tenant, desde, hasta, moneda_id, limit, offset)


def _account(db, tenant, account_id):
    row = db.execute(text("""
        SELECT id,codigo,nombre,nivel,naturaleza,es_regularizadora,acepta_movimientos,cooperativa_id
        FROM plan_cuenta WHERE id=:id AND (cooperativa_id=:tenant OR cooperativa_id IS NULL)
    """), {"id": account_id, "tenant": tenant}).mappings().one_or_none()
    if row is None or (row["cooperativa_id"] is None and row["nivel"] == 5):
        raise HTTPException(404, "Cuenta no encontrada")
    return row


def _book_major(db, tenant, cuenta_id, desde, hasta, moneda_id, include):
    account = _account(db, tenant, cuenta_id)
    children = db.execute(text("""
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM plan_cuenta WHERE id=:id
            UNION ALL SELECT pc.id FROM plan_cuenta pc JOIN descendants d ON pc.plan_cuenta_padre_id=d.id
            WHERE pc.cooperativa_id=:tenant OR pc.cooperativa_id IS NULL
        ) SELECT pc.id FROM plan_cuenta pc JOIN descendants d ON d.id=pc.id
          WHERE pc.acepta_movimientos AND pc.id<>:id
    """), {"id": cuenta_id, "tenant": tenant}).scalars().all()
    child_posting = list(children)
    has_descendants = bool(child_posting)
    if has_descendants and not include:
        raise HTTPException(422, "La cuenta no acepta movimientos; use incluir_subcuentas")
    account_ids = child_posting if has_descendants else ([cuenta_id] if account["acepta_movimientos"] else [])
    if not account_ids:
        raise HTTPException(422, "La cuenta no tiene cuentas de movimiento")
    rows = db.execute(text("""
        WITH scoped AS (
            SELECT c.id voucher_id,c.numero,c.tipo,c.estado,c.fecha_contable fecha,
                   COALESCE(d.orden,2147483647) orden,d.id detail_id,pc.codigo,d.glosa,d.debe,d.haber,
                   CASE WHEN :naturaleza='DEUDORA' THEN d.debe-d.haber ELSE d.haber-d.debe END delta
            FROM comprobante_contable c JOIN detalle_asiento d ON d.comprobante_contable_id=c.id
            JOIN plan_cuenta pc ON pc.id=d.plan_cuenta_id
            WHERE c.cooperativa_id=:tenant AND c.moneda_id=:moneda AND c.fecha_contable<=:hasta
              AND d.plan_cuenta_id=ANY(:accounts)
        ), opening AS (
            SELECT COALESCE(SUM(delta) FILTER (WHERE fecha<:desde),0) saldo_inicial FROM scoped
        ), period AS (
            SELECT scoped.*,
                   SUM(delta) OVER (ORDER BY fecha,numero,orden,detail_id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) saldo_periodo,
                   SUM(debe) OVER () total_debe,SUM(haber) OVER () total_haber
            FROM scoped WHERE fecha>=:desde
        )
        SELECT p.*,o.saldo_inicial,o.saldo_inicial+p.saldo_periodo saldo,
               COALESCE(p.total_debe,0) total_debe,COALESCE(p.total_haber,0) total_haber
        FROM opening o LEFT JOIN period p ON TRUE
        ORDER BY p.fecha,p.numero,p.orden,p.detail_id
    """), {"naturaleza": account["naturaleza"], "tenant": tenant, "moneda": moneda_id,
           "hasta": hasta, "desde": desde, "accounts": account_ids}).mappings().all()
    opening = Decimal(rows[0]["saldo_inicial"]) if rows else ZERO
    sum_debit = Decimal(rows[0]["total_debe"]) if rows else ZERO
    sum_credit = Decimal(rows[0]["total_haber"]) if rows else ZERO
    movements = []
    running = opening
    for row in rows:
        if row["voucher_id"] is None:
            continue
        debit, credit = Decimal(row["debe"]), Decimal(row["haber"])
        running = Decimal(row["saldo"])
        movements.append({
            "fecha": row["fecha"], "comprobante": {"id": row["voucher_id"], "numero": row["numero"], "tipo": row["tipo"], "estado": row["estado"]},
            "cuenta_codigo": row["codigo"], "glosa": row["glosa"], "debe": _money(debit), "haber": _money(credit), "saldo": _money(running),
        })
    return {"cuenta": {k: account[k] for k in ("id","codigo","nombre","nivel","naturaleza","es_regularizadora")},
            "saldo_inicial": _money(opening), "movimientos": movements,
            "totales": {"debe": _money(sum_debit), "haber": _money(sum_credit)}, "saldo_final": _money(running)}


@router.get("/libro-mayor")
def libro_mayor(cuenta_id: int = Query(..., ge=1), desde: date | None = None, hasta: date | None = None,
                moneda_id: int = Query(1, ge=1), incluir_subcuentas: bool = True,
                user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant, desde, hasta = _context(user, db, desde, hasta, moneda_id)
    return _book_major(db, tenant, cuenta_id, desde, hasta, moneda_id, incluir_subcuentas)


@router.get("/balance-comprobacion")
def balance_comprobacion(desde: date | None = None, hasta: date | None = None, moneda_id: int = Query(1, ge=1),
                         nivel: int = Query(4, ge=1, le=5), user: Usuario = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    tenant, desde, hasta = _context(user, db, desde, hasta, moneda_id)
    rows = db.execute(text("""
        WITH RECURSIVE accounts AS (
            SELECT id,codigo,nombre,naturaleza,nivel,plan_cuenta_padre_id
            FROM plan_cuenta WHERE cooperativa_id=:tenant OR cooperativa_id IS NULL
        ), movements AS (
            SELECT pc.id account_id,c.fecha_contable,d.debe,d.haber
            FROM comprobante_contable c JOIN detalle_asiento d ON d.comprobante_contable_id=c.id
            JOIN plan_cuenta pc ON pc.id=d.plan_cuenta_id
            WHERE c.cooperativa_id=:tenant AND c.moneda_id=:moneda AND c.fecha_contable<=:hasta
        ), source_accounts AS (
            SELECT DISTINCT a.id,a.plan_cuenta_padre_id,a.nivel
            FROM accounts a JOIN movements m ON m.account_id=a.id
        ), paths(source_id,target_id,parent_id,target_level) AS (
            SELECT id,id,plan_cuenta_padre_id,nivel FROM source_accounts
            UNION ALL
            SELECT p.source_id,a.id,a.plan_cuenta_padre_id,a.nivel
            FROM paths p JOIN accounts a ON a.id=p.parent_id WHERE p.target_level>:nivel
        ), rolled AS (
            SELECT p.target_id,
                   SUM(CASE WHEN m.fecha_contable BETWEEN :desde AND :hasta THEN m.debe ELSE 0 END) sum_debe,
                   SUM(CASE WHEN m.fecha_contable BETWEEN :desde AND :hasta THEN m.haber ELSE 0 END) sum_haber,
                   SUM(m.debe) acumulado_debe,SUM(m.haber) acumulado_haber
            FROM paths p JOIN movements m ON m.account_id=p.source_id
            WHERE NOT (p.target_level>:nivel AND p.parent_id IS NOT NULL)
            GROUP BY p.target_id
        ), classified AS (
            SELECT a.codigo,a.nombre,a.naturaleza,r.sum_debe,r.sum_haber,
                   CASE WHEN a.naturaleza='DEUDORA' THEN r.acumulado_debe-r.acumulado_haber
                        ELSE r.acumulado_haber-r.acumulado_debe END naturaleza_saldo
            FROM rolled r JOIN accounts a ON a.id=r.target_id
        ), balances AS (
            SELECT codigo,nombre,naturaleza,sum_debe,sum_haber,
                   CASE WHEN naturaleza='DEUDORA' THEN GREATEST(naturaleza_saldo,0)
                        ELSE GREATEST(-naturaleza_saldo,0) END saldo_deudor,
                   CASE WHEN naturaleza='ACREEDORA' THEN GREATEST(naturaleza_saldo,0)
                        ELSE GREATEST(-naturaleza_saldo,0) END saldo_acreedor
            FROM classified
        ), totals AS (
            SELECT COALESCE(SUM(sum_debe),0) sumas_debe,COALESCE(SUM(sum_haber),0) sumas_haber,
                   COALESCE(SUM(saldo_deudor),0) saldo_deudor,COALESCE(SUM(saldo_acreedor),0) saldo_acreedor
            FROM balances
        )
        SELECT b.*,t.sumas_debe,t.sumas_haber,t.saldo_deudor total_saldo_deudor,
               t.saldo_acreedor total_saldo_acreedor
        FROM balances b CROSS JOIN totals t ORDER BY b.codigo
    """), {"tenant": tenant, "moneda": moneda_id, "hasta": hasta, "desde": desde, "nivel": nivel}).mappings().all()
    output = [{"codigo": row["codigo"], "nombre": row["nombre"], "naturaleza": row["naturaleza"],
               "sumas": {"debe": _money(row["sum_debe"]), "haber": _money(row["sum_haber"])},
               "saldos": {"deudor": _money(row["saldo_deudor"]), "acreedor": _money(row["saldo_acreedor"])}}
              for row in rows]
    totals = {
        "sumas_debe": _money(rows[0]["sumas_debe"] if rows else ZERO),
        "sumas_haber": _money(rows[0]["sumas_haber"] if rows else ZERO),
        "saldo_deudor": _money(rows[0]["total_saldo_deudor"] if rows else ZERO),
        "saldo_acreedor": _money(rows[0]["total_saldo_acreedor"] if rows else ZERO),
    }
    cuadra = (Decimal(totals["sumas_debe"]) == Decimal(totals["sumas_haber"])
              and Decimal(totals["saldo_deudor"]) == Decimal(totals["saldo_acreedor"]))
    return {"criterio": "Sumas del período; saldos acumulados hasta la fecha hasta.", "desde": desde, "hasta": hasta,
            "nivel": nivel, "items": output, "totales": totals, "cuadra": cuadra}


def _csv_response(filename, headers, records):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(records)
    content = "\ufeff" + output.getvalue()
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _export_limit(db, tenant, desde, hasta, moneda_id, account_ids=None):
    params = {"tenant": tenant, "desde": desde, "hasta": hasta, "moneda": moneda_id}
    extra = ""
    if account_ids is not None:
        extra = " AND d.plan_cuenta_id=ANY(:accounts)"
        params["accounts"] = account_ids
    count = db.execute(text(f"""
        SELECT count(*) FROM comprobante_contable c JOIN detalle_asiento d ON d.comprobante_contable_id=c.id
        WHERE c.cooperativa_id=:tenant AND c.moneda_id=:moneda AND c.fecha_contable BETWEEN :desde AND :hasta{extra}
    """), params).scalar_one()
    if count > 20000:
        raise HTTPException(422, "La exportación excede el máximo de 20000 filas")


def _enforce_csv_record_limit(records):
    if len(records) > 20000:
        raise HTTPException(422, "La exportación excede el máximo de 20000 filas")


@router.get("/libro-diario/export")
def export_diario(desde: date | None = None, hasta: date | None = None, moneda_id: int = Query(1, ge=1),
                  user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant, desde, hasta = _context(user, db, desde, hasta, moneda_id)
    _export_limit(db, tenant, desde, hasta, moneda_id)
    data = _diario(db, tenant, desde, hasta, moneda_id, None)
    records = []
    for voucher in data["items"]:
        for line in voucher["lineas"]:
            records.append([voucher["fecha_contable"], voucher["numero"], voucher["tipo"], voucher["estado"],
                            line["orden"], line["cuenta"]["codigo"], line["cuenta"]["nombre"], line["glosa"],
                            line["debe"], line["haber"]])
    _enforce_csv_record_limit(records)
    return _csv_response(f"libro-diario-{desde}-{hasta}.csv",
                         ["fecha","numero","tipo","estado","orden","cuenta_codigo","cuenta_nombre","glosa","debe","haber"], records)


@router.get("/libro-mayor/export")
def export_mayor(cuenta_id: int = Query(..., ge=1), desde: date | None = None, hasta: date | None = None,
                 moneda_id: int = Query(1, ge=1), incluir_subcuentas: bool = True,
                 user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant, desde, hasta = _context(user, db, desde, hasta, moneda_id)
    account = _account(db, tenant, cuenta_id)
    descendants = db.execute(text("""
        WITH RECURSIVE d(id) AS (SELECT id FROM plan_cuenta WHERE id=:id UNION ALL
        SELECT p.id FROM plan_cuenta p JOIN d ON p.plan_cuenta_padre_id=d.id
        WHERE p.cooperativa_id=:tenant OR p.cooperativa_id IS NULL)
        SELECT p.id FROM plan_cuenta p JOIN d ON d.id=p.id WHERE p.acepta_movimientos AND p.id<>:id
    """), {"id": cuenta_id, "tenant": tenant}).scalars().all()
    if descendants and not incluir_subcuentas:
        raise HTTPException(422, "La cuenta no acepta movimientos; use incluir_subcuentas")
    ids = descendants or ([cuenta_id] if account["acepta_movimientos"] else [])
    if not ids:
        raise HTTPException(422, "La cuenta no tiene cuentas de movimiento")
    _export_limit(db, tenant, desde, hasta, moneda_id, ids)
    data = _book_major(db, tenant, cuenta_id, desde, hasta, moneda_id, incluir_subcuentas)
    records = [[m["fecha"],m["comprobante"]["numero"],m["comprobante"]["tipo"],m["comprobante"]["estado"],
                m["cuenta_codigo"],m["glosa"],m["debe"],m["haber"],m["saldo"]] for m in data["movimientos"]]
    _enforce_csv_record_limit(records)
    return _csv_response(f"libro-mayor-{account['codigo']}-{desde}-{hasta}.csv",
                         ["fecha","numero","tipo","estado","cuenta_codigo","glosa","debe","haber","saldo"], records)


@router.get("/balance-comprobacion/export")
def export_balance(desde: date | None = None, hasta: date | None = None, moneda_id: int = Query(1, ge=1),
                   nivel: int = Query(4, ge=1, le=5), user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant, desde, hasta = _context(user, db, desde, hasta, moneda_id)
    data = balance_comprobacion(desde, hasta, moneda_id, nivel, user, db)
    records = [[x["codigo"],x["nombre"],x["naturaleza"],x["sumas"]["debe"],x["sumas"]["haber"],
                x["saldos"]["deudor"],x["saldos"]["acreedor"]] for x in data["items"]]
    _enforce_csv_record_limit(records)
    return _csv_response(f"balance-comprobacion-{desde}-{hasta}.csv",
                         ["codigo","nombre","naturaleza","sumas_debe","sumas_haber","saldo_deudor","saldo_acreedor"], records)
