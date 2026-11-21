"""Run the SQL subscription-plan seed twice and verify demo logins."""

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.session import SessionLocal, engine
from app.models.models import Bitacora, Cooperativa, PlanSuscripcion, Rol, Suscripcion, Usuario
from main import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_SQL = PROJECT_ROOT / "scripts" / "seed_demo_planes.sql"
DEMO_PREFIX = "SI2 Demo Subscription"
DEMO_ADMIN_PASSWORD = "DemoOnly-SI2-2026!"


def _demo_admin_email(plan_code: str, number: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", plan_code.lower()).strip("-")[:24] or "plan"
    digest = hashlib.sha256(plan_code.encode("utf-8")).hexdigest()[:8]
    return f"demo-plan-{slug}-{digest}-{number:02d}@example.com"


def _snapshot():
    with SessionLocal() as db:
        plans = db.execute(
            select(PlanSuscripcion.id, PlanSuscripcion.codigo, PlanSuscripcion.nombre)
            .order_by(PlanSuscripcion.id)
        ).all()
        cooperatives = db.execute(
            select(Cooperativa).where(Cooperativa.nombre.like(f"{DEMO_PREFIX}%"))
        ).scalars().all()
        cooperative_ids = {cooperative.id for cooperative in cooperatives}
        subscriptions = db.execute(
            select(Suscripcion).where(Suscripcion.cooperativa_id.in_(cooperative_ids))
        ).scalars().all()
        users = db.execute(
            select(Usuario).where(Usuario.correo.like("demo-plan-%@example.com"))
        ).scalars().all()

        return {
            "plans": [(plan.id, plan.codigo, plan.nombre) for plan in plans],
            "cooperatives": {
                cooperative.nombre: (cooperative.id, cooperative.estado)
                for cooperative in cooperatives
            },
            "subscriptions": {
                subscription.cooperativa_id: (
                    subscription.id,
                    subscription.plan_id,
                    subscription.estado,
                )
                for subscription in subscriptions
            },
            "users": {
                user.correo: (
                    user.id,
                    user.cooperativa_id,
                    user.rol_id,
                    user.estado,
                    user.contrasena,
                )
                for user in users
            },
        }


def _run_seed_sql() -> None:
    statement = SEED_SQL.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql(statement)


@pytest.fixture
def cleanup_seed_rows_added_by_test():
    inserted = {"cooperatives": set(), "subscriptions": set(), "users": set(), "logs": set()}
    yield inserted
    if any(inserted.values()):
        with SessionLocal() as db:
            if inserted["logs"]:
                db.execute(delete(Bitacora).where(Bitacora.id.in_(inserted["logs"])))
            if inserted["subscriptions"]:
                db.execute(
                    delete(Suscripcion).where(Suscripcion.id.in_(inserted["subscriptions"]))
                )
            if inserted["users"]:
                db.execute(delete(Usuario).where(Usuario.id.in_(inserted["users"])))
            if inserted["cooperatives"]:
                db.execute(
                    delete(Cooperativa).where(Cooperativa.id.in_(inserted["cooperatives"]))
                )
            db.commit()


def test_sql_seed_is_idempotent_and_demo_admins_can_log_in(cleanup_seed_rows_added_by_test):
    before = _snapshot()
    assert before["plans"], "The test database needs at least one existing subscription plan."

    _run_seed_sql()
    after_first_run = _snapshot()
    for key, id_index in (("cooperatives", 0), ("subscriptions", 0), ("users", 0)):
        initial_ids = {row[id_index] for row in before[key].values()}
        created_ids = {row[id_index] for row in after_first_run[key].values()} - initial_ids
        cleanup_seed_rows_added_by_test[key].update(created_ids)

    expected_plans = {plan_id: code for plan_id, code, _name in after_first_run["plans"]}
    assert expected_plans
    assert len(after_first_run["cooperatives"]) == len(expected_plans) * 3
    assert len(after_first_run["subscriptions"]) == len(expected_plans) * 3
    assert len(after_first_run["users"]) == len(expected_plans) * 3

    with SessionLocal() as db:
        admin_role_id = db.execute(
            select(Rol.id).where(Rol.nombre == "ADMINISTRADOR")
        ).scalar_one()

    for plan_id, plan_code in expected_plans.items():
        for number in range(1, 4):
            cooperative_name = f"{DEMO_PREFIX} - {plan_code} - {number:02d}"
            cooperative = after_first_run["cooperatives"][cooperative_name]
            assert cooperative[1] == "ACTIVO"
            subscription = after_first_run["subscriptions"][cooperative[0]]
            assert subscription[1:] == (plan_id, "ACTIVA")
            email = _demo_admin_email(plan_code, number)
            admin = after_first_run["users"][email]
            assert admin[1:4] == (cooperative[0], admin_role_id, "ACTIVO")

    _run_seed_sql()
    after_second_run = _snapshot()
    assert after_second_run == after_first_run

    baseline_log_ids = set()
    demo_admin_ids = {row[0] for row in after_second_run["users"].values()}
    with SessionLocal() as db:
        baseline_log_ids = set(
            db.execute(
                select(Bitacora.id).where(Bitacora.usuario_id.in_(demo_admin_ids))
            ).scalars()
        )

    with TestClient(app) as client:
        for _plan_id, plan_code in expected_plans.items():
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "correo": _demo_admin_email(plan_code, 1),
                    "contrasena": DEMO_ADMIN_PASSWORD,
                },
            )
            with SessionLocal() as db:
                current_log_ids = set(
                    db.execute(
                        select(Bitacora.id).where(Bitacora.usuario_id.in_(demo_admin_ids))
                    ).scalars()
                )
            cleanup_seed_rows_added_by_test["logs"].update(current_log_ids - baseline_log_ids)
            assert response.status_code == 200, (
                f"Demo login failed for plan {plan_code}: {response.text}"
            )
            assert response.json().get("access_token")
