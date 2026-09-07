"""Pruebas SP1-15: socios, cuentas de ahorro y certificados de aportacion."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from main import app
from app.db.session import SessionLocal, engine
from app.models.models import (
    Bitacora,
    CertificadoAportacion,
    Cooperativa,
    CuentaAhorro,
    Socio,
    Usuario,
)


def _db_disponible() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_disponible(), reason="PostgreSQL no disponible"
)

ADMIN = {"correo": "admin@test.com", "contrasena": "Password123"}
SUPERADMIN = {"correo": "superadmin@test.com", "contrasena": "Password123"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client, credentials):
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def generate_ci(prefix):
    return f"{prefix}{uuid4().hex[:10]}"


def cleanup_socio(socio_id):
    with SessionLocal() as db:
        db.execute(
            delete(Bitacora).where(
                Bitacora.descripcion.like(f"%socio {socio_id}%")
            )
        )
        db.execute(
            delete(CertificadoAportacion).where(
                CertificadoAportacion.socio_id == socio_id
            )
        )
        db.execute(
            delete(CuentaAhorro).where(CuentaAhorro.socio_id == socio_id)
        )
        db.execute(delete(Socio).where(Socio.id == socio_id))
        db.commit()


def test_registrar_socio_y_abrir_cuenta(client):
    token = login(client, ADMIN)
    ci = generate_ci("SP115-")

    response = client.post(
        "/api/v1/socios/registro",
        json={"ci": ci, "nombre": "Socio", "apellido": "Prueba"},
        headers=headers(token),
    )
    assert response.status_code == 201, response.text
    socio_id = response.json()["socio"]["id"]

    try:
        response = client.post(
            "/api/v1/ahorros/cuentas",
            json={"socio_id": socio_id, "moneda_id": 1},
            headers=headers(token),
        )
        assert response.status_code == 201, response.text
        account = response.json()
        assert account["socio_id"] == socio_id
        assert account["estado"] == "ACTIVA"
        assert account["numero"].startswith("CA-")
        assert account["moneda"]["codigo_iso"] == "BOB"

        response = client.get(
            f"/api/v1/ahorros/cuentas?socio_id={socio_id}",
            headers=headers(token),
        )
        assert response.status_code == 200
        assert len(response.json()) == 1
    finally:
        cleanup_socio(socio_id)


def test_emitir_y_listar_certificado_aportacion(client):
    token = login(client, ADMIN)
    ci = generate_ci("SP15C-")
    response = client.post(
        "/api/v1/socios/registro",
        json={"ci": ci, "nombre": "Socio", "apellido": "Certificado"},
        headers=headers(token),
    )
    assert response.status_code == 201, response.text
    socio_id = response.json()["socio"]["id"]

    try:
        response = client.post(
            "/api/v1/ahorros/certificados",
            json={"socio_id": socio_id, "moneda_id": 1, "monto": "250.50"},
            headers=headers(token),
        )
        assert response.status_code == 201, response.text
        certificate = response.json()
        assert certificate["socio_id"] == socio_id
        assert certificate["monto"] == "250.50"
        assert certificate["estado"] == "EMITIDO"

        response = client.get(
            f"/api/v1/ahorros/certificados?socio_id={socio_id}",
            headers=headers(token),
        )
        assert response.status_code == 200
        assert len(response.json()) == 1
    finally:
        cleanup_socio(socio_id)


def test_tenant_no_puede_abrir_cuenta_para_socio_ajeno(client):
    super_token = login(client, SUPERADMIN)
    admin_token = login(client, ADMIN)
    response = client.get("/api/v1/auth/me", headers=headers(admin_token))
    tenant_id = response.json()["cooperativa_id"]

    with SessionLocal() as db:
        other_coop = Cooperativa(
            nombre=f"SP115 Coop {datetime.now(timezone.utc).timestamp()}"
        )
        db.add(other_coop)
        db.flush()
        socio = Socio(
            cooperativa_id=other_coop.id,
            ci=generate_ci("SP15A-"),
            nombre="Socio",
            apellido="Ajeno",
            estado="ACTIVO",
        )
        db.add(socio)
        db.commit()
        socio_id = socio.id
        other_coop_id = other_coop.id

    try:
        response = client.post(
            "/api/v1/ahorros/cuentas",
            json={"socio_id": socio_id, "moneda_id": 1},
            headers=headers(admin_token),
        )
        assert response.status_code == 404

        response = client.get(
            f"/api/v1/ahorros/cuentas?socio_id={socio_id}",
            headers=headers(admin_token),
        )
        assert response.status_code == 200
        assert response.json() == []

        response = client.post(
            "/api/v1/ahorros/cuentas",
            json={"socio_id": socio_id, "moneda_id": 1},
            headers=headers(super_token),
        )
        assert response.status_code == 201, response.text
        account_id = response.json()["id"]
    finally:
        with SessionLocal() as db:
            if "account_id" in locals():
                db.execute(delete(CuentaAhorro).where(CuentaAhorro.id == account_id))
            db.execute(delete(Bitacora).where(Bitacora.cooperativa_id == other_coop_id))
            db.execute(delete(Socio).where(Socio.id == socio_id))
            db.execute(delete(Cooperativa).where(Cooperativa.id == other_coop_id))
            db.commit()
