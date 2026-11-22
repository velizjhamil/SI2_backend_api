"""Feedback CRM: socios rate a service; staff list and get statistics of their cooperative's answers."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    from main import app
except Exception:
    app = None

pytestmark = pytest.mark.skipif(app is None, reason="PostgreSQL unavailable")


def _user(db, roles, coop_id, suffix, rol, label):
    user = models.Usuario(correo=f"fb-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                          rol_id=roles[rol].id, cooperativa_id=coop_id, nombre=f"{label} {suffix}", estado="ACTIVO")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def case():
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        roles = {r.nombre: r for r in db.query(models.Rol).all()}
        coop = models.Cooperativa(nombre=f"Feedback {suffix}", estado="ACTIVO")
        other = models.Cooperativa(nombre=f"OtraFb {suffix}", estado="ACTIVO")
        db.add_all([coop, other]); db.flush()
        admin = _user(db, roles, coop.id, suffix, "ADMINISTRADOR", "admin")
        other_admin = _user(db, roles, other.id, suffix, "ADMINISTRADOR", "otroadmin")

        def socio(label, coop_id):
            user = _user(db, roles, coop_id, suffix, "SOCIO", label)
            row = models.Socio(ci=f"F{label}{suffix}", nombre=label.title(), apellido=f"Prueba{suffix}",
                               cooperativa_id=coop_id, estado="ACTIVO", usuario_id=user.id)
            db.add(row); db.flush()
            return user

        socio_a = socio("ana", coop.id)
        socio_b = socio("beto", coop.id)
        socio_x = socio("ajeno", other.id)
        coop_id, other_id = coop.id, other.id
        tokens = {name: create_access_token(str(u.id), rol, u.cooperativa_id)[0]
                  for name, u, rol in (("admin", admin, "ADMINISTRADOR"), ("other_admin", other_admin, "ADMINISTRADOR"),
                                       ("a", socio_a, "SOCIO"), ("b", socio_b, "SOCIO"), ("x", socio_x, "SOCIO"))}
        db.commit()
    yield {"tokens": tokens, "coop_id": coop_id, "other_id": other_id}
    with SessionLocal() as db:
        db.execute(text("DELETE FROM encuesta_satisfaccion WHERE cooperativa_id IN (:a, :b)"),
                   {"a": coop_id, "b": other_id})
        db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _h(case, who):
    return {"Authorization": f"Bearer {case['tokens'][who]}"}


def _rate(client, case, who, **body):
    payload = {"servicio": "CREDITO", "calificacion": 5, "canal": "WEB", **body}
    return client.post("/api/v1/feedback", json=payload, headers=_h(case, who))


def test_socio_rates_a_service_and_lists_own_ratings(client, case):
    response = _rate(client, case, "a", calificacion=4, recomendaria=9, comentario="  Muy buena atención  ")
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["calificacion"] == 4 and created["recomendaria"] == 9
    assert created["comentario"] == "Muy buena atención"
    assert created["servicio"] == "CREDITO" and created["canal"] == "WEB"

    _rate(client, case, "b", calificacion=2)
    mine = client.get("/api/v1/feedback/mias", headers=_h(case, "a"))
    assert mine.status_code == 200
    assert [row["id"] for row in mine.json()] == [created["id"]]

    with SessionLocal() as db:
        stored = db.execute(text("SELECT cooperativa_id, calificacion FROM encuesta_satisfaccion WHERE id = :i"),
                            {"i": created["id"]}).one()
    assert stored.cooperativa_id == case["coop_id"] and stored.calificacion == 4


@pytest.mark.parametrize("body", [
    {"calificacion": 0}, {"calificacion": 6}, {"recomendaria": 11}, {"recomendaria": -1},
    {"servicio": "INVENTADO"}, {"canal": "FAX"}, {"comentario": "x" * 1001},
])
def test_invalid_rating_is_rejected_and_writes_nothing(client, case, body):
    response = _rate(client, case, "a", **body)
    assert response.status_code == 422
    with SessionLocal() as db:
        count = db.execute(text("SELECT count(*) FROM encuesta_satisfaccion WHERE cooperativa_id = :c"),
                           {"c": case["coop_id"]}).scalar_one()
    assert count == 0


def test_staff_cannot_submit_and_socio_cannot_read_cooperative_feedback(client, case):
    assert _rate(client, case, "admin").status_code == 403
    assert client.get("/api/v1/feedback", headers=_h(case, "a")).status_code == 403
    assert client.get("/api/v1/feedback/estadisticas", headers=_h(case, "a")).status_code == 403


def test_staff_lists_only_own_cooperative_with_filters(client, case):
    _rate(client, case, "a", servicio="CREDITO", calificacion=5)
    _rate(client, case, "b", servicio="CAJA", calificacion=2, comentario="Fila larga")
    _rate(client, case, "x", servicio="CAJA", calificacion=1)

    all_rows = client.get("/api/v1/feedback", headers=_h(case, "admin")).json()
    assert len(all_rows) == 2
    assert {row["socio"].split()[0] for row in all_rows} == {"Ana", "Beto"}

    caja = client.get("/api/v1/feedback", params={"servicio": "CAJA"}, headers=_h(case, "admin")).json()
    assert [row["comentario"] for row in caja] == ["Fila larga"]
    low = client.get("/api/v1/feedback", params={"calificacion": 5}, headers=_h(case, "admin")).json()
    assert [row["servicio"] for row in low] == ["CREDITO"]

    other = client.get("/api/v1/feedback", headers=_h(case, "other_admin")).json()
    assert len(other) == 1 and other[0]["calificacion"] == 1


def test_statistics_compute_average_distribution_nps_and_trend(client, case):
    for who, servicio, calificacion, recomendaria in (
        ("a", "CREDITO", 5, 10), ("a", "CREDITO", 4, 9), ("b", "CAJA", 2, 3), ("b", "CAJA", 3, None),
    ):
        assert _rate(client, case, who, servicio=servicio, calificacion=calificacion,
                     recomendaria=recomendaria, comentario=f"c{calificacion}").status_code == 201
    _rate(client, case, "x", calificacion=1, recomendaria=0)

    stats = client.get("/api/v1/feedback/estadisticas", headers=_h(case, "admin"))
    assert stats.status_code == 200, stats.text
    data = stats.json()
    assert data["total"] == 4
    assert data["promedio"] == 3.5
    assert data["distribucion"] == {"1": 0, "2": 1, "3": 1, "4": 1, "5": 1}
    # 3 NPS answers: promoters 10 and 9, detractor 3 -> (2 - 1) / 3 = 33.3
    assert data["nps"] == {"respuestas": 3, "promotores": 2, "pasivos": 0, "detractores": 1, "score": 33.3}
    por_servicio = {row["servicio"]: row for row in data["por_servicio"]}
    assert por_servicio["CREDITO"] == {"servicio": "CREDITO", "total": 2, "promedio": 4.5}
    assert por_servicio["CAJA"] == {"servicio": "CAJA", "total": 2, "promedio": 2.5}
    assert len(data["tendencia_mensual"]) == 1
    assert data["tendencia_mensual"][0]["total"] == 4
    assert len(data["ultimos_comentarios"]) == 4

    filtered = client.get("/api/v1/feedback/estadisticas", params={"servicio": "CAJA"},
                          headers=_h(case, "admin")).json()
    assert filtered["total"] == 2 and filtered["promedio"] == 2.5


def test_statistics_are_empty_without_feedback(client, case):
    data = client.get("/api/v1/feedback/estadisticas", headers=_h(case, "admin")).json()
    assert data["total"] == 0 and data["promedio"] is None
    assert data["nps"]["score"] is None
    assert data["distribucion"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}


def test_staff_filters_feedback_and_statistics_by_channel(client, case):
    _rate(client, case, "a", servicio="CREDITO", calificacion=5, canal="WEB")
    _rate(client, case, "b", servicio="CAJA", calificacion=2, canal="MOVIL")

    web = client.get("/api/v1/feedback", params={"canal": "WEB"}, headers=_h(case, "admin")).json()
    assert [row["canal"] for row in web] == ["WEB"]
    movil = client.get("/api/v1/feedback", params={"canal": "MOVIL"}, headers=_h(case, "admin")).json()
    assert [row["canal"] for row in movil] == ["MOVIL"]

    stats = client.get("/api/v1/feedback/estadisticas", params={"canal": "MOVIL"}, headers=_h(case, "admin")).json()
    assert stats["total"] == 1 and float(stats["promedio"]) == 2.0

    invalid = client.get("/api/v1/feedback", params={"canal": "FAX"}, headers=_h(case, "admin"))
    assert invalid.status_code == 422
