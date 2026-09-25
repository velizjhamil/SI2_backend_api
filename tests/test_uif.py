"""Integration tests for UIF declarations (CU-W14)."""

from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models


def _db_disponible() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")
if _DB_DISPONIBLE:
    from main import app
else:
    app = None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _fixture_caja():
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        cooperativa_id = db.execute(
            text("SELECT cooperativa_id FROM usuario WHERE correo = 'cajero@test.com'")
        ).scalar_one()
        rol_id = db.execute(text("SELECT id FROM rol WHERE nombre = 'CAJERO'")).scalar_one()
        operador = models.Usuario(
            correo=f"uif-{sufijo}@test.invalid", contrasena=hash_password("Password123"),
            rol_id=rol_id, cooperativa_id=cooperativa_id, nombre="UIF tester", estado="ACTIVO",
        )
        socio = models.Socio(
            cooperativa_id=cooperativa_id, ci=f"UIF-{sufijo}", nombre="UIF", apellido="Test",
            estado="ACTIVO",
        )
        db.add_all([operador, socio])
        db.flush()
        moneda_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
        cuenta = models.CuentaAhorro(
            numero=f"UIF-{sufijo}", tipo_producto="VISTA", saldo_disponible="0.00",
            saldo_bloqueado="0.00", estado="ACTIVA", fecha_registro=date.today(),
            socio_id=socio.id, moneda_id=moneda_id,
        )
        caja = models.Caja(
            nombre=f"UIF {sufijo}", estado="CERRADA", cooperativa_id=cooperativa_id,
            monto_maximo_efectivo=100000.00,
        )
        db.add_all([cuenta, caja])
        db.commit()
        ids = (operador.id, socio.id, cuenta.id, caja.id, cooperativa_id)
    token, _ = create_access_token(str(ids[0]), "CAJERO", ids[4])
    return (*ids, token)


def _limpiar(ids):
    operador_id, socio_id, cuenta_id, caja_id, *_ = ids
    with SessionLocal() as db:
        db.execute(text("DELETE FROM bitacora WHERE usuario_id=:id"), {"id": operador_id})
        db.execute(text("DELETE FROM transaccion WHERE cuenta_ahorro_id=:id"), {"id": cuenta_id})
        db.execute(text("DELETE FROM declaracion_jurada_uif WHERE socio_id=:id"), {"id": socio_id})
        db.execute(text("DELETE FROM cuenta_ahorro WHERE id=:id"), {"id": cuenta_id})
        db.execute(text("DELETE FROM socio WHERE id=:id"), {"id": socio_id})
        db.execute(text("DELETE FROM control_caja WHERE usuario_id=:id"), {"id": operador_id})
        db.execute(text("DELETE FROM caja WHERE id=:id"), {"id": caja_id})
        db.execute(text("DELETE FROM usuario WHERE id=:id"), {"id": operador_id})
        db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _declaracion():
    return {
        "origen": "AHORROS_PROPIOS", "destino": "AHORRO",
        "actividad_economica": "Comercio", "realizado_por": "TITULAR",
        "declara_bajo_juramento": True,
    }


def test_uif_and_dpf_models_expose_migration_fields():
    columns = {column.name for column in models.DeclaracionJuradaUIF.__table__.columns}
    assert {
        "tipo_operacion", "monto", "moneda_id", "socio_id", "realizado_por",
        "actividad_economica", "declara_bajo_juramento", "fraccionada",
        "usuario_id", "cooperativa_id", "fecha",
    } <= columns
    assert hasattr(models, "TasaDPF")
    assert hasattr(models, "DPFCronograma")


def test_uif_configuration_exposes_thresholds_and_options(client):
    ids = _fixture_caja()
    try:
        response = client.get("/api/v1/uif/configuracion", headers=_auth(ids[-1]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["umbral_bob"] == "70000.00"
        assert body["umbral_usd"] == "10000.00"
        assert "OTRO" in {item["codigo"] for item in body["origenes"]}
        assert "OTRO" in {item["codigo"] for item in body["destinos"]}
    finally:
        _limpiar(ids)


def test_deposito_requiere_uif_en_umbral_and_links_declaration(client):
    ids = _fixture_caja()
    operador_id, socio_id, cuenta_id, caja_id, _, token = ids
    try:
        apertura = client.post("/api/v1/caja/aperturas", json={"caja_id": caja_id, "monto_apertura": "200.00"}, headers=_auth(token))
        assert apertura.status_code == 201, apertura.text
        payload = {"cuenta_id": cuenta_id, "monto": "70000.00", "depositante_nombre": "Titular", "depositante_ci": f"UIF-{socio_id}"}
        response = client.post("/api/v1/caja/depositos", json=payload, headers=_auth(token))
        assert response.status_code == 428
        assert response.json() == {"detail": "Se requiere declaración jurada UIF para esta operación"}
        with SessionLocal() as db:
            assert db.execute(text("SELECT count(*) FROM transaccion WHERE control_caja_id=:id"), {"id": apertura.json()["id"]}).scalar_one() == 0
            assert db.get(models.CuentaAhorro, cuenta_id).saldo_disponible == 0
        payload["declaracion_uif"] = _declaracion()
        response = client.post("/api/v1/caja/depositos", json=payload, headers=_auth(token))
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["declaracion_uif_id"] > 0
        with SessionLocal() as db:
            link = db.execute(text("SELECT declaracion_jurada_uif_id FROM transaccion WHERE id=:id"), {"id": body["numero_operacion"]}).scalar_one()
            assert link == body["declaracion_uif_id"]
    finally:
        _limpiar(ids)


def test_same_day_cash_operations_trigger_uif_structuring(client):
    ids = _fixture_caja()
    _, socio_id, cuenta_id, caja_id, _, token = ids
    try:
        apertura = client.post("/api/v1/caja/aperturas", json={"caja_id": caja_id, "monto_apertura": "200.00"}, headers=_auth(token))
        assert apertura.status_code == 201, apertura.text
        payload = {"cuenta_id": cuenta_id, "monto": "40000.00", "depositante_nombre": "Titular", "depositante_ci": f"UIF-{socio_id}"}
        first = client.post("/api/v1/caja/depositos", json=payload, headers=_auth(token))
        assert first.status_code == 201, first.text
        payload["monto"] = "30000.00"
        second = client.post("/api/v1/caja/depositos", json=payload, headers=_auth(token))
        assert second.status_code == 428
        assert second.json()["detail"] == "Se requiere declaración jurada UIF para esta operación"
    finally:
        _limpiar(ids)


def test_uif_declaration_detail_available_to_operations_but_list_restricted(client):
    ids = _fixture_caja()
    _, socio_id, cuenta_id, caja_id, _, token = ids
    foreign_ids = None
    contador_id = None
    try:
        apertura = client.post("/api/v1/caja/aperturas", json={"caja_id": caja_id, "monto_apertura": "200.00"}, headers=_auth(token))
        assert apertura.status_code == 201, apertura.text
        response = client.post(
            "/api/v1/caja/depositos",
            json={"cuenta_id": cuenta_id, "monto": "70000.00", "depositante_nombre": "Titular", "depositante_ci": f"UIF-{socio_id}", "declaracion_uif": _declaracion()},
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        declaration_id = response.json()["declaracion_uif_id"]
        with SessionLocal() as db:
            socio_ci = db.execute(text("SELECT ci FROM socio WHERE id=:id"), {"id":socio_id}).scalar_one()
        listing = client.get("/api/v1/uif/declaraciones", headers=_auth(token))
        assert listing.status_code == 403
        detail = client.get(f"/api/v1/uif/declaraciones/{declaration_id}", headers=_auth(token))
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == declaration_id
        assert detail.json()["transaccion_id"] == response.json()["numero_operacion"]

        suffix = uuid4().hex[:12]
        with SessionLocal() as db:
            contador_role = db.execute(text("SELECT id FROM rol WHERE nombre='CONTADOR'")).scalar_one()
            contador = models.Usuario(correo=f"uif-contador-{suffix}@test.invalid", contrasena=hash_password("Password123"), rol_id=contador_role, cooperativa_id=ids[4], nombre="Compliance", estado="ACTIVO")
            other_coop = models.Cooperativa(nombre=f"UIF tenant {suffix}")
            db.add_all([contador, other_coop])
            db.flush()
            other_socio = models.Socio(cooperativa_id=other_coop.id, ci=f"UO-{suffix}", nombre="Other", apellido="Tenant", estado="ACTIVO")
            db.add(other_socio)
            db.flush()
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
            foreign_declaration = models.DeclaracionJuradaUIF(origen="AHORROS_PROPIOS", destino="AHORRO", tipo_operacion="DEPOSITO", monto="100.00", moneda_id=bob_id, socio_id=other_socio.id, realizado_por="TITULAR", actividad_economica="Comercio", declara_bajo_juramento=True, usuario_id=contador.id, cooperativa_id=other_coop.id)
            db.add(foreign_declaration)
            db.commit()
            contador_id, foreign_ids = contador.id, (other_coop.id, other_socio.id, foreign_declaration.id)
        contador_token, _ = create_access_token(str(contador_id), "CONTADOR", ids[4])
        compliant_list = client.get("/api/v1/uif/declaraciones", params={"socio_ci":socio_ci}, headers=_auth(contador_token))
        assert compliant_list.status_code == 200, compliant_list.text
        assert [item["id"] for item in compliant_list.json()] == [declaration_id]
        hidden = client.get(f"/api/v1/uif/declaraciones/{foreign_ids[2]}", headers=_auth(token))
        assert hidden.status_code == 404
    finally:
        if foreign_ids:
            with SessionLocal() as db:
                db.execute(text("DELETE FROM declaracion_jurada_uif WHERE id=:id"), {"id":foreign_ids[2]})
                db.execute(text("DELETE FROM socio WHERE id=:id"), {"id":foreign_ids[1]})
                db.execute(text("DELETE FROM cooperativa WHERE id=:id"), {"id":foreign_ids[0]})
                db.commit()
        if contador_id:
            with SessionLocal() as db:
                db.execute(text("DELETE FROM usuario WHERE id=:id"), {"id":contador_id})
                db.commit()
        _limpiar(ids)


def test_invalid_uif_declaration_uses_bad_request_and_writes_nothing(client):
    ids = _fixture_caja()
    _, socio_id, cuenta_id, caja_id, _, token = ids
    try:
        apertura = client.post("/api/v1/caja/aperturas", json={"caja_id":caja_id, "monto_apertura":"200.00"}, headers=_auth(token))
        assert apertura.status_code == 201, apertura.text
        response = client.post("/api/v1/caja/depositos", json={"cuenta_id":cuenta_id, "monto":"100.00", "depositante_nombre":"Titular", "depositante_ci":f"UIF-{socio_id}", "declaracion_uif":{"declara_bajo_juramento":True}}, headers=_auth(token))
        assert response.status_code == 400
        with SessionLocal() as db:
            assert db.execute(text("SELECT count(*) FROM transaccion WHERE cuenta_ahorro_id=:id"), {"id":cuenta_id}).scalar_one() == 0
    finally:
        _limpiar(ids)


def test_withdrawal_at_uif_threshold_returns_428_without_writes(client):
    ids = _fixture_caja()
    _, _, cuenta_id, caja_id, _, token = ids
    try:
        with SessionLocal() as db:
            account = db.get(models.CuentaAhorro, cuenta_id)
            account.saldo_disponible = 80000
            ci = db.execute(text("SELECT ci FROM socio WHERE id=:id"), {"id":account.socio_id}).scalar_one()
            db.commit()
        opening = client.post("/api/v1/caja/aperturas", json={"caja_id":caja_id, "monto_apertura":"80000.00"}, headers=_auth(token))
        assert opening.status_code == 201, opening.text
        response = client.post("/api/v1/caja/retiros", json={"cuenta_id":cuenta_id, "monto":"70000.00", "retirante":{"tipo":"TITULAR", "ci":ci}}, headers=_auth(token))
        assert response.status_code == 428
        assert response.json() == {"detail":"Se requiere declaración jurada UIF para esta operación"}
        with SessionLocal() as db:
            assert db.get(models.CuentaAhorro, cuenta_id).saldo_disponible == 80000
            assert db.execute(text("SELECT count(*) FROM transaccion WHERE cuenta_ahorro_id=:id"), {"id":cuenta_id}).scalar_one() == 0
    finally:
        _limpiar(ids)
