"""Tests for CU-W25 committee-based credit approval."""

from pathlib import Path
from decimal import Decimal
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.schemas import schemas


MIGRATION = Path(__file__).parents[1] / "migrations" / "020_sprint9_comite_credito.sql"


def _db_available():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()


@pytest.fixture
def client():
    if not DB_AVAILABLE:
        pytest.skip("Local PostgreSQL is unavailable")
    from main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def committee_data():
    if not DB_AVAILABLE:
        pytest.skip("Local PostgreSQL is unavailable")
    suffix = uuid4().hex[:12]
    with SessionLocal() as db:
        roles = {role.nombre: role.id for role in db.query(models.Rol).all()}
        currency_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
        coop = models.Cooperativa(nombre=f"Committee Test {suffix}", razon_social="Test Ltda.", estado="ACTIVO")
        db.add(coop)
        db.flush()
        users = {}
        role_names = ["ADMINISTRADOR", "OFICIAL_CREDITO", "CONTADOR"]
        for name, role_name in zip(("admin", "officer", "accountant"), role_names):
            user = models.Usuario(
                correo=f"committee-{suffix}-{name}@test.invalid",
                contrasena=hash_password("Password123"),
                rol_id=roles[role_name],
                cooperativa_id=coop.id,
                nombre=f"Committee {name}",
                estado="ACTIVO",
            )
            db.add(user)
            users[name] = user
        # A second officer is the registering user; the committee officer above remains eligible.
        registrar = models.Usuario(
            correo=f"committee-{suffix}-registrar@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=roles["OFICIAL_CREDITO"],
            cooperativa_id=coop.id,
            nombre="Committee registrar",
            estado="ACTIVO",
        )
        db.add(registrar)
        db.flush()
        users["registrar"] = registrar
        product = models.ProductoCredito(
            cooperativa_id=coop.id,
            codigo=f"CM-{suffix[:6]}",
            nombre="Committee product",
            moneda_id=currency_id,
            monto_min=Decimal("100.00"),
            monto_max=Decimal("10000.00"),
            plazo_min_meses=1,
            plazo_max_meses=24,
            tasa_interes_anual=Decimal("12.00"),
            tipo_amortizacion="FRANCES",
            dias_gracia_mora=0,
            tasa_mora_anual=Decimal("0.00"),
            relacion_cuota_ingreso_max=Decimal("40.00"),
            requiere_garantia=False,
            monto_aprobacion_directa=Decimal("0.00"),
            estado="ACTIVO",
        )
        db.add(product)
        socio = models.Socio(
            cooperativa_id=coop.id,
            ci=f"CM-{suffix}",
            nombre="Committee",
            apellido="Applicant",
            estado="ACTIVO",
            fecha_registro=date(2020, 1, 1),
        )
        db.add(socio)
        db.flush()
        field_eval = models.EvaluacionCampo(
            ingreso_mensual=Decimal("5000.00"),
            egreso_mensual=Decimal("1000.00"),
            cuota_deudas_mensual=Decimal("0.00"),
            capacidad_pago=Decimal("4000.00"),
            fecha=date.today(),
            usuario_id=users["registrar"].id,
            socio_id=socio.id,
            fuente_ingresos="DEPENDIENTE",
            antiguedad_laboral_meses=36,
            calificacion_asfi="A",
        )
        db.add(field_eval)
        db.flush()
        request = models.SolicitudCredito(
            monto=Decimal("1200.00"),
            plazo_meses=12,
            tasa_interes=Decimal("12.00"),
            estado="EN_COMITE",
            socio_id=socio.id,
            usuario_id=users["registrar"].id,
            evaluacion_campo_id=field_eval.id,
            producto_credito_id=product.id,
            moneda_id=currency_id,
            cooperativa_id=coop.id,
            numero_solicitud=f"CM-{suffix}",
            destino="CAPITAL_TRABAJO",
            ronda_comite=1,
        )
        db.add(request)
        db.flush()
        credit_eval = models.EvaluacionCrediticia(
            solicitud_credito_id=request.id,
            cooperativa_id=coop.id,
            version_modelo="rules-v1",
            score=700,
            dictamen="APROBADO",
            factores=[],
            knockouts=[],
            explicacion="The application is eligible for committee review.",
            usuario_id=users["officer"].id,
        )
        db.add(credit_eval)
        for user in users.values():
            token, _ = create_access_token(str(user.id), user.rol.nombre, user.cooperativa_id)
            user.token = token
        db.commit()
        fixture = {
            "coop_id": coop.id,
            "user_ids": [user.id for user in users.values()],
            "users": {name: {"id": user.id, "token": user.token} for name, user in users.items()},
            "product_id": product.id,
            "socio_id": socio.id,
            "request_id": request.id,
            "field_eval_id": field_eval.id,
            "credit_eval_id": credit_eval.id,
        }
    try:
        yield fixture
    finally:
        with SessionLocal() as db:
            db.query(models.VotoComite).filter(
                models.VotoComite.solicitud_credito_id == fixture["request_id"]
            ).delete(synchronize_session=False)
            db.query(models.EvaluacionCrediticia).filter(
                models.EvaluacionCrediticia.solicitud_credito_id == fixture["request_id"]
            ).delete(synchronize_session=False)
            db.query(models.SolicitudCredito).filter(
                models.SolicitudCredito.id == fixture["request_id"]
            ).delete(synchronize_session=False)
            db.query(models.EvaluacionCampo).filter(
                models.EvaluacionCampo.id == fixture["field_eval_id"]
            ).delete(synchronize_session=False)
            db.query(models.Bitacora).filter(
                models.Bitacora.usuario_id.in_(fixture["user_ids"])
            ).delete(synchronize_session=False)
            db.query(models.ProductoCredito).filter(
                models.ProductoCredito.id == fixture["product_id"]
            ).delete(synchronize_session=False)
            db.query(models.Socio).filter(
                models.Socio.id == fixture["socio_id"]
            ).delete(synchronize_session=False)
            db.query(models.Usuario).filter(
                models.Usuario.id.in_(fixture["user_ids"])
            ).delete(synchronize_session=False)
            db.query(models.Cooperativa).filter(
                models.Cooperativa.id == fixture["coop_id"]
            ).delete(synchronize_session=False)
            db.commit()


def test_committee_migration_and_model_contract_are_declared():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "alter table producto_credito add column if not exists monto_aprobacion_directa" in sql
    assert "create table if not exists voto_comite" in sql
    assert "unique (solicitud_credito_id, ronda, usuario_id)" in sql
    assert "ronda_comite" in sql and "resultado_comite" in sql
    assert "en_comite" in sql
    assert {"ronda_comite", "resultado_comite", "fecha_resolucion_comite"} <= set(
        models.SolicitudCredito.__table__.columns.keys()
    )
    assert "monto_aprobacion_directa" in models.ProductoCredito.__table__.columns
    assert getattr(models, "VotoComite", None) is not None


@pytest.mark.skipif(not DB_AVAILABLE, reason="Local PostgreSQL is unavailable")
def test_committee_migration_applies_twice():
    sql = MIGRATION.read_text(encoding="utf-8")
    for _ in range(2):
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute(sql)
            raw.commit()
            cursor.close()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


@pytest.mark.skipif(not DB_AVAILABLE, reason="Local PostgreSQL is unavailable")
def test_fresh_migration_seeds_demo_limits_but_replay_preserves_admin_edit():
    sql = MIGRATION.read_text(encoding="utf-8").replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("ALTER TABLE producto_credito DROP COLUMN monto_aprobacion_directa")
        cursor.execute(sql)
        cursor.execute(
            """SELECT codigo, monto_aprobacion_directa FROM producto_credito pc
               JOIN cooperativa c ON c.id=pc.cooperativa_id
               WHERE c.nombre='Cooperativa de Prueba SI2'
                 AND pc.codigo IN ('CONS-BOB','MICRO-BOB','VIV-USD')"""
        )
        fresh_values = dict(cursor.fetchall())
        assert fresh_values == {
            "CONS-BOB": Decimal("20000.00"),
            "MICRO-BOB": Decimal("30000.00"),
            "VIV-USD": Decimal("0.00"),
        }

        cursor.execute(
            """UPDATE producto_credito SET monto_aprobacion_directa=12345.67
               WHERE codigo='CONS-BOB'
                 AND cooperativa_id=(SELECT id FROM cooperativa WHERE nombre='Cooperativa de Prueba SI2')"""
        )
        cursor.execute(sql)
        cursor.execute(
            """SELECT monto_aprobacion_directa FROM producto_credito
               WHERE codigo='CONS-BOB'
                 AND cooperativa_id=(SELECT id FROM cooperativa WHERE nombre='Cooperativa de Prueba SI2')"""
        )
        assert cursor.fetchone()[0] == Decimal("12345.67")
    finally:
        raw.rollback()
        raw.close()


def test_committee_schemas_expose_additive_product_and_request_fields():
    assert "monto_aprobacion_directa" in schemas.ProductoCreditoCreate.model_fields
    assert "monto_aprobacion_directa" in schemas.ProductoCreditoUpdate.model_fields
    assert "monto_aprobacion_directa" in schemas.ProductoCreditoOut.model_fields
    assert "ronda_comite" in schemas.SolicitudOut.model_fields
    assert "resultado_comite" in schemas.SolicitudOut.model_fields
    assert schemas.VotoComiteIn(voto="APROBAR", comentario="Comentario válido").voto == "APROBAR"
    assert schemas.VotoOut and schemas.ComiteItemOut and schemas.ActaOut


def test_product_threshold_is_decimal_and_defaults_to_zero():
    threshold = schemas.ProductoCreditoCreate.model_fields["monto_aprobacion_directa"]
    assert threshold.default == Decimal("0.00")


def test_evaluation_route_uses_product_direct_approval_limit():
    from app.api.v1.endpoints.creditos import _estado_despues_evaluacion

    assert _estado_despues_evaluacion("RECHAZADO", Decimal("100"), Decimal("500")) == "RECHAZADO"
    assert _estado_despues_evaluacion("REVISION_MANUAL", Decimal("100"), Decimal("500")) == "EN_COMITE"
    assert _estado_despues_evaluacion("APROBADO", Decimal("500"), Decimal("500")) == "APROBADO"
    assert _estado_despues_evaluacion("APROBADO", Decimal("500.01"), Decimal("500")) == "EN_COMITE"


def test_committee_routes_are_registered():
    from fastapi import FastAPI
    from app.api.v1.router import api_router

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    paths = app.openapi()["paths"]
    assert "/api/v1/creditos/comite" in paths
    assert "/api/v1/creditos/comite/{solicitud_id}/votos" in paths
    assert "/api/v1/creditos/solicitudes/{solicitud_id}/acta-comite" in paths


def test_committee_quorum_uses_three_votes_and_simple_majority():
    from types import SimpleNamespace
    from app.api.v1.endpoints.creditos import _resultado_votacion_comite

    assert _resultado_votacion_comite([SimpleNamespace(voto="APROBAR")] * 2 + [SimpleNamespace(voto="OBSERVAR")]) == "APROBADO"
    assert _resultado_votacion_comite([SimpleNamespace(voto="RECHAZAR")] * 2 + [SimpleNamespace(voto="APROBAR")]) == "RECHAZADO"
    assert _resultado_votacion_comite([
        SimpleNamespace(voto="APROBAR"), SimpleNamespace(voto="RECHAZAR"), SimpleNamespace(voto="OBSERVAR")
    ]) == "OBSERVADA"
    assert _resultado_votacion_comite([SimpleNamespace(voto="APROBAR")] * 2) is None


@pytest.mark.skipif(not DB_AVAILABLE, reason="Local PostgreSQL is unavailable")
def test_committee_vote_quorum_resolves_atomically_and_acta_keeps_votes(client, committee_data):
    request_id = committee_data["request_id"]
    registrar = committee_data["users"]["registrar"]
    admin = committee_data["users"]["admin"]
    officer = committee_data["users"]["officer"]
    accountant = committee_data["users"]["accountant"]
    queue = client.get("/api/v1/creditos/comite", headers={"Authorization": f"Bearer {admin['token']}"})
    assert queue.status_code == 200, queue.text
    item = next(entry for entry in queue.json() if entry["solicitud"]["id"] == request_id)
    assert item["evaluacion"]["score"] == 700
    assert item["puede_votar"] is True

    forbidden = client.post(
        f"/api/v1/creditos/comite/{request_id}/votos",
        json={"voto": "APROBAR", "comentario": "Comentario válido del registrador"},
        headers={"Authorization": f"Bearer {registrar['token']}"},
    )
    assert forbidden.status_code == 403

    def cast(user, vote):
        return client.post(
            f"/api/v1/creditos/comite/{request_id}/votos",
            json={"voto": vote, "comentario": f"Voto de prueba {user['id']} válido"},
            headers={"Authorization": f"Bearer {user['token']}"},
        )

    first = cast(admin, "APROBAR")
    assert first.status_code == 201, first.text
    duplicate = cast(admin, "RECHAZAR")
    assert duplicate.status_code == 409
    second = cast(officer, "RECHAZAR")
    assert second.status_code == 201, second.text
    third = cast(accountant, "APROBAR")
    assert third.status_code == 201, third.text
    assert third.json()["solicitud"]["estado"] == "APROBADO"
    assert third.json()["solicitud"]["resultado_comite"] == "APROBADO"
    assert len(third.json()["votos"]) == 3

    acta = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/acta-comite",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert acta.status_code == 200, acta.text
    assert acta.json()["rondas"][0]["resultado"] == "APROBADO"
    assert [vote["voto"] for vote in acta.json()["rondas"][0]["votos"]] == [
        "APROBAR", "RECHAZAR", "APROBAR"
    ]
    with SessionLocal() as db:
        assert db.query(models.Bitacora).filter(
            models.Bitacora.accion == "VOTAR_COMITE",
            models.Bitacora.descripcion.contains(str(request_id)),
        ).count() == 3
        assert db.query(models.Bitacora).filter(
            models.Bitacora.accion == "RESOLVER_COMITE",
            models.Bitacora.descripcion.contains(str(request_id)),
        ).count() == 1


@pytest.mark.skipif(not DB_AVAILABLE, reason="Local PostgreSQL is unavailable")
def test_no_majority_returns_observed_and_reevaluation_opens_next_round(client, committee_data):
    request_id = committee_data["request_id"]
    voters = [committee_data["users"][key] for key in ("admin", "officer", "accountant")]
    for voter, decision in zip(voters, ("APROBAR", "RECHAZAR", "OBSERVAR")):
        response = client.post(
            f"/api/v1/creditos/comite/{request_id}/votos",
            json={"voto": decision, "comentario": f"Dictamen válido {voter['id']}"},
            headers={"Authorization": f"Bearer {voter['token']}"},
        )
        assert response.status_code == 201, response.text
    assert response.json()["solicitud"]["estado"] == "OBSERVADA"
    assert response.json()["solicitud"]["resultado_comite"] == "OBSERVADA"

    with SessionLocal() as db:
        db.query(models.SolicitudCredito).filter(
            models.SolicitudCredito.id == request_id
        ).update({"estado": "OBSERVADA"})
        db.commit()
    officer = committee_data["users"]["registrar"]
    reevaluated = client.post(
        f"/api/v1/creditos/solicitudes/{request_id}/evaluacion",
        headers={"Authorization": f"Bearer {officer['token']}"},
    )
    assert reevaluated.status_code == 201, reevaluated.text
    request_response = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}",
        headers={"Authorization": f"Bearer {officer['token']}"},
    )
    assert request_response.status_code == 200, request_response.text
    assert request_response.json()["estado"] == "EN_COMITE"
    assert request_response.json()["ronda_comite"] == 2

    acta = client.get(
        f"/api/v1/creditos/solicitudes/{request_id}/acta-comite",
        headers={"Authorization": f"Bearer {officer['token']}"},
    )
    assert acta.status_code == 200, acta.text
    assert [item["ronda"] for item in acta.json()["rondas"]] == [1, 2]
    assert acta.json()["rondas"][0]["resultado"] == "OBSERVADA"
    assert len(acta.json()["rondas"][0]["votos"]) == 3
    assert acta.json()["rondas"][1]["votos"] == []


@pytest.mark.skipif(not DB_AVAILABLE, reason="Local PostgreSQL is unavailable")
def test_request_list_can_filter_committee_state(client, committee_data):
    admin = committee_data["users"]["admin"]
    response = client.get(
        "/api/v1/creditos/solicitudes?estado=EN_COMITE",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [committee_data["request_id"]]
