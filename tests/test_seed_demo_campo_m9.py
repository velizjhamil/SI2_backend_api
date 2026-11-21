"""Integration tests for the SQL demo seed used by mobile CU-M9 field evaluation."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.api.v1.deps import get_db
from app.db.session import SessionLocal
from app.models.models import Cooperativa, Rol, SolicitudCredito, Socio, Usuario
from main import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_SQL = PROJECT_ROOT / "scripts" / "seed_demo_campo_m9.sql"
COOPERATIVE_NAME = "Cooperativa de Prueba SI2"
OFFICIAL_EMAIL = "acredito@cooperativa.com"
DEMO_REQUESTS = {
    "M9-DEMO-001": ("M9-DEMO-SOCIO-01", "MOVIL"),
    "M9-DEMO-002": ("M9-DEMO-SOCIO-02", "MOVIL"),
    "M9-DEMO-003": ("M9-DEMO-SOCIO-03", "VENTANILLA"),
}


def _snapshot(db, cooperative_id: int) -> dict:
    socios = db.execute(
        select(Socio).where(
            Socio.cooperativa_id == cooperative_id,
            Socio.ci.in_([ci for ci, _channel in DEMO_REQUESTS.values()]),
        )
    ).scalars().all()
    solicitudes = db.execute(
        select(SolicitudCredito).where(
            SolicitudCredito.cooperativa_id == cooperative_id,
            SolicitudCredito.numero_solicitud.in_(DEMO_REQUESTS),
        )
    ).scalars().all()
    return {
        "socios": {
            socio.ci: (
                socio.id,
                socio.nombre,
                socio.apellido,
                socio.estado,
                socio.usuario_id,
            )
            for socio in socios
        },
        "solicitudes": {
            solicitud.numero_solicitud: (
                solicitud.id,
                solicitud.socio_id,
                solicitud.estado,
                solicitud.canal_origen,
                solicitud.evaluacion_campo_id,
                solicitud.producto_credito_id,
                solicitud.moneda_id,
                solicitud.usuario_id,
            )
            for solicitud in solicitudes
        },
    }


def _run_seed_sql(db) -> None:
    statement = SEED_SQL.read_text(encoding="utf-8")
    db.connection().exec_driver_sql(statement)


@pytest.fixture
def cleanup_rows_created_by_test():
    db = SessionLocal()
    try:
        cooperative = db.execute(
            select(Cooperativa).where(Cooperativa.nombre == COOPERATIVE_NAME)
        ).scalar_one()
        user = db.execute(
            select(Usuario)
            .join(Rol, Usuario.rol_id == Rol.id)
            .where(
                Usuario.correo == OFFICIAL_EMAIL,
                Usuario.estado == "ACTIVO",
                Rol.nombre == "OFICIAL_CREDITO",
            )
        ).scalar_one()
        if user.cooperativa_id not in (None, cooperative.id):
            pytest.skip("The demo official belongs to a different cooperative in the test database.")
        # The partial test dump has this existing official unassigned. Keep this
        # test-only tenant binding inside the transaction; fixture teardown rolls it back.
        if user.cooperativa_id is None:
            user.cooperativa_id = cooperative.id
            db.flush()
        initial = _snapshot(db, cooperative.id)
        official = (cooperative.id, user.id)
        created = {"cooperative_id": cooperative.id}
        yield created, initial, official, db
    finally:
        # Rollback removes only rows made by the seed in this test transaction
        # and restores any test-only tenant binding without persisting user edits.
        db.rollback()
        db.close()


def test_sql_seed_is_idempotent_and_requests_are_available_to_field_official(
    cleanup_rows_created_by_test,
):
    _created, before, (cooperative_id, official_id), db = cleanup_rows_created_by_test
    assert before["socios"].keys() <= {ci for ci, _channel in DEMO_REQUESTS.values()}

    _run_seed_sql(db)
    after_first_run = _snapshot(db, cooperative_id)

    _run_seed_sql(db)
    after_second_run = _snapshot(db, cooperative_id)
    assert after_second_run == after_first_run
    assert set(after_second_run["socios"]) == {
        ci for ci, _channel in DEMO_REQUESTS.values()
    }
    assert set(after_second_run["solicitudes"]) == set(DEMO_REQUESTS)
    for request_number, (_ci, channel) in DEMO_REQUESTS.items():
        row = after_second_run["solicitudes"][request_number]
        assert row[2] == "PENDIENTE"
        assert row[3] == channel
        assert row[4] is None
        assert row[7] == official_id

    token, _ = create_access_token(
        subject=official_id,
        role="OFICIAL_CREDITO",
        cooperativa_id=cooperative_id,
    )
    headers = {"Authorization": f"Bearer {token}"}

    previous_get_db_override = app.dependency_overrides.get(get_db)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            listed_ids = set()
            for channel in {channel for _ci, channel in DEMO_REQUESTS.values()}:
                response = client.get(
                    "/api/v1/creditos/solicitudes",
                    params={"requiere_evaluacion": "true", "canal_origen": channel},
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                ids = {item["numero_solicitud"]: item["id"] for item in response.json()}
                expected = {
                    number for number, (_ci, request_channel) in DEMO_REQUESTS.items()
                    if request_channel == channel
                }
                assert expected <= ids.keys()
                listed_ids.update(ids[number] for number in expected)

            assert listed_ids == {
                row[0] for row in after_second_run["solicitudes"].values()
            }
            for request_id in listed_ids:
                ficha = client.get(
                    f"/api/v1/creditos/solicitudes/{request_id}/ficha-campo",
                    headers=headers,
                )
                assert ficha.status_code == 200, ficha.text
                assert ficha.json()["solicitud"]["requiere_evaluacion"] is True
                assert ficha.json()["evaluacion"] is None
    finally:
        if previous_get_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_get_db_override
