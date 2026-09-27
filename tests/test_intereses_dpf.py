"""Contract tests for CU-W18 monthly DPF interest processing."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import models
from tests.test_dpf import (
    _auth, _fixture_emision, _limpiar_emision, _DB_DISPONIBLE, app,
)

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _as_officer(ids):
    with SessionLocal() as db:
        role_id = db.execute(text("SELECT id FROM rol WHERE nombre='OFICIAL_CREDITO'")).scalar_one()
        db.execute(text("UPDATE usuario SET rol_id=:role WHERE id=:user"), {"role": role_id, "user": ids[0]})
        db.commit()
    token, _ = create_access_token(str(ids[0]), "OFICIAL_CREDITO", ids[2])
    ids = list(ids)
    ids[3] = token
    return tuple(ids)


def _issue_monthly(client, ids, days=90):
    with SessionLocal() as db:
        bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
    response = client.post("/api/v1/dpf", json={
        "socio_id": ids[1], "monto": "1000.00", "moneda_id": bob_id,
        "plazo_dias": days, "modalidad_pago_interes": "MENSUAL",
        "origen_fondos": "CUENTA", "cuenta_origen_id": ids[4], "cuenta_abono_id": ids[5],
    }, headers=_auth(ids[3]))
    assert response.status_code == 201, response.text
    return response.json()


def _fixture_emision_aislada(saldo_origen="5000.00"):
    """Build a disposable tenant for operations that scan a whole cooperative."""
    suffix = uuid4().hex[:12]
    with SessionLocal() as db:
        coop_id = db.execute(
            text("INSERT INTO cooperativa (nombre) VALUES (:name) RETURNING id"),
            {"name": f"DPF interest test {suffix}"},
        ).scalar_one()
        role_id = db.execute(
            text("SELECT id FROM rol WHERE nombre='CAJERO'")
        ).scalar_one()
        operator_id = db.execute(
            text("""INSERT INTO usuario
                (correo,contrasena,rol_id,cooperativa_id,nombre,estado)
                VALUES (:email,:password,:role,:coop,'DPF interest tester','ACTIVO')
                RETURNING id"""),
            {"email": f"dpf-{suffix}@test.invalid", "password": hash_password("Password123"),
             "role": role_id, "coop": coop_id},
        ).scalar_one()
        socio_id = db.execute(
            text("""INSERT INTO socio (cooperativa_id,ci,nombre,apellido,estado)
                VALUES (:coop,:ci,'DPF','Interest tester','ACTIVO') RETURNING id"""),
            {"coop": coop_id, "ci": f"DPF-{suffix}"},
        ).scalar_one()
        # DPF issuance requires the tenant's own tariff bands.
        db.execute(text("""INSERT INTO tasa_dpf
                (cooperativa_id,moneda_id,plazo_min_dias,plazo_max_dias,tna)
            SELECT :coop,moneda_id,plazo_min_dias,plazo_max_dias,tna
              FROM tasa_dpf WHERE cooperativa_id=(
                  SELECT cooperativa_id FROM usuario WHERE correo='admin@test.com')"""),
            {"coop": coop_id},
        )
        bob_id = db.execute(
            text("SELECT id FROM moneda WHERE codigo_iso='BOB'")
        ).scalar_one()
        origen_id = db.execute(text("""INSERT INTO cuenta_ahorro
                (numero,tipo_producto,saldo_disponible,saldo_bloqueado,estado,fecha_registro,socio_id,moneda_id)
                VALUES (:number,'VISTA',:saldo,0,'ACTIVA',current_date,:socio,:currency) RETURNING id"""),
            {"number": f"DPFO-{suffix}", "saldo": saldo_origen, "socio": socio_id, "currency": bob_id},
        ).scalar_one()
        abono_id = db.execute(text("""INSERT INTO cuenta_ahorro
                (numero,tipo_producto,saldo_disponible,saldo_bloqueado,estado,fecha_registro,socio_id,moneda_id)
                VALUES (:number,'VISTA',100,0,'ACTIVA',current_date,:socio,:currency) RETURNING id"""),
            {"number": f"DPFA-{suffix}", "socio": socio_id, "currency": bob_id},
        ).scalar_one()
        caja_id = db.execute(text("""INSERT INTO caja (nombre,estado,cooperativa_id,monto_maximo_efectivo)
                VALUES (:name,'CERRADA',:coop,50000) RETURNING id"""),
            {"name": f"DPF {suffix}", "coop": coop_id},
        ).scalar_one()
        db.commit()
    token, _ = create_access_token(str(operator_id), "CAJERO", coop_id)
    return (operator_id, socio_id, coop_id, token, origen_id, abono_id, caja_id)


def _limpiar_emision_aislada(ids):
    _limpiar_emision(ids)
    with SessionLocal() as db:
        db.execute(text("DELETE FROM tasa_dpf WHERE cooperativa_id=:coop"), {"coop": ids[2]})
        db.execute(text("DELETE FROM cooperativa WHERE id=:coop"), {"coop": ids[2]})
        db.commit()


def _crear_dpf_vencido_ajeno(db, coop_id, suffix, due_date):
    socio_id = db.execute(text("""INSERT INTO socio
        (cooperativa_id,ci,nombre,apellido,estado)
        VALUES (:coop,:ci,'Foreign','Interest test','ACTIVO') RETURNING id"""),
        {"coop": coop_id, "ci": f"FOREIGN-{suffix}"}).scalar_one()
    bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
    account_id = db.execute(text("""INSERT INTO cuenta_ahorro
        (numero,tipo_producto,saldo_disponible,saldo_bloqueado,estado,fecha_registro,socio_id,moneda_id)
        VALUES (:number,'VISTA',100,0,'ACTIVA',current_date,:socio,:currency) RETURNING id"""),
        {"number": f"FOREIGN-{suffix}", "socio": socio_id, "currency": bob_id}).scalar_one()
    dpf_id = db.execute(text("""INSERT INTO deposito_plazo_fijo
        (monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,
         estado,socio_id,moneda_id,numero_certificado,modalidad_pago_interes,cuenta_abono_id,cooperativa_id)
        VALUES (100,3,90,:start,:end,2.5,'VIGENTE',:socio,:currency,:cert,'MENSUAL',:account,:coop)
        RETURNING id"""),
        {"start": due_date-timedelta(days=30), "end": due_date+timedelta(days=60),
         "socio": socio_id, "currency": bob_id, "cert": f"DPF-FOREIGN-{suffix}",
         "account": account_id, "coop": coop_id}).scalar_one()
    schedule_id = db.execute(text("""INSERT INTO dpf_cronograma
        (deposito_plazo_fijo_id,numero,fecha_pago,dias,interes_bruto,retencion_rciva,interes_neto,estado)
        VALUES (:dpf,1,:due,30,2.5,0,2.5,'PENDIENTE') RETURNING id"""),
        {"dpf": dpf_id, "due": due_date}).scalar_one()
    return socio_id, account_id, dpf_id, schedule_id


def test_interest_processing_pays_due_monthly_rows_once_and_exposes_history(client):
    ids = _as_officer(_fixture_emision_aislada())
    try:
        dpf = _issue_monthly(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE dpf_cronograma SET fecha_pago=:due WHERE deposito_plazo_fijo_id=:id AND numero=1"), {"due": date.today() - timedelta(days=1), "id": dpf["id"]})
            db.commit()
        first = client.post("/api/v1/dpf/intereses/procesar", json={"fecha": date.today().isoformat()}, headers=_auth(ids[3]))
        assert first.status_code == 200, first.text
        payload = first.json()
        own_payments = [row for row in payload["pagos"] if row["dpf"]["id"] == dpf["id"]]
        assert len(own_payments) == 1
        payment = own_payments[0]
        assert payment["interes_bruto"] == "2.50"
        assert payment["retencion_rciva"] == "0.00"
        assert payment["interes_neto"] == "2.50"
        assert payment["fecha_pago_real"]
        second = client.post("/api/v1/dpf/intereses/procesar", json={"fecha": date.today().isoformat()}, headers=_auth(ids[3]))
        assert second.status_code == 200, second.text
        assert second.json()["cuotas_pagadas"] == 0
        history = client.get(f"/api/v1/dpf/{dpf['id']}/intereses", headers=_auth(ids[3]))
        assert history.status_code == 200, history.text
        assert [row["cronograma_id"] for row in history.json()] == [payment["cronograma_id"]]
        detail = client.get(f"/api/v1/dpf/{dpf['id']}", headers=_auth(ids[3]))
        assert detail.json()["cronograma"][0]["fecha_pago_real"] is not None
        with SessionLocal() as db:
            assert db.get(models.CuentaAhorro, ids[5]).saldo_disponible == Decimal("102.50")
    finally:
        _limpiar_emision_aislada(ids)


def test_processing_rejects_future_date(client):
    ids = _as_officer(_fixture_emision_aislada())
    try:
        response = client.post("/api/v1/dpf/intereses/procesar", json={"fecha": (date.today() + timedelta(days=1)).isoformat()}, headers=_auth(ids[3]))
        assert response.status_code == 400
    finally:
        _limpiar_emision_aislada(ids)


def test_processing_isolated_cooperative_does_not_pay_another_tenant(client):
    ids = _as_officer(_fixture_emision_aislada())
    foreign_coop_id = None
    foreign_ids = None
    try:
        own_dpf = _issue_monthly(client, ids)
        due_date = date.today() - timedelta(days=1)
        with SessionLocal() as db:
            db.execute(text("UPDATE dpf_cronograma SET fecha_pago=:due WHERE deposito_plazo_fijo_id=:id AND numero=1"),
                       {"due": due_date, "id": own_dpf["id"]})
            foreign_coop_id = db.execute(text("INSERT INTO cooperativa (nombre) VALUES (:name) RETURNING id"),
                                         {"name": f"DPF foreign tenant {uuid4().hex[:10]}"}).scalar_one()
            foreign_ids = _crear_dpf_vencido_ajeno(db, foreign_coop_id, uuid4().hex[:8], due_date)
            db.commit()

        response = client.post("/api/v1/dpf/intereses/procesar", json={"fecha": date.today().isoformat()}, headers=_auth(ids[3]))
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["cuotas_pagadas"] == 1
        assert [payment["dpf"]["id"] for payment in payload["pagos"]] == [own_dpf["id"]]
        with SessionLocal() as db:
            foreign_schedule = db.execute(text("SELECT estado,transaccion_id,fecha_pago_real FROM dpf_cronograma WHERE id=:id"),
                                          {"id": foreign_ids[3]}).one()
            assert tuple(foreign_schedule) == ("PENDIENTE", None, None)
            assert db.execute(text("SELECT saldo_disponible FROM cuenta_ahorro WHERE id=:id"),
                              {"id": foreign_ids[1]}).scalar_one() == Decimal("100.00")
    finally:
        if foreign_coop_id is not None:
            with SessionLocal() as db:
                if foreign_ids is not None:
                    db.execute(text("UPDATE dpf_cronograma SET transaccion_id=NULL WHERE deposito_plazo_fijo_id=:id"), {"id": foreign_ids[2]})
                    db.execute(text("DELETE FROM transaccion WHERE deposito_plazo_fijo_id=:id OR cuenta_ahorro_id=:account"),
                               {"id": foreign_ids[2], "account": foreign_ids[1]})
                    db.execute(text("DELETE FROM dpf_cronograma WHERE deposito_plazo_fijo_id=:id"), {"id": foreign_ids[2]})
                    db.execute(text("DELETE FROM deposito_plazo_fijo WHERE id=:id"), {"id": foreign_ids[2]})
                    db.execute(text("DELETE FROM cuenta_ahorro WHERE id=:id"), {"id": foreign_ids[1]})
                    db.execute(text("DELETE FROM socio WHERE id=:id"), {"id": foreign_ids[0]})
                db.execute(text("DELETE FROM cooperativa WHERE id=:id"), {"id": foreign_coop_id})
                db.commit()
        _limpiar_emision_aislada(ids)


def test_maturity_liquidation_pays_only_pending_monthly_interest(client):
    ids = _fixture_emision()
    try:
        dpf = _issue_monthly(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE deposito_plazo_fijo SET fecha_inicio=:start,fecha_vencimiento=:end WHERE id=:id"), {"start": date.today()-timedelta(days=90), "end": date.today(), "id": dpf["id"]})
            db.execute(text("UPDATE dpf_cronograma SET estado='PAGADO',fecha_pago_real=now() WHERE deposito_plazo_fijo_id=:id AND numero=1"), {"id": dpf["id"]})
            db.commit()
        preview = client.get(f"/api/v1/dpf/{dpf['id']}/liquidacion/preview", params={"tipo":"LIQUIDACION"}, headers=_auth(ids[3]))
        assert preview.status_code == 200, preview.text
        assert preview.json()["interes_ya_pagado"] == "2.50"
        assert preview.json()["interes_neto"] == "5.00"
        assert preview.json()["total_a_abonar"] == "1005.00"
        result = client.post(f"/api/v1/dpf/{dpf['id']}/liquidacion", json={"tipo":"LIQUIDACION"}, headers=_auth(ids[3]))
        assert result.status_code == 201, result.text
        assert result.json()["interes_ya_pagado"] == "2.50"
        assert result.json()["total_abonado"] == "1005.00"
    finally:
        _limpiar_emision(ids)


def test_early_cancellation_deducts_excess_paid_interest_from_capital(client):
    ids = _fixture_emision()
    try:
        dpf = _issue_monthly(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE deposito_plazo_fijo SET fecha_inicio=:start,fecha_vencimiento=:end WHERE id=:id"), {"start": date.today()-timedelta(days=30), "end": date.today()+timedelta(days=60), "id": dpf["id"]})
            db.execute(text("UPDATE dpf_cronograma SET estado='PAGADO',fecha_pago_real=now() WHERE deposito_plazo_fijo_id=:id AND numero=1"), {"id": dpf["id"]})
            db.commit()
        preview = client.get(f"/api/v1/dpf/{dpf['id']}/liquidacion/preview", params={"tipo":"CANCELACION"}, headers=_auth(ids[3]))
        assert preview.status_code == 200, preview.text
        assert preview.json()["interes_ya_pagado"] == "2.50"
        assert preview.json()["interes_neto"] == "0.00"
        assert preview.json()["descuento_capital"] == "1.67"
        assert preview.json()["capital"] == "998.33"
        result = client.post(f"/api/v1/dpf/{dpf['id']}/liquidacion", json={"tipo":"CANCELACION"}, headers=_auth(ids[3]))
        assert result.status_code == 201, result.text
        assert result.json()["total_abonado"] == "998.33"
        with SessionLocal() as db:
            liquidation = db.execute(text("SELECT interes_ya_pagado,descuento_capital,monto_capital_retornado FROM liquidacion WHERE deposito_plazo_fijo_id=:id"), {"id":dpf["id"]}).one()
            assert tuple(liquidation) == (Decimal("2.50"), Decimal("1.67"), Decimal("998.33"))
    finally:
        _limpiar_emision(ids)


def test_certificate_number_uniqueness_is_scoped_to_cooperative():
    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        try:
            coop_ids = []
            socio_ids = []
            for index in range(2):
                coop_ids.append(db.execute(text("INSERT INTO cooperativa (nombre) VALUES (:name) RETURNING id"), {"name": f"DPF uniqueness {suffix}-{index}"}).scalar_one())
            for index, coop_id in enumerate(coop_ids):
                socio_ids.append(db.execute(text("INSERT INTO socio (cooperativa_id,ci,nombre,apellido,estado) VALUES (:coop,:ci,'DPF','Tester','ACTIVO') RETURNING id"), {"coop": coop_id, "ci": f"DPF{suffix}{index}"}).scalar_one())
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
            for coop_id, socio_id in zip(coop_ids, socio_ids):
                db.execute(text("""INSERT INTO deposito_plazo_fijo
                    (monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,estado,socio_id,moneda_id,numero_certificado,cooperativa_id)
                    VALUES (100.00,1.00,30,current_date,current_date+30,1.00,'VIGENTE',:socio,:moneda,'DPF-000001',:coop)"""),
                    {"socio": socio_id, "moneda": bob_id, "coop": coop_id})
            db.rollback()
        except Exception:
            db.rollback()
            raise


def test_inactive_abono_is_skipped_without_changing_schedule_or_balance(client):
    ids = _as_officer(_fixture_emision_aislada())
    try:
        dpf = _issue_monthly(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE dpf_cronograma SET fecha_pago=:due WHERE deposito_plazo_fijo_id=:id AND numero=1"), {"due": date.today() - timedelta(days=1), "id": dpf["id"]})
            db.execute(text("UPDATE cuenta_ahorro SET estado='BLOQUEADA' WHERE id=:id"), {"id": ids[5]})
            balance_before = db.execute(text("SELECT saldo_disponible FROM cuenta_ahorro WHERE id=:id"), {"id": ids[5]}).scalar_one()
            db.commit()

        response = client.post("/api/v1/dpf/intereses/procesar", json={"fecha": date.today().isoformat()}, headers=_auth(ids[3]))
        assert response.status_code == 200, response.text
        skipped = [item for item in response.json()["omitidos"] if item["dpf_id"] == dpf["id"]]
        assert skipped == [{"dpf_id": dpf["id"], "numero_certificado": dpf["numero_certificado"], "motivo": "La cuenta de abono no está activa"}]
        with SessionLocal() as db:
            schedule = db.execute(text("SELECT estado,transaccion_id,fecha_pago_real FROM dpf_cronograma WHERE deposito_plazo_fijo_id=:id AND numero=1"), {"id": dpf["id"]}).one()
            assert tuple(schedule) == ("PENDIENTE", None, None)
            assert db.execute(text("SELECT saldo_disponible FROM cuenta_ahorro WHERE id=:id"), {"id": ids[5]}).scalar_one() == balance_before
    finally:
        _limpiar_emision_aislada(ids)


def test_cooperative_payment_history_includes_date_boundaries_and_excludes_other_tenant(client):
    ids = _as_officer(_fixture_emision())
    foreign_coop_id = foreign_socio_id = foreign_account_id = foreign_dpf_id = None
    try:
        dpf = _issue_monthly(client, ids)
        date_from = date.today() - timedelta(days=2)
        date_to = date.today() - timedelta(days=1)
        first_paid_at = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
        last_paid_at = datetime.combine(date_to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
        with SessionLocal() as db:
            db.execute(text("UPDATE dpf_cronograma SET estado='PAGADO',fecha_pago_real=:paid WHERE deposito_plazo_fijo_id=:id AND numero=1"), {"paid": first_paid_at, "id": dpf["id"]})
            db.execute(text("UPDATE dpf_cronograma SET estado='PAGADO',fecha_pago_real=:paid WHERE deposito_plazo_fijo_id=:id AND numero=2"), {"paid": last_paid_at, "id": dpf["id"]})
            foreign_coop_id = db.execute(text("INSERT INTO cooperativa (nombre) VALUES (:name) RETURNING id"), {"name": f"DPF history isolation {uuid4().hex[:10]}"}).scalar_one()
            foreign_socio_id = db.execute(text("INSERT INTO socio (cooperativa_id,ci,nombre,apellido,estado) VALUES (:coop,:ci,'History','Isolation','ACTIVO') RETURNING id"), {"coop": foreign_coop_id, "ci": f"HIST-{uuid4().hex[:12]}"}).scalar_one()
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
            foreign_account_id = db.execute(text("INSERT INTO cuenta_ahorro (numero,tipo_producto,saldo_disponible,saldo_bloqueado,estado,fecha_registro,socio_id,moneda_id) VALUES (:number,'VISTA',10,0,'ACTIVA',current_date,:socio,:moneda) RETURNING id"), {"number": f"HIST-{uuid4().hex[:12]}", "socio": foreign_socio_id, "moneda": bob_id}).scalar_one()
            foreign_dpf_id = db.execute(text("""INSERT INTO deposito_plazo_fijo
                (monto,tasa_interes_anual,plazo_dias,fecha_inicio,fecha_vencimiento,interes_calculado,estado,socio_id,moneda_id,numero_certificado,modalidad_pago_interes,cuenta_abono_id,cooperativa_id)
                VALUES (100,1,30,:start,:end,1,'VIGENTE',:socio,:moneda,:cert,'MENSUAL',:account,:coop) RETURNING id"""),
                {"start": date_from, "end": date_to, "socio": foreign_socio_id, "moneda": bob_id, "cert": f"DPF-HIST-{uuid4().hex[:8]}", "account": foreign_account_id, "coop": foreign_coop_id}).scalar_one()
            db.execute(text("""INSERT INTO dpf_cronograma
                (deposito_plazo_fijo_id,numero,fecha_pago,dias,interes_bruto,retencion_rciva,interes_neto,estado,fecha_pago_real)
                VALUES (:dpf,1,:day,30,1,0,1,'PAGADO',:paid)"""), {"dpf": foreign_dpf_id, "day": date_from, "paid": first_paid_at})
            db.commit()

        both = client.get("/api/v1/dpf/intereses/pagos", params={"desde": date_from.isoformat(), "hasta": date_to.isoformat()}, headers=_auth(ids[3]))
        assert both.status_code == 200, both.text
        assert [item["dpf"]["id"] for item in both.json()] == [dpf["id"], dpf["id"]]
        start_only = client.get("/api/v1/dpf/intereses/pagos", params={"desde": date_from.isoformat(), "hasta": date_from.isoformat()}, headers=_auth(ids[3]))
        assert start_only.status_code == 200, start_only.text
        assert [item["fecha_pago_real"][:10] for item in start_only.json()] == [date_from.isoformat()]
        end_only = client.get("/api/v1/dpf/intereses/pagos", params={"desde": date_to.isoformat(), "hasta": date_to.isoformat()}, headers=_auth(ids[3]))
        assert end_only.status_code == 200, end_only.text
        assert [item["fecha_pago_real"][:10] for item in end_only.json()] == [date_to.isoformat()]
    finally:
        if foreign_coop_id is not None:
            with SessionLocal() as db:
                db.execute(text("DELETE FROM dpf_cronograma WHERE deposito_plazo_fijo_id=:id"), {"id": foreign_dpf_id})
                db.execute(text("DELETE FROM deposito_plazo_fijo WHERE id=:id"), {"id": foreign_dpf_id})
                db.execute(text("DELETE FROM cuenta_ahorro WHERE id=:id"), {"id": foreign_account_id})
                db.execute(text("DELETE FROM socio WHERE id=:id"), {"id": foreign_socio_id})
                db.execute(text("DELETE FROM cooperativa WHERE id=:id"), {"id": foreign_coop_id})
                db.commit()
        _limpiar_emision(ids)
