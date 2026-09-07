"""Pruebas SP1-1 y SP1-7: autenticacion JWT y RBAC."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from main import app
from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models.models import Usuario


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
SOCIO = {"correo": "socio@test.com", "contrasena": "Password123"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client, credentials):
    return client.post("/api/v1/auth/login", json=credentials)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_emite_jwt_y_me_devuelve_usuario(client):
    response = login(client, ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0

    profile = client.get("/api/v1/auth/me", headers=auth(body["access_token"]))
    assert profile.status_code == 200
    assert profile.json()["correo"] == ADMIN["correo"]
    assert profile.json()["rol"]["nombre"] == "ADMINISTRADOR"


def test_login_rechaza_contrasena_incorrecta(client):
    response = login(client, {"correo": ADMIN["correo"], "contrasena": "incorrecta"})
    assert response.status_code == 401
    assert "incorrectos" in response.json()["detail"]


def test_endpoints_protegidos_rechazan_token_invalido(client):
    response = client.get(
        "/api/v1/auth/me",
        headers=auth("token-invalido"),
    )
    assert response.status_code == 401

    response = client.get("/api/v1/admin/usuarios")
    assert response.status_code == 401


def test_rbac_rechaza_socio_en_operacion_administrativa(client):
    response = login(client, SOCIO)
    assert response.status_code == 200
    token = response.json()["access_token"]

    assert client.get("/api/v1/admin/usuarios", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/ahorros/cuentas", headers=auth(token)).status_code == 403


def test_superadmin_puede_consultar_roles_y_permisos(client):
    response = login(client, SUPERADMIN)
    assert response.status_code == 200
    token = response.json()["access_token"]

    roles = client.get("/api/v1/admin/roles", headers=auth(token))
    permisos = client.get("/api/v1/admin/permisos", headers=auth(token))
    assert roles.status_code == 200
    assert permisos.status_code == 200
    assert any(rol["nombre"] == "ADMINISTRADOR" for rol in roles.json())
    assert permisos.json()


def test_usuario_inactivo_no_puede_iniciar_sesion(client):
    correo = "sp11-auth-inactive@test.com"
    with SessionLocal() as db:
        role_id = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'SOCIO'")
        ).scalar_one()
        usuario = Usuario(
            correo=correo,
            contrasena=hash_password("Password123"),
            rol_id=role_id,
            nombre="Usuario Inactivo SP1",
            estado="INACTIVO",
        )
        db.add(usuario)
        db.commit()

    try:
        response = login(client, {"correo": correo, "contrasena": "Password123"})
        assert response.status_code == 401
        assert "inactivo" in response.json()["detail"]
    finally:
        with SessionLocal() as db:
            db.execute(delete(Usuario).where(Usuario.correo == correo))
            db.commit()
