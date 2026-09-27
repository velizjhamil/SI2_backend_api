"""Integration and contract tests for CU-W27 installment collection."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.db.session import engine
from app.db.session import SessionLocal
from app.models import models
from app.schemas import schemas


def _db_disponible() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")
_MIGRATION = Path(__file__).parents[1] / "migrations" / "018_sprint8_cobro_cuotas.sql"


def test_installment_collection_migration_is_additive_and_idempotent():
    sql = _MIGRATION.read_text(encoding="utf-8").lower()
    for fragment in (
        "alter table pago_cuota",
        "add column if not exists credito_id",
        "add column if not exists cooperativa_id",
        "add column if not exists numero_recibo",
        "add column if not exists modalidad",
        "add column if not exists monto_total",
        "add column if not exists dias_atraso",
        "add column if not exists transaccion_id",
        "add column if not exists usuario_id",
        "add column if not exists fecha_pago",
        "add column if not exists fecha_actualizacion",
        "unique (cooperativa_id, numero_recibo)",
    ):
        assert fragment in sql
    assert "delete from" not in sql
    assert "truncate" not in sql


def test_installment_collection_orm_models_and_schema_are_declared():
    assert models.PagoCuota.__tablename__ == "pago_cuota"
    assert {
        "credito_id", "cooperativa_id", "numero_recibo", "modalidad",
        "monto_total", "dias_atraso", "transaccion_id", "usuario_id",
    } <= set(models.PagoCuota.__table__.columns.keys())
    assert models.Morosidad.__tablename__ == "morosidad"
    assert "fecha_actualizacion" in models.Morosidad.__table__.columns
    assert "fecha_pago" in models.TablaAmortizacion.__table__.columns
    assert schemas.DeudaCuotaOut
    assert schemas.PagoOut
    assert schemas.MoraCreditoOut


def test_installment_collection_migration_columns_are_applied():
    inspector = inspect(engine)
    payment_columns = {column["name"] for column in inspector.get_columns("pago_cuota")}
    installment_columns = {
        column["name"] for column in inspector.get_columns("tabla_amortizacion")
    }
    arrears_columns = {column["name"] for column in inspector.get_columns("morosidad")}
    assert {
        "credito_id", "cooperativa_id", "numero_recibo", "modalidad",
        "monto_total", "dias_atraso", "transaccion_id", "usuario_id",
    } <= payment_columns
    assert "fecha_pago" in installment_columns
    assert "fecha_actualizacion" in arrears_columns


def test_late_fee_helper_honors_grace_boundary_and_rounds_half_up():
    from app.services.cobro_cuotas import calcular_mora

    due_date = date(2026, 1, 1)
    at_grace_boundary = calcular_mora(
        capital_cuota=Decimal("5.00"), tasa_mora_anual=Decimal("36.00"),
        fecha_vencimiento=due_date, fecha_pago=date(2026, 1, 11),
        dias_gracia_mora=10,
    )
    beyond_grace = calcular_mora(
        capital_cuota=Decimal("5.00"), tasa_mora_anual=Decimal("36.00"),
        fecha_vencimiento=due_date, fecha_pago=date(2026, 1, 12),
        dias_gracia_mora=10,
    )
    assert at_grace_boundary == {
        "dias_atraso": 0, "en_mora": False, "mora": Decimal("0.00")
    }
    assert beyond_grace == {
        "dias_atraso": 11, "en_mora": True, "mora": Decimal("0.06")
    }


def test_late_fee_zero_rate_and_future_due_are_zero():
    from app.services.cobro_cuotas import calcular_mora

    due_date = date(2026, 1, 10)
    late_zero_rate = calcular_mora(
        capital_cuota=Decimal("1000.00"), tasa_mora_anual=Decimal("0.00"),
        fecha_vencimiento=due_date, fecha_pago=date(2026, 1, 20),
        dias_gracia_mora=0,
    )
    future_due = calcular_mora(
        capital_cuota=Decimal("1000.00"), tasa_mora_anual=Decimal("36.00"),
        fecha_vencimiento=due_date, fecha_pago=date(2026, 1, 9),
        dias_gracia_mora=0,
    )
    assert late_zero_rate == {
        "dias_atraso": 10, "en_mora": True, "mora": Decimal("0.00")
    }
    assert future_due == {
        "dias_atraso": 0, "en_mora": False, "mora": Decimal("0.00")
    }


@pytest.fixture(scope="module")
def client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client


def _canonical_admin_headers():
    with SessionLocal() as db:
        user = db.query(models.Usuario).join(models.Rol).filter(
            models.Usuario.cooperativa_id == 1,
            models.Rol.nombre == "ADMINISTRADOR",
        ).order_by(models.Usuario.id).first()
        assert user is not None
        token, _ = create_access_token(
            str(user.id), user.rol.nombre, user.cooperativa_id
        )
    return {"Authorization": f"Bearer {token}"}


def test_debt_route_uses_oldest_unpaid_installment_and_legacy_zero_mora(client):
    response = client.get(
        "/api/v1/creditos/creditos/1/deuda",
        params={"fecha": "2026-03-10"},
        headers=_canonical_admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credito_id"] == 1
    assert body["cuota"]["numero"] == 2
    assert body["cuota"]["capital"] == "2024.16"
    assert body["cuota"]["interes"] == "277.92"
    assert body["dias_gracia"] == 0
    assert body["dias_atraso"] == 0
    assert body["en_mora"] is False
    assert body["mora"] == "0.00"
    assert body["total_a_pagar"] == "2302.08"


def test_debt_route_marks_legacy_zero_rate_installment_in_arrears_after_due(client):
    response = client.get(
        "/api/v1/creditos/creditos/1/deuda",
        params={"fecha": "2026-03-21"},
        headers=_canonical_admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cuota"]["numero"] == 2
    assert body["dias_atraso"] == 11
    assert body["en_mora"] is True
    assert body["mora"] == "0.00"


@pytest.fixture
def payment_case_factory():
    """Create uniquely-owned credit/payment rows and always remove those exact rows."""
    from app.db.session import SessionLocal

    owned = {
        "coops": [], "users": [], "socios": [], "products": [], "requests": [],
        "credits": [], "installments": [], "accounts": [], "boxes": [], "controls": [],
    }

    def make_case(*, amount=Decimal("200.00"), rate=Decimal("36.00"), grace=0, account_balance=None):
        suffix = uuid4().hex[:10].upper()
        with SessionLocal() as db:
            coop = models.Cooperativa(nombre=f"Loan Test {suffix}", estado="ACTIVO")
            db.add(coop)
            db.flush()
            owned["coops"].append(coop.id)
            roles = {role.nombre: role.id for role in db.query(models.Rol).all()}
            users = {}
            for role_name in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO"):
                user = models.Usuario(
                    correo=f"loan-test-{suffix}-{role_name.lower()}@test.invalid",
                    contrasena=hash_password("Password123"),
                    rol_id=roles[role_name], cooperativa_id=coop.id,
                    nombre=f"Loan Test {role_name}", estado="ACTIVO",
                )
                db.add(user)
                users[role_name] = user
            db.flush()
            owned["users"].extend(user.id for user in users.values())

            bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
            socio = models.Socio(
                cooperativa_id=coop.id, ci=f"LT-{suffix}", nombre="Loan", apellido="Member",
                estado="ACTIVO",
            )
            db.add(socio)
            product = models.ProductoCredito(
                cooperativa_id=coop.id, codigo=f"LT-{suffix}", nombre="Loan Test Product",
                moneda_id=bob_id, monto_min=Decimal("100.00"), monto_max=Decimal("1000000.00"),
                plazo_min_meses=1, plazo_max_meses=60, tasa_interes_anual=Decimal("0.00"),
                tipo_amortizacion="FRANCES", dias_gracia_mora=grace,
                tasa_mora_anual=rate, relacion_cuota_ingreso_max=Decimal("40.00"),
                requiere_garantia=False, estado="ACTIVO",
            )
            db.add_all([socio, product])
            db.flush()
            owned["socios"].append(socio.id)
            owned["products"].append(product.id)

            request = models.SolicitudCredito(
                monto=amount, plazo_meses=2, tasa_interes=Decimal("0.00"),
                estado="DESEMBOLSADO", socio_id=socio.id,
                usuario_id=users["OFICIAL_CREDITO"].id, producto_credito_id=product.id,
                numero_solicitud=f"LT-{suffix}", destino="CONSUMO", moneda_id=bob_id,
                cooperativa_id=coop.id,
            )
            db.add(request)
            db.flush()
            owned["requests"].append(request.id)
            credit = models.Credito(
                monto_aprobado=amount, saldo_pendiente=amount, estado="VIGENTE",
                solicitud_credito_id=request.id, numero_credito=f"LT-{suffix}",
                cooperativa_id=coop.id, socio_id=socio.id, producto_credito_id=product.id,
                moneda_id=bob_id, tasa_interes=Decimal("0.00"), plazo_meses=2,
                tipo_amortizacion="FRANCES", fecha_desembolso=date.today() - timedelta(days=30),
                modalidad_desembolso="CUENTA", usuario_id=users["OFICIAL_CREDITO"].id,
            )
            db.add(credit)
            db.flush()
            owned["credits"].append(credit.id)
            first_capital = (amount / Decimal("2")).quantize(Decimal("0.01"))
            second_capital = amount - first_capital
            first = models.TablaAmortizacion(
                numero_cuota=1, fecha_vencimiento=date.today() - timedelta(days=1),
                monto_capital=first_capital, monto_interes=Decimal("2.00"),
                monto_cuota_total=first_capital + Decimal("2.00"), estado_pago="PENDIENTE",
                credito_id=credit.id, saldo_inicial=amount,
                saldo_final=second_capital, monto_pagado=Decimal("0.00"),
            )
            second = models.TablaAmortizacion(
                numero_cuota=2, fecha_vencimiento=date.today() + timedelta(days=1),
                monto_capital=second_capital, monto_interes=Decimal("1.00"),
                monto_cuota_total=second_capital + Decimal("1.00"), estado_pago="PENDIENTE",
                credito_id=credit.id, saldo_inicial=second_capital,
                saldo_final=Decimal("0.00"), monto_pagado=Decimal("0.00"),
            )
            db.add_all([first, second])
            db.flush()
            owned["installments"].extend([first.id, second.id])

            account = models.CuentaAhorro(
                numero=f"LT-{suffix}", tipo_producto="VISTA",
                saldo_disponible=amount * 2 if account_balance is None else account_balance,
                saldo_bloqueado=Decimal("0.00"), estado="ACTIVA", socio_id=socio.id,
                moneda_id=bob_id, fecha_registro=date.today(),
            )
            box = models.Caja(
                nombre=f"Loan Test {suffix}", estado="ABIERTA", cooperativa_id=coop.id,
                monto_maximo_efectivo=Decimal("500000.00"),
                umbral_diferencia_arqueo=Decimal("50.00"),
            )
            db.add_all([account, box])
            db.flush()
            owned["accounts"].append(account.id)
            owned["boxes"].append(box.id)
            control = models.ControlCaja(
                monto_apertura=amount * 2, saldo_sistema=amount * 2,
                fecha_apertura=datetime.now(), estado="ABIERTA", caja_id=box.id,
                usuario_id=users["CAJERO"].id,
            )
            db.add(control)
            db.flush()
            owned["controls"].append(control.id)
            db.commit()

            tokens = {}
            for role_name, user in users.items():
                token, _ = create_access_token(str(user.id), role_name, coop.id)
                tokens[role_name] = {"id": user.id, "token": token}
            return {
                "suffix": suffix, "coop_id": coop.id, "socio_id": socio.id,
                "credit_id": credit.id, "account_id": account.id, "control_id": control.id,
                "amount": amount, "installment_ids": [first.id, second.id],
                "tokens": tokens, "first_capital": first_capital,
                "first_total": first_capital + Decimal("2.00"), "bob_id": bob_id,
            }

    try:
        yield make_case
    finally:
        with SessionLocal() as db:
            credit_ids = owned["credits"] or [0]
            coop_ids = owned["coops"] or [0]
            control_ids = owned["controls"] or [0]
            account_ids = owned["accounts"] or [0]
            socio_ids = owned["socios"] or [0]
            user_ids = owned["users"] or [0]
            db.execute(text("UPDATE pago_cuota SET transaccion_id=NULL WHERE credito_id = ANY(:ids)"), {"ids": credit_ids})
            db.execute(text("DELETE FROM transaccion WHERE credito_id = ANY(:ids) OR control_caja_id = ANY(:controls) OR cuenta_ahorro_id = ANY(:accounts)"), {"ids": credit_ids, "controls": control_ids, "accounts": account_ids})
            db.execute(text("DELETE FROM pago_cuota WHERE credito_id = ANY(:ids)"), {"ids": credit_ids})
            db.execute(text("DELETE FROM morosidad WHERE credito_id = ANY(:ids)"), {"ids": credit_ids})
            db.execute(text("DELETE FROM declaracion_jurada_uif WHERE socio_id = ANY(:ids)"), {"ids": socio_ids})
            db.execute(text("DELETE FROM bitacora WHERE usuario_id = ANY(:ids)"), {"ids": user_ids})
            db.execute(text("DELETE FROM tabla_amortizacion WHERE credito_id = ANY(:ids)"), {"ids": credit_ids})
            db.execute(text("DELETE FROM credito WHERE id = ANY(:ids)"), {"ids": credit_ids})
            db.execute(text("DELETE FROM cuenta_ahorro WHERE id = ANY(:ids)"), {"ids": account_ids})
            db.execute(text("DELETE FROM solicitud_credito WHERE id = ANY(:ids)"), {"ids": owned["requests"] or [0]})
            db.execute(text("DELETE FROM producto_credito WHERE id = ANY(:ids)"), {"ids": owned["products"] or [0]})
            db.execute(text("DELETE FROM socio WHERE id = ANY(:ids)"), {"ids": socio_ids})
            db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id = ANY(:ids) AND tipo='PAGO_CUOTA'"), {"ids": coop_ids})
            db.execute(text("DELETE FROM control_caja WHERE id = ANY(:ids)"), {"ids": control_ids})
            db.execute(text("DELETE FROM caja WHERE id = ANY(:ids)"), {"ids": owned["boxes"] or [0]})
            db.execute(text("DELETE FROM usuario WHERE id = ANY(:ids)"), {"ids": user_ids})
            db.execute(text("DELETE FROM cooperativa WHERE id = ANY(:ids)"), {"ids": coop_ids})
            db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_account_payments_settle_one_oldest_installment_per_call(client, payment_case_factory):
    case = payment_case_factory()
    url = f"/api/v1/creditos/creditos/{case['credit_id']}/pagos"
    token = case["tokens"]["OFICIAL_CREDITO"]["token"]
    first = client.post(
        url, json={"modalidad": "CUENTA", "cuenta_ahorro_id": case["account_id"]},
        headers=_auth(token),
    )
    assert first.status_code == 201, first.text
    first_payment = first.json()
    assert first_payment["numero_recibo"] == "REC-000001"
    assert first_payment["numero_cuota"] == 1
    assert first_payment["capital"] == str(case["first_capital"])
    assert first_payment["interes"] == "2.00"
    assert first_payment["mora"] == "0.10"
    assert first_payment["total"] == str(case["first_total"] + Decimal("0.10"))
    assert first_payment["saldo_pendiente_credito"] == str(case["first_capital"])
    assert first_payment["credito_estado"] == "VIGENTE"

    with SessionLocal() as db:
        credit = db.get(models.Credito, case["credit_id"])
        account = db.get(models.CuentaAhorro, case["account_id"])
        first_row = db.get(models.TablaAmortizacion, case["installment_ids"][0])
        assert credit.saldo_pendiente == case["first_capital"]
        assert first_row.estado_pago == "PAGADA"
        assert first_row.monto_pagado == case["first_total"] + Decimal("0.10")
        assert account.saldo_disponible == case["amount"] * 2 - case["first_total"] - Decimal("0.10")
        payment_row = db.execute(text("SELECT id, transaccion_id FROM pago_cuota WHERE credito_id=:id ORDER BY id"), {"id": case["credit_id"]}).one()
        transaction = db.execute(text("SELECT tipo, canal, cuenta_ahorro_id, pago_cuota_id FROM transaccion WHERE id=:id"), {"id": payment_row.transaccion_id}).one()
        assert (transaction.tipo, transaction.canal, transaction.cuenta_ahorro_id, transaction.pago_cuota_id) == (
            "PAGO_CUOTA", "WEB", case["account_id"], payment_row.id
        )
        arrears = db.execute(text("SELECT estado, dias_de_retaso FROM morosidad WHERE credito_id=:id"), {"id": case["credit_id"]}).one()
        assert (arrears.estado, arrears.dias_de_retaso) == ("AL_DIA", 0)

    second = client.post(
        url, json={"modalidad": "CUENTA", "cuenta_ahorro_id": case["account_id"]},
        headers=_auth(token),
    )
    assert second.status_code == 201, second.text
    assert second.json()["numero_recibo"] == "REC-000002"
    assert second.json()["numero_cuota"] == 2
    assert second.json()["mora"] == "0.00"
    assert second.json()["credito_estado"] == "CANCELADO"
    history = client.get(url, headers=_auth(token))
    assert history.status_code == 200, history.text
    assert [row["numero_recibo"] for row in history.json()] == ["REC-000001", "REC-000002"]
    receipt_id = first_payment["id"]
    receipt = client.get(f"/api/v1/creditos/pagos/{receipt_id}", headers=_auth(token))
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["numero_cuota"] == 1


def test_partial_payment_keeps_verified_guarantee_and_final_payment_releases_it(client, payment_case_factory):
    case = payment_case_factory()
    token = case["tokens"]["OFICIAL_CREDITO"]["token"]
    guarantee_id = None
    url = f"/api/v1/creditos/creditos/{case['credit_id']}/pagos"
    try:
        with SessionLocal() as db:
            credit = db.get(models.Credito, case["credit_id"])
            request_row = db.get(models.SolicitudCredito, credit.solicitud_credito_id)
            guarantee = models.Garantia(
                cooperativa_id=case["coop_id"], solicitud_credito_id=request_row.id,
                tipo="HIPOTECARIA", descripcion="Garantía test payoff", moneda_id=case["bob_id"],
                valor_comercial=Decimal("1000.00"), valor_realizable=Decimal("700.00"),
                estado="VERIFICADA", usuario_registro_id=case["tokens"]["OFICIAL_CREDITO"]["id"],
                usuario_verificacion_id=case["tokens"]["ADMINISTRADOR"]["id"],
                observacion_verificacion="Verificada para probar pago final", fecha_verificacion=datetime.now(),
            )
            db.add(guarantee); db.commit(); guarantee_id = guarantee.id

        partial = client.post(url, json={"modalidad": "CUENTA", "cuenta_ahorro_id": case["account_id"]},
            headers=_auth(token))
        assert partial.status_code == 201, partial.text
        assert partial.json()["credito_estado"] == "VIGENTE"
        with SessionLocal() as db:
            guarantee = db.get(models.Garantia, guarantee_id)
            assert guarantee.estado == "VERIFICADA" and guarantee.fecha_liberacion is None

        final = client.post(url, json={"modalidad": "CUENTA", "cuenta_ahorro_id": case["account_id"]},
            headers=_auth(token))
        assert final.status_code == 201, final.text
        assert final.json()["credito_estado"] == "CANCELADO"
        with SessionLocal() as db:
            guarantee = db.get(models.Garantia, guarantee_id)
            assert guarantee.estado == "LIBERADA" and guarantee.fecha_liberacion is not None
            assert db.query(models.Bitacora).filter_by(
                usuario_id=case["tokens"]["OFICIAL_CREDITO"]["id"], accion="LIBERAR_GARANTIA"
            ).count() == 1
    finally:
        if guarantee_id is not None:
            with SessionLocal() as db:
                db.query(models.Garantia).filter_by(id=guarantee_id).delete(synchronize_session=False)
                db.commit()


def test_cash_payment_uses_open_session_and_counts_as_cash_in(client, payment_case_factory):
    case = payment_case_factory()
    token = case["tokens"]["CAJERO"]["token"]
    response = client.post(
        f"/api/v1/creditos/creditos/{case['credit_id']}/pagos",
        json={"modalidad": "EFECTIVO"}, headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    payment = response.json()
    expected_total = case["first_total"] + Decimal("0.10")
    assert payment["modalidad"] == "EFECTIVO"
    assert payment["caja_nombre"].startswith("Loan Test ")
    assert payment["total"] == str(expected_total)
    summary = client.get("/api/v1/caja/arqueos/resumen", headers=_auth(token))
    assert summary.status_code == 200, summary.text
    bob = next(row for row in summary.json()["monedas"] if row["moneda"]["codigo_iso"] == "BOB")
    assert bob["total_depositos"] == str(expected_total)
    assert bob["saldo_teorico"] == str(case["amount"] * 2 + expected_total)


def test_cash_payment_requires_uif_and_does_not_write_when_missing(client, payment_case_factory):
    case = payment_case_factory(amount=Decimal("150000.00"), rate=Decimal("0.00"))
    token = case["tokens"]["CAJERO"]["token"]
    response = client.post(
        f"/api/v1/creditos/creditos/{case['credit_id']}/pagos",
        json={"modalidad": "EFECTIVO"}, headers=_auth(token),
    )
    assert response.status_code == 428
    with SessionLocal() as db:
        credit = db.get(models.Credito, case["credit_id"])
        installment = db.get(models.TablaAmortizacion, case["installment_ids"][0])
        assert credit.saldo_pendiente == case["amount"]
        assert installment.estado_pago == "PENDIENTE"
        assert db.execute(text("SELECT count(*) FROM pago_cuota WHERE credito_id=:id"), {"id": case["credit_id"]}).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM transaccion WHERE credito_id=:id"), {"id": case["credit_id"]}).scalar_one() == 0


def test_cash_payment_structuring_counts_prior_payment_in_same_day(client, payment_case_factory):
    case = payment_case_factory(amount=Decimal("120000.00"), rate=Decimal("0.00"))
    token = case["tokens"]["CAJERO"]["token"]
    url = f"/api/v1/creditos/creditos/{case['credit_id']}/pagos"
    first = client.post(url, json={"modalidad": "EFECTIVO"}, headers=_auth(token))
    assert first.status_code == 201, first.text
    assert Decimal(first.json()["total"]) < Decimal("70000.00")
    second = client.post(url, json={"modalidad": "EFECTIVO"}, headers=_auth(token))
    assert second.status_code == 428
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM pago_cuota WHERE credito_id=:id"), {"id": case["credit_id"]}).scalar_one() == 1


def test_account_payment_preserves_minimum_balance_and_writes_nothing(client, payment_case_factory):
    case = payment_case_factory(account_balance=Decimal("20.00"))
    token = case["tokens"]["OFICIAL_CREDITO"]["token"]
    response = client.post(
        f"/api/v1/creditos/creditos/{case['credit_id']}/pagos",
        json={"modalidad": "CUENTA", "cuenta_ahorro_id": case["account_id"]},
        headers=_auth(token),
    )
    assert response.status_code == 400
    with SessionLocal() as db:
        assert db.get(models.CuentaAhorro, case["account_id"]).saldo_disponible == Decimal("20.00")
        assert db.get(models.Credito, case["credit_id"]).saldo_pendiente == case["amount"]
        assert db.execute(text("SELECT count(*) FROM pago_cuota WHERE credito_id=:id"), {"id": case["credit_id"]}).scalar_one() == 0


def test_refresh_mora_and_portfolio_include_only_overdue_unpaid_credit(client, payment_case_factory):
    case = payment_case_factory()
    with SessionLocal() as db:
        db.execute(
            text("UPDATE tabla_amortizacion SET fecha_vencimiento=:due WHERE credito_id=:id AND numero_cuota=1"),
            {"due": date.today() - timedelta(days=11), "id": case["credit_id"]},
        )
        db.commit()

    headers = _auth(case["tokens"]["ADMINISTRADOR"]["token"])
    refreshed = client.post("/api/v1/creditos/mora/actualizar", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["actualizados"] == 1
    assert refreshed.json()["en_mora"] == 1
    assert refreshed.json()["al_dia"] == 0

    portfolio = client.get("/api/v1/creditos/mora", params={"estado": "EN_MORA"}, headers=headers)
    assert portfolio.status_code == 200, portfolio.text
    row = next(item for item in portfolio.json() if item["credito_id"] == case["credit_id"])
    assert row["estado_mora"] == "EN_MORA"
    assert row["dias_de_retaso"] == 11
    assert row["cuotas_vencidas"] == 1
    assert row["monto_penalizado"] == "1.10"
    assert row["monto_vencido"] == str(case["first_total"])

    listed = client.get("/api/v1/creditos/creditos", headers=headers)
    assert listed.status_code == 200, listed.text
    credit = next(item for item in listed.json() if item["id"] == case["credit_id"])
    assert credit["estado_mora"] == "EN_MORA"
    assert credit["dias_de_retaso"] == 11


def test_refresh_mora_sets_al_dia_when_no_unpaid_installment_exceeds_grace(client, payment_case_factory):
    case = payment_case_factory(grace=10)
    with SessionLocal() as db:
        db.execute(
            text("UPDATE tabla_amortizacion SET fecha_vencimiento=:due WHERE credito_id=:id AND numero_cuota=1"),
            {"due": date.today() - timedelta(days=10), "id": case["credit_id"]},
        )
        db.commit()

    headers = _auth(case["tokens"]["ADMINISTRADOR"]["token"])
    refreshed = client.post("/api/v1/creditos/mora/actualizar", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["actualizados"] == 1
    assert refreshed.json()["en_mora"] == 0
    assert refreshed.json()["al_dia"] == 1
    portfolio = client.get("/api/v1/creditos/mora", params={"estado": "AL_DIA"}, headers=headers)
    assert portfolio.status_code == 200, portfolio.text
    row = next(item for item in portfolio.json() if item["credito_id"] == case["credit_id"])
    assert row["estado_mora"] == "AL_DIA"
    assert row["dias_de_retaso"] == 0
    assert row["monto_penalizado"] == "0.00"
