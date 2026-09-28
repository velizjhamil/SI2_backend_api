"""Disposable tenant tests for CU-W29 chart read endpoints."""
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


@pytest.fixture
def plan_case():
    suffix = uuid4().hex[:10]
    ids = {"coops": [], "users": [], "analytics": [], "cashboxes": [], "controls": [], "transactions": [], "vouchers": [], "details": []}
    tokens = []
    try:
        with SessionLocal() as db:
            roles = {name: db.query(models.Rol).filter_by(nombre=name).one() for name in ("CONTADOR", "ADMINISTRADOR")}
            parent = db.query(models.PlanCuenta).filter_by(codigo="111.01", cooperativa_id=None).one()
            for index in range(2):
                coop = models.Cooperativa(nombre=f"Plan test {suffix}-{index}", estado="ACTIVO")
                db.add(coop); db.flush(); ids["coops"].append(coop.id)
                role_name = "CONTADOR" if index == 0 else "ADMINISTRADOR"
                user = models.Usuario(correo=f"plan-{suffix}-{index}@test.invalid", contrasena=hash_password("Password123"), rol_id=roles[role_name].id, cooperativa_id=coop.id, nombre="Plan test", estado="ACTIVO")
                db.add(user); db.flush(); ids["users"].append(user.id)
                account = models.PlanCuenta(codigo="111.01.90", nombre=f"Analítica {suffix}-{index}", nivel=5, tipo="ACTIVO", naturaleza="DEUDORA", es_regularizadora=False, es_oficial=False, cooperativa_id=coop.id, estado="ACTIVA", acepta_movimientos=True, plan_cuenta_padre_id=parent.id)
                db.add(account); db.flush(); ids["analytics"].append(account.id)
                box = models.Caja(nombre=f"Plan {suffix}-{index}", estado="ABIERTA", cooperativa_id=coop.id)
                db.add(box); db.flush(); ids["cashboxes"].append(box.id)
                control = models.ControlCaja(monto_apertura=0, saldo_sistema=0, fecha_apertura="2026-09-27 10:00:00", estado="ABIERTA", caja_id=box.id, usuario_id=user.id)
                db.add(control); db.flush(); ids["controls"].append(control.id)
                token, _ = create_access_token(str(user.id), role_name, coop.id)
                tokens.append(token)
            # Own tenant has one accounting detail; the second cooperative has two.
            for index, quantity in enumerate((1, 2)):
                for _ in range(quantity):
                    result = db.execute(text("INSERT INTO transaccion (tipo,monto,canal,control_caja_id,moneda_id) VALUES ('DEPOSITO',1,'VENTANILLA',:control,1) RETURNING id"), {"control": ids["controls"][index]})
                    tx_id = result.scalar_one(); ids["transactions"].append(tx_id)
                    voucher_id = db.execute(text("""INSERT INTO comprobante_contable
                        (tipo,glosa,es_automatico,transaccion_id,cooperativa_id,numero,gestion,fecha_contable,moneda_id,estado,usuario_id,origen)
                        VALUES ('INGRESO','Read endpoint test',FALSE,:tx,:coop,:numero,2026,CURRENT_DATE,1,'REGISTRADO',:user_id,'MANUAL')
                        RETURNING id"""), {"tx": tx_id, "coop": ids["coops"][index], "numero": f"I-2026-{tx_id:06d}", "user_id": ids["users"][index]}).scalar_one()
                    ids["vouchers"].append(voucher_id)
                    detail_id = db.execute(text("INSERT INTO detalle_asiento (debe,haber,comprobante_contable_id,plan_cuenta_id) VALUES (1,0,:voucher,:account) RETURNING id"), {"voucher": voucher_id, "account": db.query(models.PlanCuenta.id).filter_by(codigo="111.01",cooperativa_id=None).one()[0]}).scalar_one()
                    ids["details"].append(detail_id)
            db.commit()
        yield {"ids": ids, "tokens": tokens}
    finally:
        with SessionLocal() as db:
            if ids["details"]:
                db.execute(text("DELETE FROM detalle_asiento WHERE id = ANY(:ids)"), {"ids": ids["details"]})
            if ids["vouchers"]:
                db.execute(text("DELETE FROM comprobante_contable WHERE id = ANY(:ids)"), {"ids": ids["vouchers"]})
            if ids["transactions"]:
                db.execute(text("DELETE FROM transaccion WHERE id = ANY(:ids)"), {"ids": ids["transactions"]})
            if ids["controls"]:
                db.execute(text("DELETE FROM control_caja WHERE id = ANY(:ids)"), {"ids": ids["controls"]})
            if ids["cashboxes"]:
                db.execute(text("DELETE FROM caja WHERE id = ANY(:ids)"), {"ids": ids["cashboxes"]})
            if ids["coops"]:
                db.execute(text("DELETE FROM plan_cuenta WHERE cooperativa_id = ANY(:ids)"), {"ids": ids["coops"]})
            if ids["analytics"]:
                db.execute(text("DELETE FROM plan_cuenta WHERE id = ANY(:ids)"), {"ids": ids["analytics"]})
            if ids["users"]:
                db.execute(text("DELETE FROM bitacora WHERE usuario_id = ANY(:ids)"), {"ids": ids["users"]})
                db.execute(text("DELETE FROM usuario WHERE id = ANY(:ids)"), {"ids": ids["users"]})
            if ids["coops"]:
                db.execute(text("DELETE FROM cooperativa WHERE id = ANY(:ids)"), {"ids": ids["coops"]})
            db.commit()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_list_supports_class_level_prefix_and_tenant_scoped_accounts(plan_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/contabilidad/plan-cuentas?clase=1&nivel=5&q=111.01.90", headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows] == [plan_case["ids"]["analytics"][0]]
    assert rows[0]["padre_codigo"] == "111.01"
    assert rows[0]["tiene_analiticas"] is False


def test_tree_requires_class_and_exposes_mcef_hierarchy(plan_case):
    with TestClient(app) as client:
        missing = client.get("/api/v1/contabilidad/plan-cuentas/arbol", headers=auth(plan_case["tokens"][0]))
        tree = client.get("/api/v1/contabilidad/plan-cuentas/arbol?clase=4", headers=auth(plan_case["tokens"][0]))
    assert missing.status_code == 422
    assert tree.status_code == 200, tree.text
    assert tree.json()[0]["codigo"] == "400.00"
    assert tree.json()[0]["nombre"] == "GASTOS"
    assert "hijos" in tree.json()[0]


def test_detail_counts_only_movements_from_authenticated_cooperative(plan_case):
    official_id = None
    with SessionLocal() as db:
        official_id = db.query(models.PlanCuenta.id).filter_by(codigo="111.01",cooperativa_id=None).one()[0]
    with TestClient(app) as client:
        response = client.get(f"/api/v1/contabilidad/plan-cuentas/{official_id}", headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["codigo"] == "111.01"
    assert body["movimientos"] == 1
    assert body["ruta"][-1]["codigo"] == "111.01"


def test_summary_counts_official_and_own_analytic_rows(plan_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/contabilidad/plan-cuentas/resumen", headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clases"]["1"]["total"] >= 1
    assert body["clases"]["1"]["niveles"]["5"] == 1


def test_read_endpoints_reject_users_without_accounting_role(plan_case):
    with SessionLocal() as db:
        role = db.query(models.Rol).filter_by(nombre="SOCIO").one()
        coop_id = plan_case["ids"]["coops"][0]
        user = models.Usuario(correo=f"plan-read-denied-{uuid4().hex[:10]}@test.invalid",contrasena=hash_password("Password123"),rol_id=role.id,cooperativa_id=coop_id,nombre="Denied",estado="ACTIVO")
        db.add(user); db.commit(); user_id=user.id
    token, _ = create_access_token(str(user_id), "SOCIO", plan_case["ids"]["coops"][0])
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/contabilidad/plan-cuentas", headers=auth(token))
        assert response.status_code == 403
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM usuario WHERE id=:id"), {"id": user_id})
            db.commit()


def test_admin_role_can_read_chart(plan_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/contabilidad/plan-cuentas?clase=4&nivel=1", headers=auth(plan_case["tokens"][1]))
    assert response.status_code == 200, response.text
    assert response.json()[0]["codigo"] == "400.00"


def test_solo_movimiento_filters_to_postable_accounts(plan_case):
    with TestClient(app) as client:
        response = client.get("/api/v1/contabilidad/plan-cuentas?clase=1&solo_movimiento=true", headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 200, response.text
    assert response.json()
    assert all(row["acepta_movimientos"] for row in response.json())
    assert all(row["nivel"] == 4 or row["nivel"] == 5 for row in response.json())


def test_detail_hides_another_cooperatives_analytic_account(plan_case):
    foreign_account_id = plan_case["ids"]["analytics"][1]
    with TestClient(app) as client:
        response = client.get(f"/api/v1/contabilidad/plan-cuentas/{foreign_account_id}", headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 404


def _attach_detail(plan_case, cooperative_index, account_id, debe="1.00", haber="0.00"):
    ids = plan_case["ids"]
    with SessionLocal() as db:
        tx_id = db.execute(text("INSERT INTO transaccion (tipo,monto,canal,control_caja_id,moneda_id) VALUES ('DEPOSITO',1,'VENTANILLA',:control,1) RETURNING id"), {"control": ids["controls"][cooperative_index]}).scalar_one()
        ids["transactions"].append(tx_id)
        voucher_id = db.execute(text("""INSERT INTO comprobante_contable
            (tipo,glosa,es_automatico,transaccion_id,cooperativa_id,numero,gestion,fecha_contable,moneda_id,estado,usuario_id,origen)
            VALUES ('INGRESO','B3 test',FALSE,:tx,:coop,:numero,2026,CURRENT_DATE,1,'REGISTRADO',:user_id,'MANUAL')
            RETURNING id"""), {"tx": tx_id, "coop": ids["coops"][cooperative_index], "numero": f"I-2026-{tx_id:06d}", "user_id": ids["users"][cooperative_index]}).scalar_one()
        ids["vouchers"].append(voucher_id)
        detail_id = db.execute(text("INSERT INTO detalle_asiento (debe,haber,comprobante_contable_id,plan_cuenta_id) VALUES (:debe,:haber,:voucher,:account) RETURNING id"), {"debe": debe, "haber": haber, "voucher": voucher_id, "account": account_id}).scalar_one()
        ids["details"].append(detail_id)
        db.commit()


def _official_id(code):
    with SessionLocal() as db:
        return db.query(models.PlanCuenta.id).filter_by(codigo=code, cooperativa_id=None).one()[0]


def _assert_audited(user_id, action):
    with SessionLocal() as db:
        row = db.query(models.Bitacora).filter_by(usuario_id=user_id, modulo="CONTABILIDAD", accion=action).one_or_none()
        assert row is not None


def test_create_analytic_generates_scoped_code_and_audits(plan_case):
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/plan-cuentas", json={"padre_id": _official_id("111.01"), "nombre": "Caja principal"}, headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["codigo"] == "111.01.91"
    assert body["cooperativa_id"] == plan_case["ids"]["coops"][0]
    assert body["naturaleza"] == "DEUDORA" and body["es_regularizadora"] is False
    plan_case["ids"]["analytics"].append(body["id"])
    _assert_audited(plan_case["ids"]["users"][0], "PLAN_CUENTAS_CREAR_ANALITICA")


def test_create_rejects_duplicate_name_under_same_parent(plan_case):
    with SessionLocal() as db:
        existing_name = db.query(models.PlanCuenta.nombre).filter_by(id=plan_case["ids"]["analytics"][0]).one()[0]
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/plan-cuentas", json={"padre_id": _official_id("111.01"), "nombre": existing_name}, headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 409


def test_create_rejects_invalid_and_inactive_parents(plan_case):
    from types import SimpleNamespace
    from app.api.v1.endpoints.plan_cuentas import _valid_parent
    from fastapi import HTTPException

    with TestClient(app) as client:
        invalid = client.post("/api/v1/contabilidad/plan-cuentas", json={"padre_id": _official_id("111.00"), "nombre": "Invalid level"}, headers=auth(plan_case["tokens"][0]))
    assert invalid.status_code == 422
    with pytest.raises(HTTPException) as inactive:
        _valid_parent(SimpleNamespace(es_oficial=True, nivel=4, estado="INACTIVA"))
    assert inactive.value.status_code == 422


def test_create_rejects_blank_name(plan_case):
    with TestClient(app) as client:
        response = client.post("/api/v1/contabilidad/plan-cuentas", json={"padre_id": _official_id("111.01"), "nombre": "   "}, headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 422


def test_edit_analytic_is_tenant_scoped_and_audited(plan_case):
    own_id, foreign_id = plan_case["ids"]["analytics"]
    with TestClient(app) as client:
        updated = client.patch(f"/api/v1/contabilidad/plan-cuentas/{own_id}", json={"nombre": "Caja editada"}, headers=auth(plan_case["tokens"][0]))
        foreign = client.patch(f"/api/v1/contabilidad/plan-cuentas/{foreign_id}", json={"nombre": "Intrusa"}, headers=auth(plan_case["tokens"][0]))
    assert updated.status_code == 200, updated.text
    assert updated.json()["nombre"] == "Caja editada"
    assert foreign.status_code == 404
    _assert_audited(plan_case["ids"]["users"][0], "PLAN_CUENTAS_MODIFICAR_ANALITICA")


def test_official_account_is_immutable_for_all_write_routes(plan_case):
    official_id = _official_id("111.01")
    headers = auth(plan_case["tokens"][0])
    with TestClient(app) as client:
        patched = client.patch(f"/api/v1/contabilidad/plan-cuentas/{official_id}", json={"nombre": "Hack"}, headers=headers)
        state = client.post(f"/api/v1/contabilidad/plan-cuentas/{official_id}/estado", json={"estado": "INACTIVA", "motivo": "Prueba"}, headers=headers)
        deleted = client.delete(f"/api/v1/contabilidad/plan-cuentas/{official_id}", headers=headers)
    assert [patched.status_code, state.status_code, deleted.status_code] == [403, 403, 403]


def test_deactivation_requires_zero_balance_and_uses_tenant_scope(plan_case):
    account_id = plan_case["ids"]["analytics"][0]
    _attach_detail(plan_case, 0, account_id, debe="10.00", haber="0.00")
    with TestClient(app) as client:
        response = client.post(f"/api/v1/contabilidad/plan-cuentas/{account_id}/estado", json={"estado": "INACTIVA", "motivo": "Cierre cuenta"}, headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.query(models.PlanCuenta.estado).filter_by(id=account_id).one()[0] == "ACTIVA"


def test_deactivation_zero_balance_is_audited_and_other_tenant_is_hidden(plan_case):
    own_id, foreign_id = plan_case["ids"]["analytics"]
    with TestClient(app) as client:
        changed = client.post(f"/api/v1/contabilidad/plan-cuentas/{own_id}/estado", json={"estado": "INACTIVA", "motivo": "Cierre cuenta"}, headers=auth(plan_case["tokens"][0]))
        foreign = client.post(f"/api/v1/contabilidad/plan-cuentas/{foreign_id}/estado", json={"estado": "INACTIVA", "motivo": "Cierre cuenta"}, headers=auth(plan_case["tokens"][0]))
    assert changed.status_code == 200, changed.text
    assert changed.json()["estado"] == "INACTIVA"
    assert foreign.status_code == 404
    _assert_audited(plan_case["ids"]["users"][0], "PLAN_CUENTAS_CAMBIAR_ESTADO")


def test_delete_rejects_accounts_with_any_movements_and_audits_successful_delete(plan_case):
    own_id = plan_case["ids"]["analytics"][0]
    _attach_detail(plan_case, 0, own_id, debe="0.01", haber="0.00")
    with TestClient(app) as client:
        blocked = client.delete(f"/api/v1/contabilidad/plan-cuentas/{own_id}", headers=auth(plan_case["tokens"][0]))
    assert blocked.status_code == 409
    deletable = plan_case["ids"]["analytics"][1]
    with TestClient(app) as client:
        deleted = client.delete(f"/api/v1/contabilidad/plan-cuentas/{deletable}", headers=auth(plan_case["tokens"][1]))
    assert deleted.status_code == 204
    _assert_audited(plan_case["ids"]["users"][1], "PLAN_CUENTAS_ELIMINAR_ANALITICA")


def test_state_change_rejects_whitespace_only_reason(plan_case):
    account_id = plan_case["ids"]["analytics"][0]
    with TestClient(app) as client:
        response = client.post(f"/api/v1/contabilidad/plan-cuentas/{account_id}/estado", json={"estado": "INACTIVA", "motivo": "     "}, headers=auth(plan_case["tokens"][0]))
    assert response.status_code == 422
