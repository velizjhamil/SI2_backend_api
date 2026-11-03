"""Subscription status and plan user limits are enforced by the API."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.v1.router import api_router
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.models import Bitacora, Cooperativa, PlanSuscripcion, Rol, Suscripcion, Usuario

CONTRASENA = "Password123!"
DETALLE_LOGIN = "La suscripción de tu cooperativa no está vigente. Contacta al administrador de la plataforma."


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


def _rol(db, nombre):
    rol = db.execute(select(Rol).where(Rol.nombre == nombre)).scalar_one_or_none()
    if rol is None:
        rol = Rol(nombre=nombre)
        db.add(rol)
        db.flush()
    return rol


def _usuario(db, rol, cooperativa_id, estado="ACTIVO"):
    usuario = Usuario(
        nombre=f"Test {rol}",
        correo=f"acceso-{uuid4().hex}@example.com",
        contrasena=hash_password(CONTRASENA),
        rol_id=_rol(db, rol).id,
        cooperativa_id=cooperativa_id,
        estado=estado,
    )
    db.add(usuario)
    db.flush()
    return usuario


class Entorno:
    """Isolated tenant with a mutable subscription and tracked rows for cleanup."""

    def __init__(self):
        self.usuarios = []

    def crear_usuario(self, rol, estado="ACTIVO", cooperativa=True):
        with SessionLocal() as db:
            usuario = _usuario(db, rol, self.cooperativa_id if cooperativa else None, estado)
            db.commit()
            self.usuarios.append(usuario.id)
            return usuario.id, usuario.correo

    def suscripcion(self, **campos):
        with SessionLocal() as db:
            suscripcion = db.get(Suscripcion, self.suscripcion_id)
            for campo, valor in campos.items():
                setattr(suscripcion, campo, valor)
            db.commit()

    def limite(self, valor):
        with SessionLocal() as db:
            db.get(PlanSuscripcion, self.plan_id).limite_usuarios = valor
            db.commit()


@pytest.fixture
def entorno():
    datos = Entorno()
    with SessionLocal() as db:
        cooperativa = Cooperativa(nombre=f"Acceso {uuid4().hex}", estado="ACTIVO")
        plan = PlanSuscripcion(
            codigo=f"T{uuid4().hex[:20]}",
            nombre="Plan de prueba",
            limite_usuarios=None,
            moneda="BOB",
            estado="ACTIVO",
        )
        db.add_all([cooperativa, plan])
        db.flush()
        suscripcion = Suscripcion(cooperativa_id=cooperativa.id, plan_id=plan.id, estado="ACTIVA")
        db.add(suscripcion)
        db.commit()
        datos.cooperativa_id = cooperativa.id
        datos.plan_id = plan.id
        datos.suscripcion_id = suscripcion.id
    yield datos
    with SessionLocal() as db:
        ids = list(
            db.execute(select(Usuario.id).where(Usuario.cooperativa_id == datos.cooperativa_id)).scalars()
        ) + datos.usuarios
        db.execute(delete(Bitacora).where(Bitacora.usuario_id.in_(ids)))
        db.execute(delete(Usuario).where(Usuario.id.in_(ids)))
        db.execute(delete(Suscripcion).where(Suscripcion.id == datos.suscripcion_id))
        db.execute(delete(PlanSuscripcion).where(PlanSuscripcion.id == datos.plan_id))
        db.execute(delete(Cooperativa).where(Cooperativa.id == datos.cooperativa_id))
        db.commit()


def _login(client, correo):
    return client.post("/api/v1/auth/login", json={"correo": correo, "contrasena": CONTRASENA})


def _headers(client, correo):
    respuesta = _login(client, correo)
    assert respuesta.status_code == 200, respuesta.text
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


def _crear_por_api(client, headers, rol="CAJERO", cooperativa_id=None):
    cuerpo = {
        "nombre": "Nuevo usuario",
        "correo": f"nuevo-{uuid4().hex}@example.com",
        "contrasena": CONTRASENA,
        "rol": rol,
    }
    if cooperativa_id is not None:
        cuerpo["cooperativa_id"] = cooperativa_id
    return client.post("/api/v1/admin/usuarios", json=cuerpo, headers=headers)


@pytest.mark.parametrize(
    "campos",
    [
        {"estado": "SUSPENDIDA"},
        {"estado": "VENCIDA"},
        {"estado": "CANCELADA"},
        {"estado": "ACTIVA", "fecha_fin": date.today() - timedelta(days=1)},
    ],
)
@pytest.mark.parametrize("rol", ["ADMINISTRADOR", "SOCIO"])
def test_suscripcion_no_vigente_impide_el_login(client, entorno, rol, campos):
    _, correo = entorno.crear_usuario(rol)
    entorno.suscripcion(**campos)

    respuesta = _login(client, correo)

    assert respuesta.status_code == 403
    assert respuesta.json() == {"detail": DETALLE_LOGIN}


@pytest.mark.parametrize(
    "fecha_fin", [None, date.today(), date.today() + timedelta(days=1)]
)
def test_suscripcion_vigente_permite_el_login(client, entorno, fecha_fin):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    entorno.suscripcion(fecha_fin=fecha_fin)

    respuesta = _login(client, correo)

    assert respuesta.status_code == 200
    assert respuesta.json()["access_token"]


def test_sesion_abierta_termina_cuando_la_suscripcion_deja_de_estar_vigente(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    headers = _headers(client, correo)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    entorno.suscripcion(estado="CANCELADA")

    respuesta = client.get("/api/v1/auth/me", headers=headers)
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == DETALLE_LOGIN


def test_superadmin_no_depende_de_una_suscripcion_y_puede_renovarla(client, entorno):
    _, correo_admin = entorno.crear_usuario("ADMINISTRADOR")
    _, correo_superadmin = entorno.crear_usuario("SUPERADMIN", cooperativa=False)
    entorno.suscripcion(estado="VENCIDA", fecha_fin=date.today() - timedelta(days=1))
    assert _login(client, correo_admin).status_code == 403

    respuesta = client.post(
        f"/api/v1/suscripciones/{entorno.suscripcion_id}/renovar",
        json={"meses": 1},
        headers=_headers(client, correo_superadmin),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert _login(client, correo_admin).status_code == 200


def test_cooperativa_sin_suscripcion_conserva_el_acceso(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    with SessionLocal() as db:
        db.execute(delete(Suscripcion).where(Suscripcion.id == entorno.suscripcion_id))
        db.commit()

    assert _login(client, correo).status_code == 200


def test_crear_usuario_respeta_el_limite_del_plan(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    entorno.limite(2)
    headers = _headers(client, correo)

    primero = _crear_por_api(client, headers)
    segundo = _crear_por_api(client, headers)

    assert primero.status_code == 201, primero.text
    assert segundo.status_code == 409
    assert segundo.json()["detail"] == "Se alcanzó el límite de 2 usuarios del plan Plan de prueba."
    with SessionLocal() as db:
        total = db.query(Usuario).filter(Usuario.cooperativa_id == entorno.cooperativa_id).count()
    assert total == 2


def test_limite_no_cuenta_socios_ni_usuarios_inactivos(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    entorno.crear_usuario("SOCIO")
    entorno.crear_usuario("CAJERO", estado="INACTIVO")
    entorno.limite(2)
    headers = _headers(client, correo)

    respuesta = _crear_por_api(client, headers)

    assert respuesta.status_code == 201, respuesta.text


def test_crear_socio_no_consume_ni_exige_cupo(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    entorno.limite(1)

    respuesta = _crear_por_api(client, _headers(client, correo), rol="SOCIO")

    assert respuesta.status_code == 201, respuesta.text


def test_plan_sin_limite_permite_crear_usuarios(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    entorno.crear_usuario("CAJERO")

    respuesta = _crear_por_api(client, _headers(client, correo))

    assert respuesta.status_code == 201, respuesta.text


def test_superadmin_tambien_respeta_el_limite_de_la_cooperativa(client, entorno):
    entorno.crear_usuario("ADMINISTRADOR")
    _, correo = entorno.crear_usuario("SUPERADMIN", cooperativa=False)
    entorno.limite(1)

    respuesta = _crear_por_api(
        client, _headers(client, correo), cooperativa_id=entorno.cooperativa_id
    )

    assert respuesta.status_code == 409


def test_reactivar_usuario_respeta_el_limite(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    usuario_id, _ = entorno.crear_usuario("CAJERO", estado="INACTIVO")
    entorno.limite(1)

    respuesta = client.post(
        f"/api/v1/admin/usuarios/{usuario_id}/reactivar", headers=_headers(client, correo)
    )

    assert respuesta.status_code == 409
    with SessionLocal() as db:
        assert db.get(Usuario, usuario_id).estado == "INACTIVO"


def test_reactivar_usuario_activo_no_requiere_cupo(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    usuario_id, _ = entorno.crear_usuario("CAJERO")
    entorno.limite(1)

    respuesta = client.post(
        f"/api/v1/admin/usuarios/{usuario_id}/reactivar", headers=_headers(client, correo)
    )

    assert respuesta.status_code == 200, respuesta.text


@pytest.mark.parametrize(
    ("rol", "estado", "cuerpo"),
    [
        ("CAJERO", "INACTIVO", {"estado": "ACTIVO"}),
        ("SOCIO", "ACTIVO", {"rol": "CAJERO"}),
    ],
)
def test_actualizar_usuario_no_evade_el_limite(client, entorno, rol, estado, cuerpo):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    usuario_id, _ = entorno.crear_usuario(rol, estado=estado)
    entorno.limite(1)

    respuesta = client.put(
        f"/api/v1/admin/usuarios/{usuario_id}", json=cuerpo, headers=_headers(client, correo)
    )

    assert respuesta.status_code == 409
    with SessionLocal() as db:
        usuario = db.get(Usuario, usuario_id)
        assert usuario.estado == estado
        assert usuario.rol.nombre == rol


def test_actualizar_usuario_que_ya_ocupa_cupo_sigue_funcionando(client, entorno):
    _, correo = entorno.crear_usuario("ADMINISTRADOR")
    usuario_id, _ = entorno.crear_usuario("CAJERO")
    entorno.limite(1)

    respuesta = client.put(
        f"/api/v1/admin/usuarios/{usuario_id}",
        json={"nombre": "Nombre actualizado"},
        headers=_headers(client, correo),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["nombre"] == "Nombre actualizado"
