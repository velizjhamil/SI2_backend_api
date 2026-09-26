"""Integration tests for CU-W20 credit-product catalog."""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.schemas import schemas
from app.api.v1.router import api_router


def _db_disponible():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
if _DB_DISPONIBLE:
    from main import app
else:
    app = None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _fixture_product_users():
    suffix = uuid4().hex[:12]
    with SessionLocal() as db:
        coops = [
            models.Cooperativa(nombre=f"Product Test {suffix} {n}", razon_social="Test Ltda.", estado="ACTIVO")
            for n in (1, 2)
        ]
        db.add_all(coops)
        db.flush()
        roles = {r.nombre: r.id for r in db.query(models.Rol).all()}
        users = {}
        for coop_num, coop in enumerate(coops, start=1):
            for role in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO", "CONTADOR", "SOCIO"):
                user = models.Usuario(
                    correo=f"prod-{suffix}-{coop_num}-{role.lower()}@test.invalid",
                    contrasena=hash_password("Password123"),
                    rol_id=roles[role],
                    cooperativa_id=coop.id,
                    nombre=f"Product {role}",
                    estado="ACTIVO",
                )
                db.add(user)
                users[(coop_num, role)] = user
        superadmin = models.Usuario(
            correo=f"prod-{suffix}-superadmin@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=roles["SUPERADMIN"],
            cooperativa_id=None,
            nombre="Product Superadmin",
            estado="ACTIVO",
        )
        db.add(superadmin)
        users[(0, "SUPERADMIN")] = superadmin
        db.flush()
        result = {"suffix": suffix, "coop_ids": [c.id for c in coops], "users": {}}
        for key, user in users.items():
            role = key[1]
            token, _ = create_access_token(str(user.id), role, user.cooperativa_id)
            result["users"][key] = {"id": user.id, "token": token}
        result["user_ids"] = [user.id for user in users.values()]
        db.commit()
    return result


def _limpiar_product_users(fixture):
    with SessionLocal() as db:
        db.query(models.ProductoCredito).filter(
            models.ProductoCredito.cooperativa_id.in_(fixture["coop_ids"])
        ).delete(synchronize_session=False)
        db.query(models.Bitacora).filter(
            models.Bitacora.usuario_id.in_(fixture["user_ids"])
        ).delete(synchronize_session=False)
        db.query(models.Usuario).filter(models.Usuario.id.in_(fixture["user_ids"])).delete(
            synchronize_session=False
        )
        db.query(models.Cooperativa).filter(
            models.Cooperativa.id.in_(fixture["coop_ids"])
        ).delete(synchronize_session=False)
        db.commit()


def _insert_product(db, coop_id, code, *, state="ACTIVO", name=None):
    bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
    product = models.ProductoCredito(
        cooperativa_id=coop_id,
        codigo=code,
        nombre=name or code,
        descripcion=None,
        moneda_id=bob_id,
        monto_min=Decimal("100.00"),
        monto_max=Decimal("1000.00"),
        plazo_min_meses=1,
        plazo_max_meses=12,
        tasa_interes_anual=Decimal("12.00"),
        tipo_amortizacion="FRANCES",
        dias_gracia_mora=0,
        tasa_mora_anual=Decimal("3.00"),
        relacion_cuota_ingreso_max=Decimal("40.00"),
        requiere_garantia=False,
        estado=state,
    )
    db.add(product)
    db.flush()
    return product


def test_producto_credito_model_and_schemas_are_defined():
    assert models.ProductoCredito.__tablename__ == "producto_credito"
    assert {"codigo", "cooperativa_id", "moneda_id", "monto_min", "monto_max"} <= set(
        models.ProductoCredito.__table__.columns.keys()
    )
    assert schemas.ProductoCreditoCreate
    assert schemas.ProductoCreditoUpdate
    assert schemas.ProductoCreditoEstadoUpdate
    assert schemas.ProductoCreditoOut


def test_credit_product_routes_are_registered():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    paths = set(app.openapi()["paths"])
    assert "/api/v1/creditos/productos" in paths
    assert "/api/v1/creditos/productos/{producto_id}" in paths


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")
def test_product_list_detail_filters_tenant_and_allows_coop_staff(client):
    fixture = _fixture_product_users()
    try:
        with SessionLocal() as db:
            coop_one, coop_two = fixture["coop_ids"]
            _insert_product(db, coop_one, "Z-ACTIVE")
            inactive = _insert_product(db, coop_one, "A-INACTIVE", state="INACTIVO")
            _insert_product(db, coop_two, "FOREIGN")
            inactive_id = inactive.id
            db.commit()

        for role in ("ADMINISTRADOR", "CAJERO", "OFICIAL_CREDITO", "CONTADOR"):
            token = fixture["users"][(1, role)]["token"]
            response = client.get("/api/v1/creditos/productos?estado=ACTIVO", headers=_auth(token))
            assert response.status_code == 200, response.text
            assert [item["codigo"] for item in response.json()] == ["Z-ACTIVE"]

        admin_token = fixture["users"][(1, "ADMINISTRADOR")]["token"]
        all_products = client.get("/api/v1/creditos/productos", headers=_auth(admin_token))
        assert all_products.status_code == 200, all_products.text
        assert [item["codigo"] for item in all_products.json()] == ["A-INACTIVE", "Z-ACTIVE"]
        assert all_products.json()[0]["monto_min"] == "100.00"
        assert set(all_products.json()[0]["moneda"]) == {"id", "codigo_iso", "nombre", "simbolo"}
        detail = client.get(f"/api/v1/creditos/productos/{inactive_id}", headers=_auth(admin_token))
        assert detail.status_code == 200, detail.text
        foreign = client.get(
            f"/api/v1/creditos/productos/{inactive_id}",
            headers=_auth(fixture["users"][(2, "ADMINISTRADOR")]["token"]),
        )
        assert foreign.status_code == 404
        denied = client.get(
            "/api/v1/creditos/productos",
            headers=_auth(fixture["users"][(1, "SOCIO")]["token"]),
        )
        assert denied.status_code == 403
        no_tenant = client.get(
            "/api/v1/creditos/productos",
            headers=_auth(fixture["users"][(0, "SUPERADMIN")]["token"]),
        )
        assert no_tenant.status_code == 403
        assert no_tenant.json()["detail"] == "Operación no disponible para este usuario"
    finally:
        _limpiar_product_users(fixture)


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")
def test_product_create_normalizes_code_validates_and_audits(client):
    fixture = _fixture_product_users()
    try:
        admin = fixture["users"][(1, "ADMINISTRADOR")]
        cashier = fixture["users"][(1, "CAJERO")]
        superadmin = fixture["users"][(0, "SUPERADMIN")]
        with SessionLocal() as db:
            bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()

        body = {
            "codigo": "consumo-demo",
            "nombre": "Consumo demo",
            "descripcion": None,
            "moneda_id": bob_id,
            "monto_min": "100.00",
            "monto_max": "1000.00",
            "plazo_min_meses": 2,
            "plazo_max_meses": 24,
            "tasa_interes_anual": "12.50",
            "tipo_amortizacion": "FRANCES",
        }
        created = client.post("/api/v1/creditos/productos", json=body, headers=_auth(admin["token"]))
        assert created.status_code == 201, created.text
        product = created.json()
        assert product["codigo"] == "CONSUMO-DEMO"
        assert product["monto_min"] == "100.00"
        assert product["tasa_interes_anual"] == "12.50"
        assert product["dias_gracia_mora"] == 0
        assert product["tasa_mora_anual"] == "0.00"
        assert product["relacion_cuota_ingreso_max"] == "40.00"
        assert product["requiere_garantia"] is False
        assert product["estado"] == "ACTIVO"

        duplicate = client.post("/api/v1/creditos/productos", json=body, headers=_auth(admin["token"]))
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "Ya existe un producto con ese código"

        invalid_cases = []
        invalid = dict(body, codigo="bad.code")
        invalid_cases.append(invalid)
        invalid = dict(body, codigo="RANGE-ERROR", monto_min="1001.00")
        invalid_cases.append(invalid)
        invalid = dict(body, codigo="RATE-ERROR", tasa_interes_anual="100.01")
        invalid_cases.append(invalid)
        invalid = dict(body, codigo="CURRENCY-ERROR", moneda_id=2147483647)
        invalid_cases.append(invalid)
        for invalid in invalid_cases:
            response = client.post("/api/v1/creditos/productos", json=invalid, headers=_auth(admin["token"]))
            assert response.status_code == 400, response.text

        denied = client.post("/api/v1/creditos/productos", json=body, headers=_auth(cashier["token"]))
        assert denied.status_code == 403
        tenantless = client.post("/api/v1/creditos/productos", json=body, headers=_auth(superadmin["token"]))
        assert tenantless.status_code == 403
        assert tenantless.json()["detail"] == "Operación no disponible para este usuario"
        with SessionLocal() as db:
            audit = db.query(models.Bitacora).filter(
                models.Bitacora.usuario_id == admin["id"],
                models.Bitacora.modulo == "CREDITOS",
                models.Bitacora.accion == "CREAR_PRODUCTO",
            ).one()
            assert str(product["id"]) in audit.descripcion
    finally:
        _limpiar_product_users(fixture)


@pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")
def test_product_partial_update_state_and_audit(client):
    fixture = _fixture_product_users()
    try:
        admin = fixture["users"][(1, "ADMINISTRADOR")]
        foreign_admin = fixture["users"][(2, "ADMINISTRADOR")]
        cashier = fixture["users"][(1, "CAJERO")]
        with SessionLocal() as db:
            bob_id = db.query(models.Moneda.id).filter(models.Moneda.codigo_iso == "BOB").scalar()
        body = {
            "codigo": "EDIT-DEMO",
            "nombre": "Edit demo",
            "moneda_id": bob_id,
            "monto_min": "100.00",
            "monto_max": "1000.00",
            "plazo_min_meses": 2,
            "plazo_max_meses": 24,
            "tasa_interes_anual": "12.50",
            "tipo_amortizacion": "FRANCES",
        }
        created = client.post("/api/v1/creditos/productos", json=body, headers=_auth(admin["token"]))
        assert created.status_code == 201, created.text
        product_id = created.json()["id"]

        updated = client.put(
            f"/api/v1/creditos/productos/{product_id}",
            json={"monto_max": "2000.00", "descripcion": "Actualizado", "codigo": "NO-CAMBIAR"},
            headers=_auth(admin["token"]),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["codigo"] == "EDIT-DEMO"
        assert updated.json()["monto_max"] == "2000.00"
        assert updated.json()["descripcion"] == "Actualizado"
        cleared = client.put(
            f"/api/v1/creditos/productos/{product_id}", json={"descripcion": None},
            headers=_auth(admin["token"]),
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["descripcion"] is None

        invalid = client.put(
            f"/api/v1/creditos/productos/{product_id}", json={"monto_min": "2001.00"},
            headers=_auth(admin["token"]),
        )
        assert invalid.status_code == 400, invalid.text
        foreign = client.put(
            f"/api/v1/creditos/productos/{product_id}", json={"nombre": "Foreign"},
            headers=_auth(foreign_admin["token"]),
        )
        assert foreign.status_code == 404

        deactivated = client.patch(
            f"/api/v1/creditos/productos/{product_id}/estado", json={"estado": "INACTIVO"},
            headers=_auth(admin["token"]),
        )
        assert deactivated.status_code == 200, deactivated.text
        assert deactivated.json()["estado"] == "INACTIVO"
        invalid_state = client.patch(
            f"/api/v1/creditos/productos/{product_id}/estado", json={"estado": "BORRADO"},
            headers=_auth(admin["token"]),
        )
        assert invalid_state.status_code == 400
        denied = client.patch(
            f"/api/v1/creditos/productos/{product_id}/estado", json={"estado": "ACTIVO"},
            headers=_auth(cashier["token"]),
        )
        assert denied.status_code == 403
        with SessionLocal() as db:
            actions = [row[0] for row in db.query(models.Bitacora.accion).filter(
                models.Bitacora.usuario_id == admin["id"],
                models.Bitacora.modulo == "CREDITOS",
            ).order_by(models.Bitacora.id).all()]
            assert actions == ["CREAR_PRODUCTO", "ACTUALIZAR_PRODUCTO", "ACTUALIZAR_PRODUCTO", "DESACTIVAR_PRODUCTO"]
    finally:
        _limpiar_product_users(fixture)
