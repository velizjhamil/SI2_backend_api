"""Integration and contract tests for CU-W26 credit disbursement."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.db.session import engine
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
_MIGRATION = Path(__file__).parents[1] / "migrations" / "017_sprint7_desembolso_credito.sql"


def test_credit_disbursement_migration_is_additive_and_idempotent():
    sql = _MIGRATION.read_text(encoding="utf-8").lower()
    assert "alter table credito" in sql
    assert "add column if not exists numero_credito" in sql
    assert "add column if not exists transaccion_desembolso_id" in sql
    assert "add column if not exists saldo_inicial" in sql
    assert "add column if not exists monto_pagado" in sql
    assert "add column if not exists credito_id" in sql
    assert "unique (cooperativa_id, numero_credito)" in sql
    assert "delete from" not in sql
    assert "truncate" not in sql


def test_credit_disbursement_orm_models_expose_new_columns():
    assert models.Credito.__tablename__ == "credito"
    assert {
        "numero_credito", "cooperativa_id", "socio_id", "producto_credito_id",
        "moneda_id", "tasa_interes", "plazo_meses", "tipo_amortizacion",
        "fecha_desembolso", "modalidad_desembolso", "cuenta_desembolso_id",
        "transaccion_desembolso_id", "usuario_id", "fecha_creacion",
    } <= set(models.Credito.__table__.columns.keys())
    assert models.TablaAmortizacion.__tablename__ == "tabla_amortizacion"
    assert {"saldo_inicial", "saldo_final", "monto_pagado"} <= set(
        models.TablaAmortizacion.__table__.columns.keys()
    )


def test_credit_disbursement_response_schemas_are_declared():
    assert schemas.CuotaOut
    assert schemas.PlanPagosOut
    assert schemas.CreditoOut
    assert schemas.CreditoDetalleOut


def test_credit_disbursement_migration_columns_are_applied():
    inspector = inspect(engine)
    credito_columns = {column["name"] for column in inspector.get_columns("credito")}
    installment_columns = {
        column["name"] for column in inspector.get_columns("tabla_amortizacion")
    }
    transaction_columns = {
        column["name"] for column in inspector.get_columns("transaccion")
    }
    assert {
        "numero_credito", "cooperativa_id", "socio_id", "producto_credito_id",
        "moneda_id", "tasa_interes", "plazo_meses", "tipo_amortizacion",
        "fecha_desembolso", "modalidad_desembolso", "cuenta_desembolso_id",
        "transaccion_desembolso_id", "usuario_id", "fecha_creacion",
    } <= credito_columns
    assert {"saldo_inicial", "saldo_final", "monto_pagado"} <= installment_columns
    assert "credito_id" in transaction_columns


@pytest.fixture(scope="module")
def client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def credit_fixture():
    suffix = uuid4().hex[:10].upper()
    result = {
        "suffix": suffix,
        "coop_ids": [], "user_ids": [], "users": {}, "product_ids": [],
        "socio_ids": [], "evaluation_ids": [], "request_ids": [], "account_ids": [],
        "box_ids": [], "control_ids": [], "credit_ids": [],
    }
    try:
        with SessionLocal() as db:
            coops = [models.Cooperativa(nombre=f"Desembolso {suffix} {n}", estado="ACTIVO") for n in (1, 2)]
            db.add_all(coops)
            db.flush()
            result["coop_ids"] = [coop.id for coop in coops]
            roles = {role.nombre: role.id for role in db.query(models.Rol).all()}
            for coop_number, coop in enumerate(coops, start=1):
                for role_name in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO", "CONTADOR"):
                    user = models.Usuario(
                        correo=f"desembolso-{suffix}-{coop_number}-{role_name.lower()}@test.invalid",
                        contrasena=hash_password("Password123"),
                        rol_id=roles[role_name], cooperativa_id=coop.id,
                        nombre=f"Desembolso {role_name} {coop_number}", estado="ACTIVO",
                    )
                    db.add(user)
                    result["users"][(coop_number, role_name)] = user
            bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
            for amortization in ("FRANCES", "ALEMAN"):
                product = models.ProductoCredito(
                    cooperativa_id=coops[0].id,
                    codigo=f"{suffix}-{amortization[:2]}", nombre=f"Producto {amortization}",
                    moneda_id=bob_id, monto_min=Decimal("100.00"), monto_max=Decimal("50000.00"),
                    plazo_min_meses=1, plazo_max_meses=60,
                    tasa_interes_anual=Decimal("18.00"), tipo_amortizacion=amortization,
                    dias_gracia_mora=0, tasa_mora_anual=Decimal("0.00"),
                    relacion_cuota_ingreso_max=Decimal("40.00"), requiere_garantia=False,
                    estado="ACTIVO",
                )
                db.add(product)
                result["product_ids"].append(product)
            db.flush()
            result["product_ids"] = [product.id for product in result["product_ids"]]
            db.commit()
            result["bob_id"] = bob_id
            for (coop_number, role_name), user in result["users"].items():
                token, _ = create_access_token(str(user.id), role_name, user.cooperativa_id)
                result["users"][(coop_number, role_name)] = {"id": user.id, "token": token}
                result["user_ids"].append(user.id)

        def make_request(*, coop_number=1, state="APROBADO", with_product=True,
                         amortization="FRANCES", rate_snapshot=Decimal("18.00"),
                         amount=Decimal("1200.00"), existing_socio_id=None):
            with SessionLocal() as db:
                if existing_socio_id is None:
                    socio = models.Socio(
                        cooperativa_id=result["coop_ids"][coop_number - 1], ci=f"DC-{suffix}-{len(result['socio_ids'])+1}",
                        nombre="Credit", apellido="Applicant", estado="ACTIVO", fecha_registro=date(2020, 1, 1),
                    )
                    db.add(socio)
                    db.flush()
                    socio_id = socio.id
                else:
                    socio_id = existing_socio_id
                user_id = result["users"][(coop_number, "OFICIAL_CREDITO")]["id"]
                evaluation = models.EvaluacionCampo(
                    ingreso_mensual=Decimal("6000.00"), egreso_mensual=Decimal("1000.00"),
                    cuota_deudas_mensual=Decimal("0.00"), capacidad_pago=Decimal("5000.00"),
                    actividad_economica="Comercio", fuente_ingresos="INDEPENDIENTE",
                    antiguedad_laboral_meses=36, calificacion_asfi="A", usuario_id=user_id,
                    socio_id=socio_id, fecha=date.today(),
                )
                db.add(evaluation)
                db.flush()
                product_id = result["product_ids"][0 if amortization == "FRANCES" else 1] if with_product else None
                request_row = models.SolicitudCredito(
                    monto=amount, plazo_meses=12, tasa_interes=rate_snapshot,
                    estado=state, socio_id=socio_id, usuario_id=user_id,
                    evaluacion_campo_id=evaluation.id, producto_credito_id=product_id,
                    moneda_id=result["bob_id"] if with_product else None,
                    cooperativa_id=result["coop_ids"][coop_number - 1],
                    numero_solicitud=f"DC-{suffix}-{len(result['request_ids'])+1}",
                    destino="CAPITAL_TRABAJO",
                )
                db.add(request_row)
                db.commit()
                if existing_socio_id is None:
                    result["socio_ids"].append(socio_id)
                result["evaluation_ids"].append(evaluation.id)
                result["request_ids"].append(request_row.id)
                return request_row.id

        result["make_request"] = make_request

        def make_account(request_id, *, balance=Decimal("5000.00")):
            with SessionLocal() as db:
                request_row = db.get(models.SolicitudCredito, request_id)
                account = models.CuentaAhorro(
                    numero=f"DC-{suffix}-{len(result['account_ids'])+1}",
                    tipo_producto="VISTA", saldo_disponible=balance,
                    saldo_bloqueado=Decimal("0.00"), estado="ACTIVA",
                    fecha_registro=date.today(), socio_id=request_row.socio_id,
                    moneda_id=request_row.moneda_id,
                )
                db.add(account)
                db.commit()
                result["account_ids"].append(account.id)
                return account.id

        def make_open_box(*, opening=Decimal("5000.00"), user_role="CAJERO"):
            with SessionLocal() as db:
                coop_id = result["coop_ids"][0]
                user_id = result["users"][(1, user_role)]["id"]
                box = models.Caja(
                    nombre=f"Desembolso {suffix}", estado="ABIERTA", cooperativa_id=coop_id,
                    monto_maximo_efectivo=Decimal("100000.00"),
                    umbral_diferencia_arqueo=Decimal("50.00"),
                )
                db.add(box)
                db.flush()
                control = models.ControlCaja(
                    monto_apertura=opening, saldo_sistema=opening,
                    fecha_apertura=date.today(), estado="ABIERTA", caja_id=box.id,
                    usuario_id=user_id,
                )
                db.add(control)
                db.commit()
                result["box_ids"].append(box.id)
                result["control_ids"].append(control.id)
                return control.id

        result["make_account"] = make_account
        result["make_open_box"] = make_open_box

        def make_legacy_credit(request_id):
            with SessionLocal() as db:
                request_row = db.get(models.SolicitudCredito, request_id)
                credit = models.Credito(
                    monto_aprobado=request_row.monto,
                    saldo_pendiente=request_row.monto,
                    solicitud_credito_id=request_id,
                    estado="VIGENTE",
                )
                db.add(credit)
                db.commit()
                result["credit_ids"].append(credit.id)
                return credit.id

        result["make_legacy_credit"] = make_legacy_credit

        def make_cooperative_reader(cooperative_id):
            with SessionLocal() as db:
                role_id = db.query(models.Rol.id).filter(models.Rol.nombre == "CONTADOR").scalar()
                user = models.Usuario(
                    correo=f"desembolso-{suffix}-canonical@test.invalid",
                    contrasena=hash_password("Password123"), rol_id=role_id,
                    cooperativa_id=cooperative_id, nombre="Canonical portfolio reader",
                    estado="ACTIVO",
                )
                db.add(user)
                db.commit()
                result["user_ids"].append(user.id)
                token, _ = create_access_token(str(user.id), "CONTADOR", cooperative_id)
                return token

        result["make_cooperative_reader"] = make_cooperative_reader
        yield result
    finally:
        if result["coop_ids"]:
            with SessionLocal() as db:
                credit_ids = db.execute(
                    text("SELECT id FROM credito WHERE solicitud_credito_id = ANY(:ids)"),
                    {"ids": result["request_ids"] or [0]},
                ).scalars().all()
                db.execute(text("UPDATE credito SET transaccion_desembolso_id=NULL WHERE id = ANY(:ids)"), {"ids": credit_ids or [0]})
                db.execute(text("UPDATE transaccion SET transaccion_contraparte_id=NULL WHERE transaccion_contraparte_id IN (SELECT id FROM transaccion WHERE credito_id = ANY(:ids))"), {"ids": credit_ids or [0]})
                db.execute(text("DELETE FROM transaccion WHERE credito_id = ANY(:ids)"), {"ids": credit_ids or [0]})
                db.execute(text("DELETE FROM tabla_amortizacion WHERE credito_id = ANY(:ids)"), {"ids": credit_ids or [0]})
                db.execute(text("DELETE FROM credito WHERE id = ANY(:ids)"), {"ids": credit_ids or [0]})
                if result["account_ids"]:
                    db.execute(text("DELETE FROM transaccion WHERE cuenta_ahorro_id = ANY(:ids)"), {"ids": result["account_ids"]})
                    db.execute(text("DELETE FROM cuenta_ahorro WHERE id = ANY(:ids)"), {"ids": result["account_ids"]})
                if result["control_ids"]:
                    db.execute(text("DELETE FROM transaccion WHERE control_caja_id = ANY(:ids)"), {"ids": result["control_ids"]})
                    db.execute(text("DELETE FROM control_caja WHERE id = ANY(:ids)"), {"ids": result["control_ids"]})
                if result["socio_ids"]:
                    db.execute(text("DELETE FROM declaracion_jurada_uif WHERE socio_id = ANY(:ids)"), {"ids": result["socio_ids"]})
                if result["user_ids"]:
                    db.execute(text("DELETE FROM bitacora WHERE usuario_id = ANY(:ids)"), {"ids": result["user_ids"]})
                if result["request_ids"]:
                    db.execute(text("DELETE FROM evaluacion_crediticia WHERE solicitud_credito_id = ANY(:ids)"), {"ids": result["request_ids"]})
                    db.execute(text("DELETE FROM solicitud_credito WHERE id = ANY(:ids)"), {"ids": result["request_ids"]})
                if result["evaluation_ids"]:
                    db.execute(text("DELETE FROM evaluacion_campo WHERE id = ANY(:ids)"), {"ids": result["evaluation_ids"]})
                if result["product_ids"]:
                    db.execute(text("DELETE FROM producto_credito WHERE id = ANY(:ids)"), {"ids": result["product_ids"]})
                if result["socio_ids"]:
                    db.execute(text("DELETE FROM socio WHERE id = ANY(:ids)"), {"ids": result["socio_ids"]})
                if result["user_ids"]:
                    db.execute(text("DELETE FROM usuario WHERE id = ANY(:ids)"), {"ids": result["user_ids"]})
                if result["coop_ids"]:
                    db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id = ANY(:ids) AND tipo='CREDITO'"), {"ids": result["coop_ids"]})
                if result["box_ids"]:
                    db.execute(text("DELETE FROM caja WHERE id = ANY(:ids)"), {"ids": result["box_ids"]})
                if result["coop_ids"]:
                    db.execute(text("DELETE FROM cooperativa WHERE id = ANY(:ids)"), {"ids": result["coop_ids"]})
                db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_plan_pagos_route_is_registered(client):
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/creditos/solicitudes/{solicitud_id}/plan-pagos" in paths


def test_plan_pagos_preview_returns_complete_schedule(client, credit_fixture):
    request_id = credit_fixture["make_request"]()
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/plan-pagos",
        params={"fecha_desembolso": "2025-12-17", "fecha_primer_vencimiento": "2026-01-31"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "tipo_amortizacion", "monto", "tasa_interes", "plazo_meses", "moneda",
        "fecha_desembolso", "fecha_primer_vencimiento", "cuotas", "total_capital",
        "total_interes", "total_pagar",
    }
    assert body["tipo_amortizacion"] == "FRANCES"
    assert body["fecha_primer_vencimiento"] == "2026-01-31"
    assert len(body["cuotas"]) == 12
    assert body["cuotas"][0]["fecha_vencimiento"] == "2026-01-31"
    assert body["cuotas"][0]["cuota"] == "110.02"
    assert body["cuotas"][-1]["saldo_final"] == "0.00"
    assert body["total_capital"] == "1200.00"
    assert all(row["estado_pago"] == "PENDIENTE" for row in body["cuotas"])


def test_plan_pagos_uses_request_rate_snapshot(client, credit_fixture):
    request_id = credit_fixture["make_request"](rate_snapshot=Decimal("14.50"))
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/plan-pagos",
        params={"fecha_desembolso": "2025-12-17", "fecha_primer_vencimiento": "2026-01-31"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["tasa_interes"] == "14.50"


def test_plan_pagos_hides_requests_from_other_cooperatives(client, credit_fixture):
    request_id = credit_fixture["make_request"](coop_number=2)
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/plan-pagos", headers=_auth(token)
    )
    assert response.status_code == 404


def test_plan_pagos_defaults_to_today_and_one_month_first_due(client, credit_fixture):
    request_id = credit_fixture["make_request"]()
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/plan-pagos", headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    today = date.today()
    month = today.month % 12 + 1
    year = today.year + (today.month // 12)
    expected_first_due = date(year, month, min(today.day, monthrange(year, month)[1]))
    assert response.json()["fecha_desembolso"] == today.isoformat()
    assert response.json()["fecha_primer_vencimiento"] == expected_first_due.isoformat()


@pytest.mark.parametrize(
    ("state", "with_product", "status_code"),
    [("PENDIENTE", True, 409), ("APROBADO", False, 409)],
)
def test_plan_pagos_rejects_unapproved_or_productless_request(
    client, credit_fixture, state, with_product, status_code
):
    request_id = credit_fixture["make_request"](state=state, with_product=with_product)
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/plan-pagos", headers=_auth(token)
    )
    assert response.status_code == status_code


@pytest.mark.parametrize("first_due", ["2025-12-31", "2026-02-01"])
def test_plan_pagos_rejects_first_due_date_outside_15_to_45_days(
    client, credit_fixture, first_due
):
    request_id = credit_fixture["make_request"]()
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/plan-pagos",
        params={"fecha_desembolso": "2025-12-17", "fecha_primer_vencimiento": first_due},
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_disbursements_route_is_registered(client):
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/creditos/solicitudes/{solicitud_id}/desembolso" in paths


def test_disburse_credit_to_savings_account_persists_credit_schedule_and_audit(
    client, credit_fixture
):
    request_id = credit_fixture["make_request"]()
    account_id = credit_fixture["make_account"](request_id)
    user_id = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["id"]
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]

    response = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/desembolso",
        json={"modalidad": "CUENTA", "cuenta_ahorro_id": account_id},
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["numero_credito"].startswith("CRE-")
    assert body["modalidad_desembolso"] == "CUENTA"
    assert body["cuenta_desembolso"] == {"id": account_id, "numero": f"DC-{credit_fixture['suffix']}-1"}
    assert len(body["cronograma"]) == 12
    assert body["cronograma"][0]["cuota"] == "110.02"
    assert body["cronograma"][-1]["saldo_final"] == "0.00"
    assert body["transaccion_desembolso_id"] is not None
    credit_fixture["credit_ids"].append(body["id"])
    with SessionLocal() as db:
        request_row = db.get(models.SolicitudCredito, request_id)
        account = db.get(models.CuentaAhorro, account_id)
        transaction = db.execute(
            text("SELECT tipo, canal, credito_id, cuenta_ahorro_id, monto FROM transaccion WHERE id=:id"),
            {"id": body["transaccion_desembolso_id"]},
        ).one()
        audit = db.execute(
            text("SELECT modulo, accion FROM bitacora WHERE usuario_id=:uid AND modulo='CREDITOS' ORDER BY id DESC LIMIT 1"),
            {"uid": user_id},
        ).one()
        assert request_row.estado == "DESEMBOLSADO"
        assert account.saldo_disponible == Decimal("6200.00")
        assert transaction.tipo == "DESEMBOLSO_CREDITO"
        assert transaction.canal == "WEB"
        assert transaction.credito_id == body["id"]
        assert transaction.cuenta_ahorro_id == account_id
        assert Decimal(transaction.monto) == Decimal("1200.00")
        assert audit.modulo == "CREDITOS" and audit.accion == "DESEMBOLSAR_CREDITO"


def test_disburse_cash_uses_open_box_and_links_cash_transaction(client, credit_fixture):
    request_id = credit_fixture["make_request"]()
    control_id = credit_fixture["make_open_box"](opening=Decimal("2000.00"))
    token = credit_fixture["users"][(1, "CAJERO")]["token"]

    response = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/desembolso",
        json={"modalidad": "EFECTIVO"},
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["modalidad_desembolso"] == "EFECTIVO"
    assert body["cuenta_desembolso"] is None
    credit_fixture["credit_ids"].append(body["id"])
    with SessionLocal() as db:
        control = db.get(models.ControlCaja, control_id)
        transaction = db.execute(
            text("SELECT tipo, canal, control_caja_id, credito_id FROM transaccion WHERE id=:id"),
            {"id": body["transaccion_desembolso_id"]},
        ).one()
        assert control.saldo_sistema == Decimal("800.00")
        assert transaction.tipo == "RETIRO"
        assert transaction.canal == "VENTANILLA"
        assert transaction.control_caja_id == control_id
        assert transaction.credito_id == body["id"]


def test_cash_disbursement_requires_enough_open_box_cash(client, credit_fixture):
    request_id = credit_fixture["make_request"]()
    control_id = credit_fixture["make_open_box"](opening=Decimal("100.00"))
    token = credit_fixture["users"][(1, "CAJERO")]["token"]
    response = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/desembolso",
        json={"modalidad": "EFECTIVO"},
        headers=_auth(token),
    )
    assert response.status_code == 400
    with SessionLocal() as db:
        assert db.get(models.SolicitudCredito, request_id).estado == "APROBADO"
        assert db.execute(text("SELECT COUNT(*) FROM credito WHERE solicitud_credito_id=:id"), {"id": request_id}).scalar_one() == 0
        assert db.get(models.ControlCaja, control_id).saldo_sistema == Decimal("100.00")


def test_cash_disbursement_requires_uif_before_persisting_credit(client, credit_fixture):
    product_id = credit_fixture["product_ids"][0]
    with SessionLocal() as db:
        product = db.get(models.ProductoCredito, product_id)
        product.monto_max = Decimal("100000.00")
        db.commit()
    request_id = credit_fixture["make_request"](
        amount=Decimal("70000.00"), rate_snapshot=Decimal("18.00")
    )
    control_id = credit_fixture["make_open_box"](opening=Decimal("80000.00"))
    token = credit_fixture["users"][(1, "CAJERO")]["token"]
    response = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/desembolso",
        json={"modalidad": "EFECTIVO"},
        headers=_auth(token),
    )
    assert response.status_code == 428
    with SessionLocal() as db:
        assert db.get(models.SolicitudCredito, request_id).estado == "APROBADO"
        assert db.execute(text("SELECT COUNT(*) FROM credito WHERE solicitud_credito_id=:id"), {"id": request_id}).scalar_one() == 0
        assert db.get(models.ControlCaja, control_id).saldo_sistema == Decimal("80000.00")


def test_credit_linked_cash_transactions_count_toward_same_day_uif_aggregation(
    client, credit_fixture
):
    previous_request = credit_fixture["make_request"]()
    with SessionLocal() as db:
        socio_id = db.get(models.SolicitudCredito, previous_request).socio_id
        previous_credit_id = db.execute(
            text("""
                INSERT INTO credito (monto_aprobado, saldo_pendiente, solicitud_credito_id,
                    cooperativa_id, socio_id, moneda_id, estado)
                VALUES (1000, 1000, :request_id, :coop_id, :socio_id, :currency_id, 'VIGENTE')
                RETURNING id
            """),
            {"request_id": previous_request, "coop_id": credit_fixture["coop_ids"][0],
             "socio_id": socio_id, "currency_id": credit_fixture["bob_id"]},
        ).scalar_one()
        db.commit()
        credit_fixture["credit_ids"].append(previous_credit_id)
    control_id = credit_fixture["make_open_box"](opening=Decimal("100000.00"))
    with SessionLocal() as db:
        db.execute(
            text("""
                INSERT INTO transaccion (tipo, monto, canal, control_caja_id, moneda_id, credito_id)
                VALUES ('RETIRO', 69950, 'VENTANILLA', :control_id, :currency_id, :credit_id)
            """),
            {"control_id": control_id, "currency_id": credit_fixture["bob_id"],
             "credit_id": previous_credit_id},
        )
        db.commit()
    request_id = credit_fixture["make_request"](
        amount=Decimal("100.00"), existing_socio_id=socio_id
    )
    token = credit_fixture["users"][(1, "CAJERO")]["token"]
    response = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/desembolso",
        json={"modalidad": "EFECTIVO"},
        headers=_auth(token),
    )
    assert response.status_code == 428
    with SessionLocal() as db:
        assert db.get(models.SolicitudCredito, request_id).estado == "APROBADO"
        assert db.execute(text("SELECT COUNT(*) FROM credito WHERE solicitud_credito_id=:id"), {"id": request_id}).scalar_one() == 0


def test_credit_portfolio_route_is_registered(client):
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/creditos/creditos" in paths
    assert "/api/v1/creditos/creditos/{credito_id}" in paths


def test_credit_portfolio_is_cooperative_scoped_and_includes_legacy_snapshots(
    client, credit_fixture
):
    own_request = credit_fixture["make_request"]()
    own_credit_id = credit_fixture["make_legacy_credit"](own_request)
    other_request = credit_fixture["make_request"](coop_number=2)
    other_credit_id = credit_fixture["make_legacy_credit"](other_request)
    token = credit_fixture["users"][(1, "CONTADOR")]["token"]

    response = client.get("/api/v1/creditos/creditos", headers=_auth(token))

    assert response.status_code == 200, response.text
    rows = response.json()
    own = next(row for row in rows if row["id"] == own_credit_id)
    assert own["numero_credito"] is None
    assert own["socio"]["id"] == credit_fixture["socio_ids"][0]
    assert own["producto"] is not None
    assert own["moneda"]["codigo_iso"] == "BOB"
    assert own["cuotas_totales"] == 0
    assert all(row["id"] != other_credit_id for row in rows)


def test_credit_detail_includes_schedule_and_hides_other_tenant(client, credit_fixture):
    own_request = credit_fixture["make_request"]()
    own_credit_id = credit_fixture["make_legacy_credit"](own_request)
    other_request = credit_fixture["make_request"](coop_number=2)
    other_credit_id = credit_fixture["make_legacy_credit"](other_request)
    token = credit_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]

    response = client.get(
        f"/api/v1/creditos/creditos/{own_credit_id}", headers=_auth(token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == own_credit_id
    assert body["cronograma"] == []
    assert body["transaccion_desembolso_id"] is None
    outside = client.get(
        f"/api/v1/creditos/creditos/{other_credit_id}", headers=_auth(token)
    )
    assert outside.status_code == 404


def test_seed_credit_is_exposed_only_to_unique_canonical_demo_cooperative(
    client, credit_fixture
):
    with SessionLocal() as db:
        canonical_ids = db.query(models.Cooperativa.id).filter(
            models.Cooperativa.nombre == "Cooperativa de Prueba SI2"
        ).all()
    assert len(canonical_ids) == 1, "the demo cooperative natural key must be unique"
    canonical_id = canonical_ids[0][0]
    canonical_token = credit_fixture["make_cooperative_reader"](canonical_id)
    unrelated_token = credit_fixture["users"][(1, "CONTADOR")]["token"]

    canonical_response = client.get(
        "/api/v1/creditos/creditos", headers=_auth(canonical_token)
    )
    unrelated_response = client.get(
        "/api/v1/creditos/creditos", headers=_auth(unrelated_token)
    )

    assert canonical_response.status_code == 200, canonical_response.text
    assert any(row["id"] == 1 for row in canonical_response.json())
    assert unrelated_response.status_code == 200, unrelated_response.text
    assert all(row["id"] != 1 for row in unrelated_response.json())
