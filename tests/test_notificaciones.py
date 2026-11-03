"""Gestionar notificaciones: staff sends reminders/promotions, socios read them in their inbox."""
from datetime import date, timedelta
from decimal import Decimal
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
    user = models.Usuario(correo=f"notif-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                          rol_id=roles[rol].id, cooperativa_id=coop_id, nombre=f"{label} {suffix}", estado="ACTIVO")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def case():
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        roles = {r.nombre: r for r in db.query(models.Rol).all()}
        coop = models.Cooperativa(nombre=f"Notif {suffix}", estado="ACTIVO")
        other = models.Cooperativa(nombre=f"Otra {suffix}", estado="ACTIVO")
        db.add_all([coop, other]); db.flush()
        staff = {rol: _user(db, roles, coop.id, suffix, rol, rol.lower())
                 for rol in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO")}

        def socio(label, coop_id=coop.id, account=True):
            user = _user(db, roles, coop_id, suffix, "SOCIO", label) if account else None
            row = models.Socio(ci=f"N{label}{suffix}", nombre=label.title(), apellido=f"Prueba{suffix}",
                               cooperativa_id=coop_id, estado="ACTIVO", usuario_id=user.id if user else None)
            db.add(row); db.flush()
            return row, user

        moroso, moroso_user = socio("moroso")
        al_dia, al_dia_user = socio("aldia")
        sin_credito, _ = socio("sincredito")
        sin_cuenta, _ = socio("sincuenta", account=False)
        ajeno, _ = socio("ajeno", coop_id=other.id)

        product = models.ProductoCredito(cooperativa_id=coop.id, codigo=f"N{suffix}", nombre="Consumo",
              moneda_id=1, monto_min=1, monto_max=100000, plazo_min_meses=1, plazo_max_meses=60,
              tasa_interes_anual=0, tipo_amortizacion="FRANCES", dias_gracia_mora=0, estado="ACTIVO")
        db.add(product); db.flush()
        credits = []
        for owner, due, paid in ((moroso, date.today() - timedelta(days=10), Decimal("50")),
                                 (al_dia, date.today() + timedelta(days=3), Decimal("0")),
                                 (sin_cuenta, date.today() + timedelta(days=2), Decimal("0"))):
            request = models.SolicitudCredito(monto=1000, plazo_meses=12, tasa_interes=0, socio_id=owner.id,
                  usuario_id=staff["ADMINISTRADOR"].id, producto_credito_id=product.id,
                  numero_solicitud=f"N{owner.id}{suffix}", destino="CONSUMO", moneda_id=1, cooperativa_id=coop.id)
            db.add(request); db.flush()
            credit = models.Credito(monto_aprobado=1000, saldo_pendiente=1000, estado="VIGENTE",
                  solicitud_credito_id=request.id, numero_credito=f"N{owner.id}{suffix}", cooperativa_id=coop.id,
                  socio_id=owner.id, producto_credito_id=product.id, moneda_id=1,
                  fecha_desembolso=date.today() - timedelta(days=40), usuario_id=staff["ADMINISTRADOR"].id)
            db.add(credit); db.flush()
            db.add(models.TablaAmortizacion(numero_cuota=1, fecha_vencimiento=due, monto_capital=250,
                   monto_interes=0, monto_cuota_total=250, monto_pagado=paid, estado_pago="PENDIENTE",
                   credito_id=credit.id))
            credits.append(credit.id)
        db.commit()
        tokens = {rol: create_access_token(str(u.id), rol, coop.id)[0] for rol, u in staff.items()}
        tokens["moroso"] = create_access_token(str(moroso_user.id), "SOCIO", coop.id)[0]
        tokens["aldia"] = create_access_token(str(al_dia_user.id), "SOCIO", coop.id)[0]
        ids = {"coops": [coop.id, other.id], "credits": credits, "product": product.id,
               "socios": [moroso.id, al_dia.id, sin_credito.id, sin_cuenta.id, ajeno.id],
               "moroso": moroso.id, "aldia": al_dia.id, "sin_cuenta": sin_cuenta.id, "ajeno": ajeno.id}
    try:
        yield {"tokens": tokens, "ids": ids}
    finally:
        with SessionLocal() as db:
            params = {"coops": ids["coops"], "credits": ids["credits"]}
            for sql in (
                "DELETE FROM notificacion WHERE cooperativa_id = ANY(:coops)",
                "DELETE FROM bitacora WHERE usuario_id IN (SELECT id FROM usuario WHERE cooperativa_id = ANY(:coops))",
                "DELETE FROM tabla_amortizacion WHERE credito_id = ANY(:credits)",
                "DELETE FROM credito WHERE id = ANY(:credits)",
                "DELETE FROM solicitud_credito WHERE cooperativa_id = ANY(:coops)",
                "DELETE FROM producto_credito WHERE cooperativa_id = ANY(:coops)",
                "DELETE FROM socio WHERE cooperativa_id = ANY(:coops)",
                "DELETE FROM usuario WHERE cooperativa_id = ANY(:coops)",
                "DELETE FROM cooperativa WHERE id = ANY(:coops)",
            ):
                db.execute(text(sql), params)
            db.commit()


def h(case, who):
    return {"Authorization": f"Bearer {case['tokens'][who]}"}


def send(client, case, who="ADMINISTRADOR", **body):
    payload = {"tipo": "AVISO", "titulo": "Aviso", "mensaje": "Hola {nombre}", "destino": "TODOS", **body}
    return client.post("/api/v1/notificaciones", json=payload, headers=h(case, who))


def test_only_admin_and_cajero_can_send(case):
    with TestClient(app) as client:
        assert send(client, case, "ADMINISTRADOR").status_code == 201
        assert send(client, case, "CAJERO").status_code == 201
        assert send(client, case, "OFICIAL_CREDITO").status_code == 403
        assert send(client, case, "moroso").status_code == 403
        assert client.get("/api/v1/notificaciones/segmentos", headers=h(case, "OFICIAL_CREDITO")).status_code == 403


def test_segments_count_only_socios_with_accounts_of_own_cooperative(case):
    with TestClient(app) as client:
        body = client.get("/api/v1/notificaciones/segmentos", headers=h(case, "CAJERO")).json()
    seg = body["segmentos"]
    assert seg["TODOS"] == {"destinatarios": 3, "sin_cuenta": 1}
    assert seg["CUOTAS_POR_VENCER"] == {"destinatarios": 1, "sin_cuenta": 1}
    assert seg["EN_MORA"] == {"destinatarios": 1, "sin_cuenta": 0}


def test_payment_reminder_to_overdue_socios_is_personalized(case):
    with TestClient(app) as client:
        sent = send(client, case, tipo="RECORDATORIO_PAGO", destino="EN_MORA", titulo="Cuota vencida",
                    mensaje="Hola {nombre}, tu cuota de {monto_cuota} venció el {fecha_cuota}. — {cooperativa}")
        assert sent.status_code == 201, sent.text
        assert sent.json()["total_destinatarios"] == 1
        inbox = client.get("/api/v1/notificaciones/mias", headers=h(case, "moroso")).json()
        other_inbox = client.get("/api/v1/notificaciones/mias", headers=h(case, "aldia")).json()
    due = (date.today() - timedelta(days=10)).strftime("%d/%m/%Y")
    assert inbox["no_leidas"] == 1
    # Saldo de la cuota: 250 - 50 ya pagados; {cooperativa} es "Notif <sufijo>".
    assert inbox["items"][0]["mensaje"].startswith(f"Hola Moroso, tu cuota de Bs. 200.00 venció el {due}. — Notif ")
    assert inbox["items"][0]["tipo"] == "RECORDATORIO_PAGO"
    assert other_inbox["items"] == []


def test_send_to_one_socio_rejects_foreign_or_accountless_socios(case):
    ids = case["ids"]
    with TestClient(app) as client:
        one = send(client, case, destino="SOCIO", socio_ids=[ids["aldia"]], tipo="PROMOCION", titulo="Promo")
        assert one.status_code == 201 and one.json()["total_destinatarios"] == 1
        assert send(client, case, destino="SOCIO", socio_ids=[ids["ajeno"]]).status_code == 422
        none = send(client, case, destino="SOCIO", socio_ids=[ids["sin_cuenta"]])
        assert none.status_code == 422
        assert send(client, case, destino="SOCIO", socio_ids=[]).status_code == 422
        assert send(client, case, tipo="SPAM").status_code == 422
        assert client.get("/api/v1/notificaciones/mias", headers=h(case, "moroso")).json()["items"] == []


def test_inbox_read_flow_and_history_counts(case):
    with TestClient(app) as client:
        send(client, case, titulo="Uno")
        send(client, case, titulo="Dos")
        inbox = client.get("/api/v1/notificaciones/mias", headers=h(case, "aldia")).json()
        assert inbox["no_leidas"] == 2 and [i["titulo"] for i in inbox["items"]] == ["Dos", "Uno"]
        first = inbox["items"][0]["id"]
        # Another socio cannot mark someone else's notification.
        assert client.post(f"/api/v1/notificaciones/mias/{first}/leer", headers=h(case, "moroso")).status_code == 404
        assert client.post(f"/api/v1/notificaciones/mias/{first}/leer", headers=h(case, "aldia")).status_code == 200
        assert client.get("/api/v1/notificaciones/mias/no-leidas", headers=h(case, "aldia")).json() == {"no_leidas": 1}
        assert client.post("/api/v1/notificaciones/mias/leer-todas", headers=h(case, "aldia")).json() == {"marcadas": 1}
        history = client.get("/api/v1/notificaciones", headers=h(case, "ADMINISTRADOR")).json()
    assert [n["titulo"] for n in history[:2]] == ["Dos", "Uno"]
    assert history[0]["total_destinatarios"] == 3 and history[0]["leidas"] == 1


def test_socio_search_is_tenant_scoped(case):
    with TestClient(app) as client:
        found = client.get("/api/v1/notificaciones/socios", params={"q": "moroso"},
                           headers=h(case, "CAJERO")).json()
        foreign = client.get("/api/v1/notificaciones/socios", params={"q": "ajeno"},
                             headers=h(case, "CAJERO")).json()
    assert [s["id"] for s in found] == [case["ids"]["moroso"]] and found[0]["tiene_cuenta"] is True
    assert foreign == []
