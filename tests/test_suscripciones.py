"""Pruebas del módulo de suscripciones institucionales (SaaS).

Requiere PostgreSQL levantado con el esquema inicial y la migración de
suscripciones aplicada (032_suscripciones.sql). Si la base no está disponible,
la suite se marca como skip en lugar de fallar.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.core.security import create_access_token
from app.db.session import SessionLocal, engine
from app.models.models import Cooperativa, Suscripcion, Usuario


def _db_disponible() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(
    not _DB_DISPONIBLE, reason="PostgreSQL no disponible"
)

# `main.py` ejecuta `init_db()` al importarse, por lo que se importa solo si la
# base está disponible; de lo contrario la colección rompería antes del skip.
if _DB_DISPONIBLE:
    from main import app
else:
    app = None


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def contexto():
    """Crea un superadmin, una cooperativa y su admin; limpia al final."""
    with SessionLocal() as db:
        rol_superadmin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'SUPERADMIN'")
        ).scalar_one()
        rol_admin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'ADMINISTRADOR'")
        ).scalar_one()

        cooperativa = Cooperativa(nombre="Coop Suscripciones Test")
        db.add(cooperativa)
        db.flush()

        superadmin = Usuario(
            correo="suscripciones.superadmin@test.com",
            contrasena="x",
            rol_id=rol_superadmin,
            nombre="Super Suscripciones",
            estado="ACTIVO",
        )
        admin = Usuario(
            correo="suscripciones.admin@test.com",
            contrasena="x",
            rol_id=rol_admin,
            cooperativa_id=cooperativa.id,
            nombre="Admin Suscripciones",
            estado="ACTIVO",
        )
        db.add_all([superadmin, admin])
        db.commit()

        datos = {
            "cooperativa_id": cooperativa.id,
            "superadmin_id": superadmin.id,
            "admin_id": admin.id,
        }

    yield datos

    with SessionLocal() as db:
        db.execute(
            delete(Suscripcion).where(
                Suscripcion.cooperativa_id == datos["cooperativa_id"]
            )
        )
        db.execute(
            delete(Usuario).where(
                Usuario.correo.in_(
                    [
                        "suscripciones.superadmin@test.com",
                        "suscripciones.admin@test.com",
                    ]
                )
            )
        )
        db.execute(
            delete(Cooperativa).where(Cooperativa.id == datos["cooperativa_id"])
        )
        db.commit()


def _token_superadmin(datos) -> dict:
    token, _ = create_access_token(
        subject=datos["superadmin_id"], role="SUPERADMIN"
    )
    return _auth(token)


def _token_admin(datos) -> dict:
    token, _ = create_access_token(
        subject=datos["admin_id"],
        role="ADMINISTRADOR",
        cooperativa_id=datos["cooperativa_id"],
    )
    return _auth(token)


def test_catalogo_de_planes(client, contexto):
    resp = client.get("/api/v1/suscripciones/planes", headers=_token_superadmin(contexto))
    assert resp.status_code == 200
    codigos = {plan["codigo"] for plan in resp.json()}
    assert "RED_ENTERPRISE" in codigos


def test_catalogo_requiere_autenticacion(client):
    assert client.get("/api/v1/suscripciones/planes").status_code == 401


def test_mi_suscripcion_devuelve_plan_de_la_cooperativa(client, contexto):
    resp = client.get(
        "/api/v1/suscripciones/mi-suscripcion", headers=_token_admin(contexto)
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["cooperativa_id"] == contexto["cooperativa_id"]
    assert cuerpo["estado"] == "ACTIVA"
    assert cuerpo["plan"]["codigo"] == "RED_ENTERPRISE"
    assert cuerpo["plan"]["features"]


def test_mi_suscripcion_de_superadmin_sin_cooperativa_404(client, contexto):
    resp = client.get(
        "/api/v1/suscripciones/mi-suscripcion", headers=_token_superadmin(contexto)
    )
    assert resp.status_code == 404


def test_superadmin_puede_crear_cambiar_y_renovar(client, contexto):
    headers = _token_superadmin(contexto)

    # Asegura una cooperativa sin suscripción (se elimina la creada por seed).
    with SessionLocal() as db:
        db.execute(
            delete(Suscripcion).where(
                Suscripcion.cooperativa_id == contexto["cooperativa_id"]
            )
        )
        db.commit()

    planes = client.get("/api/v1/suscripciones/planes", headers=headers).json()
    plan_crecimiento = next(p for p in planes if p["codigo"] == "CRECIMIENTO_PRO")

    resp = client.post(
        "/api/v1/suscripciones",
        json={"cooperativa_id": contexto["cooperativa_id"], "plan_id": plan_crecimiento["id"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    suscripcion = resp.json()
    assert suscripcion["plan"]["codigo"] == "CRECIMIENTO_PRO"
    assert suscripcion["fecha_fin"] is None

    # Cambiar de plan y estado
    resp = client.put(
        f"/api/v1/suscripciones/{suscripcion['id']}",
        json={"estado": "SUSPENDIDA"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "SUSPENDIDA"

    # Renovar reactiva y fija fecha_fin
    resp = client.post(
        f"/api/v1/suscripciones/{suscripcion['id']}/renovar",
        json={"meses": 6},
        headers=headers,
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "ACTIVA"
    assert cuerpo["fecha_fin"] is not None
    assert cuerpo["dias_restantes"] is not None and cuerpo["dias_restantes"] > 150

    # Baja lógica
    resp = client.delete(f"/api/v1/suscripciones/{suscripcion['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["estado"] == "CANCELADA"


def test_listado_solo_superadmin(client, contexto):
    assert client.get(
        "/api/v1/suscripciones", headers=_token_admin(contexto)
    ).status_code == 403

    resp = client.get("/api/v1/suscripciones", headers=_token_superadmin(contexto))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_crear_cooperativa_asigna_suscripcion_por_defecto(client, contexto):
    headers = _token_superadmin(contexto)
    resp = client.post(
        "/api/v1/cooperativas",
        json={"nombre": "Coop Autosuscripcion Test"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    coop_id = resp.json()["id"]

    try:
        resp = client.get(
            f"/api/v1/suscripciones/cooperativa/{coop_id}", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["plan"]["codigo"] == "RED_ENTERPRISE"
    finally:
        with SessionLocal() as db:
            db.execute(delete(Suscripcion).where(Suscripcion.cooperativa_id == coop_id))
            db.execute(delete(Cooperativa).where(Cooperativa.id == coop_id))
            db.commit()
