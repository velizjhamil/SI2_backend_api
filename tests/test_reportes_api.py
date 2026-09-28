"""Tenant-scoped read-only management report endpoints (CU-W33)."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
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
def report_case():
    suffix = uuid4().hex[:10]
    ids = {key: [] for key in ("coops", "users", "socios", "products", "applications", "credits", "installments", "savings", "dpfs", "vouchers", "details", "rates")}
    with SessionLocal() as db:
        role = db.query(models.Rol).filter_by(nombre="ADMINISTRADOR").one()
        coop = models.Cooperativa(nombre=f"Reportes {suffix}", estado="ACTIVO")
        db.add(coop); db.flush(); ids["coops"].append(coop.id)
        user = models.Usuario(correo=f"reportes-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                              rol_id=role.id, cooperativa_id=coop.id, nombre="Reportes", estado="ACTIVO")
        db.add(user); db.flush(); ids["users"].append(user.id)
        socio = models.Socio(ci=f"R{suffix}", nombre="Test", apellido="Reportes", cooperativa_id=coop.id, estado="ACTIVO")
        db.add(socio); db.flush(); ids["socios"].append(socio.id)
        savings = models.CuentaAhorro(numero=f"R{suffix}", tipo_producto="VISTA", saldo_disponible=Decimal("75.55"),
              saldo_bloqueado=0, estado="ACTIVA", fecha_registro=date.today(), socio_id=socio.id, moneda_id=1)
        db.add(savings); db.flush(); ids["savings"].append(savings.id)
        dpf = models.DepositoPlazoFijo(monto=Decimal("200.25"), tasa_interes_anual=1, plazo_dias=60,
              fecha_inicio=date.today()-timedelta(days=45), fecha_vencimiento=date.today()+timedelta(days=15),
              interes_calculado=0, estado="VIGENTE", socio_id=socio.id, moneda_id=1,
              numero_certificado=f"R{suffix}")
        db.add(dpf); db.flush(); ids["dpfs"].append(dpf.id)
        product = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"R{suffix}", nombre="Consumo",
              moneda_id=1, monto_min=1, monto_max=100000, plazo_min_meses=1, plazo_max_meses=60,
              tasa_interes_anual=0, tipo_amortizacion="FRANCES", dias_gracia_mora=0, estado="ACTIVO")
        db.add(product); db.flush(); ids["products"].append(product.id)
        request = models.SolicitudCredito(monto=100, plazo_meses=12, tasa_interes=0, socio_id=socio.id,
              usuario_id=user.id, producto_credito_id=product.id, numero_solicitud=f"R{suffix}", destino="CONSUMO",
              moneda_id=1, cooperativa_id=coop.id)
        db.add(request); db.flush(); ids["applications"].append(request.id)
        credit = models.Credito(monto_aprobado=100, saldo_pendiente=100, estado="VIGENTE",
              solicitud_credito_id=request.id, numero_credito=f"R{suffix}", cooperativa_id=coop.id,
              socio_id=socio.id, producto_credito_id=product.id, moneda_id=1,
              fecha_desembolso=date.today() - timedelta(days=14), usuario_id=user.id)
        db.add(credit); db.flush(); ids["credits"].append(credit.id)
        past = models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=date.today()-timedelta(days=30),
              monto_capital=10, monto_interes=0, monto_cuota_total=10, estado_pago="PENDIENTE", credito_id=credit.id)
        db.add(past); db.flush(); ids["installments"].append(past.id)
        account_ids = {code: db.execute(text("SELECT id FROM plan_cuenta WHERE codigo=:code AND cooperativa_id IS NULL"), {"code": code}).scalar_one()
                       for code in ("111.01", "211.00", "212.00", "139.01", "311.01", "511.01")}
        voucher_id = db.execute(text("""
            INSERT INTO comprobante_contable
              (tipo,glosa,es_automatico,cooperativa_id,numero,gestion,fecha_contable,moneda_id,estado,usuario_id,origen)
            VALUES ('INGRESO','W33 hand-calculated fixture',FALSE,:coop,:number,:year,:day,1,'REGISTRADO',:user,'MANUAL')
            RETURNING id
        """), {"coop": coop.id, "number": f"R{suffix}", "year": date.today().year,
                "day": date.today(), "user": user.id}).scalar_one()
        ids["vouchers"].append(voucher_id)
        # Deliberately hand-calculable balances: cash 100; liabilities 75; provision 10;
        # equity 5; YTD income 10. The overdue portfolio fixture is 100.
        for code, debit, credit in (("111.01", 100, 0), ("211.00", 0, 50), ("212.00", 0, 25),
                                    ("139.01", 0, 10), ("311.01", 0, 5), ("511.01", 0, 10)):
            detail_id = db.execute(text("""
                INSERT INTO detalle_asiento (debe,haber,comprobante_contable_id,plan_cuenta_id)
                VALUES (:debit,:credit,:voucher,:account) RETURNING id
            """), {"debit": debit, "credit": credit, "voucher": voucher_id,
                    "account": account_ids[code]}).scalar_one()
            ids["details"].append(detail_id)
        db.commit()
        token, _ = create_access_token(str(user.id), "ADMINISTRADOR", coop.id)
    try:
        yield {"ids": ids, "token": token, "coop_id": ids["coops"][0]}
    finally:
        with SessionLocal() as db:
            for table, key in (("tabla_amortizacion", "installments"), ("credito", "credits"),
                               ("detalle_asiento", "details"), ("comprobante_contable", "vouchers"),
                               ("tipo_cambio", "rates"),
                               ("solicitud_credito", "applications"), ("producto_credito", "products"),
                               ("deposito_plazo_fijo", "dpfs"), ("cuenta_ahorro", "savings"),
                               ("socio", "socios"), ("usuario", "users"), ("cooperativa", "coops")):
                if ids[key]:
                    db.execute(text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": ids[key]})
            db.commit()


def auth(case):
    return {"Authorization": f"Bearer {case['token']}"}


def test_cartera_uses_w28_mora_classification_and_formats_amounts(report_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/cartera", headers=auth(report_case))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totales"]["cartera_bruta"] == "100.00"
    assert body["totales"]["cartera_en_mora"] == "100.00"
    assert body["totales"]["indice_mora"] == "100.00"
    assert body["totales"]["creditos"] == 1
    assert body["advertencias"] == []


def test_cartera_warns_for_eligible_credits_without_currency(report_case):
    with SessionLocal() as db:
        socio = models.Socio(ci=f"N{uuid4().hex[:10]}", nombre="No", apellido="Currency",
            cooperativa_id=report_case["coop_id"], estado="ACTIVO")
        db.add(socio); db.flush(); report_case["ids"]["socios"].append(socio.id)
        user_id = report_case["ids"]["users"][0]
        product = models.ProductoCredito(cooperativa_id=report_case["coop_id"],
            codigo=f"N{uuid4().hex[:10]}", nombre="Sin moneda", moneda_id=1,
            monto_min=1, monto_max=100000, plazo_min_meses=1, plazo_max_meses=60,
            tasa_interes_anual=0, tipo_amortizacion="FRANCES", dias_gracia_mora=0, estado="ACTIVO")
        db.add(product); db.flush(); report_case["ids"]["products"].append(product.id)
        request = models.SolicitudCredito(monto=250, plazo_meses=12, tasa_interes=0,
            socio_id=socio.id, usuario_id=user_id, producto_credito_id=product.id,
            numero_solicitud=f"N{uuid4().hex[:10]}", destino="CONSUMO", moneda_id=None,
            cooperativa_id=report_case["coop_id"], fecha_solicitud=date.today())
        db.add(request); db.flush(); report_case["ids"]["applications"].append(request.id)
        credit = models.Credito(monto_aprobado=250, saldo_pendiente=Decimal("250.00"),
            estado="VIGENTE", solicitud_credito_id=request.id,
            numero_credito=f"N{uuid4().hex[:10]}", cooperativa_id=report_case["coop_id"],
            socio_id=socio.id, producto_credito_id=product.id, moneda_id=None,
            fecha_desembolso=date.today(), usuario_id=user_id)
        db.add(credit); db.flush(); report_case["ids"]["credits"].append(credit.id)
        db.commit()
    with TestClient(app) as client:
        portfolio = client.get("/api/v1/reportes/cartera", headers=auth(report_case))
        summary = client.get("/api/v1/reportes/resumen-ejecutivo", headers=auth(report_case))
        exported = client.get("/api/v1/reportes/cartera/export", headers=auth(report_case))
    assert portfolio.status_code == 200, portfolio.text
    assert summary.status_code == 200, summary.text
    assert exported.status_code == 200, exported.text
    warning = {"codigo": "CREDITOS_SIN_MONEDA", "creditos": 1, "saldo": "250.00",
               "mensaje": "Hay créditos vigentes sin moneda identificable; se excluyen de los totales por moneda."}
    body = portfolio.json()
    assert body["advertencias"] == [warning]
    assert body["totales"]["cartera_bruta"] == "100.00"
    assert body["totales"]["creditos"] == 1
    assert summary.json()["advertencias"] == [warning]
    assert "CREDITOS_SIN_MONEDA" in exported.text
    assert "250.00" in exported.text


def test_captaciones_groups_savings_and_dpf_by_term_and_expiration(report_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/captaciones", headers=auth(report_case))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ahorros_por_tipo"] == [{"tipo": "VISTA", "cuentas": 1, "saldo": "75.55"}]
    assert {"rango": "31-60", "certificados": 1, "capital": "200.25"} in body["dpf_por_plazo"]
    assert body["dpf_vencimientos_proximos"] == {"certificados": 1, "capital": "200.25"}


def test_indicators_report_management_ratios_and_executive_summary(report_case):
    with TestClient(app) as client:
        indicators = client.get("/api/v1/reportes/indicadores", headers=auth(report_case))
        summary = client.get("/api/v1/reportes/resumen-ejecutivo", headers=auth(report_case))
    assert indicators.status_code == 200, indicators.text
    body = indicators.json()
    assert body["tipo"] == "indicadores de gestión"
    assert {row["clave"] for row in body["indicadores"]} == {"liquidez", "cobertura_previsiones", "roa", "roe"}
    assert all("definicion" in row and row["unidad"] == "%" for row in body["indicadores"])
    values = {row["clave"]: row["valor"] for row in body["indicadores"]}
    assert values == {"liquidez": "133.33", "cobertura_previsiones": "10.00", "roa": "14.97", "roe": "89.79"}, body["indicadores"]
    assert summary.status_code == 200, summary.text
    assert {"cartera", "captaciones", "indicadores"} <= summary.json().keys()


def test_report_exports_include_utf8_bom_and_date_filename(report_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/cartera/export", headers=auth(report_case))
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert f"cartera-{date.today().isoformat()}.csv" in response.headers["content-disposition"]


def test_indicators_return_null_with_reason_when_foreign_activity_lacks_rate(report_case):
    with SessionLocal() as db:
        usd_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='USD'")).scalar_one()
        account_id = db.execute(text("SELECT id FROM plan_cuenta WHERE codigo='111.01' AND cooperativa_id IS NULL")).scalar_one()
        voucher_id = db.execute(text("""
            INSERT INTO comprobante_contable
              (tipo,glosa,es_automatico,cooperativa_id,numero,gestion,fecha_contable,moneda_id,estado,usuario_id,origen)
            VALUES ('INGRESO','W33 missing FX fixture',FALSE,:coop,:number,:year,:day,:currency,'REGISTRADO',:user,'MANUAL')
            RETURNING id
        """), {"coop": report_case["coop_id"], "number": f"U{uuid4().hex[:10]}", "year": date.today().year,
                "day": date.today(), "currency": usd_id, "user": report_case["ids"]["users"][0]}).scalar_one()
        report_case["ids"]["vouchers"].append(voucher_id)
        detail_id = db.execute(text("""
            INSERT INTO detalle_asiento (debe,haber,comprobante_contable_id,plan_cuenta_id)
            VALUES (1,0,:voucher,:account) RETURNING id
        """), {"voucher": voucher_id, "account": account_id}).scalar_one()
        report_case["ids"]["details"].append(detail_id)
        db.commit()
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/indicadores", headers=auth(report_case))
    assert response.status_code == 200, response.text
    indicators = response.json()["indicadores"]
    assert all(item["valor"] is None and item.get("motivo") for item in indicators)


def test_reports_reject_future_cutoff_and_unknown_currency(report_case):
    with TestClient(app) as client:
        future = client.get(f"/api/v1/reportes/cartera?fecha_corte={(date.today()+timedelta(days=1)).isoformat()}", headers=auth(report_case))
        unknown = client.get("/api/v1/reportes/captaciones?moneda_id=32767", headers=auth(report_case))
    assert future.status_code == 422
    assert unknown.status_code == 422


def test_cartera_zero_gross_has_null_index_and_reason(report_case):
    with SessionLocal() as db:
        db.execute(text("UPDATE credito SET estado='PAGADO' WHERE id=:id"), {"id": report_case["ids"]["credits"][0]})
        db.commit()
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/cartera", headers=auth(report_case))
    assert response.status_code == 200, response.text
    totals = response.json()["totales"]
    assert totals["cartera_bruta"] == "0.00"
    assert totals["indice_mora"] is None
    assert totals["motivo_indice_mora"]


def test_captaciones_includes_accounts_registered_later_on_cutoff_day(report_case):
    with SessionLocal() as db:
        db.execute(text("UPDATE cuenta_ahorro SET fecha_registro=:registered WHERE id=:id"),
                   {"registered": datetime.combine(date.today(), time(23, 59, 59)), "id": report_case["ids"]["savings"][0]})
        db.commit()
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/captaciones", headers=auth(report_case))
    assert response.status_code == 200, response.text
    assert response.json()["totales"]["saldo_ahorro"] == "75.55"


def test_captaciones_and_executive_summary_include_definitions(report_case):
    with TestClient(app) as client:
        deposits = client.get("/api/v1/reportes/captaciones", headers=auth(report_case))
        summary = client.get("/api/v1/reportes/resumen-ejecutivo", headers=auth(report_case))
    assert deposits.status_code == 200, deposits.text
    assert deposits.json()["definicion"]
    assert summary.status_code == 200, summary.text
    assert summary.json()["definicion"]


def test_pre_ytd_foreign_posting_prevents_balance_bob_fallback(report_case):
    with SessionLocal() as db:
        usd_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='USD'")).scalar_one()
        account_id = db.execute(text("SELECT id FROM plan_cuenta WHERE codigo='111.01' AND cooperativa_id IS NULL")).scalar_one()
        voucher_id = db.execute(text("""
            INSERT INTO comprobante_contable
              (tipo,glosa,es_automatico,cooperativa_id,numero,gestion,fecha_contable,moneda_id,estado,usuario_id,origen)
            VALUES ('INGRESO','W33 pre-YTD FX fixture',FALSE,:coop,:number,:year,:day,:currency,'REGISTRADO',:user,'MANUAL')
            RETURNING id
        """), {"coop": report_case["coop_id"], "number": f"P{uuid4().hex[:10]}",
                "year": date.today().year-1, "day": date(date.today().year-1,12,31),
                "currency": usd_id, "user": report_case["ids"]["users"][0]}).scalar_one()
        report_case["ids"]["vouchers"].append(voucher_id)
        detail_id = db.execute(text("""
            INSERT INTO detalle_asiento (debe,haber,comprobante_contable_id,plan_cuenta_id)
            VALUES (1,0,:voucher,:account) RETURNING id
        """), {"voucher": voucher_id, "account": account_id}).scalar_one()
        report_case["ids"]["details"].append(detail_id)
        db.commit()
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/indicadores", headers=auth(report_case))
    assert response.status_code == 200, response.text
    indicators = {row["clave"]: row for row in response.json()["indicadores"]}
    assert indicators["liquidez"]["valor"] is None
    assert "tipo de cambio" in indicators["liquidez"]["motivo"]
    # ROA still needs cumulative balance assets, which cannot be converted without that prior-year rate.
    assert indicators["roa"]["valor"] is None


def test_coverage_converts_foreign_portfolio_to_bob(report_case):
    with SessionLocal() as db:
        usd_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='USD'")).scalar_one()
        foreign_socio = models.Socio(ci=f"U{uuid4().hex[:10]}", nombre="Test", apellido="Foreign",
            cooperativa_id=report_case["coop_id"], estado="ACTIVO")
        db.add(foreign_socio); db.flush(); report_case["ids"]["socios"].append(foreign_socio.id)
        request = models.SolicitudCredito(monto=100, plazo_meses=12, tasa_interes=0,
            socio_id=foreign_socio.id,
            usuario_id=report_case["ids"]["users"][0], producto_credito_id=report_case["ids"]["products"][0],
            numero_solicitud=f"U{uuid4().hex[:10]}", destino="CONSUMO", moneda_id=usd_id,
            cooperativa_id=report_case["coop_id"])
        db.add(request); db.flush(); report_case["ids"]["applications"].append(request.id)
        credit = models.Credito(monto_aprobado=100, saldo_pendiente=100, estado="VIGENTE",
            solicitud_credito_id=request.id, numero_credito=f"U{uuid4().hex[:10]}", cooperativa_id=report_case["coop_id"],
            socio_id=request.socio_id, producto_credito_id=report_case["ids"]["products"][0], moneda_id=usd_id,
            fecha_desembolso=date.today()-timedelta(days=14), usuario_id=report_case["ids"]["users"][0])
        db.add(credit); db.flush(); report_case["ids"]["credits"].append(credit.id)
        installment = models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=date.today()-timedelta(days=30),
            monto_capital=10, monto_interes=0, monto_cuota_total=10, estado_pago="PENDIENTE", credito_id=credit.id)
        db.add(installment); db.flush(); report_case["ids"]["installments"].append(installment.id)
        rate = models.TipoCambio(cooperativa_id=report_case["coop_id"], moneda_id=usd_id, fecha=date.today(),
            valor=Decimal("6.90000"), fuente="TEST", usuario_id=report_case["ids"]["users"][0])
        db.add(rate); db.flush(); report_case["ids"]["rates"].append(rate.id)
        db.commit()
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/indicadores", headers=auth(report_case))
    assert response.status_code == 200, response.text
    indicators = {row["clave"]: row for row in response.json()["indicadores"]}
    assert indicators["cobertura_previsiones"]["valor"] == "1.27"


def test_missing_portfolio_fx_nulls_coverage_without_nulling_other_indicators(report_case):
    with SessionLocal() as db:
        usd_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='USD'")).scalar_one()
        socio = models.Socio(ci=f"M{uuid4().hex[:10]}", nombre="Test", apellido="NoRate",
            cooperativa_id=report_case["coop_id"], estado="ACTIVO")
        db.add(socio); db.flush(); report_case["ids"]["socios"].append(socio.id)
        request = models.SolicitudCredito(monto=100, plazo_meses=12, tasa_interes=0,
            socio_id=socio.id, usuario_id=report_case["ids"]["users"][0],
            producto_credito_id=report_case["ids"]["products"][0], numero_solicitud=f"M{uuid4().hex[:10]}",
            destino="CONSUMO", moneda_id=usd_id, cooperativa_id=report_case["coop_id"])
        db.add(request); db.flush(); report_case["ids"]["applications"].append(request.id)
        credit = models.Credito(monto_aprobado=100, saldo_pendiente=100, estado="VIGENTE",
            solicitud_credito_id=request.id, numero_credito=f"M{uuid4().hex[:10]}", cooperativa_id=report_case["coop_id"],
            socio_id=socio.id, producto_credito_id=report_case["ids"]["products"][0], moneda_id=usd_id,
            fecha_desembolso=date.today()-timedelta(days=14), usuario_id=report_case["ids"]["users"][0])
        db.add(credit); db.flush(); report_case["ids"]["credits"].append(credit.id)
        installment = models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=date.today()-timedelta(days=30),
            monto_capital=10, monto_interes=0, monto_cuota_total=10, estado_pago="PENDIENTE", credito_id=credit.id)
        db.add(installment); db.flush(); report_case["ids"]["installments"].append(installment.id)
        db.commit()
    with TestClient(app) as client:
        response = client.get("/api/v1/reportes/indicadores", headers=auth(report_case))
    assert response.status_code == 200, response.text
    indicators = {row["clave"]: row for row in response.json()["indicadores"]}
    assert indicators["cobertura_previsiones"]["valor"] is None
    assert "tipo de cambio" in indicators["cobertura_previsiones"]["motivo"]
    assert indicators["liquidez"]["valor"] == "133.33"


def test_cartera_legacy_credit_uses_request_currency_and_effective_date_and_matches_w28(report_case):
    request_date = date.today() - timedelta(days=45)
    with SessionLocal() as db:
        socio = models.Socio(ci=f"L{uuid4().hex[:10]}", nombre="Legacy", apellido="Report",
            cooperativa_id=report_case["coop_id"], estado="ACTIVO")
        db.add(socio); db.flush(); report_case["ids"]["socios"].append(socio.id)
        request = models.SolicitudCredito(monto=100, plazo_meses=12, tasa_interes=0,
            socio_id=socio.id, usuario_id=report_case["ids"]["users"][0],
            producto_credito_id=report_case["ids"]["products"][0], numero_solicitud=f"L{uuid4().hex[:10]}",
            destino="CONSUMO", moneda_id=1, cooperativa_id=report_case["coop_id"],
            fecha_solicitud=datetime.combine(request_date, time(10), tzinfo=timezone.utc))
        db.add(request); db.flush(); report_case["ids"]["applications"].append(request.id)
        credit = models.Credito(monto_aprobado=100, saldo_pendiente=100, estado="VIGENTE",
            solicitud_credito_id=request.id, numero_credito=f"L{uuid4().hex[:10]}",
            cooperativa_id=report_case["coop_id"], socio_id=socio.id,
            producto_credito_id=report_case["ids"]["products"][0], moneda_id=None,
            fecha_desembolso=None, usuario_id=report_case["ids"]["users"][0])
        db.add(credit); db.flush(); report_case["ids"]["credits"].append(credit.id)
        installment = models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=date.today()-timedelta(days=30),
            monto_capital=10, monto_interes=0, monto_cuota_total=10, estado_pago="PENDIENTE", credito_id=credit.id)
        db.add(installment); db.flush(); report_case["ids"]["installments"].append(installment.id)
        db.commit()
    with TestClient(app) as client:
        portfolio = client.get("/api/v1/reportes/cartera", headers=auth(report_case))
        w28 = client.get("/api/v1/creditos/monitoreo/resumen", headers=auth(report_case))
    assert portfolio.status_code == 200, portfolio.text
    assert w28.status_code == 200, w28.text
    body = portfolio.json()
    assert body["totales"]["cartera_bruta"] == "200.00"
    assert body["totales"]["cartera_en_mora"] == "200.00"
    assert {"mes": request_date.strftime("%Y-%m"), "monto": "100.00", "creditos": 1} in body["colocaciones_mes"]
    bob_summary = next(item for item in w28.json()["por_moneda"] if item["moneda"] == "BOB")
    assert bob_summary["cartera_total"] == body["totales"]["cartera_bruta"]
    assert bob_summary["cartera_en_mora"] == body["totales"]["cartera_en_mora"]
