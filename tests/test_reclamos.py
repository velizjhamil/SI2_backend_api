"""Customer claims (Punto de Reclamo, Ley 393) with second-instance escalation to ASFI."""
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.services.reclamos import sumar_dias_habiles

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    from main import app
except Exception:
    app = None

needs_db = pytest.mark.skipif(app is None, reason="PostgreSQL unavailable")


@pytest.mark.parametrize("inicio,esperado", [
    (date(2026, 11, 20), date(2026, 11, 27)),  # Friday
    (date(2026, 11, 23), date(2026, 11, 30)),  # Monday
    (date(2026, 11, 21), date(2026, 11, 27)),  # Saturday
])
def test_response_deadline_is_five_business_days(inicio, esperado):
    assert sumar_dias_habiles(inicio, 5) == esperado


def _user(db, roles, coop_id, suffix, rol, label):
    user = models.Usuario(correo=f"rc-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                          rol_id=roles[rol].id, cooperativa_id=coop_id, nombre=f"{label} {suffix}", estado="ACTIVO")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def case():
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        roles = {r.nombre: r for r in db.query(models.Rol).all()}
        coop = models.Cooperativa(nombre=f"Reclamos {suffix}", estado="ACTIVO")
        other = models.Cooperativa(nombre=f"OtraRc {suffix}", estado="ACTIVO")
        db.add_all([coop, other]); db.flush()
        users = {
            "admin": _user(db, roles, coop.id, suffix, "ADMINISTRADOR", "admin"),
            "cajero": _user(db, roles, coop.id, suffix, "CAJERO", "cajero"),
            "other_admin": _user(db, roles, other.id, suffix, "ADMINISTRADOR", "otro"),
        }
        socios = {}
        for label, coop_id in (("ana", coop.id), ("beto", coop.id), ("ajeno", other.id)):
            user = _user(db, roles, coop_id, suffix, "SOCIO", label)
            row = models.Socio(ci=f"R{label}{suffix}", nombre=label.title(), apellido=f"Prueba{suffix}",
                               cooperativa_id=coop_id, estado="ACTIVO", usuario_id=user.id)
            db.add(row); db.flush()
            users[label] = user
            socios[label] = row.id
        tokens = {name: create_access_token(str(u.id), "SOCIO" if name in socios else u.rol.nombre,
                                            u.cooperativa_id)[0] for name, u in users.items()}
        data = {"tokens": tokens, "coop_id": coop.id, "other_id": other.id, "socios": socios}
        db.commit()
    yield data
    with SessionLocal() as db:
        db.execute(text("DELETE FROM reclamo WHERE cooperativa_id IN (:a, :b)"), {"a": data["coop_id"], "b": data["other_id"]})
        db.execute(text("DELETE FROM secuencia_documento WHERE cooperativa_id IN (:a, :b)"),
                   {"a": data["coop_id"], "b": data["other_id"]})
        db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _h(case, who):
    return {"Authorization": f"Bearer {case['tokens'][who]}"}


def _create(client, case, who="ana", **body):
    payload = {"tipo": "COBRO_INDEBIDO", "descripcion": "Me cobraron dos veces la cuota", **body}
    return client.post("/api/v1/socio/reclamos", json=payload, headers=_h(case, who))


@needs_db
def test_socio_registers_claim_with_number_deadline_and_lists_only_own(client, case):
    response = _create(client, case)
    assert response.status_code == 201, response.text
    claim = response.json()
    assert claim["numero_reclamo"] == f"PR-{date.today().year}-000001"
    assert claim["estado"] == "REGISTRADO" and claim["canal"] == "MOVIL" and claim["vencido"] is False
    assert claim["fecha_limite_respuesta"] == sumar_dias_habiles(date.today(), 5).isoformat()
    assert _create(client, case, "beto").json()["numero_reclamo"] == f"PR-{date.today().year}-000002"

    mine = client.get("/api/v1/socio/reclamos", headers=_h(case, "ana"))
    assert [row["id"] for row in mine.json()] == [claim["id"]]
    with SessionLocal() as db:
        stored = db.execute(text("SELECT cooperativa_id, socio_id, estado FROM reclamo WHERE id = :i"),
                            {"i": claim["id"]}).one()
    assert (stored.cooperativa_id, stored.socio_id, stored.estado) == (case["coop_id"], case["socios"]["ana"], "REGISTRADO")


@needs_db
@pytest.mark.parametrize("body", [{"tipo": "INVENTADO"}, {"descripcion": ""}, {"descripcion": "x" * 2001}])
def test_invalid_claim_is_rejected(client, case, body):
    assert _create(client, case, **body).status_code == 422


@needs_db
def test_claim_on_foreign_credit_is_not_found(client, case):
    assert _create(client, case, credito_id=999999999).status_code == 404


@needs_db
def test_staff_attends_responds_and_socio_accepts(client, case):
    claim = _create(client, case).json()
    listed = client.get("/api/v1/reclamos?estado=REGISTRADO", headers=_h(case, "cajero"))
    assert listed.status_code == 200 and [r["id"] for r in listed.json()] == [claim["id"]]
    assert listed.json()[0]["socio"].startswith("Ana")
    assert client.get("/api/v1/reclamos", headers=_h(case, "other_admin")).json() == []
    assert client.get(f"/api/v1/reclamos/{claim['id']}", headers=_h(case, "other_admin")).status_code == 404

    attended = client.post(f"/api/v1/reclamos/{claim['id']}/atender", headers=_h(case, "admin"))
    assert attended.status_code == 200 and attended.json()["estado"] == "EN_ATENCION"
    assert client.post(f"/api/v1/reclamos/{claim['id']}/atender", headers=_h(case, "admin")).status_code == 409

    early = client.post(f"/api/v1/socio/reclamos/{claim['id']}/conformidad", json={"conforme": True}, headers=_h(case, "ana"))
    assert early.status_code == 409

    answered = client.post(f"/api/v1/reclamos/{claim['id']}/responder",
                           json={"respuesta": "Se devolvió el cobro duplicado", "procedente": True}, headers=_h(case, "admin"))
    assert answered.status_code == 200
    assert answered.json()["estado"] == "RESPONDIDO" and answered.json()["procedente"] is True

    closed = client.post(f"/api/v1/socio/reclamos/{claim['id']}/conformidad", json={"conforme": True}, headers=_h(case, "ana"))
    assert closed.status_code == 200 and closed.json()["estado"] == "CERRADO" and closed.json()["conforme"] is True
    with SessionLocal() as db:
        actions = db.execute(text("SELECT accion FROM bitacora WHERE cooperativa_id = :c AND modulo = 'RECLAMOS'"),
                             {"c": case["coop_id"]}).scalars().all()
    assert {"REGISTRAR_RECLAMO", "ATENDER_RECLAMO", "RESPONDER_RECLAMO", "CERRAR_RECLAMO"} <= set(actions)


@needs_db
def test_socio_escalates_to_asfi_when_disagreeing(client, case):
    claim = _create(client, case).json()
    client.post(f"/api/v1/reclamos/{claim['id']}/responder",
                json={"respuesta": "No corresponde", "procedente": False}, headers=_h(case, "admin"))
    no_reason = client.post(f"/api/v1/socio/reclamos/{claim['id']}/conformidad", json={"conforme": False}, headers=_h(case, "ana"))
    assert no_reason.status_code == 422
    escalated = client.post(f"/api/v1/socio/reclamos/{claim['id']}/conformidad",
                            json={"conforme": False, "motivo": "La respuesta no explica el cobro"}, headers=_h(case, "ana"))
    assert escalated.status_code == 200
    body = escalated.json()
    assert body["estado"] == "ESCALADO_ASFI" and body["motivo_disconformidad"] == "La respuesta no explica el cobro"
    assert body["fecha_escalamiento"] is not None


@needs_db
def test_overdue_claim_can_be_escalated_without_answer(client, case):
    claim = _create(client, case).json()
    blocked = client.post(f"/api/v1/socio/reclamos/{claim['id']}/conformidad",
                          json={"conforme": False, "motivo": "Sin respuesta"}, headers=_h(case, "ana"))
    assert blocked.status_code == 409
    with SessionLocal() as db:
        db.execute(text("UPDATE reclamo SET fecha_limite_respuesta = :d WHERE id = :i"),
                   {"d": date.today() - timedelta(days=1), "i": claim["id"]})
        db.commit()
    overdue = client.get("/api/v1/reclamos?vencido=true", headers=_h(case, "admin")).json()
    assert [r["id"] for r in overdue] == [claim["id"]] and overdue[0]["vencido"] is True
    escalated = client.post(f"/api/v1/socio/reclamos/{claim['id']}/conformidad",
                            json={"conforme": False, "motivo": "Sin respuesta"}, headers=_h(case, "ana"))
    assert escalated.status_code == 200 and escalated.json()["estado"] == "ESCALADO_ASFI"


@needs_db
def test_staff_registers_counter_claim_and_socio_cannot_use_staff_routes(client, case):
    created = client.post("/api/v1/reclamos", headers=_h(case, "cajero"), json={
        "socio_id": case["socios"]["beto"], "tipo": "ATENCION", "descripcion": "Demora en ventanilla"})
    assert created.status_code == 201, created.text
    assert created.json()["canal"] == "VENTANILLA" and created.json()["socio_id"] == case["socios"]["beto"]
    foreign = client.post("/api/v1/reclamos", headers=_h(case, "cajero"), json={
        "socio_id": case["socios"]["ajeno"], "tipo": "ATENCION", "descripcion": "x"})
    assert foreign.status_code == 404
    assert client.get("/api/v1/reclamos", headers=_h(case, "ana")).status_code == 403
    assert client.post(f"/api/v1/socio/reclamos/{created.json()['id']}/conformidad",
                       json={"conforme": True}, headers=_h(case, "ana")).status_code == 404


def _set_registro(claim_id, registro, limite):
    with SessionLocal() as db:
        db.execute(text("UPDATE reclamo SET fecha_registro = :r, fecha_limite_respuesta = :l WHERE id = :i"),
                   {"r": registro, "l": limite, "i": claim_id})
        db.commit()


def _ampliar(client, case, claim_id, nueva_fecha, motivo="Requiere revisar movimientos del sistema"):
    return client.post(f"/api/v1/reclamos/{claim_id}/ampliar", headers=_h(case, "admin"),
                       json={"nueva_fecha": nueva_fecha.isoformat(), "motivo": motivo})


@needs_db
def test_first_extension_is_capped_at_ten_business_days(client, case):
    claim = _create(client, case).json()
    registro = date.today()
    limite10 = sumar_dias_habiles(registro, 10)
    too_far = _ampliar(client, case, claim["id"], limite10 + timedelta(days=1))
    assert too_far.status_code == 400
    assert too_far.json()["detail"] == "La primera ampliación no puede superar los 10 días hábiles desde la recepción del reclamo"
    not_later = _ampliar(client, case, claim["id"], date.today())
    assert not_later.status_code == 400
    no_reason = client.post(f"/api/v1/reclamos/{claim['id']}/ampliar", headers=_h(case, "admin"),
                            json={"nueva_fecha": limite10.isoformat(), "motivo": " "})
    assert no_reason.status_code == 422

    extended = _ampliar(client, case, claim["id"], limite10)
    assert extended.status_code == 200, extended.text
    body = extended.json()
    assert body["fecha_limite_respuesta"] == limite10.isoformat()
    assert body["ampliaciones"] == 1 and body["notificado_asfi"] is False
    assert body["motivo_ampliacion"] == "Requiere revisar movimientos del sistema"


@needs_db
def test_additional_extensions_notify_asfi_and_are_limited_to_two(client, case):
    claim = _create(client, case).json()
    limite = sumar_dias_habiles(date.today(), 10)
    assert _ampliar(client, case, claim["id"], limite).status_code == 200
    over = _ampliar(client, case, claim["id"], sumar_dias_habiles(limite, 5) + timedelta(days=1))
    assert over.status_code == 400 and over.json()["detail"] == "Cada ampliación adicional no puede superar 5 días hábiles"
    for numero in (2, 3):
        limite = sumar_dias_habiles(limite, 5)
        response = _ampliar(client, case, claim["id"], limite)
        assert response.status_code == 200, response.text
        assert response.json()["ampliaciones"] == numero and response.json()["notificado_asfi"] is True
    fourth = _ampliar(client, case, claim["id"], sumar_dias_habiles(limite, 1))
    assert fourth.status_code == 409 and fourth.json()["detail"] == "Ya se usaron las dos ampliaciones adicionales permitidas"


@needs_db
def test_extension_requires_open_claim_within_current_deadline(client, case):
    claim = _create(client, case).json()
    _set_registro(claim["id"], date.today() - timedelta(days=14), date.today() - timedelta(days=1))
    late = _ampliar(client, case, claim["id"], date.today() + timedelta(days=1))
    assert late.status_code == 409
    other = _create(client, case).json()
    client.post(f"/api/v1/reclamos/{other['id']}/responder", headers=_h(case, "admin"),
                json={"respuesta": "Resuelto", "procedente": True})
    answered = _ampliar(client, case, other["id"], sumar_dias_habiles(date.today(), 6))
    assert answered.status_code == 409
    assert client.post(f"/api/v1/reclamos/{other['id']}/ampliar", headers=_h(case, "ana"),
                       json={"nueva_fecha": date.today().isoformat(), "motivo": "x"}).status_code == 403
