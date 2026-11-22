"""Bolivian regulatory rules: ASFI credit types, social housing (VIS) and TEA/TEAC."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.services import informes_asfi
from app.services.amortizacion import generar_plan_pagos


def _db_disponible():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
needs_db = pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos de prueba no está disponible")


# --- S1: ASFI credit types -------------------------------------------------

@pytest.mark.parametrize("tipo,dias,categoria", [
    ("VIVIENDA_SOCIAL", 30, "A"), ("VIVIENDA_SOCIAL", 31, "B"), ("VIVIENDA_SOCIAL", 361, "F"),
    ("PYME", 5, "A"), ("PYME", 6, "B"), ("PYME", 91, "F"),
    ("EMPRESARIAL", 30, "B"), ("EMPRESARIAL", 31, "C"),
])
def test_new_types_use_their_arrears_schedule(tipo, dias, categoria):
    assert informes_asfi.categoria_por_mora(tipo, dias) == categoria


@pytest.mark.parametrize("tipo,moneda,categoria,productivo,esperada", [
    ("VIVIENDA_SOCIAL", "BOB", "A", False, "0.25"),
    ("VIVIENDA_SOCIAL", "USD", "B", False, "5"),
    ("PYME", "BOB", "A", True, "0"),
    ("PYME", "BOB", "A", False, "0.25"),
    ("EMPRESARIAL", "BOB", "B", True, "2.5"),
    ("EMPRESARIAL", "USD", "A", False, "2.5"),
    ("PYME", "BOB", "D", False, "50"),
])
def test_new_types_specific_reserve(tipo, moneda, categoria, productivo, esperada):
    assert informes_asfi.porcentaje_prevision_especifica(categoria, tipo, moneda, productivo) == Decimal(esperada)


@pytest.mark.parametrize("tipo,moneda,esperada", [
    ("VIVIENDA_SOCIAL", "BOB", "1.05"), ("PYME", "BOB", "1.10"), ("EMPRESARIAL", "USD", "1.90"),
])
def test_new_types_cyclical_reserve(tipo, moneda, esperada):
    assert informes_asfi.porcentaje_prevision_ciclica("A", tipo, moneda) == Decimal(esperada)


def test_existing_types_keep_their_reserve():
    assert informes_asfi.categoria_por_mora("CONSUMO", 6) == "B"
    assert informes_asfi.porcentaje_prevision_especifica("A", "VIVIENDA_SIN_GARANTIA", "BOB") == Decimal("3")
    with pytest.raises(ValueError):
        informes_asfi.categoria_por_mora(None, 0)


# --- S3: TEA / TEAC --------------------------------------------------------

def _plan(seguro="0.00", tasa="12.00"):
    return generar_plan_pagos(
        monto=Decimal("10000.00"), tasa_anual=Decimal(tasa), plazo_meses=12,
        tipo_amortizacion="FRANCES", fecha_desembolso=date(2026, 1, 15),
        tasa_seguro_desgravamen=Decimal(seguro),
    )


def test_plan_exposes_tea_from_nominal_rate():
    assert _plan()["tea"] == Decimal("12.68")
    assert _plan(tasa="0.00")["tea"] == Decimal("0.00")


def test_teac_matches_tea_without_insurance_and_grows_with_insurance():
    assert _plan()["teac"] == Decimal("12.68")
    con_seguro = _plan(seguro="0.10")
    assert con_seguro["teac"] > con_seguro["tea"]
    assert con_seguro["teac"] == Decimal("14.03")


def test_plan_keeps_its_previous_totals():
    plan = _plan()
    assert plan["total_capital"] == Decimal("10000.00")
    assert plan["total_pagar"] == plan["total_capital"] + plan["total_interes"] + plan["total_seguro"]


# --- S1/S2 API -------------------------------------------------------------

def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def vis_fixture():
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        coop = models.Cooperativa(nombre=f"VIS Test {suffix}", estado="ACTIVO")
        db.add(coop)
        db.flush()
        roles = {r.nombre: r.id for r in db.query(models.Rol).all()}
        admin = models.Usuario(
            correo=f"vis-{suffix}@test.invalid", contrasena=hash_password("Password123"),
            rol_id=roles["ADMINISTRADOR"], cooperativa_id=coop.id, nombre="VIS Admin", estado="ACTIVO",
        )
        db.add(admin)
        bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
        products = {}
        for code, tipo, tasa in (("VIS", "VIVIENDA_SOCIAL", "6.00"), ("CON", "CONSUMO", "18.00")):
            products[code] = models.ProductoCredito(
                cooperativa_id=coop.id, codigo=f"{suffix.upper()}-{code}", nombre=f"Prod {code}",
                moneda_id=bob_id, monto_min=Decimal("1000.00"), monto_max=Decimal("900000.00"),
                plazo_min_meses=1, plazo_max_meses=240, tasa_interes_anual=Decimal(tasa),
                tipo_amortizacion="FRANCES", tipo_credito_asfi=tipo, estado="ACTIVO",
            )
            db.add(products[code])
        socio = models.Socio(cooperativa_id=coop.id, ci=f"{suffix.upper()}-1", nombre="Vera",
                             apellido="Social", estado="ACTIVO")
        db.add(socio)
        db.flush()
        token, _ = create_access_token(str(admin.id), "ADMINISTRADOR", coop.id)
        data = {"coop_id": coop.id, "admin_id": admin.id, "token": token, "socio_id": socio.id,
                "vis_id": products["VIS"].id, "con_id": products["CON"].id, "bob_id": bob_id,
                "suffix": suffix}
        db.commit()
    try:
        yield data
    finally:
        with SessionLocal() as db:
            db.query(models.SolicitudCredito).filter(models.SolicitudCredito.socio_id == data["socio_id"]).delete()
            db.query(models.EvaluacionCampo).filter(models.EvaluacionCampo.socio_id == data["socio_id"]).delete()
            db.query(models.Bitacora).filter(models.Bitacora.cooperativa_id == data["coop_id"]).delete()
            db.query(models.ProductoCredito).filter(models.ProductoCredito.cooperativa_id == data["coop_id"]).delete()
            db.query(models.Socio).filter(models.Socio.id == data["socio_id"]).delete()
            db.query(models.Usuario).filter(models.Usuario.id == data["admin_id"]).delete()
            db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id = :c"), {"c": data["coop_id"]})
            db.query(models.Cooperativa).filter(models.Cooperativa.id == data["coop_id"]).delete()
            db.commit()


def _product_payload(fx, **overrides):
    payload = {
        "codigo": f"P{fx['suffix'][:6]}".upper(), "nombre": "Producto nuevo", "moneda_id": fx["bob_id"],
        "monto_min": "1000.00", "monto_max": "5000.00", "plazo_min_meses": 1, "plazo_max_meses": 24,
        "tasa_interes_anual": "6.50", "tipo_amortizacion": "FRANCES", "tipo_credito_asfi": "VIVIENDA_SOCIAL",
    }
    payload.update(overrides)
    return payload


def _request_payload(fx, producto_id, monto="300000.00", **overrides):
    payload = {
        "socio_id": fx["socio_id"], "producto_id": producto_id, "monto": monto, "plazo_meses": 120,
        "destino": "VIVIENDA",
        "evaluacion": {
            "ingreso_mensual": "60000.00", "egreso_mensual": "2000.00", "cuota_deudas_mensual": "0.00",
            "actividad_economica": "Docente", "fuente_ingresos": "DEPENDIENTE",
            "antiguedad_laboral_meses": 36, "calificacion_asfi": "A",
        },
    }
    payload.update(overrides)
    return payload


@needs_db
@pytest.mark.parametrize("tipo", ["EMPRESARIAL", "PYME", "VIVIENDA_SOCIAL"])
def test_product_accepts_new_asfi_types(client, vis_fixture, tipo):
    response = client.post("/api/v1/creditos/productos", headers=_auth(vis_fixture["token"]),
                           json=_product_payload(vis_fixture, tipo_credito_asfi=tipo, tasa_interes_anual="6.00"))
    assert response.status_code == 201, response.text
    assert response.json()["tipo_credito_asfi"] == tipo


@needs_db
def test_vis_product_rate_cannot_exceed_6_50(client, vis_fixture):
    message = "La tasa de un producto de vivienda de interés social no puede superar 6.50% anual"
    created = client.post("/api/v1/creditos/productos", headers=_auth(vis_fixture["token"]),
                          json=_product_payload(vis_fixture, tasa_interes_anual="6.51"))
    assert created.status_code == 400 and created.json()["detail"] == message
    updated = client.put(f"/api/v1/creditos/productos/{vis_fixture['vis_id']}",
                         headers=_auth(vis_fixture["token"]), json={"tasa_interes_anual": "7.00"})
    assert updated.status_code == 400 and updated.json()["detail"] == message
    other = client.post("/api/v1/creditos/productos", headers=_auth(vis_fixture["token"]),
                        json=_product_payload(vis_fixture, tipo_credito_asfi="CONSUMO", tasa_interes_anual="18.00"))
    assert other.status_code == 201


@needs_db
@pytest.mark.parametrize("vivienda,detail", [
    (None, "La solicitud de vivienda de interés social requiere los datos de la vivienda"),
    ({"valor_vivienda": "500000.00", "tipo_inmueble": "CASA", "unica_vivienda": False},
     "La vivienda de interés social debe ser la única vivienda del socio"),
    ({"valor_vivienda": "1196001.00", "tipo_inmueble": "CASA", "unica_vivienda": True},
     "El valor de la vivienda supera el límite de vivienda de interés social"),
    ({"valor_vivienda": "1040001.00", "tipo_inmueble": "DEPARTAMENTO", "unica_vivienda": True},
     "El valor de la vivienda supera el límite de vivienda de interés social"),
    ({"valor_vivienda": "650000.00", "tipo_inmueble": "CASA", "unica_vivienda": True},
     "La tasa del producto supera la tasa máxima de vivienda de interés social (5.50% anual)"),
])
def test_vis_request_rejections(client, vis_fixture, vivienda, detail):
    payload = _request_payload(vis_fixture, vis_fixture["vis_id"])
    if vivienda is not None:
        payload["vivienda_social"] = vivienda
    response = client.post("/api/v1/creditos/solicitudes", headers=_auth(vis_fixture["token"]), json=payload)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == detail


@needs_db
def test_vis_request_stores_housing_data_and_returns_it(client, vis_fixture):
    payload = _request_payload(vis_fixture, vis_fixture["vis_id"], vivienda_social={
        "valor_vivienda": "780000.00", "tipo_inmueble": "CASA", "unica_vivienda": True,
    })
    response = client.post("/api/v1/creditos/solicitudes", headers=_auth(vis_fixture["token"]), json=payload)
    assert response.status_code == 201, response.text
    vis = response.json()["vivienda_social"]
    assert vis == {"valor_vivienda": "780000.00", "tipo_inmueble": "CASA", "unica_vivienda": True,
                   "valor_ufv": "300000.00", "tasa_maxima": "6.00"}
    with SessionLocal() as db:
        stored = db.get(models.SolicitudCredito, response.json()["id"]).datos_declarados
    assert stored["vivienda_social"]["tasa_maxima"] == "6.00"


@needs_db
def test_non_vis_request_ignores_housing_data(client, vis_fixture):
    response = client.post("/api/v1/creditos/solicitudes", headers=_auth(vis_fixture["token"]),
                           json=_request_payload(vis_fixture, vis_fixture["con_id"], monto="5000.00"))
    assert response.status_code == 201, response.text
    assert response.json()["vivienda_social"] is None
