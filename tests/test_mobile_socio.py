"""Disposable-tenant API tests for socio self-service endpoints."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

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
def socio_case():
    suffix = uuid4().hex[:10]
    ids = {"coop": None, "user": None, "staff_user": None, "socio": None, "foreign_socio": None, "accounts": [], "products": [], "requests": [], "mobile_requests": [], "credits": [], "installments": [], "dpfs": [], "dpf_schedules": [], "offers": [], "generated_requests": []}
    try:
        with SessionLocal() as db:
            coop = models.Cooperativa(nombre=f"Mobile {suffix}", estado="ACTIVO")
            db.add(coop); db.flush(); ids["coop"] = coop.id
            role = db.query(models.Rol).filter_by(nombre="SOCIO").one()
            user = models.Usuario(correo=f"mobile-{suffix}@test.invalid", contrasena=hash_password("Password123"), rol_id=role.id, cooperativa_id=coop.id, nombre="Mobile test", estado="ACTIVO")
            db.add(user); db.flush(); ids["user"] = user.id
            staff_role = db.query(models.Rol).filter_by(nombre="OFICIAL_CREDITO").one()
            staff_user = models.Usuario(correo=f"mobile-staff-{suffix}@test.invalid", contrasena=hash_password("Password123"), rol_id=staff_role.id, cooperativa_id=coop.id, nombre="Mobile staff", estado="ACTIVO")
            db.add(staff_user); db.flush(); ids["staff_user"] = staff_user.id
            member = models.Socio(cooperativa_id=coop.id, ci=f"MOB-{suffix}", nombre="Mobile", apellido="Test", estado="ACTIVO", usuario_id=user.id)
            db.add(member); db.flush(); ids["socio"] = member.id
            foreign_member = models.Socio(cooperativa_id=coop.id, ci=f"MOB-F-{suffix}", nombre="Foreign", apellido="Test", estado="ACTIVO", fecha_registro=date.today())
            db.add(foreign_member); db.flush(); ids["foreign_socio"] = foreign_member.id
            currency = db.query(models.Moneda).filter_by(codigo_iso="BOB").one()
            account = models.CuentaAhorro(numero=f"MOB-{suffix}", tipo_producto="VISTA", saldo_disponible=Decimal("75.00"), saldo_bloqueado=0, estado="ACTIVA", fecha_registro=date.today(), socio_id=member.id, moneda_id=currency.id)
            db.add(account); db.flush(); ids["accounts"].append(account.id)
            foreign_account = models.CuentaAhorro(numero=f"MOB-F-{suffix}", tipo_producto="VISTA", saldo_disponible=Decimal("1000.00"), saldo_bloqueado=0, estado="ACTIVA", fecha_registro=date.today(), socio_id=foreign_member.id, moneda_id=currency.id)
            db.add(foreign_account); db.flush(); ids["accounts"].append(foreign_account.id)
            usd = db.query(models.Moneda).filter_by(codigo_iso="USD").one_or_none()
            if usd:
                wrong_currency_account = models.CuentaAhorro(numero=f"MOB-U-{suffix}", tipo_producto="VISTA", saldo_disponible=Decimal("1000.00"), saldo_bloqueado=0, estado="ACTIVA", fecha_registro=date.today(), socio_id=member.id, moneda_id=usd.id)
                db.add(wrong_currency_account); db.flush(); ids["accounts"].append(wrong_currency_account.id)
            product = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"MOB-{suffix}", nombre="Mobile Loan", moneda_id=currency.id, monto_min=Decimal("100"), monto_max=Decimal("5000"), plazo_min_meses=1, plazo_max_meses=24, tasa_interes_anual=Decimal("12"), tipo_amortizacion="FRANCES", dias_gracia_mora=0, tasa_mora_anual=Decimal("0"), relacion_cuota_ingreso_max=Decimal("50"), estado="ACTIVO")
            db.add(product); db.flush(); ids["products"].append(product.id)
            request = models.SolicitudCredito(monto=Decimal("1000"), plazo_meses=2, tasa_interes=Decimal("12"), estado="DESEMBOLSADO", socio_id=foreign_member.id, usuario_id=user.id, producto_credito_id=product.id, moneda_id=currency.id, numero_solicitud=f"MOB-{suffix}", destino="CONSUMO", cooperativa_id=coop.id)
            db.add(request); db.flush(); ids["requests"].append(request.id)
            credit = models.Credito(monto_aprobado=Decimal("1000"), saldo_pendiente=Decimal("1000"), estado="VIGENTE", solicitud_credito_id=request.id, numero_credito=f"MOB-{suffix}", cooperativa_id=coop.id, socio_id=member.id, producto_credito_id=product.id, moneda_id=currency.id, tasa_interes=Decimal("12"), plazo_meses=2, tipo_amortizacion="FRANCES", fecha_desembolso=date.today(), modalidad_desembolso="CUENTA", usuario_id=user.id)
            db.add(credit); db.flush(); ids["credits"].append(credit.id)
            installment = models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=date.today() - timedelta(days=1), monto_capital=Decimal("500"), monto_interes=Decimal("10"), monto_cuota_total=Decimal("510"), estado_pago="PENDIENTE", credito_id=credit.id, saldo_inicial=Decimal("1000"), saldo_final=Decimal("500"), monto_pagado=Decimal("0"))
            db.add(installment); db.flush(); ids["installments"].append(installment.id)
            db.commit()
            token, _ = create_access_token(str(user.id), "SOCIO", coop.id)
            staff_token, _ = create_access_token(str(staff_user.id), "OFICIAL_CREDITO", coop.id)
            yield {"token": token, "staff_token": staff_token, "socio": member.id, "account": account.id, "ids": ids}
    finally:
        with SessionLocal() as db:
            if ids["credits"]:
                db.execute(text("UPDATE pago_cuota SET transaccion_id=NULL WHERE credito_id = ANY(:ids)"), {"ids": ids["credits"]})
                db.execute(text("DELETE FROM transaccion WHERE credito_id = ANY(:ids)"), {"ids": ids["credits"]})
            if ids["dpf_schedules"]:
                db.execute(delete(models.DPFCronograma).where(models.DPFCronograma.id.in_(ids["dpf_schedules"])))
            if ids["dpfs"]:
                db.execute(delete(models.DepositoPlazoFijo).where(models.DepositoPlazoFijo.id.in_(ids["dpfs"])))
            if ids["accounts"]:
                db.execute(text("DELETE FROM transaccion WHERE cuenta_ahorro_id = ANY(:ids)"), {"ids": ids["accounts"]})
                db.execute(delete(models.CuentaAhorro).where(models.CuentaAhorro.id.in_(ids["accounts"])))
            if ids["offers"]:
                db.execute(delete(models.OfertaRecredito).where(models.OfertaRecredito.id.in_(ids["offers"])))
            if ids["generated_requests"]:
                db.execute(delete(models.SolicitudCredito).where(models.SolicitudCredito.id.in_(ids["generated_requests"])))
                db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id=:id AND tipo='SOLICITUD_CREDITO'"), {"id": ids["coop"]})
            if ids["credits"]:
                db.execute(delete(models.PagoCuota).where(models.PagoCuota.credito_id.in_(ids["credits"])))
                db.execute(delete(models.Morosidad).where(models.Morosidad.credito_id.in_(ids["credits"])))
                db.execute(delete(models.TablaAmortizacion).where(models.TablaAmortizacion.credito_id.in_(ids["credits"])))
                db.execute(delete(models.Credito).where(models.Credito.id.in_(ids["credits"])))
                db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id=:id AND tipo='PAGO_CUOTA'"), {"id": ids["coop"]})
            if ids["requests"]:
                db.execute(delete(models.SolicitudCredito).where(models.SolicitudCredito.id.in_(ids["requests"])))
            if ids["mobile_requests"]:
                db.execute(delete(models.EvaluacionCrediticia).where(models.EvaluacionCrediticia.solicitud_credito_id.in_(ids["mobile_requests"])))
                db.execute(delete(models.SolicitudCredito).where(models.SolicitudCredito.id.in_(ids["mobile_requests"])))
            fixture_socio_ids = [value for value in (ids["socio"], ids["foreign_socio"]) if value is not None]
            if fixture_socio_ids:
                db.execute(delete(models.EvaluacionCampo).where(models.EvaluacionCampo.socio_id.in_(fixture_socio_ids)))
            if ids["products"]:
                db.execute(delete(models.ProductoCredito).where(models.ProductoCredito.id.in_(ids["products"])))
            if ids["socio"]:
                db.execute(delete(models.Socio).where(models.Socio.id == ids["socio"]))
            if ids["foreign_socio"]:
                db.execute(delete(models.Socio).where(models.Socio.id == ids["foreign_socio"]))
            if ids["user"]:
                db.execute(delete(models.Bitacora).where(models.Bitacora.usuario_id == ids["user"]))
                db.execute(delete(models.Usuario).where(models.Usuario.id == ids["user"]))
            if ids["staff_user"]:
                db.execute(delete(models.Bitacora).where(models.Bitacora.usuario_id == ids["staff_user"]))
                db.execute(delete(models.Usuario).where(models.Usuario.id == ids["staff_user"]))
            if ids["coop"]:
                db.execute(delete(models.Cooperativa).where(models.Cooperativa.id == ids["coop"]))
            db.commit()


def auth(case):
    return {"Authorization": f"Bearer {case['token']}"}


def staff_auth(case):
    return {"Authorization": f"Bearer {case['staff_token']}"}


def add_offer(case, *, days=10):
    with SessionLocal() as db:
        offer = models.OfertaRecredito(cooperativa_id=case["ids"]["coop"], socio_id=case["socio"],
            credito_origen_id=case["ids"]["credits"][0], producto_credito_id=case["ids"]["products"][0],
            monto_sugerido=Decimal("900"), plazo_meses=12, tasa_interes=Decimal("12"),
            cuota_estimada=Decimal("80"), probabilidad_mora=Decimal("0.1"), nivel_riesgo="BAJO",
            motivos=["Test offer"], estado="VIGENTE", fecha_vencimiento=date.today()+timedelta(days=days),
            usuario_id=case["ids"]["user"])
        db.add(offer); db.flush(); case["ids"]["offers"].append(offer.id); db.commit(); return offer.id


def test_mobile_statement_returns_account_and_zeroed_range(socio_case):
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/cuentas/{socio_case['account']}/extracto", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cuenta"]["id"] == socio_case["account"]
    assert body["saldo_inicial"] == body["saldo_final"] == "75.00"
    assert body["total"] == 0 and body["movimientos"] == []


def test_mobile_credit_products_and_simulation(socio_case):
    headers = auth(socio_case)
    with TestClient(app) as client:
        products = client.get("/api/v1/socio/productos-credito", headers=headers)
        simulation = client.get("/api/v1/socio/creditos/simulacion", headers=headers, params={
            "producto_id": socio_case["ids"]["products"][0], "monto": "1000.00", "plazo_meses": 12,
        })
    assert products.status_code == 200, products.text
    assert any(row["id"] == socio_case["ids"]["products"][0] for row in products.json())
    assert simulation.status_code == 200, simulation.text
    body = simulation.json()
    assert body["cuota_estimada"] and body["total_intereses"] and body["total_a_pagar"]
    assert len(body["cronograma"]) == 12
    assert all(isinstance(body[key], str) and len(body[key].rsplit(".", 1)[-1]) == 2
               for key in ("cuota_estimada", "total_intereses", "total_a_pagar"))


def test_mobile_simulation_rejects_out_of_range_values(socio_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/socio/creditos/simulacion", headers=auth(socio_case), params={
            "producto_id": socio_case["ids"]["products"][0], "monto": "99.00", "plazo_meses": 12,
        })
    assert response.status_code == 422
    assert response.json()["detail"] == "El monto está fuera del rango permitido para el producto"


def test_mobile_create_list_detail_cancel_and_staff_tolerance(socio_case):
    body = {"producto_id": socio_case["ids"]["products"][0], "monto": "1200.00", "plazo_meses": 12,
        "destino": "CONSUMO", "datos_declarados": {"ingreso_mensual": "2500.00",
        "egreso_mensual": "900.00", "actividad_economica": "Comercio", "fuente_ingresos": "INDEPENDIENTE"}}
    with TestClient(app) as client:
        created = client.post("/api/v1/socio/solicitudes", headers=auth(socio_case), json=body)
        assert created.status_code == 201, created.text
        row = created.json(); socio_case["ids"]["mobile_requests"].append(row["id"])
        assert row["canal_origen"] == "MOVIL" and row["requiere_evaluacion"] is True
        assert row["monto"] == "1200.00"
        with SessionLocal() as db:
            stored = db.get(models.SolicitudCredito, row["id"])
            assert stored.evaluacion_campo_id is None
            assert stored.datos_declarados["ingreso_mensual"] == "2500.00"
        listed = client.get("/api/v1/socio/solicitudes", headers=auth(socio_case))
        detailed = client.get(f"/api/v1/socio/solicitudes/{row['id']}", headers=auth(socio_case))
        staff_list = client.get("/api/v1/creditos/solicitudes", headers=staff_auth(socio_case))
        staff_detail = client.get(f"/api/v1/creditos/solicitudes/{row['id']}", headers=staff_auth(socio_case))
        score = client.post(f"/api/v1/creditos/solicitudes/{row['id']}/evaluacion", headers=staff_auth(socio_case))
        canceled = client.post(f"/api/v1/socio/solicitudes/{row['id']}/cancelar", headers=auth(socio_case), json={"motivo": "Cambio de planes"})
    assert listed.status_code == 200 and row["id"] in {r["id"] for r in listed.json()}
    assert detailed.status_code == 200 and detailed.json()["linea_tiempo"]
    assert staff_list.status_code == 200 and next(x for x in staff_list.json() if x["id"] == row["id"])["requiere_evaluacion"] is True
    assert staff_detail.status_code == 200 and staff_detail.json()["canal_origen"] == "MOVIL"
    assert score.status_code == 422 and score.json()["detail"] == "Falta la evaluación de campo del oficial"
    assert canceled.status_code == 200 and canceled.json()["estado"] == "ANULADA"


def test_canceled_mobile_request_does_not_require_field_evaluation(socio_case):
    body = {"producto_id": socio_case["ids"]["products"][0], "monto": "1200.00", "plazo_meses": 12,
        "destino": "CONSUMO", "datos_declarados": {"ingreso_mensual": "2500.00",
        "egreso_mensual": "900.00", "actividad_economica": "Comercio", "fuente_ingresos": "INDEPENDIENTE"}}
    with TestClient(app) as client:
        created = client.post("/api/v1/socio/solicitudes", headers=auth(socio_case), json=body)
        assert created.status_code == 201, created.text
        row = created.json()
        socio_case["ids"]["mobile_requests"].append(row["id"])
        canceled = client.post(f"/api/v1/socio/solicitudes/{row['id']}/cancelar", headers=auth(socio_case),
            json={"motivo": "Cambio de planes"})
        worklist = client.get("/api/v1/creditos/solicitudes", headers=staff_auth(socio_case),
            params={"requiere_evaluacion": "true", "canal_origen": "MOVIL"})
        staff_list = client.get("/api/v1/creditos/solicitudes", headers=staff_auth(socio_case))
        ficha = client.get(f"/api/v1/creditos/solicitudes/{row['id']}/ficha-campo", headers=staff_auth(socio_case))
        mobile_list = client.get("/api/v1/socio/solicitudes", headers=auth(socio_case))

    assert canceled.status_code == 200 and canceled.json()["requiere_evaluacion"] is False
    assert worklist.status_code == 200 and row["id"] not in {item["id"] for item in worklist.json()}
    assert staff_list.status_code == 200
    assert next(item for item in staff_list.json() if item["id"] == row["id"])["requiere_evaluacion"] is False
    assert ficha.status_code == 200 and ficha.json()["solicitud"]["requiere_evaluacion"] is False
    assert mobile_list.status_code == 200
    assert next(item for item in mobile_list.json() if item["id"] == row["id"])["requiere_evaluacion"] is False


def test_mobile_duplicate_and_foreign_request_are_rejected(socio_case):
    body = {"producto_id": socio_case["ids"]["products"][0], "monto": "1200", "plazo_meses": 12,
        "destino": "CONSUMO", "datos_declarados": {"ingreso_mensual": "2500",
        "egreso_mensual": "900", "actividad_economica": "Comercio", "fuente_ingresos": "INDEPENDIENTE"}}
    with TestClient(app) as client:
        first = client.post("/api/v1/socio/solicitudes", headers=auth(socio_case), json=body)
        assert first.status_code == 201, first.text
        socio_case["ids"]["mobile_requests"].append(first.json()["id"])
        duplicate = client.post("/api/v1/socio/solicitudes", headers=auth(socio_case), json=body)
        foreign = client.get(f"/api/v1/socio/solicitudes/{socio_case['ids']['requests'][0]}", headers=auth(socio_case))
    assert duplicate.status_code == 409
    assert foreign.status_code == 404


def test_mobile_request_can_be_completed_by_w21_officer(socio_case):
    body = {"producto_id": socio_case["ids"]["products"][0], "monto": "1300.00", "plazo_meses": 12,
        "destino": "CONSUMO", "datos_declarados": {"ingreso_mensual": "3000.00",
        "egreso_mensual": "1000.00", "actividad_economica": "Comercio", "fuente_ingresos": "INDEPENDIENTE"}}
    evaluation = {"ingreso_mensual": "2800.00", "egreso_mensual": "1100.00",
        "cuota_deudas_mensual": "100.00", "actividad_economica": "Tienda minorista",
        "fuente_ingresos": "INDEPENDIENTE", "antiguedad_laboral_meses": 36,
        "calificacion_asfi": "A"}
    with TestClient(app) as client:
        created = client.post("/api/v1/socio/solicitudes", headers=auth(socio_case), json=body)
        assert created.status_code == 201, created.text
        row = created.json(); socio_case["ids"]["mobile_requests"].append(row["id"])
        assert row["canal_origen"] == "MOVIL" and row["requiere_evaluacion"] is True
        staff_list = client.get("/api/v1/creditos/solicitudes", headers=staff_auth(socio_case))
        staff_detail = client.get(f"/api/v1/creditos/solicitudes/{row['id']}", headers=staff_auth(socio_case))
        updated = client.put(f"/api/v1/creditos/solicitudes/{row['id']}", headers=staff_auth(socio_case),
            json={"evaluacion": evaluation})
        after_list = client.get("/api/v1/creditos/solicitudes", headers=staff_auth(socio_case))
        after_detail = client.get(f"/api/v1/creditos/solicitudes/{row['id']}", headers=staff_auth(socio_case))
    assert staff_list.status_code == staff_detail.status_code == 200
    for result in (staff_list.json(),):
        staff_row = next(item for item in result if item["id"] == row["id"])
        assert staff_row["canal_origen"] == "MOVIL" and staff_row["requiere_evaluacion"] is True
    assert staff_detail.json()["canal_origen"] == "MOVIL"
    assert staff_detail.json()["requiere_evaluacion"] is True
    assert updated.status_code == 200, updated.text
    assert updated.json()["evaluacion"]["ingreso_mensual"] == "2800.00"
    assert updated.json()["requiere_evaluacion"] is False
    after_row = next(item for item in after_list.json() if item["id"] == row["id"])
    assert after_row["canal_origen"] == "MOVIL" and after_row["requiere_evaluacion"] is False
    assert after_detail.json()["canal_origen"] == "MOVIL"
    assert after_detail.json()["requiere_evaluacion"] is False


def test_w23_rejects_mobile_request_without_officer_evaluation(socio_case):
    body = {"producto_id": socio_case["ids"]["products"][0], "monto": "1300.00", "plazo_meses": 12,
        "destino": "CONSUMO", "datos_declarados": {"ingreso_mensual": "3000.00",
        "egreso_mensual": "1000.00", "actividad_economica": "Comercio", "fuente_ingresos": "INDEPENDIENTE"}}
    with TestClient(app) as client:
        created = client.post("/api/v1/socio/solicitudes", headers=auth(socio_case), json=body)
        assert created.status_code == 201, created.text
        row = created.json(); socio_case["ids"]["mobile_requests"].append(row["id"])
        response = client.post(f"/api/v1/creditos/solicitudes/{row['id']}/evaluacion", headers=staff_auth(socio_case))
    assert response.status_code == 422
    assert response.json()["detail"] == "Falta la evaluación de campo del oficial"


def test_mobile_statement_calculates_full_range_before_pagination(socio_case):
    with SessionLocal() as db:
        db.execute(text("INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,fecha_hora) VALUES ('DEPOSITO',100,'VENTANILLA',1,:account,:today),('RETIRO',25,'WEB',1,:account,:today)"),
                   {"account": socio_case["account"], "today": datetime.combine(date.today(), datetime.min.time())})
        db.commit()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/cuentas/{socio_case['account']}/extracto?desde={date.today()}&hasta={date.today()}&limit=1", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2 and len(body["movimientos"]) == 1
    assert body["movimientos"][0]["tipo"] == "RETIRO"
    assert body["movimientos"][0]["saldo_resultante"] == "75.00"
    assert body["saldo_final"] == "75.00" and body["total_creditos"] == "100.00" and body["total_debitos"] == "25.00"


def test_mobile_statement_anchors_opening_balance_to_current_account_balance(socio_case):
    with SessionLocal() as db:
        db.execute(text("""
            INSERT INTO transaccion(tipo,monto,canal,moneda_id,cuenta_ahorro_id,fecha_hora)
            VALUES ('DEPOSITO',100,'VENTANILLA',1,:account,:today)
        """), {"account": socio_case["account"], "today": datetime.combine(date.today(), datetime.min.time())})
        db.query(models.CuentaAhorro).filter_by(id=socio_case["account"]).update({"saldo_disponible": Decimal("600.00")})
        db.commit()
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/socio/cuentas/{socio_case['account']}/extracto?desde={date.today()}&hasta={date.today()}",
            headers=auth(socio_case),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saldo_inicial"] == "500.00"
    assert body["saldo_final"] == "600.00"
    assert Decimal(body["saldo_final"]) - Decimal(body["saldo_inicial"]) == Decimal(body["total_creditos"]) - Decimal(body["total_debitos"])


def test_mobile_statement_rejects_reversed_dates(socio_case):
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/cuentas/{socio_case['account']}/extracto?desde=2026-09-20&hasta=2026-09-19", headers=auth(socio_case))
    assert response.status_code == 422


def test_mobile_statement_rejects_too_large_range_and_foreign_account(socio_case):
    with TestClient(app) as client:
        too_wide = client.get(f"/api/v1/socio/cuentas/{socio_case['account']}/extracto?desde=2024-01-01&hasta=2025-01-02", headers=auth(socio_case))
        foreign = client.get(f"/api/v1/socio/cuentas/{socio_case['ids']['accounts'][1]}/extracto", headers=auth(socio_case))
    assert too_wide.status_code == 422
    assert foreign.status_code == 404


def test_mobile_credit_list_returns_only_own_credit(socio_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/socio/creditos", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == socio_case["ids"]["credits"][0]
    assert response.json()[0]["proxima_cuota"]["monto_total"] == "510.00"


def test_mobile_credit_debt_reuses_due_calculation(socio_case):
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/deuda", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert response.json()["total_a_pagar"] == "510.00"


def test_mobile_credit_schedule_uses_mobile_fields_and_ownership(socio_case):
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/cronograma", headers=auth(socio_case))
        foreign = client.get(f"/api/v1/socio/creditos/999999999/cronograma", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    row = response.json()["cuotas"][0]
    assert {"numero", "fecha_vencimiento", "capital", "interes", "seguro", "monto_total", "saldo_capital", "estado", "fecha_pago"} <= row.keys()
    assert foreign.status_code == 404


def test_mobile_payment_uses_movil_channel_and_own_user(socio_case):
    with SessionLocal() as db:
        db.query(models.CuentaAhorro).filter_by(id=socio_case["account"]).update({"saldo_disponible": Decimal("1000.00")})
        db.commit()
    with TestClient(app) as client:
        response = client.post(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/pagos", json={"cuenta_ahorro_id": socio_case["account"]}, headers=auth(socio_case))
    assert response.status_code == 201, response.text
    assert response.json()["total"] == "510.00"
    with TestClient(app) as client:
        history = client.get(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/pagos", headers=auth(socio_case))
    assert history.status_code == 200, history.text
    assert [row["id"] for row in history.json()] == [response.json()["id"]]
    with SessionLocal() as db:
        pago = db.query(models.PagoCuota).filter_by(id=response.json()["id"]).one()
        assert pago.usuario_id == socio_case["ids"]["user"]
        assert db.execute(text("SELECT canal FROM transaccion WHERE id=:id"), {"id": pago.transaccion_id}).scalar_one() == "MOVIL"


def test_mobile_payment_history_is_own_credit_history(socio_case):
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/pagos", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_mobile_payment_hides_foreign_account_and_maps_insufficient_balance_to_422(socio_case):
    with TestClient(app) as client:
        foreign = client.post(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/pagos",
            json={"cuenta_ahorro_id": socio_case["ids"]["accounts"][1]}, headers=auth(socio_case))
    assert foreign.status_code == 404
    with SessionLocal() as db:
        db.query(models.CuentaAhorro).filter_by(id=socio_case["account"]).update({"saldo_disponible": Decimal("20.00")})
        db.commit()
    with TestClient(app) as client:
        insufficient = client.post(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/pagos",
            json={"cuenta_ahorro_id": socio_case["account"]}, headers=auth(socio_case))
    assert insufficient.status_code == 422
    if len(socio_case["ids"]["accounts"]) > 2:
        with SessionLocal() as db:
            db.query(models.CuentaAhorro).filter_by(id=socio_case["account"]).update({"saldo_disponible": Decimal("1000.00")})
            db.commit()
        with TestClient(app) as client:
            currency = client.post(f"/api/v1/socio/creditos/{socio_case['ids']['credits'][0]}/pagos",
                json={"cuenta_ahorro_id": socio_case["ids"]["accounts"][2]}, headers=auth(socio_case))
        assert currency.status_code == 422


def test_mobile_dpf_list_is_empty_for_new_member(socio_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/socio/dpf", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_mobile_dpf_detail_returns_contract_fields_and_schedule(socio_case):
    with SessionLocal() as db:
        dpf = models.DepositoPlazoFijo(monto=Decimal("1000"), tasa_interes_anual=Decimal("5"), plazo_dias=90,
            fecha_inicio=date.today(), fecha_vencimiento=date.today() + timedelta(days=90), interes_calculado=Decimal("12.50"),
            estado="VIGENTE", socio_id=socio_case["socio"], moneda_id=1, numero_certificado="MOB-DPF-" + uuid4().hex[:8],
            modalidad_pago_interes="MENSUAL", interes_bruto=Decimal("12.50"), retencion_rciva=Decimal("1.63"), interes_neto=Decimal("10.87"),
            cuenta_abono_id=socio_case["account"], usuario_id=socio_case["ids"]["user"], cooperativa_id=socio_case["ids"]["coop"])
        db.add(dpf); db.flush(); socio_case["ids"]["dpfs"].append(dpf.id)
        schedule = models.DPFCronograma(deposito_plazo_fijo_id=dpf.id, numero=1, fecha_pago=date.today()+timedelta(days=30), dias=30,
            interes_bruto=Decimal("4.17"), retencion_rciva=Decimal("0.54"), interes_neto=Decimal("3.63"), estado="PENDIENTE")
        db.add(schedule); db.flush(); socio_case["ids"]["dpf_schedules"].append(schedule.id); db.commit()
        dpf_id = dpf.id
    with TestClient(app) as client:
        response = client.get(f"/api/v1/socio/dpf/{dpf_id}", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert response.json()["monto"] == "1000.00"
    assert len(response.json()["cronograma"]) == 1


def test_mobile_recredit_list_is_empty_for_new_member(socio_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/socio/recreditos", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_mobile_recredit_accepts_offer_with_optional_term(socio_case):
    offer_id = add_offer(socio_case)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/socio/recreditos/{offer_id}/aceptar", json={"plazo_meses": 9}, headers=auth(socio_case))
    assert response.status_code == 201, response.text
    assert response.json()["solicitud"]["id"]
    assert response.json()["oferta"]["estado"] == "ACEPTADA"
    with SessionLocal() as db:
        offer = db.get(models.OfertaRecredito, offer_id)
        case_request = offer.solicitud_generada_id
        assert db.get(models.SolicitudCredito, case_request).usuario_id == socio_case["ids"]["user"]
        socio_case["ids"]["generated_requests"].append(case_request)


def test_mobile_recredit_discards_own_offer(socio_case):
    offer_id = add_offer(socio_case)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/socio/recreditos/{offer_id}/descartar", headers=auth(socio_case))
    assert response.status_code == 200, response.text
    assert response.json()["estado"] == "DESCARTADA"


def test_mobile_recredit_cannot_discover_foreign_offer(socio_case):
    offer_id = add_offer(socio_case)
    with SessionLocal() as db:
        foreign = db.query(models.Socio).filter_by(id=socio_case["ids"]["foreign_socio"]).one()
        offer = db.get(models.OfertaRecredito, offer_id)
        offer.socio_id = foreign.id
        db.commit()
    with TestClient(app) as client:
        response = client.post(f"/api/v1/socio/recreditos/{offer_id}/aceptar", headers=auth(socio_case))
    assert response.status_code == 404


def test_mobile_recredit_rejects_expired_offer(socio_case):
    offer_id = add_offer(socio_case, days=-1)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/socio/recreditos/{offer_id}/aceptar", headers=auth(socio_case))
    assert response.status_code == 422
    assert response.json()["detail"] == "La oferta no está vigente"
    with SessionLocal() as db:
        db.query(models.OfertaRecredito).filter_by(id=offer_id).update({"estado": "EXPIRADA"})
        db.commit()
    discard_id = add_offer(socio_case, days=-1)
    with TestClient(app) as client:
        discard = client.post(f"/api/v1/socio/recreditos/{discard_id}/descartar", headers=auth(socio_case))
    assert discard.status_code == 422
