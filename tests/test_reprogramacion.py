"""Credit rescheduling (reprogramación): request, preview, approval and closed REPROGRAMADA installments."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from dateutil.relativedelta import relativedelta
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.services.amortizacion import generar_plan_pagos
from app.services.reprogramacion import plan_reprogramacion

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    from main import app
except Exception:
    app = None

needs_db = pytest.mark.skipif(app is None, reason="PostgreSQL unavailable")
HOY = date.today()


class _Cuota:
    def __init__(self, numero, vencimiento, capital, interes, estado="PENDIENTE"):
        self.numero_cuota, self.fecha_vencimiento = numero, vencimiento
        self.monto_capital, self.monto_interes, self.estado_pago = Decimal(capital), Decimal(interes), estado
        self.monto_cuota_total = Decimal(capital) + Decimal(interes)


def test_plan_carries_overdue_interest_without_capitalizing_it():
    cuotas = [
        _Cuota(1, HOY - relativedelta(months=2), "300", "30", "PAGADA"),
        _Cuota(2, HOY - relativedelta(months=1), "310", "20.50"),
        _Cuota(3, HOY + relativedelta(days=5), "320", "10"),
    ]
    result = plan_reprogramacion(
        cuotas=cuotas, saldo_capital=Decimal("630.00"), tasa_anual=Decimal("12.00"),
        tipo_amortizacion="FRANCES", tasa_seguro_desgravamen=Decimal("0.00"), nuevo_plazo_meses=6,
        hoy=HOY, tasa_mora_anual=Decimal("36.00"), dias_gracia_mora=0,
    )
    base = generar_plan_pagos(monto=Decimal("630.00"), tasa_anual=Decimal("12.00"), plazo_meses=6,
                              tipo_amortizacion="FRANCES", fecha_desembolso=HOY)
    assert result["interes_diferido"] == Decimal("20.50")
    assert result["cuotas_reprogramadas"] == 2
    assert result["mora_condonada"] > 0
    plan = result["plan"]
    assert plan["total_capital"] == Decimal("630.00")
    assert plan["cuotas"][0]["interes"] == base["cuotas"][0]["interes"] + Decimal("20.50")
    assert plan["cuotas"][0]["cuota"] == base["cuotas"][0]["cuota"] + Decimal("20.50")
    assert plan["cuotas"][1:] == base["cuotas"][1:]
    assert plan["total_interes"] == base["total_interes"] + Decimal("20.50")
    assert plan["total_pagar"] == base["total_pagar"] + Decimal("20.50")


def _user(db, roles, coop_id, suffix, rol, label):
    user = models.Usuario(correo=f"rp-{label}-{suffix}@test.invalid", contrasena=hash_password("Password123"),
                          rol_id=roles[rol].id, cooperativa_id=coop_id, nombre=f"{label} {suffix}", estado="ACTIVO")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def case():
    """A 12-month credit disbursed two months ago: installment 1 paid, installment 2 overdue."""
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        roles = {r.nombre: r for r in db.query(models.Rol).all()}
        coop = models.Cooperativa(nombre=f"Reprog {suffix}", estado="ACTIVO")
        other = models.Cooperativa(nombre=f"OtraRp {suffix}", estado="ACTIVO")
        db.add_all([coop, other]); db.flush()
        admin = _user(db, roles, coop.id, suffix, "ADMINISTRADOR", "admin")
        oficial = _user(db, roles, coop.id, suffix, "OFICIAL_CREDITO", "oficial")
        cajero = _user(db, roles, coop.id, suffix, "CAJERO", "cajero")
        other_admin = _user(db, roles, other.id, suffix, "ADMINISTRADOR", "otro")
        socio_user = _user(db, roles, coop.id, suffix, "SOCIO", "ana")
        intruso_user = _user(db, roles, coop.id, suffix, "SOCIO", "beto")
        bob = db.query(models.Moneda).filter(models.Moneda.codigo_iso == "BOB").one()
        product = models.ProductoCredito(
            cooperativa_id=coop.id, codigo=f"RP{suffix[:6]}".upper(), nombre="Consumo Reprog", moneda_id=bob.id,
            monto_min=Decimal("1000"), monto_max=Decimal("50000"), plazo_min_meses=1, plazo_max_meses=36,
            tasa_interes_anual=Decimal("12.00"), tasa_seguro_desgravamen=Decimal("0.05"), tipo_amortizacion="FRANCES",
            tasa_mora_anual=Decimal("10.00"), tipo_credito_asfi="CONSUMO", estado="ACTIVO",
        )
        socio = models.Socio(ci=f"RP{suffix}", nombre="Ana", apellido=f"Reprog{suffix}", cooperativa_id=coop.id,
                             estado="ACTIVO", usuario_id=socio_user.id)
        intruso = models.Socio(ci=f"RPX{suffix}", nombre="Beto", apellido=f"Ajeno{suffix}", cooperativa_id=coop.id,
                               estado="ACTIVO", usuario_id=intruso_user.id)
        db.add_all([product, socio, intruso]); db.flush()
        solicitud = models.SolicitudCredito(
            monto=Decimal("12000.00"), plazo_meses=12, tasa_interes=Decimal("12.00"), estado="DESEMBOLSADO",
            socio_id=socio.id, usuario_id=oficial.id, producto_credito_id=product.id, moneda_id=bob.id,
            cooperativa_id=coop.id, numero_solicitud=f"SOL-RP{suffix[:5]}",
        )
        db.add(solicitud); db.flush()
        desembolso = HOY - relativedelta(months=2) - relativedelta(days=3)
        plan = generar_plan_pagos(monto=Decimal("12000.00"), tasa_anual=Decimal("12.00"), plazo_meses=12,
                                  tipo_amortizacion="FRANCES", fecha_desembolso=desembolso,
                                  tasa_seguro_desgravamen=Decimal("0.05"))
        credito = models.Credito(
            monto_aprobado=Decimal("12000.00"), saldo_pendiente=plan["cuotas"][1]["saldo_inicial"], estado="VIGENTE",
            solicitud_credito_id=solicitud.id, numero_credito=f"CR-RP{suffix[:5]}", cooperativa_id=coop.id,
            socio_id=socio.id, producto_credito_id=product.id, moneda_id=bob.id, tasa_interes=Decimal("12.00"),
            plazo_meses=12, tipo_amortizacion="FRANCES", fecha_desembolso=desembolso, usuario_id=oficial.id,
        )
        db.add(credito); db.flush()
        for row in plan["cuotas"]:
            db.add(models.TablaAmortizacion(
                credito_id=credito.id, numero_cuota=row["numero"], fecha_vencimiento=row["fecha_vencimiento"],
                monto_capital=row["capital"], monto_interes=row["interes"], monto_seguro=row["seguro"],
                monto_cuota_total=row["cuota"], saldo_inicial=row["saldo_inicial"], saldo_final=row["saldo_final"],
                estado_pago="PAGADA" if row["numero"] == 1 else "PENDIENTE",
                monto_pagado=row["cuota"] if row["numero"] == 1 else Decimal("0.00"),
            ))
        tokens = {name: create_access_token(str(u.id), rol, u.cooperativa_id)[0] for name, u, rol in (
            ("admin", admin, "ADMINISTRADOR"), ("oficial", oficial, "OFICIAL_CREDITO"), ("cajero", cajero, "CAJERO"),
            ("other_admin", other_admin, "ADMINISTRADOR"), ("socio", socio_user, "SOCIO"),
            ("intruso", intruso_user, "SOCIO"))}
        data = {"tokens": tokens, "coop_id": coop.id, "other_id": other.id, "credito_id": credito.id,
                "saldo": plan["cuotas"][1]["saldo_inicial"], "interes_vencido": plan["cuotas"][1]["interes"]}
        db.commit()
    yield data
    with SessionLocal() as db:
        db.execute(text("DELETE FROM reprogramacion_credito WHERE credito_id = :c"), {"c": data["credito_id"]})
        db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _h(case, who):
    return {"Authorization": f"Bearer {case['tokens'][who]}"}


def _url(case, suffix=""):
    return f"/api/v1/creditos/creditos/{case['credito_id']}/reprogramaciones{suffix}"


def _solicitar(client, case, who="oficial", plazo=18):
    return client.post(_url(case), json={"motivo": "Baja de ventas por temporada", "nuevo_plazo_meses": plazo},
                       headers=_h(case, who))


@needs_db
def test_preview_returns_new_plan_without_changing_anything(client, case):
    response = client.post(_url(case, "/simulacion"), json={"nuevo_plazo_meses": 18}, headers=_h(case, "oficial"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["saldo_capital"]) == case["saldo"]
    assert Decimal(body["interes_diferido"]) == case["interes_vencido"]
    assert len(body["cuotas"]) == 18 and Decimal(body["total_capital"]) == case["saldo"]
    assert {"tea", "teac", "total_seguro", "mora_condonada"} <= body.keys()
    with SessionLocal() as db:
        estados = db.execute(text("SELECT estado_pago FROM tabla_amortizacion WHERE credito_id = :c"),
                             {"c": case["credito_id"]}).scalars().all()
    assert "REPROGRAMADA" not in estados


@needs_db
def test_request_validations(client, case):
    assert _solicitar(client, case, plazo=37).status_code == 400
    zero = _solicitar(client, case, plazo=0)
    assert zero.status_code == 400 and zero.json()["detail"] == "El nuevo plazo debe ser de al menos 1 mes"
    assert _solicitar(client, case, who="cajero").status_code == 403
    assert _solicitar(client, case, who="other_admin").status_code == 404
    created = _solicitar(client, case)
    assert created.status_code == 201, created.text
    assert created.json()["estado"] == "SOLICITADA" and created.json()["canal"] == "VENTANILLA"
    assert _solicitar(client, case).status_code == 409


@needs_db
def test_approval_reschedules_unpaid_installments(client, case):
    assert client.post("/api/v1/creditos/mora/actualizar", headers=_h(case, "admin")).status_code == 200
    with SessionLocal() as db:
        assert db.query(models.Morosidad).filter_by(credito_id=case["credito_id"]).one().estado == "EN_MORA"
    request_id = _solicitar(client, case).json()["id"]
    pending = client.get("/api/v1/creditos/reprogramaciones?estado=SOLICITADA", headers=_h(case, "admin"))
    assert [row["id"] for row in pending.json()] == [request_id]

    approved = client.post(f"/api/v1/creditos/reprogramaciones/{request_id}/aprobar", headers=_h(case, "admin"))
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["estado"] == "APROBADA" and body["cuotas_reprogramadas"] == 11
    assert Decimal(body["interes_diferido"]) == case["interes_vencido"] and body["plazo_anterior"] == 12
    assert client.post(f"/api/v1/creditos/reprogramaciones/{request_id}/aprobar",
                       headers=_h(case, "admin")).status_code == 409

    with SessionLocal() as db:
        rows = db.execute(text("""SELECT numero_cuota, estado_pago, monto_capital FROM tabla_amortizacion
                                  WHERE credito_id = :c ORDER BY numero_cuota"""), {"c": case["credito_id"]}).all()
        credito = db.get(models.Credito, case["credito_id"])
        assert credito.numero_reprogramaciones == 1 and credito.plazo_meses == 19
        assert credito.saldo_pendiente == case["saldo"]
    assert [r.estado_pago for r in rows[:12]] == ["PAGADA"] + ["REPROGRAMADA"] * 11
    nuevas = rows[12:]
    assert [r.numero_cuota for r in nuevas] == list(range(13, 31))
    assert all(r.estado_pago == "PENDIENTE" for r in nuevas)
    assert sum(r.monto_capital for r in nuevas) == case["saldo"]

    detail = client.get(f"/api/v1/creditos/creditos/{case['credito_id']}", headers=_h(case, "admin")).json()
    assert detail["numero_reprogramaciones"] == 1 and detail["cuotas_totales"] == 19
    assert detail["proxima_cuota"]["numero"] == 13
    assert detail["estado_mora"] == "AL_DIA" and detail["dias_de_retaso"] == 0
    with SessionLocal() as db:
        seguro_vigente = db.execute(text("""SELECT sum(monto_seguro) FROM tabla_amortizacion
                                            WHERE credito_id = :c AND estado_pago <> 'REPROGRAMADA'"""),
                                    {"c": case["credito_id"]}).scalar_one()
    assert Decimal(str(detail["total_seguro"])) == seguro_vigente
    socio_schedule = client.get(f"/api/v1/socio/creditos/{case['credito_id']}/cronograma", headers=_h(case, "socio"))
    assert socio_schedule.status_code == 200, socio_schedule.text
    assert Decimal(socio_schedule.json()["total_seguro"]) == seguro_vigente
    estados = [row["estado"] for row in socio_schedule.json()["cuotas"]]
    assert estados.count("REPROGRAMADA") == 11
    debt = client.get(f"/api/v1/creditos/creditos/{case['credito_id']}/deuda", headers=_h(case, "admin"))
    assert debt.status_code == 200 and debt.json()["cuota"]["numero"] == 13
    history = client.get(_url(case), headers=_h(case, "oficial")).json()
    assert [row["estado"] for row in history] == ["APROBADA"]


@needs_db
def test_rejection_keeps_the_schedule(client, case):
    request_id = _solicitar(client, case).json()["id"]
    no_reason = client.post(f"/api/v1/creditos/reprogramaciones/{request_id}/rechazar", json={"motivo": " "},
                            headers=_h(case, "admin"))
    assert no_reason.status_code == 422
    rejected = client.post(f"/api/v1/creditos/reprogramaciones/{request_id}/rechazar",
                           json={"motivo": "Capacidad de pago insuficiente"}, headers=_h(case, "admin"))
    assert rejected.status_code == 200 and rejected.json()["estado"] == "RECHAZADA"
    assert rejected.json()["motivo_rechazo"] == "Capacidad de pago insuficiente"
    with SessionLocal() as db:
        assert db.get(models.Credito, case["credito_id"]).numero_reprogramaciones == 0
        count = db.execute(text("SELECT count(*) FROM tabla_amortizacion WHERE credito_id = :c"),
                           {"c": case["credito_id"]}).scalar_one()
    assert count == 12


@needs_db
def test_socio_requests_and_sees_own_reschedules_only(client, case):
    url = f"/api/v1/socio/creditos/{case['credito_id']}/reprogramaciones"
    created = client.post(url, json={"motivo": "Enfermedad", "nuevo_plazo_meses": 24}, headers=_h(case, "socio"))
    assert created.status_code == 201, created.text
    assert created.json()["canal"] == "MOVIL"
    assert [r["id"] for r in client.get(url, headers=_h(case, "socio")).json()] == [created.json()["id"]]
    assert client.get(url, headers=_h(case, "intruso")).status_code == 404
    assert client.post(url, json={"motivo": "x", "nuevo_plazo_meses": 6}, headers=_h(case, "intruso")).status_code == 404


@needs_db
def test_asfi_portfolio_flags_rescheduled_credit(client, case):
    request_id = _solicitar(client, case).json()["id"]
    client.post(f"/api/v1/creditos/reprogramaciones/{request_id}/aprobar", headers=_h(case, "admin"))
    response = client.get(f"/api/v1/informes-asfi/calificacion-cartera?fecha_corte={HOY.isoformat()}",
                          headers=_h(case, "admin"))
    assert response.status_code == 200, response.text
    row = next(r for r in response.json()["creditos"] if r["numero_credito"].startswith("CR-RP"))
    assert row["reprogramado"] is True and row["numero_reprogramaciones"] == 1
