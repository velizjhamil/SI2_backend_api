"""Pruebas del asistente virtual (chatbot).

Requiere PostgreSQL disponible; si no lo está, la suite se marca como skip.
Se verifica el enrutamiento de intenciones, la estructura de la respuesta y el
uso de datos reales tanto para personal como para socios.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.core.security import create_access_token
from app.db.session import SessionLocal, engine
from app.models.models import Cooperativa, CuentaAhorro, Socio, Usuario


def _db_disponible() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")

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
    with SessionLocal() as db:
        rol_superadmin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'SUPERADMIN'")
        ).scalar_one()
        rol_admin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'ADMINISTRADOR'")
        ).scalar_one()
        rol_socio = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'SOCIO'")
        ).scalar_one()
        moneda_id = db.execute(text("SELECT id FROM moneda ORDER BY id LIMIT 1")).scalar_one()

        cooperativa = Cooperativa(nombre="Coop Chatbot Test")
        db.add(cooperativa)
        db.flush()

        superadmin = Usuario(
            correo="chatbot.superadmin@test.com",
            contrasena="x",
            rol_id=rol_superadmin,
            nombre="Super Chatbot",
            estado="ACTIVO",
        )
        admin = Usuario(
            correo="chatbot.admin@test.com",
            contrasena="x",
            rol_id=rol_admin,
            cooperativa_id=cooperativa.id,
            nombre="Admin Chatbot",
            estado="ACTIVO",
        )
        socio_usuario = Usuario(
            correo="chatbot.socio@test.com",
            contrasena="x",
            rol_id=rol_socio,
            cooperativa_id=cooperativa.id,
            nombre="Socio Chatbot",
            estado="ACTIVO",
        )
        db.add_all([superadmin, admin, socio_usuario])
        db.flush()

        socio = Socio(
            cooperativa_id=cooperativa.id,
            ci="99999999-CHAT",
            nombre="Ana",
            apellido="Pérez",
            estado="ACTIVO",
            usuario_id=socio_usuario.id,
        )
        db.add(socio)
        db.flush()

        cuenta = CuentaAhorro(
            numero="CHAT-0001",
            tipo_producto="VISTA",
            saldo_disponible=1500,
            saldo_bloqueado=0,
            estado="ACTIVA",
            fecha_registro=date.today(),
            socio_id=socio.id,
            moneda_id=moneda_id,
        )
        db.add(cuenta)
        db.commit()

        datos = {
            "cooperativa_id": cooperativa.id,
            "superadmin_id": superadmin.id,
            "admin_id": admin.id,
            "socio_usuario_id": socio_usuario.id,
            "socio_id": socio.id,
        }

    yield datos

    with SessionLocal() as db:
        db.execute(delete(CuentaAhorro).where(CuentaAhorro.numero == "CHAT-0001"))
        db.execute(delete(Socio).where(Socio.ci == "99999999-CHAT"))
        db.execute(
            delete(Usuario).where(
                Usuario.correo.in_(
                    [
                        "chatbot.superadmin@test.com",
                        "chatbot.admin@test.com",
                        "chatbot.socio@test.com",
                    ]
                )
            )
        )
        db.execute(delete(Cooperativa).where(Cooperativa.id == datos["cooperativa_id"]))
        db.commit()


def _token_admin(datos) -> dict:
    token, _ = create_access_token(
        subject=datos["admin_id"],
        role="ADMINISTRADOR",
        cooperativa_id=datos["cooperativa_id"],
    )
    return _auth(token)


def _token_socio(datos) -> dict:
    token, _ = create_access_token(
        subject=datos["socio_usuario_id"],
        role="SOCIO",
        cooperativa_id=datos["cooperativa_id"],
    )
    return _auth(token)


def test_chat_requiere_autenticacion(client):
    assert client.post("/api/v1/chatbot/chat", json={"mensaje": "hola"}).status_code == 401


def test_chat_admin_responde_mora_con_datos_reales(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cómo está la mora?"},
        headers=_token_admin(contexto),
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["intent"] == "mora"
    assert cuerpo["fuente"] == "PLANTILLA"
    assert isinstance(cuerpo["respuesta"], str) and cuerpo["respuesta"]
    assert "mora" in cuerpo["datos"]
    assert cuerpo["sugerencias"]


def test_chat_admin_cuenta_socios_del_tenant(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuántos socios activos hay?"},
        headers=_token_admin(contexto),
    )
    cuerpo = resp.json()
    assert cuerpo["intent"] == "socios"
    assert cuerpo["datos"]["socios"]["total"] >= 1
    assert cuerpo["datos"]["alcance"] == "COOPERATIVA"


def test_chat_admin_ayuda_lista_capacidades(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "ayuda"},
        headers=_token_admin(contexto),
    )
    assert resp.json()["intent"] == "ayuda"


def test_chat_superadmin_alcance_global(client, contexto):
    token, _ = create_access_token(subject=contexto["superadmin_id"], role="SUPERADMIN")
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "estado de caja"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["datos"]["alcance"] == "GLOBAL"


def test_chat_socio_devuelve_contexto_personal(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuál es mi saldo?"},
        headers=_token_socio(contexto),
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["datos"]["es_socio"] is True
    assert cuerpo["datos"]["socio"]["nombre"] == "Ana Pérez"
    assert cuerpo["datos"]["ahorro_total"] == 1500.0


def test_chat_socio_precalificacion_usa_su_historial(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿cómo precalifico un crédito?"},
        headers=_token_socio(contexto),
    )
    cuerpo = resp.json()
    assert cuerpo["intent"] == "precalificacion"
    assert "Ana" in cuerpo["respuesta"]
