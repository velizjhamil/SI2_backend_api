"""API contract for the authenticated plan module catalogue."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.v1.router import api_router
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.models import Cooperativa, PlanSuscripcion, Rol, Suscripcion, Usuario

COMMON_MODULES = {"reportes_gerenciales"}
GROWTH_MODULES = {
    "banca_movil", "scoring_crediticio", "informes_asfi",
    "respaldos_bitacora", "chatbot_ia", "notificaciones_socios",
}
ENTERPRISE_MODULES = GROWTH_MODULES | {"recredito_ia"}
ALL_MODULES = COMMON_MODULES | ENTERPRISE_MODULES


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


class Identity:
    def __init__(self, plan_code=None, role="ADMINISTRADOR", with_subscription=True):
        self.plan_code = plan_code
        self.role = role
        self.with_subscription = with_subscription
        self.user_id = None
        self.coop_id = None
        self.created_plan_id = None

    def create(self):
        suffix = uuid4().hex
        with SessionLocal() as db:
            role = db.execute(select(Rol).where(Rol.nombre == self.role)).scalar_one()
            coop = None
            plan = None
            if self.plan_code is not None:
                plan = db.execute(
                    select(PlanSuscripcion).where(PlanSuscripcion.codigo == self.plan_code)
                ).scalar_one_or_none()
                if plan is None:
                    plan = PlanSuscripcion(
                        codigo=self.plan_code,
                        nombre="Plan desconocido de prueba",
                        moneda="BOB",
                        estado="ACTIVO",
                    )
                    db.add(plan)
                    db.flush()
                    self.created_plan_id = plan.id
                coop = Cooperativa(nombre=f"Mis módulos {suffix}", estado="ACTIVO")
                db.add(coop)
                db.flush()

            user = Usuario(
                nombre="Mis módulos test",
                correo=f"mis-modulos-{suffix}@example.com",
                contrasena="not-used",
                rol_id=role.id,
                cooperativa_id=coop.id if coop else None,
                estado="ACTIVO",
            )
            db.add(user)
            db.flush()
            if self.with_subscription and coop is not None:
                db.add(Suscripcion(cooperativa_id=coop.id, plan_id=plan.id, estado="ACTIVA"))
            db.commit()
            self.user_id = user.id
            self.coop_id = coop.id if coop else None
        return self

    def headers(self):
        token, _ = create_access_token(self.user_id, self.role, self.coop_id)
        return {"Authorization": f"Bearer {token}"}

    def cleanup(self):
        if self.user_id is None:
            return
        with SessionLocal() as db:
            if self.coop_id is not None:
                db.execute(delete(Suscripcion).where(Suscripcion.cooperativa_id == self.coop_id))
                db.execute(delete(Usuario).where(Usuario.id == self.user_id))
                db.execute(delete(Cooperativa).where(Cooperativa.id == self.coop_id))
            else:
                db.execute(delete(Usuario).where(Usuario.id == self.user_id))
            if self.created_plan_id is not None:
                db.execute(delete(PlanSuscripcion).where(PlanSuscripcion.id == self.created_plan_id))
            db.commit()


@pytest.mark.parametrize(
    ("plan_code", "plan_name", "modules"),
    [
        ("BASICO", "Básico", COMMON_MODULES),
        ("CRECIMIENTO_PRO", "Crecimiento Pro", COMMON_MODULES | GROWTH_MODULES),
        ("RED_ENTERPRISE", "Red Enterprise", COMMON_MODULES | ENTERPRISE_MODULES),
    ],
)
def test_mis_modulos_reports_plan_entitlements_for_each_plan(client, request, plan_code, plan_name, modules):
    identity = Identity(plan_code).create()
    request.addfinalizer(identity.cleanup)

    response = client.get("/api/v1/suscripciones/mis-modulos", headers=identity.headers())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "plan": {"codigo": plan_code, "nombre": plan_name},
        "modulos": sorted(modules),
        "sin_restriccion": False,
    }


def test_mis_modulos_unknown_plan_uses_existing_fallback(client, request):
    identity = Identity(f"UNKNOWN-{uuid4().hex.upper()}").create()
    request.addfinalizer(identity.cleanup)

    response = client.get("/api/v1/suscripciones/mis-modulos", headers=identity.headers())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "plan": {"codigo": identity.plan_code, "nombre": "Plan desconocido de prueba"},
        "modulos": [],
        "sin_restriccion": False,
    }


@pytest.mark.parametrize(
    ("role", "plan_code", "with_subscription"),
    [
        ("SUPERADMIN", None, False),
        ("SOCIO", None, False),
        ("SOCIO", "BASICO", False),
    ],
)
def test_mis_modulos_keeps_unrestricted_identity_exceptions(client, request, role, plan_code, with_subscription):
    identity = Identity(plan_code, role=role, with_subscription=with_subscription).create()
    request.addfinalizer(identity.cleanup)

    response = client.get("/api/v1/suscripciones/mis-modulos", headers=identity.headers())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "plan": None,
        "modulos": sorted(ALL_MODULES),
        "sin_restriccion": True,
    }
