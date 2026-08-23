"""Pruebas de separación y seguridad multi-tenant (Sprint 0).

Requiere PostgreSQL levantado (docker compose up -d) con la migración aplicada:

    psql "$DATABASE_URL" -f migrations/002_sprint0_multi_tenant.sql
    python -m app.db.seed

Si la base no está disponible, la suite se marca como skip en lugar de fallar.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from main import app
from app.core.security import decode_access_token
from app.db.session import SessionLocal, engine
from app.models.models import Cooperativa, Usuario
from app.core.tenancy import puede_acceder_recurso, scope_cooperativa


def _db_disponible() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_disponible(), reason="PostgreSQL no disponible"
)


SUPERADMIN = {"correo": "superadmin@si2.com", "contrasena": "SuperAdmin123!"}
ADMIN_COOP = {"correo": "admin@cooperativa.com", "contrasena": "Admin123!"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, credenciales):
    resp = client.post("/api/v1/auth/login", json=credenciales)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _crear_cooperativa(client, nit=None, **extra):
    payload = {
        "nombre": extra.get("nombre", f"Coop Test {datetime.now(timezone.utc).timestamp()}"),
        "razon_social": extra.get("razon_social", "Coop Test Ltda."),
        "nit": nit,
        "correo": extra.get("correo", "test@coop.bo"),
        "telefono": extra.get("telefono", "+591 70000000"),
        "direccion": extra.get("direccion", "Calle Test #1"),
    }
    resp = client.post(
        "/api/v1/cooperativas", json=payload, headers=_auth(extra["token"])
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _limpiar_cooperativa(coop_id):
    with SessionLocal() as db:
        db.execute(delete(Cooperativa).where(Cooperativa.id == coop_id))
        db.commit()


# ---------------------------------------------------------------------------
# Seguridad del módulo de cooperativas
# ---------------------------------------------------------------------------


def test_cooperativas_exige_token(client):
    resp = client.get("/api/v1/cooperativas")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "credenciales",
    [
        ADMIN_COOP,
        {"correo": "asesor1@cooperativa.com", "contrasena": "Asesor123!"},
        {"correo": "socio1@cooperativa.com", "contrasena": "Socio123!"},
    ],
)
def test_roles_de_cooperativa_sin_acceso_a_tenants(client, credenciales):
    """Un usuario de una cooperativa jamás gestiona tenants ni ve el listado."""
    token = _login(client, credenciales)
    resp = client.get("/api/v1/cooperativas", headers=_auth(token))
    assert resp.status_code == 403


def test_superadmin_puede_listar_y_crear(client):
    token = _login(client, SUPERADMIN)

    resp = client.get("/api/v1/cooperativas", headers=_auth(token))
    assert resp.status_code == 200
    antes = len(resp.json())

    creada = _crear_cooperativa(client, nit=None, token=token)
    assert creada["estado"] == "ACTIVO"
    assert creada["fecha_baja"] is None

    resp = client.get("/api/v1/cooperativas", headers=_auth(token))
    assert len(resp.json()) == antes + 1

    _limpiar_cooperativa(creada["id"])


def test_detalle_inexistente_404(client):
    token = _login(client, SUPERADMIN)
    resp = client.get("/api/v1/cooperativas/99999999", headers=_auth(token))
    assert resp.status_code == 404


def test_nit_duplicado_rechazado_409(client):
    token = _login(client, SUPERADMIN)
    creada = _crear_cooperativa(client, nit=f"NIT-{datetime.now(timezone.utc).timestamp()}", token=token)

    duplicada = {
        "nombre": "Otra Coop",
        "nit": creada["nit"],
    }
    resp = client.post("/api/v1/cooperativas", json=duplicada, headers=_auth(token))
    assert resp.status_code == 409

    _limpiar_cooperativa(creada["id"])


def test_actualizacion_de_datos(client):
    token = _login(client, SUPERADMIN)
    creada = _crear_cooperativa(client, token=token)

    resp = client.put(
        f"/api/v1/cooperativas/{creada['id']}",
        json={"nombre": "Coop Renombrada", "correo": "nuevo@coop.bo"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["nombre"] == "Coop Renombrada"
    assert cuerpo["correo"] == "nuevo@coop.bo"

    _limpiar_cooperativa(creada["id"])


def test_baja_logica_y_reactivacion(client):
    token = _login(client, SUPERADMIN)
    creada = _crear_cooperativa(client, token=token)

    resp = client.delete(f"/api/v1/cooperativas/{creada['id']}", headers=_auth(token))
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "INACTIVO"
    assert cuerpo["fecha_baja"] is not None

    # La baja es lógica: el registro sigue existiendo
    resp = client.get(f"/api/v1/cooperativas/{creada['id']}", headers=_auth(token))
    assert resp.status_code == 200

    resp = client.post(f"/api/v1/cooperativas/{creada['id']}/reactivar", headers=_auth(token))
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "ACTIVO"
    assert cuerpo["fecha_baja"] is None

    _limpiar_cooperativa(creada["id"])


# ---------------------------------------------------------------------------
# Contexto de tenant en sesión (JWT / me)
# ---------------------------------------------------------------------------


def test_login_propaga_cooperativa_en_jwt_y_me(client):
    token = _login(client, ADMIN_COOP)
    payload = decode_access_token(token)
    assert payload.get("coop") is not None

    resp = client.get("/api/v1/auth/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["cooperativa_id"] == payload["coop"]

    token_sa = _login(client, SUPERADMIN)
    payload_sa = decode_access_token(token_sa)
    assert "coop" not in payload_sa

    resp = client.get("/api/v1/auth/me", headers=_auth(token_sa))
    assert resp.json()["cooperativa_id"] is None


# ---------------------------------------------------------------------------
# Aislamiento entre tenants (helper de scoping)
# ---------------------------------------------------------------------------


def test_aislamiento_entre_cooperativas(client):
    """Dos usuarios de cooperativas distintas solo ven su propio tenant."""
    with SessionLocal() as db:
        coop_a = Cooperativa(nombre=f"Aislamiento A {datetime.now(timezone.utc).timestamp()}")
        coop_b = Cooperativa(nombre=f"Aislamiento B {datetime.now(timezone.utc).timestamp()}")
        db.add_all([coop_a, coop_b])
        db.flush()

        rol_admin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'ADMINISTRADOR'")
        ).scalar_one()
        usuario_a = Usuario(
            correo="userA@test.coop",
            contrasena="x",
            rol_id=rol_admin,
            cooperativa_id=coop_a.id,
            nombre="Usuario A",
            estado="ACTIVO",
        )
        usuario_b = Usuario(
            correo="userB@test.coop",
            contrasena="x",
            rol_id=rol_admin,
            cooperativa_id=coop_b.id,
            nombre="Usuario B",
            estado="ACTIVO",
        )
        db.add_all([usuario_a, usuario_b])
        db.commit()

        try:
            base = select(Usuario).where(Usuario.estado == "ACTIVO")

            visibles_a = list(db.execute(
                scope_cooperativa(base, Usuario, usuario_a)
            ).scalars().all())
            assert all(u.cooperativa_id == coop_a.id for u in visibles_a)
            assert usuario_b.id not in [u.id for u in visibles_a]

            visibles_b = list(db.execute(
                scope_cooperativa(base, Usuario, usuario_b)
            ).scalars().all())
            assert all(u.cooperativa_id == coop_b.id for u in visibles_b)

            sa = db.execute(
                select(Usuario).where(Usuario.correo == SUPERADMIN["correo"])
            ).scalar_one()
            visibles_sa = list(db.execute(
                scope_cooperativa(base, Usuario, sa)
            ).scalars().all())
            ids_sa = {u.id for u in visibles_sa}
            assert usuario_a.id in ids_sa and usuario_b.id in ids_sa

            assert puede_acceder_recurso(coop_b.id, usuario_a) is False
            assert puede_acceder_recurso(coop_a.id, usuario_a) is True
            assert puede_acceder_recurso(coop_b.id, sa) is True
        finally:
            db.execute(delete(Usuario).where(
                Usuario.correo.in_(["userA@test.coop", "userB@test.coop"])
            ))
            db.execute(delete(Cooperativa).where(
                Cooperativa.id.in_([coop_a.id, coop_b.id])
            ))
            db.commit()
