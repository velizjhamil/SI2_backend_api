"""Tenant-isolated integration coverage for CU-W30 manual vouchers."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    from main import app
except Exception:
    app = None

pytestmark = pytest.mark.skipif(app is None, reason="PostgreSQL unavailable")


@pytest.fixture
def voucher_case():
    suffix = uuid4().hex[:10]
    ids = {"coops": [], "users": [], "accounts": []}
    tokens = []
    try:
        with SessionLocal() as db:
            roles = {
                name: db.query(models.Rol).filter_by(nombre=name).one()
                for name in ("CONTADOR", "ADMINISTRADOR", "SOCIO")
            }
            parents = {
                code: db.query(models.PlanCuenta).filter_by(codigo=code, cooperativa_id=None).one()
                for code in ("111.00", "111.01", "212.01")
            }
            for index in range(2):
                coop = models.Cooperativa(nombre=f"Voucher test {suffix}-{index}", estado="ACTIVO")
                db.add(coop)
                db.flush()
                ids["coops"].append(coop.id)
                role_name = "CONTADOR" if index == 0 else "ADMINISTRADOR"
                user = models.Usuario(
                    correo=f"voucher-{suffix}-{index}@test.invalid",
                    contrasena=hash_password("Password123"),
                    rol_id=roles[role_name].id,
                    cooperativa_id=coop.id,
                    nombre="Voucher test",
                    estado="ACTIVO",
                )
                db.add(user)
                db.flush()
                ids["users"].append(user.id)
                for code in ("111.01", "212.01"):
                    parent = parents[code]
                    account = models.PlanCuenta(
                        codigo=f"{code}.{index + 1:02d}",
                        nombre=f"Analytic {code} {suffix}-{index}",
                        nivel=5,
                        tipo=parent.tipo,
                        naturaleza=parent.naturaleza,
                        es_regularizadora=False,
                        es_oficial=False,
                        cooperativa_id=coop.id,
                        estado="ACTIVA",
                        acepta_movimientos=True,
                        plan_cuenta_padre_id=parent.id,
                    )
                    db.add(account)
                    db.flush()
                    ids["accounts"].append(account.id)
                token, _ = create_access_token(str(user.id), role_name, coop.id)
                tokens.append(token)

            denied = models.Usuario(
                correo=f"voucher-denied-{suffix}@test.invalid",
                contrasena=hash_password("Password123"),
                rol_id=roles["SOCIO"].id,
                cooperativa_id=ids["coops"][0],
                nombre="Voucher denied",
                estado="ACTIVO",
            )
            db.add(denied)
            db.flush()
            ids["users"].append(denied.id)
            token, _ = create_access_token(str(denied.id), "SOCIO", ids["coops"][0])
            tokens.append(token)
            ids["official"] = {code: parent.id for code, parent in parents.items()}
            db.commit()
        yield {"ids": ids, "tokens": tokens, "today": date.today().isoformat()}
    finally:
        with SessionLocal() as db:
            if ids["coops"]:
                db.execute(text("DELETE FROM tipo_cambio WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM detalle_asiento WHERE comprobante_contable_id IN (SELECT id FROM comprobante_contable WHERE cooperativa_id = ANY(:coops))"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM comprobante_contable WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("UPDATE dpf_cronograma SET transaccion_id=NULL WHERE deposito_plazo_fijo_id IN (SELECT id FROM deposito_plazo_fijo WHERE cooperativa_id=ANY(:coops))"), {"coops":ids["coops"]})
                db.execute(text("UPDATE pago_cuota SET transaccion_id=NULL WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM transaccion WHERE control_caja_id IN (SELECT cc.id FROM control_caja cc JOIN caja c ON c.id=cc.caja_id WHERE c.cooperativa_id = ANY(:coops)) OR cuenta_ahorro_id IN (SELECT a.id FROM cuenta_ahorro a JOIN socio s ON s.id=a.socio_id WHERE s.cooperativa_id = ANY(:coops)) OR pago_cuota_id IN (SELECT id FROM pago_cuota WHERE cooperativa_id=ANY(:coops)) OR credito_id IN (SELECT id FROM credito WHERE cooperativa_id=ANY(:coops)) OR deposito_plazo_fijo_id IN (SELECT id FROM deposito_plazo_fijo WHERE cooperativa_id=ANY(:coops)) OR liquidacion_id IN (SELECT l.id FROM liquidacion l JOIN deposito_plazo_fijo d ON d.id=l.deposito_plazo_fijo_id WHERE d.cooperativa_id=ANY(:coops))"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM pago_cuota WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM tabla_amortizacion WHERE credito_id IN (SELECT id FROM credito WHERE cooperativa_id = ANY(:coops))"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM credito WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM solicitud_credito WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM dpf_cronograma WHERE deposito_plazo_fijo_id IN (SELECT id FROM deposito_plazo_fijo WHERE cooperativa_id=ANY(:coops))"), {"coops":ids["coops"]})
                db.execute(text("DELETE FROM liquidacion WHERE deposito_plazo_fijo_id IN (SELECT id FROM deposito_plazo_fijo WHERE cooperativa_id=ANY(:coops))"), {"coops":ids["coops"]})
                db.execute(text("DELETE FROM deposito_plazo_fijo WHERE cooperativa_id=ANY(:coops)"), {"coops":ids["coops"]})
                db.execute(text("DELETE FROM control_caja WHERE caja_id IN (SELECT id FROM caja WHERE cooperativa_id = ANY(:coops))"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM caja WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM cuenta_ahorro WHERE socio_id IN (SELECT id FROM socio WHERE cooperativa_id = ANY(:coops))"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM socio WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM detalle_asiento WHERE comprobante_contable_id IN (SELECT id FROM comprobante_contable WHERE cooperativa_id = ANY(:coops))"), {"coops": ids["coops"]})
                db.execute(text("UPDATE comprobante_contable SET comprobante_reversion_id=NULL, revierte_a_id=NULL WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM comprobante_contable WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
                db.execute(text("DELETE FROM parametro_contable WHERE cooperativa_id = ANY(:coops)"), {"coops": ids["coops"]})
            if ids["users"]:
                db.execute(text("DELETE FROM bitacora WHERE usuario_id = ANY(:users)"), {"users": ids["users"]})
                db.execute(text("DELETE FROM usuario WHERE id = ANY(:users)"), {"users": ids["users"]})
            if ids["accounts"]:
                db.execute(text("DELETE FROM plan_cuenta WHERE id = ANY(:accounts)"), {"accounts": ids["accounts"]})
            if ids["coops"]:
                db.execute(text("DELETE FROM cooperativa WHERE id = ANY(:coops)"), {"coops": ids["coops"]})
            db.commit()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def manual_payload(case, *, tipo="TRASPASO", glosa="Manual test voucher", amount="10.00", fecha=None):
    return {
        "tipo": tipo,
        "fecha_contable": fecha or case["today"],
        "glosa": glosa,
        "moneda_id": 1,
        "lineas": [
            {"plan_cuenta_id": case["ids"]["accounts"][0], "debe": amount, "haber": "0"},
            {"plan_cuenta_id": case["ids"]["accounts"][1], "debe": "0", "haber": amount},
        ],
    }


def test_create_rounds_half_up_and_list_detail_are_tenant_scoped_and_audited(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/contabilidad/comprobantes",
            json=manual_payload(case, amount="10.005"),
            headers=auth(case["tokens"][0]),
        )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["numero"] == "T-2026-000001"
    assert body["total_debe"] == "10.01"
    assert body["total_haber"] == "10.01"
    with TestClient(app) as client:
        listed = client.get("/api/v1/contabilidad/comprobantes?origen=MANUAL&q=Manual", headers=auth(case["tokens"][0]))
        detail = client.get(f"/api/v1/contabilidad/comprobantes/{body['id']}", headers=auth(case["tokens"][0]))
        foreign_detail = client.get(f"/api/v1/contabilidad/comprobantes/{body['id']}", headers=auth(case["tokens"][1]))
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == body["id"]
    assert listed.json()["items"][0]["total_debe"] == listed.json()["items"][0]["total_haber"] == "10.01"
    assert detail.status_code == 200, detail.text
    assert detail.json()["lineas"][0]["debe"] == "10.01"
    assert detail.json()["lineas"][1]["haber"] == "10.01"
    assert detail.json()["total_debe"] == detail.json()["total_haber"] == "10.01"
    assert isinstance(body["total_debe"], str)
    assert isinstance(detail.json()["lineas"][0]["debe"], str)
    assert foreign_detail.status_code == 404
    with SessionLocal() as db:
        audit = db.query(models.Bitacora).filter_by(
            usuario_id=case["ids"]["users"][0], modulo="CONTABILIDAD", accion="COMPROBANTE_CREAR_MANUAL"
        ).one_or_none()
        assert audit is not None
        assert audit.cooperativa_id == case["ids"]["coops"][0]


def test_libro_diario_returns_page_and_range_totals(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/contabilidad/comprobantes",
            json=manual_payload(case, amount="13.25"),
            headers=auth(case["tokens"][0]),
        )
        assert created.status_code == 201, created.text
        response = client.get(
            f"/api/v1/contabilidad/libro-diario?desde={case['today']}&hasta={case['today']}&limit=1&offset=0",
            headers=auth(case["tokens"][0]),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["totales_periodo"] == {"debe": "13.25", "haber": "13.25"}
    assert len(body["items"]) == 1
    assert body["items"][0]["numero"] == created.json()["numero"]
    assert body["items"][0]["lineas"][0]["cuenta"]["codigo"]


def test_libro_diario_rejects_invalid_range(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/contabilidad/libro-diario?desde={case['today']}&hasta=2020-01-01",
            headers=auth(case["tokens"][0]),
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "La fecha desde no puede ser posterior a hasta"


@pytest.mark.parametrize(
    "query,detail",
    [
        ("desde=2024-01-01&hasta=2025-01-02", "El rango de fechas no puede exceder 366 días"),
        ("moneda_id=999", "La moneda indicada no existe"),
    ],
)
def test_libro_diario_validates_period_and_currency(voucher_case, query, detail):
    case = voucher_case
    with TestClient(app) as client:
        response = client.get(f"/api/v1/contabilidad/libro-diario?{query}",
                              headers=auth(case["tokens"][0]))
    assert response.status_code == 422
    assert response.json()["detail"] == detail


def test_libro_diario_and_mayor_are_tenant_scoped(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case, amount="8.00"),
                              headers=auth(case["tokens"][0]))
        foreign_daily = client.get(
            f"/api/v1/contabilidad/libro-diario?desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][1]))
        foreign_account = client.get(
            f"/api/v1/contabilidad/libro-mayor?cuenta_id={case['ids']['accounts'][0]}&desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][1]))
    assert created.status_code == 201
    assert foreign_daily.status_code == 200
    assert foreign_daily.json()["total"] == 0
    assert foreign_account.status_code == 404


def test_libro_diario_isolates_currency(voucher_case):
    case = voucher_case
    bob = manual_payload(case, amount="11.00")
    usd = manual_payload(case, amount="23.00")
    usd["moneda_id"] = 2
    with TestClient(app) as client:
        first = client.post("/api/v1/contabilidad/comprobantes", json=bob, headers=auth(case["tokens"][0]))
        second = client.post("/api/v1/contabilidad/comprobantes", json=usd, headers=auth(case["tokens"][0]))
        bob_book = client.get(
            f"/api/v1/contabilidad/libro-diario?desde={case['today']}&hasta={case['today']}&moneda_id=1",
            headers=auth(case["tokens"][0]))
        usd_book = client.get(
            f"/api/v1/contabilidad/libro-diario?desde={case['today']}&hasta={case['today']}&moneda_id=2",
            headers=auth(case["tokens"][0]))
    assert first.status_code == second.status_code == 201
    assert bob_book.json()["total"] == usd_book.json()["total"] == 1
    assert bob_book.json()["items"][0]["id"] == first.json()["id"]
    assert usd_book.json()["items"][0]["id"] == second.json()["id"]


def test_major_and_trial_balance_aggregate_in_sql(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes",
                              json=manual_payload(case, amount="19.00"),
                              headers=auth(case["tokens"][0]))
        assert created.status_code == 201, created.text
        captured = []
        def record_sql(conn, cursor, statement, parameters, context, executemany):
            normalized = " ".join(statement.lower().split())
            if "comprobante_contable" in normalized and "detalle_asiento" in normalized:
                captured.append(normalized)
        event.listen(engine, "before_cursor_execute", record_sql)
        try:
            mayor = client.get(
                f"/api/v1/contabilidad/libro-mayor?cuenta_id={case['ids']['accounts'][0]}&desde={case['today']}&hasta={case['today']}",
                headers=auth(case["tokens"][0]))
            balance = client.get(
                f"/api/v1/contabilidad/balance-comprobacion?desde={case['today']}&hasta={case['today']}",
                headers=auth(case["tokens"][0]))
        finally:
            event.remove(engine, "before_cursor_execute", record_sql)
    assert mayor.status_code == balance.status_code == 200
    assert mayor.json()["saldo_final"] == "19.00"
    assert balance.json()["cuadra"] is True
    assert any("sum(" in sql and "over (" in sql for sql in captured), captured
    assert any("sum(" in sql and "group by" in sql for sql in captured), captured


def test_libro_mayor_running_balance_and_title_account_rollup(voucher_case):
    case = voucher_case
    previous_day = (date.fromisoformat(case["today"]) - timedelta(days=1)).isoformat()
    with TestClient(app) as client:
        opening = client.post("/api/v1/contabilidad/comprobantes",
                              json=manual_payload(case, amount="12.60", fecha=previous_day),
                              headers=auth(case["tokens"][0]))
        created = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case, amount="27.40"),
                              headers=auth(case["tokens"][0]))
        assert opening.status_code == 201, opening.text
        assert created.status_code == 201, created.text
        posting = client.get(
            f"/api/v1/contabilidad/libro-mayor?cuenta_id={case['ids']['accounts'][0]}&desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][0]))
        title = client.get(
            f"/api/v1/contabilidad/libro-mayor?cuenta_id={case['ids']['official']['111.01']}&desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][0]))
    assert posting.status_code == title.status_code == 200, (posting.text, title.text)
    assert posting.json()["saldo_inicial"] == "12.60"
    assert posting.json()["movimientos"][0]["saldo"] == posting.json()["saldo_final"] == "40.00"
    assert posting.json()["totales"] == {"debe": "27.40", "haber": "0.00"}
    assert title.json()["saldo_final"] == "40.00"
    assert len(title.json()["movimientos"]) == 1


def test_balance_comprobacion_is_balanced_and_exports_csv_with_bom(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case, amount="30.00"),
                              headers=auth(case["tokens"][0]))
        assert created.status_code == 201, created.text
        response = client.get(
            f"/api/v1/contabilidad/balance-comprobacion?desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][0]))
        exported = client.get(
            f"/api/v1/contabilidad/libro-diario/export?desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][0]))
    assert response.status_code == 200, response.text
    assert response.json()["cuadra"] is True, response.json()
    assert response.json()["totales"]["sumas_debe"] == response.json()["totales"]["sumas_haber"] == "30.00"
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert f"libro-diario-{case['today']}-{case['today']}.csv" in exported.headers["content-disposition"]
    assert b"30.00" in exported.content


def test_libro_diario_keeps_voided_voucher_and_reversal_net_zero(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case, amount="17.00"),
                              headers=auth(case["tokens"][0]))
        original_id = created.json()["id"]
        reversal = client.post(f"/api/v1/contabilidad/comprobantes/{original_id}/anular",
                               json={"motivo": "Test reversal"}, headers=auth(case["tokens"][0]))
        daily = client.get(
            f"/api/v1/contabilidad/libro-diario?desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][0]))
        major = client.get(
            f"/api/v1/contabilidad/libro-mayor?cuenta_id={case['ids']['accounts'][0]}&desde={case['today']}&hasta={case['today']}",
            headers=auth(case["tokens"][0]))
    assert reversal.status_code == 201, reversal.text
    assert daily.status_code == 200
    entries = daily.json()["items"]
    original = next(row for row in entries if row["id"] == original_id)
    inverse = next(row for row in entries if row["id"] == reversal.json()["id"])
    assert original["estado"] == "ANULADO"
    assert original["anulado_por_numero"] == inverse["numero"]
    assert inverse["revierte_a_numero"] == original["numero"]
    assert daily.json()["totales_periodo"] == {"debe": "34.00", "haber": "34.00"}
    assert major.status_code == 200
    assert major.json()["saldo_final"] == "0.00"


@pytest.mark.parametrize(
    "mutate,expected_detail",
    [
        (lambda p: p.update(lineas=p["lineas"][:1]), "al menos dos líneas"),
        (lambda p: p["lineas"][0].update(debe="0", haber="0"), "exactamente uno de debe o haber"),
        (lambda p: p["lineas"][1].update(haber="9.99"), "deben ser iguales"),
        (lambda p: p.update(glosa="  abc  "), "glosa debe contener al menos cinco caracteres"),
        (lambda p: p.update(fecha_contable="2099-01-01"), "fecha contable no puede ser futura"),
        (lambda p: p.update(moneda_id=999999), "la moneda seleccionada no existe"),
    ],
)
def test_create_rejects_invalid_header_or_unbalanced_lines(voucher_case, mutate, expected_detail):
    payload = manual_payload(voucher_case)
    mutate(payload)
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/comprobantes", json=payload, headers=auth(voucher_case["tokens"][0]))
    assert response.status_code == 422, response.text
    assert expected_detail.lower() in response.text.lower()


def test_body_validation_message_does_not_include_pydantic_prefix(voucher_case):
    payload = manual_payload(voucher_case)
    payload["glosa"] = "  "
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/comprobantes", json=payload, headers=auth(voucher_case["tokens"][0]))
    assert response.status_code == 422
    assert "Value error, " not in response.json()["detail"]
    assert "glosa debe contener al menos cinco caracteres" in response.json()["detail"].lower()


def test_void_response_amounts_are_fixed_decimal_strings(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case), headers=auth(case["tokens"][0]))
        reversal = client.post(
            f"/api/v1/contabilidad/comprobantes/{created.json()['id']}/anular",
            json={"motivo": "Error de digitación"}, headers=auth(case["tokens"][0]),
        )
    assert reversal.status_code == 201
    assert reversal.json()["total_debe"] == reversal.json()["total_haber"] == "10.00"
    assert all(isinstance(line["debe"], str) and isinstance(line["haber"], str) for line in reversal.json()["lineas"])


def test_create_rejects_nonmovement_inactive_foreign_and_analytic_parent_accounts(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        official_nonmovement = db.get(models.PlanCuenta, case["ids"]["official"]["111.00"])
        inactive = models.PlanCuenta(
            codigo=f"111.01.90.{uuid4().hex[:2]}", nombre="Inactive test", nivel=5, tipo="ACTIVO",
            naturaleza="DEUDORA", es_regularizadora=False, es_oficial=False,
            cooperativa_id=case["ids"]["coops"][0], estado="INACTIVA", acepta_movimientos=True,
            plan_cuenta_padre_id=official_nonmovement.id,
        )
        db.add(inactive)
        db.commit()
        inactive_id = inactive.id
    try:
        cases = []
        payload = manual_payload(case)
        payload["lineas"][0]["plan_cuenta_id"] = case["ids"]["official"]["111.01"]  # has tenant analytic descendants
        cases.append((payload, 422, "cuenta analítica"))
        payload = manual_payload(case)
        payload["lineas"][0]["plan_cuenta_id"] = case["ids"]["official"]["111.00"]  # official parent, not postable
        cases.append((payload, 422, "no acepta movimientos"))
        payload = manual_payload(case)
        payload["lineas"][0]["plan_cuenta_id"] = inactive_id
        cases.append((payload, 422, "no está activa"))
        payload = manual_payload(case)
        payload["lineas"][0]["plan_cuenta_id"] = case["ids"]["accounts"][2]
        cases.append((payload, 404, "cuenta contable no encontrada"))
        for payload, expected_status, detail in cases:
            with TestClient(app) as client:
                response = client.post("/api/v1/contabilidad/comprobantes", json=payload, headers=auth(case["tokens"][0]))
            assert response.status_code == expected_status, response.text
            assert detail.lower() in response.text.lower()
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM plan_cuenta WHERE id=:id"), {"id": inactive_id})
            db.commit()


def test_create_requires_accounting_role_and_generates_correlative_numbers_by_type(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        denied = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case), headers=auth(case["tokens"][2]))
        first = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case), headers=auth(case["tokens"][0]))
        second = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case), headers=auth(case["tokens"][0]))
        other_type = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case, tipo="INGRESO"), headers=auth(case["tokens"][0]))
    assert denied.status_code == 403
    assert first.json()["numero"] == "T-2026-000001"
    assert second.json()["numero"] == "T-2026-000002"
    assert other_type.json()["numero"] == "I-2026-000001"


def test_void_creates_linked_reversal_with_swapped_lines_and_audit(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes", json=manual_payload(case), headers=auth(case["tokens"][0]))
        voucher_id = created.json()["id"]
        voided = client.post(
            f"/api/v1/contabilidad/comprobantes/{voucher_id}/anular",
            json={"motivo": "Error de digitación"},
            headers=auth(case["tokens"][0]),
        )
        repeated = client.post(
            f"/api/v1/contabilidad/comprobantes/{voucher_id}/anular",
            json={"motivo": "Segundo intento"},
            headers=auth(case["tokens"][0]),
        )
    assert created.status_code == 201, created.text
    assert voided.status_code == 201, voided.text
    assert repeated.status_code == 409
    reversal = voided.json()
    assert reversal["numero"] == "T-2026-000002"
    assert reversal["revierte_a_id"] == voucher_id
    assert reversal["total_debe"] == reversal["total_haber"] == "10.00"
    with TestClient(app) as client:
        original = client.get(f"/api/v1/contabilidad/comprobantes/{voucher_id}", headers=auth(case["tokens"][0]))
        reversed_detail = client.get(f"/api/v1/contabilidad/comprobantes/{reversal['id']}", headers=auth(case["tokens"][0]))
    assert original.json()["estado"] == "ANULADO"
    assert original.json()["comprobante_reversion_id"] == reversal["id"]
    assert original.json()["motivo_anulacion"] == "Error de digitación"
    assert reversed_detail.json()["revierte_a_id"] == voucher_id
    assert reversed_detail.json()["lineas"][0]["haber"] == "10.00"
    assert reversed_detail.json()["lineas"][0]["debe"] == "0.00"


def test_automatic_generation_reports_pending_sources_and_is_idempotent(voucher_case):
    case = voucher_case
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/contabilidad/comprobantes/generar-automaticos",
            json={}, headers=auth(case["tokens"][0]),
        )
        second = client.post(
            "/api/v1/contabilidad/comprobantes/generar-automaticos",
            json={}, headers=auth(case["tokens"][0]),
        )
    assert first.status_code == 200, first.text
    assert first.json() == {"procesadas": 0, "generados": 0, "omitidas": [], "por_tipo": {}}
    assert second.status_code == 200, second.text
    assert second.json() == {"procesadas": 0, "generados": 0, "omitidas": [], "por_tipo": {}}
    with SessionLocal() as db:
        audits = db.query(models.Bitacora).filter_by(
            usuario_id=case["ids"]["users"][0], modulo="CONTABILIDAD", accion="COMPROBANTES_GENERAR_AUTOMATICOS"
        ).count()
        assert audits == 2


def test_automatic_generation_posts_cash_deposit_once_with_balanced_currency_lines(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        coop_id = case["ids"]["coops"][0]
        for key, account_id in (("CAJA", case["ids"]["accounts"][0]), ("AHORRO_VISTA", case["ids"]["accounts"][1])):
            db.add(models.ParametroContable(cooperativa_id=coop_id, clave=key, plan_cuenta_id=account_id))
        user_id = case["ids"]["users"][0]
        socio_id = db.execute(text("INSERT INTO socio(ci,nombre,apellido,cooperativa_id) VALUES (:ci,'Test','Member',:coop) RETURNING id"), {"ci":f"T{uuid4().hex[:15]}","coop":coop_id}).scalar_one()
        cuenta_id = db.execute(text("INSERT INTO cuenta_ahorro(numero,fecha_registro,socio_id,moneda_id,tipo_producto) VALUES (:num,CURRENT_DATE,:socio,1,'VISTA') RETURNING id"), {"num":f"TEST-{uuid4().hex[:8]}","socio":socio_id}).scalar_one()
        caja_id = db.execute(text("INSERT INTO caja(nombre,cooperativa_id) VALUES ('Test till',:coop) RETURNING id"), {"coop":coop_id}).scalar_one()
        control_id = db.execute(text("INSERT INTO control_caja(monto_apertura,saldo_sistema,fecha_apertura,caja_id,usuario_id) VALUES (0,0,CURRENT_TIMESTAMP,:caja,:user) RETURNING id"), {"caja":caja_id,"user":user_id}).scalar_one()
        trans_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,control_caja_id,moneda_id,cuenta_ahorro_id) VALUES ('DEPOSITO',25.50,'VENTANILLA',:control,1,:cuenta) RETURNING id"), {"control":control_id,"cuenta":cuenta_id}).scalar_one()
        withdrawal_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,control_caja_id,moneda_id,cuenta_ahorro_id) VALUES ('RETIRO',5,'VENTANILLA',:control,1,:cuenta) RETURNING id"), {"control":control_id,"cuenta":cuenta_id}).scalar_one()
        opening_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id) VALUES ('APERTURA',10,'WEB',1,:cuenta) RETURNING id"), {"cuenta":cuenta_id}).scalar_one()
        db.commit()
        ids = {"socio":socio_id,"cuenta":cuenta_id,"caja":caja_id,"control":control_id,"trans":trans_id,"withdrawal":withdrawal_id,"opening":opening_id}
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/contabilidad/comprobantes/generar-automaticos", json={}, headers=auth(case["tokens"][0]))
            rerun = client.post("/api/v1/contabilidad/comprobantes/generar-automaticos", json={}, headers=auth(case["tokens"][0]))
        assert response.status_code == 200, response.text
        assert response.json()["generados"] == 2
        assert response.json()["omitidas"] == [{"transaccion_id": ids["opening"], "tipo": "APERTURA", "motivo": "Movimiento de cuenta sin fuente de caja explícita; no se infiere una contrapartida"}]
        assert rerun.json()["generados"] == 0
        with SessionLocal() as db:
            voucher = db.query(models.ComprobanteContable).filter_by(transaccion_id=ids["trans"], origen="AUTOMATICO").one()
            lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=voucher.id).order_by(models.DetalleAsiento.orden).all()
            assert voucher.tipo == "INGRESO"
            assert voucher.moneda_id == 1
            assert [line.plan_cuenta_id for line in lines] == case["ids"]["accounts"][:2]
            assert [(line.debe, line.haber) for line in lines] == [(25.50, 0), (0, 25.50)]
            withdrawal = db.query(models.ComprobanteContable).filter_by(transaccion_id=ids["withdrawal"]).one()
            withdrawal_lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=withdrawal.id).order_by(models.DetalleAsiento.orden).all()
            assert withdrawal.tipo == "EGRESO"
            assert [(line.debe,line.haber) for line in withdrawal_lines] == [(5,0),(0,5)]
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM detalle_asiento WHERE comprobante_contable_id IN (SELECT id FROM comprobante_contable WHERE transaccion_id = ANY(:ids))"), {"ids":[ids["trans"],ids["withdrawal"]]})
            db.execute(text("DELETE FROM comprobante_contable WHERE transaccion_id = ANY(:ids)"), {"ids":[ids["trans"],ids["withdrawal"]]})
            db.execute(text("DELETE FROM transaccion WHERE id = ANY(:ids)"), {"ids":[ids["trans"],ids["withdrawal"],ids["opening"]]})
            db.execute(text("DELETE FROM control_caja WHERE id=:id"), {"id":ids["control"]})
            db.execute(text("DELETE FROM caja WHERE id=:id"), {"id":ids["caja"]})
            db.execute(text("DELETE FROM cuenta_ahorro WHERE id=:id"), {"id":ids["cuenta"]})
            db.execute(text("DELETE FROM socio WHERE id=:id"), {"id":ids["socio"]})
            db.commit()


def test_automatic_generation_posts_transfer_once_from_reciprocal_pair(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        coop_id, user_id = case["ids"]["coops"][0], case["ids"]["users"][0]
        db.add(models.ParametroContable(cooperativa_id=coop_id, clave="CAJA", plan_cuenta_id=case["ids"]["accounts"][0]))
        db.add(models.ParametroContable(cooperativa_id=coop_id, clave="AHORRO_VISTA", plan_cuenta_id=case["ids"]["accounts"][1]))
        socio_id = db.execute(text("INSERT INTO socio(ci,nombre,apellido,cooperativa_id) VALUES (:ci,'Transfer','Member',:coop) RETURNING id"), {"ci":f"T{uuid4().hex[:15]}","coop":coop_id}).scalar_one()
        ids = []
        for index in range(2):
            ids.append(db.execute(text("INSERT INTO cuenta_ahorro(numero,fecha_registro,socio_id,moneda_id,tipo_producto) VALUES (:num,CURRENT_DATE,:socio,1,'VISTA') RETURNING id"), {"num":f"TR-{uuid4().hex[:8]}","socio":socio_id}).scalar_one())
        salida = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id) VALUES ('TRANSFERENCIA_SALIDA',12.34,'WEB',1,:account) RETURNING id"), {"account":ids[0]}).scalar_one()
        entrada = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,transaccion_contraparte_id) VALUES ('TRANSFERENCIA_ENTRADA',12.34,'WEB',1,:account,:salida) RETURNING id"), {"account":ids[1],"salida":salida}).scalar_one()
        db.execute(text("UPDATE transaccion SET transaccion_contraparte_id=:entrada WHERE id=:salida"), {"entrada":entrada,"salida":salida})
        db.commit()
    with TestClient(app) as client:
        result = client.post("/api/v1/contabilidad/comprobantes/generar-automaticos", json={}, headers=auth(case["tokens"][0]))
    assert result.status_code == 200, result.text
    assert result.json()["generados"] == 1
    assert result.json()["por_tipo"] == {"TRASPASO":1}
    assert result.json()["omitidas"] == [{"transaccion_id":entrada,"tipo":"TRANSFERENCIA_ENTRADA","motivo":"Entrada de transferencia cubierta por la salida contraparte; no se duplica"}]


def test_automatic_generation_uses_persisted_payment_components(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        coop_id, user_id = case["ids"]["coops"][0], case["ids"]["users"][0]
        source_keys = (("CAJA",None),("AHORRO_VISTA",None),("CARTERA_VIGENTE","131.05"),("INTERES_CARTERA","513.05"),("INTERES_PENAL","515.03"))
        for key, code in source_keys:
            account_id = case["ids"]["accounts"][0] if key == "CAJA" else case["ids"]["accounts"][1] if key == "AHORRO_VISTA" else db.query(models.PlanCuenta).filter_by(codigo=code, cooperativa_id=None).one().id
            db.add(models.ParametroContable(cooperativa_id=coop_id, clave=key, plan_cuenta_id=account_id))
        socio_id = db.execute(text("INSERT INTO socio(ci,nombre,apellido,cooperativa_id) VALUES (:ci,'Pay','Member',:coop) RETURNING id"), {"ci":f"P{uuid4().hex[:15]}","coop":coop_id}).scalar_one()
        solicitud_id = db.execute(text("INSERT INTO solicitud_credito(monto,plazo_meses,tasa_interes,socio_id,usuario_id,cooperativa_id,moneda_id) VALUES (1000,12,10,:socio,:user,:coop,1) RETURNING id"), {"socio":socio_id,"user":user_id,"coop":coop_id}).scalar_one()
        credito_id = db.execute(text("INSERT INTO credito(monto_aprobado,saldo_pendiente,solicitud_credito_id,cooperativa_id,socio_id,moneda_id) VALUES (1000,900,:solicitud,:coop,:socio,1) RETURNING id"), {"solicitud":solicitud_id,"coop":coop_id,"socio":socio_id}).scalar_one()
        cuota_id = db.execute(text("INSERT INTO tabla_amortizacion(numero_cuota,fecha_vencimiento,monto_capital,monto_interes,monto_cuota_total,credito_id) VALUES (1,CURRENT_DATE,100,8,108,:credito) RETURNING id"), {"credito":credito_id}).scalar_one()
        pago_id = db.execute(text("INSERT INTO pago_cuota(monto_capital,monto_interes_pagado,monto_mora,tabla_amortizacion_id,credito_id,cooperativa_id,modalidad,monto_total,usuario_id) VALUES (100,8,2,:cuota,:credito,:coop,'EFECTIVO',110,:user) RETURNING id"), {"cuota":cuota_id,"credito":credito_id,"coop":coop_id,"user":user_id}).scalar_one()
        caja_id = db.execute(text("INSERT INTO caja(nombre,cooperativa_id) VALUES ('Payment till',:coop) RETURNING id"), {"coop":coop_id}).scalar_one()
        control_id = db.execute(text("INSERT INTO control_caja(monto_apertura,saldo_sistema,fecha_apertura,caja_id,usuario_id) VALUES (0,0,CURRENT_TIMESTAMP,:caja,:user) RETURNING id"), {"caja":caja_id,"user":user_id}).scalar_one()
        trans_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,control_caja_id,moneda_id,pago_cuota_id) VALUES ('PAGO_CUOTA',110,'VENTANILLA',:control,1,:pago) RETURNING id"), {"control":control_id,"pago":pago_id}).scalar_one()
        account_id = db.execute(text("INSERT INTO cuenta_ahorro(numero,fecha_registro,socio_id,moneda_id,tipo_producto) VALUES (:num,CURRENT_DATE,:socio,1,'VISTA') RETURNING id"), {"num":f"LOAN-{uuid4().hex[:8]}","socio":socio_id}).scalar_one()
        disbursement_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,credito_id) VALUES ('DESEMBOLSO_CREDITO',50,'WEB',1,:account,:credito) RETURNING id"), {"account":account_id,"credito":credito_id}).scalar_one()
        cash_disbursement_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,control_caja_id,moneda_id,credito_id) VALUES ('RETIRO',40,'VENTANILLA',:control,1,:credito) RETURNING id"), {"control":control_id,"credito":credito_id}).scalar_one()
        db.execute(text("UPDATE pago_cuota SET transaccion_id=:trans WHERE id=:pago"), {"trans":trans_id,"pago":pago_id})
        db.commit()
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/comprobantes/generar-automaticos", json={}, headers=auth(case["tokens"][0]))
    assert response.status_code == 200, response.text
    assert response.json()["generados"] == 3, response.json()
    with SessionLocal() as db:
        voucher = db.query(models.ComprobanteContable).filter_by(transaccion_id=trans_id).one()
        lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=voucher.id).order_by(models.DetalleAsiento.orden).all()
        assert voucher.tipo == "INGRESO"
        assert sum(line.debe for line in lines) == sum(line.haber for line in lines) == 110
        assert [(line.debe,line.haber) for line in lines] == [(110,0),(0,100),(0,8),(0,2)]
        disbursement = db.query(models.ComprobanteContable).filter_by(transaccion_id=disbursement_id).one()
        disb_lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=disbursement.id).order_by(models.DetalleAsiento.orden).all()
        assert [(line.debe,line.haber) for line in disb_lines] == [(50,0),(0,50)]
        cash_disbursement = db.query(models.ComprobanteContable).filter_by(transaccion_id=cash_disbursement_id).one()
        cash_lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=cash_disbursement.id).order_by(models.DetalleAsiento.orden).all()
        assert cash_disbursement.tipo == "EGRESO"
        assert [(line.debe,line.haber) for line in cash_lines] == [(40,0),(0,40)]


def test_automatic_generation_uses_persisted_dpf_cronograma_gross_tax_and_net(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        coop_id, user_id = case["ids"]["coops"][0], case["ids"]["users"][0]
        for key, code, account_id in (("AHORRO_VISTA",None,case["ids"]["accounts"][1]),("CAJA",None,case["ids"]["accounts"][0]),("INTERES_DPF","411.04",None),("RETENCION_RCIVA","242.03",None),("DPF_31_60","213.02",None)):
            if account_id is None:
                account_id = db.query(models.PlanCuenta).filter_by(codigo=code,cooperativa_id=None).one().id
            db.add(models.ParametroContable(cooperativa_id=coop_id,clave=key,plan_cuenta_id=account_id))
        socio_id = db.execute(text("INSERT INTO socio(ci,nombre,apellido,cooperativa_id) VALUES (:ci,'DPF','Member',:coop) RETURNING id"), {"ci":f"D{uuid4().hex[:15]}","coop":coop_id}).scalar_one()
        cuenta_id = db.execute(text("INSERT INTO cuenta_ahorro(numero,fecha_registro,socio_id,moneda_id,tipo_producto) VALUES (:num,CURRENT_DATE,:socio,1,'VISTA') RETURNING id"), {"num":f"DPF-{uuid4().hex[:8]}","socio":socio_id}).scalar_one()
        dpf_id = db.execute(text("INSERT INTO deposito_plazo_fijo(monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,socio_id,moneda_id,cooperativa_id,cuenta_abono_id) VALUES (1000,10,30,CURRENT_DATE,CURRENT_DATE+30,10,:socio,1,:coop,:cuenta) RETURNING id"), {"socio":socio_id,"coop":coop_id,"cuenta":cuenta_id}).scalar_one()
        trans_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,deposito_plazo_fijo_id) VALUES ('PAGO_INTERES_DPF',9.50,'WEB',1,:cuenta,:dpf) RETURNING id"), {"cuenta":cuenta_id,"dpf":dpf_id}).scalar_one()
        db.execute(text("INSERT INTO dpf_cronograma(deposito_plazo_fijo_id,numero,fecha_pago,dias,interes_bruto,retencion_rciva,interes_neto,estado,transaccion_id) VALUES (:dpf,1,CURRENT_DATE,30,10,0.50,9.50,'PAGADO',:trans)"), {"dpf":dpf_id,"trans":trans_id})
        issue_dpf_id = db.execute(text("INSERT INTO deposito_plazo_fijo(monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,socio_id,moneda_id,cooperativa_id) VALUES (500,8,45,CURRENT_DATE,CURRENT_DATE+45,5,:socio,1,:coop) RETURNING id"), {"socio":socio_id,"coop":coop_id}).scalar_one()
        caja_id = db.execute(text("INSERT INTO caja(nombre,cooperativa_id) VALUES ('DPF till',:coop) RETURNING id"), {"coop":coop_id}).scalar_one()
        control_id = db.execute(text("INSERT INTO control_caja(monto_apertura,saldo_sistema,fecha_apertura,caja_id,usuario_id) VALUES (0,0,CURRENT_TIMESTAMP,:caja,:user) RETURNING id"), {"caja":caja_id,"user":user_id}).scalar_one()
        issue_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,control_caja_id,moneda_id,deposito_plazo_fijo_id) VALUES ('DEPOSITO',500,'VENTANILLA',:control,1,:dpf) RETURNING id"), {"control":control_id,"dpf":issue_dpf_id}).scalar_one()
        account_issue_dpf = db.execute(text("INSERT INTO deposito_plazo_fijo(monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,socio_id,moneda_id,cooperativa_id) VALUES (600,8,45,CURRENT_DATE,CURRENT_DATE+45,6,:socio,1,:coop) RETURNING id"), {"socio":socio_id,"coop":coop_id}).scalar_one()
        account_issue_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,deposito_plazo_fijo_id) VALUES ('DEPOSITO',600,'WEB',1,:cuenta,:dpf) RETURNING id"), {"cuenta":cuenta_id,"dpf":account_issue_dpf}).scalar_one()
        legacy_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,deposito_plazo_fijo_id) VALUES ('APERTURA_DPF',600,'WEB',1,:dpf) RETURNING id"), {"dpf":account_issue_dpf}).scalar_one()
        liquidation_id = db.execute(text("INSERT INTO liquidacion(monto_capital_retornado,monto_interes_pagado,tipo_operacion,deposito_plazo_fijo_id,retencion_rciva,usuario_id,cuenta_abono_id) VALUES (1000,20,'LIQUIDACION',:dpf,0,:user,:cuenta) RETURNING id"), {"dpf":issue_dpf_id,"user":user_id,"cuenta":cuenta_id}).scalar_one()
        liquidation_tx_id = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,deposito_plazo_fijo_id,liquidacion_id) VALUES ('DEPOSITO',1020,'WEB',1,:cuenta,:dpf,:liquidacion) RETURNING id"), {"cuenta":cuenta_id,"dpf":issue_dpf_id,"liquidacion":liquidation_id}).scalar_one()
        renewed_dpf = db.execute(text("INSERT INTO deposito_plazo_fijo(monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,socio_id,moneda_id,cooperativa_id) VALUES (800,8,45,CURRENT_DATE,CURRENT_DATE+45,8,:socio,1,:coop) RETURNING id"), {"socio":socio_id,"coop":coop_id}).scalar_one()
        renewal_original = db.execute(text("INSERT INTO deposito_plazo_fijo(monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,socio_id,moneda_id,cooperativa_id) VALUES (1000,8,45,CURRENT_DATE,CURRENT_DATE+45,8,:socio,1,:coop) RETURNING id"), {"socio":socio_id,"coop":coop_id}).scalar_one()
        renewal_id = db.execute(text("INSERT INTO liquidacion(monto_capital_retornado,monto_interes_pagado,tipo_operacion,deposito_plazo_fijo_id,retencion_rciva,usuario_id,cuenta_abono_id,dpf_renovado_id) VALUES (1000,20,'RENOVACION',:dpf,0,:user,:cuenta,:renewed) RETURNING id"), {"dpf":renewal_original,"user":user_id,"cuenta":cuenta_id,"renewed":renewed_dpf}).scalar_one()
        renewal_payout = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,deposito_plazo_fijo_id,liquidacion_id) VALUES ('DEPOSITO',1020,'WEB',1,:cuenta,:dpf,:liquidation) RETURNING id"), {"cuenta":cuenta_id,"dpf":renewal_original,"liquidation":renewal_id}).scalar_one()
        renewal_investment = db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,deposito_plazo_fijo_id,liquidacion_id) VALUES ('RETIRO',800,'WEB',1,:cuenta,:dpf,:liquidation) RETURNING id"), {"cuenta":cuenta_id,"dpf":renewed_dpf,"liquidation":renewal_id}).scalar_one()
        db.commit()
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/comprobantes/generar-automaticos",json={},headers=auth(case["tokens"][0]))
    assert response.status_code == 200, response.text
    assert response.json()["generados"] == 6, response.json()
    assert response.json()["omitidas"] == [{"transaccion_id":legacy_id,"tipo":"APERTURA_DPF","motivo":"APERTURA_DPF solo aparece en datos legacy y no tiene escritor runtime"}]
    with SessionLocal() as db:
        voucher = db.query(models.ComprobanteContable).filter_by(transaccion_id=trans_id).one()
        lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=voucher.id).order_by(models.DetalleAsiento.orden).all()
        assert [(line.debe,line.haber) for line in lines] == [(10,0),(0,9.50),(0,0.50)]
        issue_voucher = db.query(models.ComprobanteContable).filter_by(transaccion_id=issue_id).one()
        issue_lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=issue_voucher.id).order_by(models.DetalleAsiento.orden).all()
        assert issue_voucher.tipo == "INGRESO"
        assert [(line.debe,line.haber) for line in issue_lines] == [(500,0),(0,500)]
        account_issue = db.query(models.ComprobanteContable).filter_by(transaccion_id=account_issue_id).one()
        assert account_issue.tipo == "TRASPASO"
        liquidation_voucher = db.query(models.ComprobanteContable).filter_by(transaccion_id=liquidation_tx_id).one()
        liquidation_lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=liquidation_voucher.id).order_by(models.DetalleAsiento.orden).all()
        assert sum(line.debe for line in liquidation_lines) == sum(line.haber for line in liquidation_lines) == 1020
        renewal_voucher = db.query(models.ComprobanteContable).filter_by(transaccion_id=renewal_investment).one()
        renewal_lines = db.query(models.DetalleAsiento).filter_by(comprobante_contable_id=renewal_voucher.id).order_by(models.DetalleAsiento.orden).all()
        assert [(line.debe,line.haber) for line in renewal_lines] == [(800,0),(0,800)]


def test_automatic_voucher_cannot_be_manually_voided(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        automatic = models.ComprobanteContable(
            tipo="INGRESO", glosa="Automatic fixture", es_automatico=True,
            cooperativa_id=case["ids"]["coops"][0], numero="I-2026-000099", gestion=2026,
            fecha_contable=date.today(), moneda_id=1, estado="REGISTRADO", origen="AUTOMATICO",
        )
        db.add(automatic)
        db.flush()
        db.add(models.DetalleAsiento(debe=1, haber=0, comprobante_contable_id=automatic.id, plan_cuenta_id=case["ids"]["accounts"][0]))
        db.add(models.DetalleAsiento(debe=0, haber=1, comprobante_contable_id=automatic.id, plan_cuenta_id=case["ids"]["accounts"][1]))
        db.commit()
        voucher_id = automatic.id
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/contabilidad/comprobantes/{voucher_id}/anular",
            json={"motivo": "Error de digitación"},
            headers=auth(case["tokens"][0]),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "Los comprobantes automáticos se revierten con la operación de origen"


def test_accounting_parameters_get_defaults_and_update_analytic_account_is_tenant_scoped(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        official_credit = db.query(models.PlanCuenta).filter_by(codigo="131.05",cooperativa_id=None).one().id
    with TestClient(app) as client:
        listed = client.get("/api/v1/contabilidad/parametros-contables", headers=auth(case["tokens"][0]))
        updated = client.put(
            "/api/v1/contabilidad/parametros-contables/CAJA",
            json={"plan_cuenta_id":case["ids"]["accounts"][0]},
            headers=auth(case["tokens"][0]),
        )
        rejected_parent = client.put(
            "/api/v1/contabilidad/parametros-contables/CAJA",
            json={"plan_cuenta_id":case["ids"]["official"]["111.01"]},
            headers=auth(case["tokens"][0]),
        )
        denied = client.get("/api/v1/contabilidad/parametros-contables", headers=auth(case["tokens"][2]))
        foreign = client.put(
            "/api/v1/contabilidad/parametros-contables/CAJA",
            json={"plan_cuenta_id":case["ids"]["accounts"][2]},
            headers=auth(case["tokens"][0]),
        )
        allowed_official = client.put(
            "/api/v1/contabilidad/parametros-contables/CARTERA_VIGENTE",
            json={"plan_cuenta_id":official_credit},
            headers=auth(case["tokens"][0]),
        )
        rejected_wrong_account = client.put(
            "/api/v1/contabilidad/parametros-contables/CARTERA_VIGENTE",
            json={"plan_cuenta_id":case["ids"]["accounts"][0]},
            headers=auth(case["tokens"][0]),
        )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 18
    assert {item["clave"] for item in listed.json()} >= {"CAJA","AHORRO_VISTA","CARTERA_VIGENTE","INTERES_DPF"}
    assert next(item for item in listed.json() if item["clave"]=="CAJA")["plan_cuenta_id"] == case["ids"]["accounts"][0]
    assert updated.status_code == 200, updated.text
    assert updated.json()["plan_cuenta_id"] == case["ids"]["accounts"][0]
    assert rejected_parent.status_code == 422
    assert "analítica" in rejected_parent.text.lower()
    assert denied.status_code == 403
    assert foreign.status_code == 404
    assert allowed_official.status_code == 200, allowed_official.text
    assert rejected_wrong_account.status_code == 422
    assert "MCEF 131.05" in rejected_wrong_account.text
    with SessionLocal() as db:
        audit_count = db.query(models.Bitacora).filter_by(
            usuario_id=case["ids"]["users"][0], modulo="CONTABILIDAD", accion="PARAMETRO_CONTABLE_ACTUALIZAR"
        ).count()
        assert audit_count == 2


def test_accounting_parameter_rejects_inactive_and_nonmovement_accounts(voucher_case):
    case = voucher_case
    with SessionLocal() as db:
        parent = db.get(models.PlanCuenta,case["ids"]["official"]["111.00"])
        inactive = models.PlanCuenta(
            codigo=f"111.00.90.{uuid4().hex[:2]}",nombre="Inactive parameter test",nivel=5,
            tipo=parent.tipo,naturaleza=parent.naturaleza,es_regularizadora=False,es_oficial=False,
            cooperativa_id=case["ids"]["coops"][0],estado="INACTIVA",acepta_movimientos=True,
            plan_cuenta_padre_id=parent.id,
        )
        db.add(inactive); db.commit(); inactive_id=inactive.id
    try:
        with TestClient(app) as client:
            inactive_response=client.put("/api/v1/contabilidad/parametros-contables/CAJA",json={"plan_cuenta_id":inactive_id},headers=auth(case["tokens"][0]))
            parent_response=client.put("/api/v1/contabilidad/parametros-contables/CAJA",json={"plan_cuenta_id":case["ids"]["official"]["111.00"]},headers=auth(case["tokens"][0]))
        assert inactive_response.status_code == 422
        assert "activa" in inactive_response.text.lower()
        assert parent_response.status_code == 422
        assert "movimientos" in parent_response.text.lower()
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM plan_cuenta WHERE id=:id"),{"id":inactive_id});db.commit()


def test_void_requires_a_nonblank_reason(voucher_case):
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/contabilidad/comprobantes/999999999/anular",
            json={"motivo": "     "},
            headers=auth(voucher_case["tokens"][0]),
        )
    assert response.status_code == 422
    assert "al menos cinco caracteres no blancos" in response.text


def _add_statement_accounts(case):
    """Create disposable tenant analytic accounts used by statement fixtures."""
    parents_by_code = ("513.05", "411.04")
    accounts = {}
    with SessionLocal() as db:
        for code in parents_by_code:
            parent = db.query(models.PlanCuenta).filter_by(codigo=code, cooperativa_id=None).one()
            account = models.PlanCuenta(
                codigo=f"{code}.{case['ids']['coops'][0] % 10000:04d}",
                nombre=f"Statement analytic {code}", nivel=5, tipo=parent.tipo,
                naturaleza=parent.naturaleza, es_regularizadora=False, es_oficial=False,
                cooperativa_id=case["ids"]["coops"][0], estado="ACTIVA",
                acepta_movimientos=True, plan_cuenta_padre_id=parent.id,
            )
            db.add(account)
            db.flush()
            case["ids"]["accounts"].append(account.id)
            accounts[code] = account.id
        db.commit()
    return accounts


def _post_lines(case, lines, *, currency=1, date_value=None, glosa="Statement integration fixture"):
    return {
        "tipo": "TRASPASO", "fecha_contable": date_value or case["today"],
        "glosa": glosa, "moneda_id": currency, "lineas": lines,
    }


def test_exchange_rates_validate_tenant_duplicate_base_and_future_dates(voucher_case):
    case = voucher_case
    today = date.today()
    headers = auth(case["tokens"][0])
    with TestClient(app) as client:
        base = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 1, "fecha": today.isoformat(), "valor": "1.00000",
        }, headers=headers)
        future = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 2, "fecha": (today + timedelta(days=1)).isoformat(), "valor": "6.90000",
        }, headers=headers)
        created = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 2, "fecha": today.isoformat(), "valor": "6.90000",
        }, headers=headers)
        duplicate = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 2, "fecha": today.isoformat(), "valor": "7.00000",
        }, headers=headers)
        listed = client.get(f"/api/v1/contabilidad/tipos-cambio?moneda_id=2&desde={today}&hasta={today}", headers=headers)
        foreign = client.get(f"/api/v1/contabilidad/tipos-cambio?moneda_id=2", headers=auth(case["tokens"][1]))
    assert base.status_code == 422, base.text
    assert future.status_code == 422, future.text
    assert created.status_code == 201, created.text
    assert duplicate.status_code == 409, duplicate.text
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["valor"] == "6.90000"
    assert foreign.status_code == 200 and foreign.json() == []
    with SessionLocal() as db:
        audit = db.query(models.Bitacora).filter_by(
            usuario_id=case["ids"]["users"][0], modulo="CONTABILIDAD", accion="TIPO_CAMBIO_CREAR"
        ).one_or_none()
        assert audit is not None and audit.cooperativa_id == case["ids"]["coops"][0]


def test_exchange_rate_update_corrects_value_and_is_tenant_scoped(voucher_case):
    case = voucher_case
    today = date.today()
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 2, "fecha": today.isoformat(), "valor": "6.86000",
        }, headers=auth(case["tokens"][0]))
        assert created.status_code == 201, created.text
        rate_id = created.json()["id"]
        updated = client.put(f"/api/v1/contabilidad/tipos-cambio/{rate_id}", json={"valor": "6.87000"},
                             headers=auth(case["tokens"][0]))
        foreign_update = client.put(f"/api/v1/contabilidad/tipos-cambio/{rate_id}", json={"valor": "7.00000"},
                                    headers=auth(case["tokens"][1]))
    assert updated.status_code == 200, updated.text
    assert updated.json()["valor"] == "6.87000"
    assert foreign_update.status_code == 404


def test_balance_general_and_income_statement_reconcile_with_usd_conversion(voucher_case):
    case = voucher_case
    accounts = _add_statement_accounts(case)
    today = date.today()
    asset_id, liability_id = case["ids"]["accounts"][:2]
    income_id, expense_id = accounts["513.05"], accounts["411.04"]
    headers = auth(case["tokens"][0])
    with TestClient(app) as client:
        missing_rate = client.get(f"/api/v1/contabilidad/balance-general?fecha_corte={today}&moneda=CONSOLIDADO", headers=headers)
        rate = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 2, "fecha": today.isoformat(), "valor": "6.86000",
        }, headers=headers)
        assert rate.status_code == 201, rate.text
        income = client.post("/api/v1/contabilidad/comprobantes", json=_post_lines(case, [
            {"plan_cuenta_id": asset_id, "debe": "100.00", "haber": "0"},
            {"plan_cuenta_id": income_id, "debe": "0", "haber": "100.00"},
        ], currency=2), headers=headers)
        expense = client.post("/api/v1/contabilidad/comprobantes", json=_post_lines(case, [
            {"plan_cuenta_id": expense_id, "debe": "30.00", "haber": "0"},
            {"plan_cuenta_id": liability_id, "debe": "0", "haber": "30.00"},
        ], currency=2), headers=headers)
        balance = client.get(f"/api/v1/contabilidad/balance-general?fecha_corte={today}&moneda=CONSOLIDADO", headers=headers)
        level_two = client.get(f"/api/v1/contabilidad/balance-general?fecha_corte={today}&moneda=CONSOLIDADO&nivel=2", headers=headers)
        results = client.get(f"/api/v1/contabilidad/estado-resultados?desde={today.replace(day=1)}&hasta={today}&moneda=CONSOLIDADO", headers=headers)
        results_export = client.get(f"/api/v1/contabilidad/estado-resultados/export?desde={today.replace(day=1)}&hasta={today}&moneda=CONSOLIDADO", headers=headers)
    assert income.status_code == expense.status_code == 201
    groups_two = [g for section in level_two.json()["secciones"].values() for g in section.values()]
    assert level_two.status_code == 200 and any(g["codigo"] != "RESULTADO_GESTION" for g in groups_two) and all(g["cuentas"] == [] for g in groups_two)
    assert balance.status_code == 200, balance.text
    assert results.status_code == 200, results.text
    assert len(results.json()["lineas"]) == 9
    assert results.json()["formato"] == "MCEF grupos (Título II)"
    assert results_export.status_code == 200 and results_export.content.startswith(b"\xef\xbb\xbf")
    assert f"estado-resultados-{today.replace(day=1)}-{today}.csv" in results_export.headers["content-disposition"]
    assert balance.json()["cuadra"] is True
    assert balance.json()["total_activo"] == "686.00"
    assert balance.json()["resultado_gestion"] == results.json()["resultado_neto_gestion"]
    assert balance.json()["tipo_cambio"]["valor"] == "6.86000"
    assert missing_rate.status_code == 422
    assert f"vigente al {today}" in missing_rate.json()["detail"]


def test_statement_levels_authorization_csv_and_reversal_net_out(voucher_case):
    case = voucher_case
    today = date.today()
    asset_id, liability_id = case["ids"]["accounts"][:2]
    headers = auth(case["tokens"][0])
    with TestClient(app) as client:
        created = client.post("/api/v1/contabilidad/comprobantes", json=_post_lines(case, [
            {"plan_cuenta_id": asset_id, "debe": "25.00", "haber": "0"},
            {"plan_cuenta_id": liability_id, "debe": "0", "haber": "25.00"},
        ]), headers=headers)
        assert created.status_code == 201, created.text
        voided = client.post(f"/api/v1/contabilidad/comprobantes/{created.json()['id']}/anular",
                             json={"motivo": "Cancel statement fixture"}, headers=headers)
        active = client.post("/api/v1/contabilidad/comprobantes", json=_post_lines(case, [
            {"plan_cuenta_id": asset_id, "debe": "10.00", "haber": "0"},
            {"plan_cuenta_id": liability_id, "debe": "0", "haber": "10.00"},
        ]), headers=headers)
        level_two = client.get(f"/api/v1/contabilidad/balance-general?fecha_corte={today}&moneda=BOB&nivel=2", headers=headers)
        export = client.get(f"/api/v1/contabilidad/balance-general/export?fecha_corte={today}&moneda=BOB", headers=headers)
        invalid_period = client.get("/api/v1/contabilidad/estado-resultados?desde=2025-12-31&hasta=2026-01-01&moneda=BOB", headers=headers)
        denied = client.get(f"/api/v1/contabilidad/balance-general?fecha_corte={today}", headers=auth(case["tokens"][2]))
    assert voided.status_code == 201, voided.text
    assert level_two.status_code == 200, level_two.text
    assert active.status_code == 201, active.text
    assert level_two.json()["cuadra"] is True
    assert level_two.json()["total_activo"] == "10.00"
    assert all(not group["cuentas"] for section in level_two.json()["secciones"].values() for group in section.values())
    assert export.status_code == 200 and export.content.startswith(b"\xef\xbb\xbf")
    assert f"balance-general-{today}.csv" in export.headers["content-disposition"]
    assert invalid_period.status_code == 422
    assert denied.status_code == 403


def test_consolidated_rounds_after_combining_analytic_accounts(voucher_case):
    case = voucher_case
    asset_id, liability_id = case["ids"]["accounts"][:2]
    with SessionLocal() as db:
        original = db.get(models.PlanCuenta, asset_id)
        parent = db.get(models.PlanCuenta, original.plan_cuenta_padre_id)
        second = models.PlanCuenta(
            codigo=f"{parent.codigo}.{case['ids']['coops'][0] % 10000:04d}B",
            nombre="Second analytic rounding fixture", nivel=5, tipo=parent.tipo,
            naturaleza=parent.naturaleza, es_regularizadora=False, es_oficial=False,
            cooperativa_id=case["ids"]["coops"][0], estado="ACTIVA", acepta_movimientos=True,
            plan_cuenta_padre_id=parent.id,
        )
        db.add(second)
        db.flush()
        case["ids"]["accounts"].append(second.id)
        second_id = second.id
        db.commit()
    today = date.today()
    headers = auth(case["tokens"][0])
    with TestClient(app) as client:
        rate = client.post("/api/v1/contabilidad/tipos-cambio", json={
            "moneda_id": 2, "fecha": today.isoformat(), "valor": "0.50000",
        }, headers=headers)
        assert rate.status_code == 201, rate.text
        posted = client.post("/api/v1/contabilidad/comprobantes", json=_post_lines(case, [
            {"plan_cuenta_id": asset_id, "debe": "0.01", "haber": "0"},
            {"plan_cuenta_id": second_id, "debe": "0.01", "haber": "0"},
            {"plan_cuenta_id": liability_id, "debe": "0", "haber": "0.02"},
        ], currency=2), headers=headers)
        report = client.get(f"/api/v1/contabilidad/balance-general?fecha_corte={today}&moneda=CONSOLIDADO", headers=headers)
    assert posted.status_code == 201, posted.text
    assert report.status_code == 200, report.text
    assert report.json()["total_activo"] == "0.01"
    assert report.json()["total_pasivo"] == "0.01"
    assert report.json()["cuadra"] is True
