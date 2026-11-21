"""Portfolio report filters: product, ASFI credit type, officer, placement date and arrears state."""
from datetime import date, timedelta
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
TODAY = date.today()


@pytest.fixture
def case():
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        role = db.query(models.Rol).filter_by(nombre="ADMINISTRADOR").one()
        oficial_role = db.query(models.Rol).filter_by(nombre="OFICIAL_CREDITO").one()
        coop = models.Cooperativa(nombre=f"Filtros {suffix}", estado="ACTIVO")
        db.add(coop); db.flush()

        def user(label, rol):
            row = models.Usuario(correo=f"filtros-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                                 rol_id=rol.id, cooperativa_id=coop.id, nombre=f"{label.title()} {suffix}",
                                 estado="ACTIVO")
            db.add(row); db.flush()
            return row

        admin = user("admin", role)
        ana, beto = user("ana", oficial_role), user("beto", oficial_role)
        def product(code, name, tipo):
            row = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"{code}{suffix}", nombre=name,
                  moneda_id=1, monto_min=1, monto_max=100000, plazo_min_meses=1, plazo_max_meses=60,
                  tasa_interes_anual=0, tipo_amortizacion="FRANCES", dias_gracia_mora=0, estado="ACTIVO",
                  tipo_credito_asfi=tipo)
            db.add(row); db.flush()
            return row

        consumo = product("C", "Consumo", "CONSUMO")
        micro = product("M", "Micro", "MICROCREDITO")
        credits = {}
        for label, prod, oficial, saldo, disbursed, overdue in (
            ("A", consumo, ana, 100, TODAY - timedelta(days=200), True),
            ("B", micro, beto, 200, TODAY - timedelta(days=14), False),
            ("C", consumo, beto, 300, TODAY - timedelta(days=14), False),
        ):
            socio = models.Socio(ci=f"FI{label}{suffix}", nombre="Socio", apellido=label, cooperativa_id=coop.id,
                                 estado="ACTIVO")
            db.add(socio); db.flush()
            request = models.SolicitudCredito(monto=saldo, plazo_meses=12, tasa_interes=0, socio_id=socio.id,
                  usuario_id=oficial.id, producto_credito_id=prod.id, numero_solicitud=f"{label}{suffix}",
                  destino="CONSUMO", moneda_id=1, cooperativa_id=coop.id)
            db.add(request); db.flush()
            credit = models.Credito(monto_aprobado=saldo, saldo_pendiente=saldo, estado="VIGENTE",
                  solicitud_credito_id=request.id, numero_credito=f"{label}{suffix}", cooperativa_id=coop.id,
                  socio_id=socio.id, producto_credito_id=prod.id, moneda_id=1, fecha_desembolso=disbursed,
                  usuario_id=admin.id)
            db.add(credit); db.flush()
            due = TODAY - timedelta(days=40) if overdue else TODAY + timedelta(days=20)
            db.add(models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=due, monto_capital=10, monto_interes=0,
                                            monto_cuota_total=10, estado_pago="PENDIENTE", credito_id=credit.id))
            credits[label] = credit.id
        db.commit()
        data = {"token": create_access_token(str(admin.id), "ADMINISTRADOR", coop.id)[0], "coop_id": coop.id,
                "consumo": consumo.id, "micro": micro.id, "ana": ana.id, "beto": beto.id, "credits": credits}
    yield data
    with SessionLocal() as db:
        ids = list(data["credits"].values())
        db.execute(text("DELETE FROM tabla_amortizacion WHERE credito_id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM credito WHERE id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM solicitud_credito WHERE cooperativa_id = :c"), {"c": data["coop_id"]})
        db.execute(text("DELETE FROM producto_credito WHERE cooperativa_id = :c"), {"c": data["coop_id"]})
        db.execute(text("DELETE FROM socio WHERE cooperativa_id = :c"), {"c": data["coop_id"]})
        db.execute(text("DELETE FROM usuario WHERE cooperativa_id = :c"), {"c": data["coop_id"]})
        db.execute(text("DELETE FROM cooperativa WHERE id = :c"), {"c": data["coop_id"]})
        db.commit()


def _get(case, path="/api/v1/reportes/cartera", **params):
    with TestClient(app) as client:
        return client.get(path, params=params, headers={"Authorization": f"Bearer {case['token']}"})


def _totals(response):
    assert response.status_code == 200, response.text
    return response.json()["totales"]


def test_unfiltered_portfolio_keeps_previous_totals(case):
    body = _get(case).json()
    assert body["totales"]["cartera_bruta"] == "600.00"
    assert body["totales"]["cartera_en_mora"] == "100.00"
    assert body["totales"]["creditos"] == 3
    assert body["filtros"] == {"producto_id": None, "tipo_credito": None, "oficial_id": None,
                               "desde": None, "estado_mora": None}


def test_filter_by_product(case):
    totals = _totals(_get(case, producto_id=case["consumo"]))
    assert totals["cartera_bruta"] == "400.00" and totals["creditos"] == 2


def test_filter_by_asfi_credit_type(case):
    totals = _totals(_get(case, tipo_credito="MICROCREDITO"))
    assert totals["cartera_bruta"] == "200.00" and totals["creditos"] == 1


def test_filter_by_officer(case):
    body = _get(case, oficial_id=case["beto"]).json()
    assert body["totales"]["cartera_bruta"] == "500.00"
    assert {row["producto"] for row in body["por_producto"]} == {"Consumo", "Micro"}


def test_filter_by_placement_date_range(case):
    totals = _totals(_get(case, desde=(TODAY - timedelta(days=30)).isoformat()))
    assert totals["cartera_bruta"] == "500.00" and totals["creditos"] == 2


def test_filter_by_arrears_state(case):
    en_mora = _get(case, estado_mora="EN_MORA").json()
    assert en_mora["totales"]["cartera_bruta"] == "100.00"
    assert [row["creditos"] for row in en_mora["por_rango_mora"] if row["rango"] == "0"] == [0]
    al_dia = _totals(_get(case, estado_mora="AL_DIA"))
    assert al_dia["cartera_bruta"] == "500.00" and al_dia["cartera_en_mora"] == "0.00"


def test_filters_combine(case):
    totals = _totals(_get(case, producto_id=case["consumo"], oficial_id=case["beto"]))
    assert totals["cartera_bruta"] == "300.00" and totals["creditos"] == 1


def test_filters_apply_to_exports(case):
    response = _get(case, "/api/v1/reportes/mora/export", producto_id=case["micro"])
    assert response.status_code == 200
    assert "200.00" in response.text and "600.00" not in response.text


@pytest.mark.parametrize("params", [
    {"tipo_credito": "INVENTADO"}, {"estado_mora": "TAL_VEZ"},
    {"desde": (TODAY + timedelta(days=1)).isoformat()},
])
def test_invalid_filters_are_rejected(case, params):
    assert _get(case, **params).status_code == 422


def test_filter_options_list_products_types_and_officers(case):
    response = _get(case, "/api/v1/reportes/filtros")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["id"] for row in body["productos"]} == {case["consumo"], case["micro"]}
    assert {row["id"] for row in body["oficiales"]} == {case["ana"], case["beto"]}
    assert "MICROCREDITO" in body["tipos_credito"] and "CONSUMO" in body["tipos_credito"]
    assert body["estados_mora"] == ["AL_DIA", "EN_MORA"]
