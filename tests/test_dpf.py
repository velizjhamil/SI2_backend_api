"""Integration and contract tests for DPF operations (CU-W17/CU-W19)."""

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.schemas import schemas


def _db_disponible():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")
if _DB_DISPONIBLE:
    from main import app
else:
    app = None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _fixture_dpf():
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        coop_id = db.execute(text("SELECT cooperativa_id FROM usuario WHERE correo='cajero@test.com'")).scalar_one()
        role_id = db.execute(text("SELECT id FROM rol WHERE nombre='CAJERO'")).scalar_one()
        operador = models.Usuario(correo=f"dpf-{sufijo}@test.invalid", contrasena=hash_password("Password123"), rol_id=role_id, cooperativa_id=coop_id, nombre="DPF tester", estado="ACTIVO")
        socio = models.Socio(cooperativa_id=coop_id, ci=f"DPF-{sufijo}", nombre="DPF", apellido="Test", estado="ACTIVO")
        db.add_all([operador, socio])
        db.commit()
        ids = (operador.id, socio.id, coop_id)
    token, _ = create_access_token(str(ids[0]), "CAJERO", ids[2])
    return (*ids, token)


def _limpiar(ids):
    with SessionLocal() as db:
        db.execute(text("DELETE FROM bitacora WHERE usuario_id=:id"), {"id": ids[0]})
        db.execute(text("DELETE FROM socio WHERE id=:id"), {"id": ids[1]})
        db.execute(text("DELETE FROM usuario WHERE id=:id"), {"id": ids[0]})
        db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _fixture_emision(saldo_origen="5000.00"):
    base = _fixture_dpf()
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        moneda_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
        origen = models.CuentaAhorro(numero=f"DPFO-{sufijo}", tipo_producto="VISTA", saldo_disponible=saldo_origen, saldo_bloqueado="0.00", estado="ACTIVA", fecha_registro=date.today(), socio_id=base[1], moneda_id=moneda_id)
        abono = models.CuentaAhorro(numero=f"DPFA-{sufijo}", tipo_producto="VISTA", saldo_disponible="100.00", saldo_bloqueado="0.00", estado="ACTIVA", fecha_registro=date.today(), socio_id=base[1], moneda_id=moneda_id)
        caja = models.Caja(nombre=f"DPF {sufijo}", estado="CERRADA", cooperativa_id=base[2], monto_maximo_efectivo=50000.00)
        db.add_all([origen, abono, caja])
        db.commit()
        more = (origen.id, abono.id, caja.id)
    return (*base, *more)


def _limpiar_emision(ids):
    operador_id, socio_id, _, _, origen_id, abono_id, caja_id = ids
    with SessionLocal() as db:
        dpf_ids = db.execute(text("SELECT id FROM deposito_plazo_fijo WHERE socio_id=:id"), {"id": socio_id}).scalars().all()
        db.execute(text("DELETE FROM transaccion WHERE deposito_plazo_fijo_id = ANY(:ids) OR cuenta_ahorro_id = ANY(:cuentas)"), {"ids": dpf_ids or [0], "cuentas": [origen_id, abono_id]})
        db.execute(text("DELETE FROM dpf_cronograma WHERE deposito_plazo_fijo_id = ANY(:ids)"), {"ids": dpf_ids or [0]})
        db.execute(text("DELETE FROM liquidacion WHERE deposito_plazo_fijo_id = ANY(:ids) OR dpf_renovado_id = ANY(:ids)"), {"ids": dpf_ids or [0]})
        db.execute(text("DELETE FROM deposito_plazo_fijo WHERE socio_id=:id"), {"id": socio_id})
        db.execute(text("DELETE FROM declaracion_jurada_uif WHERE socio_id=:id"), {"id": socio_id})
        db.execute(text("DELETE FROM cuenta_ahorro WHERE id = ANY(:ids)"), {"ids": [origen_id, abono_id]})
        db.execute(text("DELETE FROM control_caja WHERE usuario_id=:id"), {"id": operador_id})
        db.execute(text("DELETE FROM caja WHERE id=:id"), {"id": caja_id})
        db.execute(text("DELETE FROM bitacora WHERE usuario_id=:id"), {"id": operador_id})
        db.execute(text("DELETE FROM socio WHERE id=:id"), {"id": socio_id})
        db.execute(text("DELETE FROM usuario WHERE id=:id"), {"id": operador_id})
        db.commit()


def test_dpf_models_and_request_schemas_exist():
    assert models.DepositoPlazoFijo.__tablename__ == "deposito_plazo_fijo"
    assert models.TasaDPF.__tablename__ == "tasa_dpf"
    assert models.DPFCronograma.__tablename__ == "dpf_cronograma"
    assert hasattr(schemas, "DeclaracionUifIn")
    assert hasattr(schemas, "DPFCreate")


def test_dpf_tariff_and_simulation_use_rciva_and_reconciled_months(client):
    ids = _fixture_dpf()
    try:
        tariff = client.get("/api/v1/dpf/tarifario", headers=_auth(ids[-1]))
        assert tariff.status_code == 200, tariff.text
        bob = next(item for item in tariff.json() if item["moneda"]["codigo_iso"] == "BOB" and item["plazo_min_dias"] == 90)
        assert bob["tna"] == "3.00"
        with SessionLocal() as db:
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
        simulation = client.post("/api/v1/dpf/simulacion", json={"monto":"10000.00", "moneda_id":bob_id, "plazo_dias":100, "modalidad_pago_interes":"MENSUAL"}, headers=_auth(ids[-1]))
        assert simulation.status_code == 200, simulation.text
        body = simulation.json()
        assert body["interes_bruto"] == "83.33"
        assert body["exento_rciva"] is True
        assert body["retencion_rciva"] == "0.00"
        assert [item["dias"] for item in body["cronograma"]] == [30, 30, 30, 10]
        assert sum(Decimal(item["interes_bruto"]) for item in body["cronograma"]) == Decimal(body["interes_bruto"])
    finally:
        _limpiar(ids)


def test_dpf_simulation_rejects_short_term(client):
    ids = _fixture_dpf()
    try:
        with SessionLocal() as db:
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
        response = client.post("/api/v1/dpf/simulacion", json={"monto":"100.00", "moneda_id":bob_id, "plazo_dias":29, "modalidad_pago_interes":"VENCIMIENTO"}, headers=_auth(ids[-1]))
        assert response.status_code == 400
    finally:
        _limpiar(ids)


def test_dpf_issue_debits_account_or_counts_cash_in_session(client):
    ids = _fixture_emision()
    operador_id, socio_id, _, token, origen_id, abono_id, caja_id = ids
    try:
        with SessionLocal() as db:
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
        account_body = {"socio_id":socio_id, "monto":"1000.00", "moneda_id":bob_id, "plazo_dias":90, "modalidad_pago_interes":"VENCIMIENTO", "origen_fondos":"CUENTA", "cuenta_origen_id":origen_id, "cuenta_abono_id":abono_id}
        response = client.post("/api/v1/dpf", json=account_body, headers=_auth(token))
        assert response.status_code == 201, response.text
        certificate = response.json()
        assert re.fullmatch(r"DPF-\d{6}", certificate["numero_certificado"])
        signed = "|".join((certificate["numero_certificado"], certificate["socio"]["ci"], "1000.00", "BOB", certificate["fecha_inicio"], certificate["fecha_vencimiento"], "3.00"))
        assert certificate["codigo_verificacion"] == hashlib.sha256(signed.encode()).hexdigest()[:16]
        with SessionLocal() as db:
            assert db.get(models.CuentaAhorro, origen_id).saldo_disponible == 4000

        opening = client.post("/api/v1/caja/aperturas", json={"caja_id":caja_id, "monto_apertura":"200.00"}, headers=_auth(token))
        assert opening.status_code == 201, opening.text
        cash_body = {**account_body, "monto":"500.00", "origen_fondos":"EFECTIVO"}
        cash_body.pop("cuenta_origen_id")
        cash = client.post("/api/v1/dpf", json=cash_body, headers=_auth(token))
        assert cash.status_code == 201, cash.text
        assert int(cash.json()["numero_certificado"].split("-")[1]) == int(certificate["numero_certificado"].split("-")[1]) + 1
        summary = client.get("/api/v1/caja/arqueos/resumen", headers=_auth(token))
        assert summary.status_code == 200, summary.text
        bob = next(item for item in summary.json()["monedas"] if item["moneda"]["codigo_iso"] == "BOB")
        assert bob["saldo_teorico"] == "700.00"
    finally:
        _limpiar_emision(ids)


def test_dpf_issuance_requires_and_links_uif_declaration(client):
    ids = _fixture_emision("100000.00")
    _, socio_id, _, token, origen_id, abono_id, _ = ids
    try:
        with SessionLocal() as db:
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
        body = {"socio_id":socio_id, "monto":"70000.00", "moneda_id":bob_id, "plazo_dias":90, "modalidad_pago_interes":"VENCIMIENTO", "origen_fondos":"CUENTA", "cuenta_origen_id":origen_id, "cuenta_abono_id":abono_id}
        missing = client.post("/api/v1/dpf", json=body, headers=_auth(token))
        assert missing.status_code == 428
        assert missing.json()["detail"] == "Se requiere declaración jurada UIF para esta operación"
        with SessionLocal() as db:
            assert db.execute(text("SELECT count(*) FROM deposito_plazo_fijo WHERE socio_id=:id"), {"id":socio_id}).scalar_one() == 0
            assert db.get(models.CuentaAhorro, origen_id).saldo_disponible == 100000
        body["declaracion_uif"] = {"origen":"AHORROS_PROPIOS", "destino":"CONSTITUCION_DPF", "actividad_economica":"Comercio", "realizado_por":"TITULAR", "declara_bajo_juramento":True}
        created = client.post("/api/v1/dpf", json=body, headers=_auth(token))
        assert created.status_code == 201, created.text
        declaration_id = created.json()["declaracion_uif_id"]
        with SessionLocal() as db:
            link = db.execute(text("SELECT declaracion_jurada_uif_id FROM transaccion WHERE deposito_plazo_fijo_id=:id"), {"id":created.json()["id"]}).scalar_one()
            assert link == declaration_id
    finally:
        _limpiar_emision(ids)


def test_dpf_list_and_detail_scope_and_expiry_filter(client):
    ids = _fixture_emision()
    _, socio_id, _, token, origen_id, abono_id, _ = ids
    try:
        with SessionLocal() as db:
            bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
            socio_ci = db.execute(text("SELECT ci FROM socio WHERE id=:id"), {"id": socio_id}).scalar_one()
        response = client.post("/api/v1/dpf", json={"socio_id":socio_id, "monto":"1000.00", "moneda_id":bob_id, "plazo_dias":90, "modalidad_pago_interes":"VENCIMIENTO", "origen_fondos":"CUENTA", "cuenta_origen_id":origen_id, "cuenta_abono_id":abono_id}, headers=_auth(token))
        assert response.status_code == 201, response.text
        certificate_id = response.json()["id"]
        listing = client.get("/api/v1/dpf", params={"estado":"VIGENTE", "vence_en_dias":90, "socio_ci":socio_ci}, headers=_auth(token))
        assert listing.status_code == 200, listing.text
        assert any(item["id"] == certificate_id and item["dias_para_vencer"] == 90 for item in listing.json())
        detail = client.get(f"/api/v1/dpf/{certificate_id}", headers=_auth(token))
        assert detail.status_code == 200, detail.text
        assert detail.json()["numero_certificado"] == response.json()["numero_certificado"]
        assert detail.json()["cronograma"][0]["estado"] == "PENDIENTE"
    finally:
        _limpiar_emision(ids)


def _emitir_para_liquidacion(client, ids, amount="1000.00"):
    with SessionLocal() as db:
        bob_id = db.execute(text("SELECT id FROM moneda WHERE codigo_iso='BOB'")).scalar_one()
    response = client.post("/api/v1/dpf", json={"socio_id":ids[1], "monto":amount, "moneda_id":bob_id, "plazo_dias":90, "modalidad_pago_interes":"VENCIMIENTO", "origen_fondos":"CUENTA", "cuenta_origen_id":ids[4], "cuenta_abono_id":ids[5]}, headers=_auth(ids[3]))
    assert response.status_code == 201, response.text
    return response.json()


def test_dpf_maturity_preview_and_liquidation_credit_account(client):
    ids = _fixture_emision("10000.00")
    try:
        certificate = _emitir_para_liquidacion(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE deposito_plazo_fijo SET fecha_inicio=:start, fecha_vencimiento=:end WHERE id=:id"), {"start":date.today()-timedelta(days=90), "end":date.today(), "id":certificate["id"]})
            db.commit()
        preview = client.get(f"/api/v1/dpf/{certificate['id']}/liquidacion/preview", params={"tipo":"LIQUIDACION"}, headers=_auth(ids[3]))
        assert preview.status_code == 200, preview.text
        assert preview.json()["permitido"] is True
        assert preview.json()["total_a_abonar"] == "1007.50"
        settled = client.post(f"/api/v1/dpf/{certificate['id']}/liquidacion", json={"tipo":"LIQUIDACION"}, headers=_auth(ids[3]))
        assert settled.status_code == 201, settled.text
        assert settled.json()["dpf"]["estado"] == "LIQUIDADO"
        assert settled.json()["total_abonado"] == "1007.50"
    finally:
        _limpiar_emision(ids)


def test_dpf_early_cancellation_applies_penalty_and_renewal_links_origin(client):
    ids = _fixture_emision("10000.00")
    try:
        cancellation = _emitir_para_liquidacion(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE deposito_plazo_fijo SET fecha_inicio=:start, fecha_vencimiento=:end WHERE id=:id"), {"start":date.today()-timedelta(days=10), "end":date.today()+timedelta(days=80), "id":cancellation["id"]})
            db.commit()
        preview = client.get(f"/api/v1/dpf/{cancellation['id']}/liquidacion/preview", params={"tipo":"CANCELACION"}, headers=_auth(ids[3]))
        assert preview.status_code == 200, preview.text
        assert preview.json()["anticipada"] is True
        assert preview.json()["tasa_aplicada"] == "1.00"
        assert preview.json()["interes_bruto"] == "0.28"
        assert preview.json()["retencion_rciva"] == "0.04"
        assert preview.json()["interes_neto"] == "0.24"
        cancelled = client.post(f"/api/v1/dpf/{cancellation['id']}/liquidacion", json={"tipo":"CANCELACION"}, headers=_auth(ids[3]))
        assert cancelled.status_code == 201, cancelled.text
        assert cancelled.json()["dpf"]["estado"] == "CANCELADO"

        renewal = _emitir_para_liquidacion(client, ids)
        with SessionLocal() as db:
            db.execute(text("UPDATE deposito_plazo_fijo SET fecha_inicio=:start, fecha_vencimiento=:end WHERE id=:id"), {"start":date.today()-timedelta(days=90), "end":date.today(), "id":renewal["id"]})
            db.commit()
        renewed = client.post(f"/api/v1/dpf/{renewal['id']}/liquidacion", json={"tipo":"RENOVACION", "capitalizar":False, "plazo_dias":60, "modalidad_pago_interes":"MENSUAL"}, headers=_auth(ids[3]))
        assert renewed.status_code == 201, renewed.text
        assert renewed.json()["dpf"]["estado"] == "RENOVADO"
        assert renewed.json()["nuevo_dpf"]["dpf_origen_id"] == renewal["id"]
        assert renewed.json()["nuevo_dpf"]["plazo_dias"] == 60
        replacement_id = renewed.json()["nuevo_dpf"]["id"]
        with SessionLocal() as db:
            db.execute(text("UPDATE deposito_plazo_fijo SET fecha_inicio=:start, fecha_vencimiento=:end WHERE id=:id"), {"start":date.today()-timedelta(days=60), "end":date.today(), "id":replacement_id})
            balance_before = db.get(models.CuentaAhorro, ids[5]).saldo_disponible
            db.commit()
        capitalized = client.post(f"/api/v1/dpf/{replacement_id}/liquidacion", json={"tipo":"RENOVACION", "capitalizar":True}, headers=_auth(ids[3]))
        assert capitalized.status_code == 201, capitalized.text
        assert capitalized.json()["nuevo_dpf"]["monto"] == "1004.17"
        assert capitalized.json()["total_abonado"] == "0.00"
        with SessionLocal() as db:
            assert db.get(models.CuentaAhorro, ids[5]).saldo_disponible == balance_before
    finally:
        _limpiar_emision(ids)
