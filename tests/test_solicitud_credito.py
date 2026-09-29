"""Integration tests for CU-W21 loan request registration."""

import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.db.session import engine
from app.models import models
from app.schemas import schemas


def _db_disponible():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()


@pytest.fixture(scope="module")
def client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _request_payload(socio_id, producto_id, **overrides):
    payload = {
        "socio_id": socio_id,
        "producto_id": producto_id,
        "monto": "1200.00",
        "plazo_meses": 12,
        "destino": "CAPITAL_TRABAJO",
        "evaluacion": {
            "ingreso_mensual": "6000.00",
            "egreso_mensual": "2000.00",
            "cuota_deudas_mensual": "500.00",
            "actividad_economica": "Comercio minorista",
            "fuente_ingresos": "INDEPENDIENTE",
            "antiguedad_laboral_meses": 36,
            "calificacion_asfi": "A",
            "coordenadas": "-17.78,-63.18",
            "observaciones": "Verificación en campo",
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def loan_fixture():
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        coops = [
            models.Cooperativa(nombre=f"Loan Test {suffix} {number}", estado="ACTIVO")
            for number in (1, 2)
        ]
        db.add_all(coops)
        db.flush()
        role_ids = {role.nombre: role.id for role in db.query(models.Rol).all()}
        users = {}
        for coop_number, coop in enumerate(coops, start=1):
            for role in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO", "CONTADOR"):
                user = models.Usuario(
                    correo=f"loan-{suffix}-{coop_number}-{role.lower()}@test.invalid",
                    contrasena=hash_password("Password123"),
                    rol_id=role_ids[role],
                    cooperativa_id=coop.id,
                    nombre=f"Loan {role} {coop_number}",
                    estado="ACTIVO",
                )
                db.add(user)
                users[(coop_number, role)] = user
        superadmin = models.Usuario(
            correo=f"loan-{suffix}-superadmin@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=role_ids["SUPERADMIN"],
            cooperativa_id=None,
            nombre="Loan Superadmin",
            estado="ACTIVO",
        )
        db.add(superadmin)
        users[(0, "SUPERADMIN")] = superadmin
        db.flush()

        bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
        product_models = []
        for code, amortization in (("FR", "FRANCES"), ("AL", "ALEMAN")):
            product = models.ProductoCredito(
                cooperativa_id=coops[0].id,
                codigo=f"{suffix.upper()}-{code}",
                nombre=f"Loan Product {amortization}",
                moneda_id=bob_id,
                monto_min=Decimal("1000.00"),
                monto_max=Decimal("50000.00"),
                plazo_min_meses=1,
                plazo_max_meses=60,
                tasa_interes_anual=Decimal("18.00"),
                tipo_amortizacion=amortization,
                dias_gracia_mora=0,
                tasa_mora_anual=Decimal("3.00"),
                relacion_cuota_ingreso_max=Decimal("40.00"),
                requiere_garantia=False,
                estado="ACTIVO",
            )
            db.add(product)
            product_models.append(product)

        socio = models.Socio(
            cooperativa_id=coops[0].id,
            ci=f"{suffix.upper()}-0001",
            nombre=f"Ana {suffix}",
            apellido="Prueba",
            estado="ACTIVO",
        )
        foreign_socio = models.Socio(
            cooperativa_id=coops[1].id,
            ci=f"{suffix.upper()}-0002",
            nombre=f"Luis {suffix}",
            apellido="Ajeno",
            estado="ACTIVO",
        )
        second_local_socio = models.Socio(
            cooperativa_id=coops[0].id,
            ci=f"{suffix.upper()}-0003",
            nombre=f"Elena {suffix}",
            apellido="Segunda",
            estado="ACTIVO",
        )
        db.add_all([socio, foreign_socio, second_local_socio])
        db.flush()
        result = {
            "suffix": suffix,
            "coop_ids": [coop.id for coop in coops],
            "users": {},
            "user_ids": [user.id for user in users.values()],
            "product_ids": [product.id for product in product_models],
            "socio_ids": [socio.id, foreign_socio.id, second_local_socio.id],
            "socios": [socio, foreign_socio],
            "foreign_ci": foreign_socio.ci,
            "local_socio_id": socio.id,
            "second_local_socio_id": second_local_socio.id,
            "foreign_socio_id": foreign_socio.id,
            "products": product_models,
        }
        for key, user in users.items():
            token, _ = create_access_token(str(user.id), key[1], user.cooperativa_id)
            result["users"][key] = {"id": user.id, "token": token}
        db.commit()

    try:
        yield result
    finally:
        with SessionLocal() as db:
            db.query(models.SolicitudCredito).filter(
                models.SolicitudCredito.socio_id.in_(result["socio_ids"])
            ).delete(synchronize_session=False)
            db.query(models.EvaluacionCampo).filter(
                models.EvaluacionCampo.socio_id.in_(result["socio_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Bitacora).filter(
                models.Bitacora.usuario_id.in_(result["user_ids"])
            ).delete(synchronize_session=False)
            db.query(models.ProductoCredito).filter(
                models.ProductoCredito.id.in_(result["product_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Socio).filter(
                models.Socio.id.in_(result["socio_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Usuario).filter(
                models.Usuario.id.in_(result["user_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Cooperativa).filter(
                models.Cooperativa.id.in_(result["coop_ids"])
            ).delete(synchronize_session=False)
            db.execute(text("""
                DELETE FROM secuencia_documento
                WHERE cooperativa_id = ANY(:coop_ids) AND tipo = 'SOLICITUD_CREDITO'
            """), {"coop_ids": result["coop_ids"]})
            db.commit()


def test_credit_request_routes_are_registered():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    paths = set(app.openapi()["paths"])
    assert "/api/v1/creditos/socios/buscar" in paths
    assert "/api/v1/creditos/solicitudes/simulacion" in paths
    assert "/api/v1/creditos/solicitudes" in paths


def test_credit_request_models_and_schemas_are_defined():
    assert models.SolicitudCredito.__tablename__ == "solicitud_credito"
    assert models.EvaluacionCampo.__tablename__ == "evaluacion_campo"
    assert {
        "numero_solicitud",
        "moneda_id",
        "cooperativa_id",
        "destino",
        "fecha_solicitud",
        "fecha_actualizacion",
    } <= set(models.SolicitudCredito.__table__.columns.keys())
    assert {
        "socio_id",
        "cuota_deudas_mensual",
        "actividad_economica",
        "fuente_ingresos",
        "antiguedad_laboral_meses",
        "calificacion_asfi",
        "observaciones",
    } <= set(models.EvaluacionCampo.__table__.columns.keys())
    assert schemas.SolicitudOut
    assert schemas.EvaluacionOut


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_mobile_field_worklist_filters_and_field_sheet_are_tenant_scoped(client, loan_fixture):
    suffix = loan_fixture["suffix"]
    with SessionLocal() as db:
        officer_id = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["id"]
        socio_id = loan_fixture["local_socio_id"]
        product_id = loan_fixture["product_ids"][0]
        currency_id = db.query(models.Moneda.id).filter_by(codigo_iso="BOB").scalar()
        evaluation = models.EvaluacionCampo(
            ingreso_mensual=Decimal("5000"), egreso_mensual=Decimal("2000"),
            capacidad_pago=Decimal("3000"), cuota_deudas_mensual=Decimal("0"),
            fecha=date.today(),
            actividad_economica="Comercio", fuente_ingresos="INDEPENDIENTE",
            antiguedad_laboral_meses=24, calificacion_asfi="A", usuario_id=officer_id,
            socio_id=loan_fixture["second_local_socio_id"], observaciones="Visita verificada",
        )
        db.add(evaluation)
        db.flush()
        pending = models.SolicitudCredito(
            monto=Decimal("1200"), plazo_meses=12, tasa_interes=Decimal("18"),
            estado="PENDIENTE", socio_id=socio_id, usuario_id=officer_id,
            producto_credito_id=product_id, moneda_id=currency_id,
            numero_solicitud=f"M8-{suffix}-1", destino="CAPITAL_TRABAJO",
            destino_detalle="Mercadería", cooperativa_id=loan_fixture["coop_ids"][0],
            canal_origen="MOVIL", datos_declarados={"ingreso_mensual": "6000"},
        )
        complete = models.SolicitudCredito(
            monto=Decimal("1300"), plazo_meses=12, tasa_interes=Decimal("18"),
            estado="PENDIENTE", socio_id=loan_fixture["second_local_socio_id"], usuario_id=officer_id,
            producto_credito_id=product_id, moneda_id=currency_id,
            numero_solicitud=f"M8-{suffix}-2", destino="CAPITAL_TRABAJO",
            cooperativa_id=loan_fixture["coop_ids"][0], canal_origen="VENTANILLA",
            evaluacion=evaluation,
        )
        foreign = models.SolicitudCredito(
            monto=Decimal("1400"), plazo_meses=12, tasa_interes=Decimal("18"),
            estado="PENDIENTE", socio_id=loan_fixture["foreign_socio_id"], usuario_id=officer_id,
            producto_credito_id=product_id, moneda_id=currency_id,
            numero_solicitud=f"M8-{suffix}-3", destino="CAPITAL_TRABAJO",
            cooperativa_id=loan_fixture["coop_ids"][1], canal_origen="MOVIL",
        )
        db.add_all([pending, complete, foreign])
        db.commit()
        pending_id, complete_id, foreign_id = pending.id, complete.id, foreign.id

    headers = _auth(loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"])
    base = "/api/v1/creditos/solicitudes"
    todo = client.get(base, headers=headers, params={"requiere_evaluacion": "true"})
    assert todo.status_code == 200
    assert {row["id"] for row in todo.json()} == {pending_id}
    mobile = client.get(base, headers=headers, params={"canal_origen": "MOVIL"})
    assert mobile.status_code == 200
    assert {row["id"] for row in mobile.json()} == {pending_id}
    completed = client.get(base, headers=headers, params={"requiere_evaluacion": "false"})
    assert completed.status_code == 200
    assert {row["id"] for row in completed.json()} == {complete_id}
    invalid = client.get(base, headers=headers, params={"canal_origen": "APP"})
    assert invalid.status_code == 422

    sheet = client.get(f"{base}/{pending_id}/ficha-campo", headers=headers)
    assert sheet.status_code == 200
    data = sheet.json()
    assert data["solicitud"] == {
        "id": pending_id, "numero_solicitud": f"M8-{suffix}-1", "producto": "Loan Product FRANCES",
        "monto": 1200.0, "plazo_meses": 12, "destino": "CAPITAL_TRABAJO",
        "destino_detalle": "Mercadería", "estado": "PENDIENTE", "canal_origen": "MOVIL",
        "requiere_evaluacion": True,
    }
    assert data["socio"]["id"] == loan_fixture["local_socio_id"]
    assert data["datos_declarados"] == {"ingreso_mensual": "6000"}
    assert data["evaluacion"] is None
    assert data["catalogos"] == {
        "fuentes_ingresos": ["DEPENDIENTE", "INDEPENDIENTE", "MIXTO"],
        "calificaciones_asfi": ["A", "B", "C", "D", "E", "F"],
    }
    foreign_sheet = client.get(f"{base}/{foreign_id}/ficha-campo", headers=headers)
    assert foreign_sheet.status_code == 404
    admin_headers = _auth(loan_fixture["users"][(1, "ADMINISTRADOR")]["token"])
    admin_sheet = client.get(f"{base}/{complete_id}/ficha-campo", headers=admin_headers)
    assert admin_sheet.status_code == 200
    assert admin_sheet.json()["evaluacion"]["observaciones"] == "Visita verificada"


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_socio_search_is_tenant_scoped_and_limited(client, loan_fixture):
    suffix = loan_fixture["suffix"]
    with SessionLocal() as db:
        extra_socios = [
            models.Socio(
                cooperativa_id=loan_fixture["coop_ids"][0],
                ci=f"{suffix.upper()}-{index + 1000:04d}",
                nombre=f"Buscar {suffix} {index:02d}",
                apellido="Limit",
                estado="ACTIVO",
            )
            for index in range(21)
        ]
        db.add_all(extra_socios)
        db.commit()
        loan_fixture["socio_ids"].extend(socio.id for socio in extra_socios)

    token = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    response = client.get(
        "/api/v1/creditos/socios/buscar",
        params={"q": f"buscar {suffix}"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 20
    assert all(item["estado"] == "ACTIVO" for item in response.json())
    assert all(suffix.lower() in item["nombre_completo"].lower() for item in response.json())

    foreign_ci = client.get(
        "/api/v1/creditos/socios/buscar",
        params={"q": loan_fixture["foreign_ci"][:4]},
        headers=_auth(token),
    )
    assert foreign_ci.status_code == 200
    assert all(item["id"] != loan_fixture["socio_ids"][1] for item in foreign_ci.json())


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_simulation_uses_french_and_german_installment_formulas(client, loan_fixture):
    token = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    french = client.post(
        "/api/v1/creditos/solicitudes/simulacion",
        json={"producto_id": loan_fixture["product_ids"][0], "monto": 1200.0, "plazo_meses": 12,
              "ingreso_mensual": "5000.00", "egreso_mensual": "2000.00", "cuota_deudas_mensual": "500.00"},
        headers=_auth(token),
    )
    assert french.status_code == 200, french.text
    assert french.json()["cuota_estimada"] == "110.02"
    assert french.json()["total_intereses_estimado"] == "120.20"
    assert french.json()["capacidad_pago"] == "2500.00"
    assert french.json()["relacion_cuota_ingreso"] == "2.20"
    assert french.json()["supera_relacion_maxima"] is False

    german = client.post(
        "/api/v1/creditos/solicitudes/simulacion",
        json={"producto_id": loan_fixture["product_ids"][1], "monto": 1200.0, "plazo_meses": 12},
        headers=_auth(token),
    )
    assert german.status_code == 200, german.text
    assert german.json()["cuota_estimada"] == "118.00"
    assert german.json()["total_intereses_estimado"] == "117.00"


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_simulation_reports_out_of_range_and_rejects_foreign_product(client, loan_fixture):
    token = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    out_of_range = client.post(
        "/api/v1/creditos/solicitudes/simulacion",
        json={"producto_id": loan_fixture["product_ids"][0], "monto": "500.00", "plazo_meses": 12},
        headers=_auth(token),
    )
    assert out_of_range.status_code == 200, out_of_range.text
    assert out_of_range.json()["dentro_de_rangos"] is False
    assert out_of_range.json()["errores"]

    foreign = client.post(
        "/api/v1/creditos/solicitudes/simulacion",
        json={"producto_id": 999999999, "monto": 1200.0, "plazo_meses": 12},
        headers=_auth(token),
    )
    assert foreign.status_code == 404


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_socio_search_rejects_short_query_and_non_staff(client, loan_fixture):
    admin = loan_fixture["users"][(1, "ADMINISTRADOR")]["token"]
    short = client.get("/api/v1/creditos/socios/buscar", params={"q": "ab"}, headers=_auth(admin))
    assert short.status_code == 400

    no_coop = loan_fixture["users"][(0, "SUPERADMIN")]["token"]
    forbidden = client.get(
        "/api/v1/creditos/socios/buscar", params={"q": "ana"}, headers=_auth(no_coop)
    )
    assert forbidden.status_code == 403


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_create_credit_request_computes_evaluation_correlative_and_audit(client, loan_fixture):
    token = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    first = client.post(
        "/api/v1/creditos/solicitudes",
        json=_request_payload(loan_fixture["local_socio_id"], loan_fixture["product_ids"][0]),
        headers=_auth(token),
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["estado"] == "PENDIENTE"
    assert first_body["numero_solicitud"] == "SOL-000001"
    assert first_body["tasa_interes"] == "18.00"
    assert first_body["moneda"]["codigo_iso"] == "BOB"
    assert first_body["tiene_deudas"] is True
    assert first_body["evaluacion"]["capacidad_pago"] == "3500.00"

    second = client.post(
        "/api/v1/creditos/solicitudes",
        json=_request_payload(loan_fixture["second_local_socio_id"], loan_fixture["product_ids"][0]),
        headers=_auth(token),
    )
    assert second.status_code == 201, second.text
    assert second.json()["numero_solicitud"] == "SOL-000002"

    with SessionLocal() as db:
        audit_count = db.query(models.Bitacora).filter(
            models.Bitacora.usuario_id == loan_fixture["users"][(1, "OFICIAL_CREDITO")]["id"],
            models.Bitacora.modulo == "CREDITOS",
            models.Bitacora.accion == "REGISTRAR_SOLICITUD",
            models.Bitacora.descripcion.contains(str(first_body["id"])),
        ).count()
        assert audit_count == 1


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_create_credit_request_validates_range_fields_and_tenant(client, loan_fixture):
    token = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    base = _request_payload(loan_fixture["local_socio_id"], loan_fixture["product_ids"][0])

    invalid_payloads = []
    amount = dict(base, monto="999.00")
    invalid_payloads.append(amount)
    term = dict(base, plazo_meses=61)
    invalid_payloads.append(term)
    destination = dict(base, destino="UNKNOWN")
    invalid_payloads.append(destination)
    other_without_detail = dict(base, destino="OTRO")
    invalid_payloads.append(other_without_detail)
    invalid_source = dict(base)
    invalid_source["evaluacion"] = dict(base["evaluacion"], fuente_ingresos="OTRO")
    invalid_payloads.append(invalid_source)
    invalid_asfi = dict(base)
    invalid_asfi["evaluacion"] = dict(base["evaluacion"], calificacion_asfi="Z")
    invalid_payloads.append(invalid_asfi)
    negative_income = dict(base)
    negative_income["evaluacion"] = dict(base["evaluacion"], ingreso_mensual="-1.00")
    invalid_payloads.append(negative_income)

    for payload in invalid_payloads:
        response = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(token))
        assert response.status_code == 400, response.text

    foreign_socio = client.post(
        "/api/v1/creditos/solicitudes",
        json=_request_payload(loan_fixture["foreign_socio_id"], loan_fixture["product_ids"][0]),
        headers=_auth(token),
    )
    assert foreign_socio.status_code == 404

    foreign_product = client.post(
        "/api/v1/creditos/solicitudes",
        json=_request_payload(loan_fixture["local_socio_id"], 999999999),
        headers=_auth(token),
    )
    assert foreign_product.status_code == 404


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_create_credit_request_enforces_roles_active_product_and_single_open_request(client, loan_fixture):
    officer = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    cashier = loan_fixture["users"][(1, "CAJERO")]["token"]
    superadmin = loan_fixture["users"][(0, "SUPERADMIN")]["token"]
    payload = _request_payload(loan_fixture["local_socio_id"], loan_fixture["product_ids"][0])

    forbidden = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(cashier))
    assert forbidden.status_code == 403
    no_coop = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(superadmin))
    assert no_coop.status_code == 403

    with SessionLocal() as db:
        product = db.get(models.ProductoCredito, loan_fixture["product_ids"][0])
        product.estado = "INACTIVO"
        db.commit()
    inactive_product = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(officer))
    assert inactive_product.status_code == 400
    with SessionLocal() as db:
        product = db.get(models.ProductoCredito, loan_fixture["product_ids"][0])
        product.estado = "ACTIVO"
        db.commit()

    with SessionLocal() as db:
        socio = db.get(models.Socio, loan_fixture["local_socio_id"])
        socio.estado = "INACTIVO"
        db.commit()
    inactive_socio = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(officer))
    assert inactive_socio.status_code == 409
    with SessionLocal() as db:
        socio = db.get(models.Socio, loan_fixture["local_socio_id"])
        socio.estado = "ACTIVO"
        db.commit()

    created = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(officer))
    assert created.status_code == 201, created.text
    duplicate = client.post("/api/v1/creditos/solicitudes", json=payload, headers=_auth(officer))
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "El socio ya tiene una solicitud en curso"


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_request_list_detail_and_legacy_without_product(client, loan_fixture):
    with SessionLocal() as db:
        legacy = models.SolicitudCredito(
            monto=Decimal("25000.00"),
            plazo_meses=12,
            tasa_interes=Decimal("14.50"),
            calificacion_asfi="A",
            tiene_deudas=False,
            estado="APROBADO",
            socio_id=loan_fixture["local_socio_id"],
            usuario_id=loan_fixture["users"][(1, "OFICIAL_CREDITO")]["id"],
            evaluacion_campo_id=None,
            producto_credito_id=None,
            numero_solicitud=f"LEG-{loan_fixture['suffix']}",
            cooperativa_id=loan_fixture["coop_ids"][0],
        )
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id
        db.commit()

    admin = loan_fixture["users"][(1, "ADMINISTRADOR")]["token"]
    listed = client.get(
        "/api/v1/creditos/solicitudes",
        params={"estado": "APROBADO"},
        headers=_auth(admin),
    )
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json() if row["id"] == legacy_id)
    assert item["producto"] is None
    assert item["moneda"] is None
    assert item["cuota_estimada"] is None
    assert item["relacion_cuota_ingreso"] is None
    assert item["evaluacion"] is None
    assert item["socio"]["nombre_completo"].startswith("Ana ")

    detail = client.get(f"/api/v1/creditos/solicitudes/{legacy_id}", headers=_auth(admin))
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == legacy_id
    assert detail.json()["producto"] is None

    foreign_admin = loan_fixture["users"][(2, "ADMINISTRADOR")]["token"]
    hidden = client.get(f"/api/v1/creditos/solicitudes/{legacy_id}", headers=_auth(foreign_admin))
    assert hidden.status_code == 404


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_request_currency_remains_snapshotted_after_product_currency_changes(client, loan_fixture):
    officer = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    created = client.post(
        "/api/v1/creditos/solicitudes",
        json=_request_payload(loan_fixture["local_socio_id"], loan_fixture["product_ids"][0]),
        headers=_auth(officer),
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["moneda"]["codigo_iso"] == "BOB"

    with SessionLocal() as db:
        usd_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "USD").scalar()
        product = db.get(models.ProductoCredito, loan_fixture["product_ids"][0])
        product.moneda_id = usd_id
        db.commit()

    detail = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}",
        headers=_auth(officer),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["moneda"]["codigo_iso"] == "BOB"


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_request_partial_update_and_annulment(client, loan_fixture):
    officer = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    created = client.post(
        "/api/v1/creditos/solicitudes",
        json=_request_payload(loan_fixture["local_socio_id"], loan_fixture["product_ids"][0]),
        headers=_auth(officer),
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    with SessionLocal() as db:
        usd_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "USD").scalar()
        german_product = db.get(models.ProductoCredito, loan_fixture["product_ids"][1])
        german_product.tasa_interes_anual = Decimal("24.00")
        german_product.moneda_id = usd_id
        db.query(models.SolicitudCredito).filter(
            models.SolicitudCredito.id == request_id
        ).update({models.SolicitudCredito.estado: "OBSERVADA"})
        db.commit()

    updated = client.put(
        f"/api/v1/creditos/solicitudes/{request_id}",
        json={"producto_id": loan_fixture["product_ids"][1], "monto": "1500.00",
              "observaciones": "Actualización parcial", "evaluacion": {"egreso_mensual": "2100.00"}},
        headers=_auth(officer),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["estado"] == "OBSERVADA"
    assert updated.json()["tasa_interes"] == "24.00"
    assert updated.json()["moneda"]["codigo_iso"] == "USD"
    assert updated.json()["producto"]["tipo_amortizacion"] == "ALEMAN"
    assert updated.json()["evaluacion"]["ingreso_mensual"] == "6000.00"
    assert updated.json()["evaluacion"]["egreso_mensual"] == "2100.00"
    assert updated.json()["evaluacion"]["capacidad_pago"] == "3400.00"

    with SessionLocal() as db:
        bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
        db.get(models.ProductoCredito, loan_fixture["product_ids"][1]).moneda_id = bob_id
        db.commit()
    detail = client.get(f"/api/v1/creditos/solicitudes/{request_id}", headers=_auth(officer))
    assert detail.status_code == 200, detail.text
    assert detail.json()["moneda"]["codigo_iso"] == "USD"

    short_reason = client.patch(
        f"/api/v1/creditos/solicitudes/{request_id}/anulacion",
        json={"motivo": "no"},
        headers=_auth(officer),
    )
    assert short_reason.status_code == 400
    cancelled = client.patch(
        f"/api/v1/creditos/solicitudes/{request_id}/anulacion",
        json={"motivo": "Solicitud duplicada"},
        headers=_auth(officer),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["estado"] == "ANULADA"
    assert cancelled.json()["motivo_anulacion"] == "Solicitud duplicada"

    with SessionLocal() as db:
        actions = db.query(models.Bitacora.accion).filter(
            models.Bitacora.usuario_id == loan_fixture["users"][(1, "OFICIAL_CREDITO")]["id"],
            models.Bitacora.modulo == "CREDITOS",
            models.Bitacora.descripcion.contains(str(request_id)),
        ).all()
        assert {action[0] for action in actions} >= {
            "REGISTRAR_SOLICITUD",
            "ACTUALIZAR_SOLICITUD",
            "ANULAR_SOLICITUD",
        }


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_request_cannot_edit_or_annul_closed_state(client, loan_fixture):
    with SessionLocal() as db:
        closed = models.SolicitudCredito(
            monto=Decimal("25000.00"),
            plazo_meses=12,
            tasa_interes=Decimal("14.50"),
            calificacion_asfi="A",
            tiene_deudas=False,
            estado="APROBADO",
            socio_id=loan_fixture["local_socio_id"],
            usuario_id=loan_fixture["users"][(1, "OFICIAL_CREDITO")]["id"],
            numero_solicitud=f"CLOSED-{loan_fixture['suffix']}",
            cooperativa_id=loan_fixture["coop_ids"][0],
        )
        db.add(closed)
        db.flush()
        request_id = closed.id
        db.commit()

    officer = loan_fixture["users"][(1, "OFICIAL_CREDITO")]["token"]
    update = client.put(
        f"/api/v1/creditos/solicitudes/{request_id}", json={"observaciones": "No permitido"},
        headers=_auth(officer),
    )
    assert update.status_code == 409
    annul = client.patch(
        f"/api/v1/creditos/solicitudes/{request_id}/anulacion", json={"motivo": "Cierre de prueba"},
        headers=_auth(officer),
    )
    assert annul.status_code == 409


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="La base de datos local no está disponible")
def test_credit_request_migration_backfills_existing_records_and_indexes():
    expected_request_columns = {
        "numero_solicitud",
        "moneda_id",
        "destino",
        "destino_detalle",
        "observaciones",
        "motivo_anulacion",
        "fecha_solicitud",
        "fecha_actualizacion",
        "cooperativa_id",
    }
    expected_evaluation_columns = {
        "socio_id",
        "cuota_deudas_mensual",
        "actividad_economica",
        "fuente_ingresos",
        "antiguedad_laboral_meses",
        "calificacion_asfi",
        "observaciones",
    }
    with engine.connect() as connection:
        request_columns = set(connection.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'solicitud_credito'
        """)).scalars())
        evaluation_columns = set(connection.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'evaluacion_campo'
        """)).scalars())
        assert expected_request_columns <= request_columns
        assert expected_evaluation_columns <= evaluation_columns

        invalid_requests = connection.execute(text("""
            SELECT COUNT(*) FROM solicitud_credito sc
            LEFT JOIN socio s ON s.id = sc.socio_id
            WHERE sc.numero_solicitud IS NULL
               OR sc.cooperativa_id IS DISTINCT FROM s.cooperativa_id
        """)).scalar_one()
        assert invalid_requests == 0

        invalid_currency_snapshots = connection.execute(text("""
            SELECT COUNT(*) FROM solicitud_credito sc
            LEFT JOIN producto_credito p ON p.id = sc.producto_credito_id
            WHERE (sc.producto_credito_id IS NULL AND sc.moneda_id IS NOT NULL)
               OR (sc.producto_credito_id IS NOT NULL AND sc.moneda_id IS DISTINCT FROM p.moneda_id)
        """)).scalar_one()
        assert invalid_currency_snapshots == 0

        invalid_evaluations = connection.execute(text("""
            SELECT COUNT(*) FROM solicitud_credito sc
            JOIN evaluacion_campo e ON e.id = sc.evaluacion_campo_id
            WHERE e.socio_id IS DISTINCT FROM sc.socio_id
        """)).scalar_one()
        assert invalid_evaluations == 0

        unique_definition = connection.execute(text("""
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'solicitud_credito'::regclass
              AND conname = 'uq_solicitud_credito_coop_numero'
        """)).scalar_one_or_none()
        assert unique_definition is not None
        assert "UNIQUE (cooperativa_id, numero_solicitud)" in unique_definition
        index_definition = connection.execute(text("""
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = current_schema() AND tablename = 'solicitud_credito'
              AND indexname = 'uq_solicitud_credito_un_socio_en_curso'
        """)).scalar_one_or_none()
        assert index_definition is not None
        assert "UNIQUE INDEX" in index_definition
        assert "WHERE" in index_definition
        assert all(state in index_definition for state in ("PENDIENTE", "OBSERVADA", "EN_EVALUACION"))
