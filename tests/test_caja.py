"""Pruebas de caja de ventanilla (CU-W10, CU-W11 y CU-W13)."""

from datetime import date
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import models
from app.schemas import schemas
from app.db.session import engine


def _db_disponible() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# `main.py` runs `init_db()` on import; check PostgreSQL before importing it so
# the module-level skip works even when the local integration database is down.
_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(
    not _DB_DISPONIBLE, reason="PostgreSQL no disponible"
)

if _DB_DISPONIBLE:
    from main import app
else:
    app = None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _crear_operador_y_caja():
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        cooperativa_id = db.execute(
            text("SELECT cooperativa_id FROM usuario WHERE correo = 'cajero@test.com'")
        ).scalar_one()
        rol_id = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'CAJERO'")
        ).scalar_one()
        operador = models.Usuario(
            correo=f"caja-{sufijo}@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=rol_id,
            cooperativa_id=cooperativa_id,
            nombre="Operador de prueba",
            estado="ACTIVO",
        )
        db.add(operador)
        db.flush()
        caja = models.Caja(
            nombre=f"Caja {sufijo}",
            estado="CERRADA",
            cooperativa_id=cooperativa_id,
            monto_maximo_efectivo=500.00,
        )
        db.add(caja)
        db.commit()
        operador_id, caja_id = operador.id, caja.id
    token, _ = create_access_token(str(operador_id), "CAJERO", cooperativa_id)
    return operador_id, caja_id, caja.nombre, token


def _crear_cuenta(cooperativa_id, *, saldo="100.00"):
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        socio = models.Socio(
            cooperativa_id=cooperativa_id,
            ci=f"CI-{sufijo}",
            nombre="Titular",
            apellido="Ventanilla",
            estado="ACTIVO",
        )
        moneda_id = db.execute(
            text("SELECT id FROM moneda WHERE codigo_iso = 'BOB'")
        ).scalar_one()
        db.add(socio)
        db.flush()
        cuenta = models.CuentaAhorro(
            numero=f"CV-{sufijo}",
            tipo_producto="VISTA",
            saldo_disponible=saldo,
            saldo_bloqueado=0.00,
            estado="ACTIVA",
            fecha_registro=date.today(),
            socio_id=socio.id,
            moneda_id=moneda_id,
        )
        db.add(cuenta)
        db.commit()
        return socio.id, cuenta.id, cuenta.numero, socio.ci


def _limpiar_operador_y_caja(operador_id, caja_id, cuenta_ids=(), socio_ids=()):
    with SessionLocal() as db:
        if cuenta_ids:
            db.execute(
                text("UPDATE transaccion SET transaccion_contraparte_id = NULL WHERE cuenta_ahorro_id = ANY(:ids)"),
                {"ids": list(cuenta_ids)},
            )
            db.execute(
                text("DELETE FROM transaccion WHERE cuenta_ahorro_id = ANY(:ids)"),
                {"ids": list(cuenta_ids)},
            )
            db.execute(
                text("DELETE FROM cuenta_ahorro WHERE id = ANY(:ids)"),
                {"ids": list(cuenta_ids)},
            )
        if socio_ids:
            db.execute(
                text("DELETE FROM socio WHERE id = ANY(:ids)"),
                {"ids": list(socio_ids)},
            )
        db.execute(text("DELETE FROM bitacora WHERE usuario_id = :id"), {"id": operador_id})
        db.execute(text("DELETE FROM control_caja WHERE usuario_id = :id"), {"id": operador_id})
        db.execute(text("DELETE FROM caja WHERE id = :id"), {"id": caja_id})
        db.execute(text("DELETE FROM usuario WHERE id = :id"), {"id": operador_id})
        db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _abrir_sesion(client, caja_id, token):
    response = client.post(
        "/api/v1/caja/aperturas",
        json={"caja_id": caja_id, "monto_apertura": "200.00"},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_migracion_caja_agrega_columnas_requeridas():
    inspector = inspect(engine)
    caja_columns = {column["name"] for column in inspector.get_columns("caja")}
    transaccion_columns = {
        column["name"] for column in inspector.get_columns("transaccion")
    }

    assert {"cooperativa_id", "monto_maximo_efectivo"} <= caja_columns
    assert {"depositante_nombre", "depositante_ci"} <= transaccion_columns


def test_modelos_y_schemas_de_caja_estan_disponibles():
    assert hasattr(models, "Caja")
    assert hasattr(models, "ControlCaja")
    assert hasattr(schemas, "CajaOut")
    assert hasattr(schemas, "SesionCajaOut")
    assert hasattr(schemas, "AperturaCajaCreate")
    assert hasattr(schemas, "DepositoVentanillaCreate")
    assert hasattr(schemas, "TransferenciaVentanillaCreate")


def test_sesion_actual_devuelve_404_sin_apertura(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    try:
        response = client.get("/api/v1/caja/sesion-actual", headers=_auth(token))
        assert response.status_code == 404
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id)


def test_apertura_crea_sesion_y_actualiza_estado_de_caja(client):
    operador_id, caja_id, nombre, token = _crear_operador_y_caja()
    try:
        response = client.post(
            "/api/v1/caja/aperturas",
            json={"caja_id": caja_id, "monto_apertura": "125.00"},
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["caja_id"] == caja_id
        assert body["caja_nombre"] == nombre
        assert body["monto_apertura"] == "125.00"
        assert body["saldo_sistema"] == "125.00"
        assert body["estado"] == "ABIERTA"

        sesion = client.get("/api/v1/caja/sesion-actual", headers=_auth(token))
        assert sesion.status_code == 200
        assert sesion.json()["id"] == body["id"]
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id)


def test_listar_cajas_devuelve_caja_de_la_cooperativa(client):
    operador_id, caja_id, nombre, token = _crear_operador_y_caja()
    try:
        response = client.get("/api/v1/caja/cajas", headers=_auth(token))
        assert response.status_code == 200, response.text
        caja = next(item for item in response.json() if item["id"] == caja_id)
        assert caja == {
            "id": caja_id,
            "nombre": nombre,
            "estado": "CERRADA",
            "monto_maximo_efectivo": "500.00",
        }
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id)


def test_apertura_rechaza_monto_superior_al_limite(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    try:
        response = client.post(
            "/api/v1/caja/aperturas",
            json={"caja_id": caja_id, "monto_apertura": "500.01"},
            headers=_auth(token),
        )
        assert response.status_code == 400
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id)


def test_busqueda_de_cuenta_por_numero_y_ci(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_id, cuenta_id, numero, ci = _crear_cuenta(cooperativa_id)
    try:
        por_numero = client.get(
            "/api/v1/caja/cuentas/buscar",
            params={"q": numero},
            headers=_auth(token),
        )
        assert por_numero.status_code == 200, por_numero.text
        cuenta = next(item for item in por_numero.json() if item["id"] == cuenta_id)
        assert cuenta["numero"] == numero
        assert cuenta["titular"] == {
            "socio_id": socio_id,
            "nombre_completo": "Titular Ventanilla",
            "ci": ci,
        }
        assert cuenta["moneda"]["codigo_iso"] == "BOB"

        por_ci = client.get(
            "/api/v1/caja/cuentas/buscar",
            params={"q": ci},
            headers=_auth(token),
        )
        assert por_ci.status_code == 200, por_ci.text
        assert any(item["id"] == cuenta_id for item in por_ci.json())
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id, [cuenta_id], [socio_id])


def test_deposito_requiere_sesion_abierta(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_id, cuenta_id, _, _ = _crear_cuenta(cooperativa_id)
    try:
        response = client.post(
            "/api/v1/caja/depositos",
            json={
                "cuenta_id": cuenta_id,
                "monto": "10.00",
                "depositante_nombre": "Depositante",
                "depositante_ci": "1234567",
            },
            headers=_auth(token),
        )
        assert response.status_code == 400
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id, [cuenta_id], [socio_id])


def test_deposito_actualiza_saldo_caja_y_emite_comprobante(client):
    operador_id, caja_id, caja_nombre, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_id, cuenta_id, numero, _ = _crear_cuenta(cooperativa_id)
    try:
        apertura = client.post(
            "/api/v1/caja/aperturas",
            json={"caja_id": caja_id, "monto_apertura": "200.00"},
            headers=_auth(token),
        )
        assert apertura.status_code == 201, apertura.text

        response = client.post(
            "/api/v1/caja/depositos",
            json={
                "cuenta_id": cuenta_id,
                "monto": "25.50",
                "depositante_nombre": "Depositante",
                "depositante_ci": "1234567",
            },
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["numero_operacion"] > 0
        assert body["cuenta_numero"] == numero
        assert body["monto"] == "25.50"
        assert body["saldo_actualizado"] == "125.50"
        assert body["depositante_nombre"] == "Depositante"
        assert body["caja_nombre"] == caja_nombre
        with SessionLocal() as db:
            cuenta = db.get(models.CuentaAhorro, cuenta_id)
            control = db.get(models.ControlCaja, apertura.json()["id"])
            transaccion = db.execute(
                text("SELECT tipo, canal, control_caja_id, depositante_ci FROM transaccion WHERE id = :id"),
                {"id": body["numero_operacion"]},
            ).one()
            assert cuenta.saldo_disponible == 125.50
            assert control.saldo_sistema == 225.50
            assert transaccion == ("DEPOSITO", "VENTANILLA", control.id, "1234567")
    finally:
        _limpiar_operador_y_caja(operador_id, caja_id, [cuenta_id], [socio_id])


def test_preview_muestra_titulares_y_monedas(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_origen, cuenta_origen, numero_origen, _ = _crear_cuenta(cooperativa_id)
    socio_destino, cuenta_destino, numero_destino, _ = _crear_cuenta(cooperativa_id)
    try:
        _abrir_sesion(client, caja_id, token)
        response = client.get(
            "/api/v1/caja/transferencias/preview",
            params={"origen": numero_origen, "destino": numero_destino},
            headers=_auth(token),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["origen"]["cuenta_id"] == cuenta_origen
        assert body["origen"]["numero"] == numero_origen
        assert body["origen"]["titular"]["socio_id"] == socio_origen
        assert body["origen"]["saldo_disponible"] == "100.00"
        assert body["destino"]["cuenta_id"] == cuenta_destino
        assert body["destino"]["titular"]["socio_id"] == socio_destino
        assert body["origen"]["moneda"]["codigo_iso"] == "BOB"
    finally:
        _limpiar_operador_y_caja(
            operador_id,
            caja_id,
            [cuenta_origen, cuenta_destino],
            [socio_origen, socio_destino],
        )


def test_transferencia_ventanilla_requiere_sesion_abierta(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_origen, cuenta_origen, _, _ = _crear_cuenta(cooperativa_id)
    socio_destino, cuenta_destino, _, _ = _crear_cuenta(cooperativa_id)
    try:
        response = client.post(
            "/api/v1/caja/transferencias",
            json={
                "cuenta_origen_id": cuenta_origen,
                "cuenta_destino_id": cuenta_destino,
                "monto": "10.00",
            },
            headers=_auth(token),
        )
        assert response.status_code == 400
    finally:
        _limpiar_operador_y_caja(
            operador_id,
            caja_id,
            [cuenta_origen, cuenta_destino],
            [socio_origen, socio_destino],
        )


def test_transferencia_ventanilla_actualiza_saldos_sin_mover_efectivo(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_origen, cuenta_origen, numero_origen, _ = _crear_cuenta(cooperativa_id)
    socio_destino, cuenta_destino, numero_destino, _ = _crear_cuenta(cooperativa_id)
    try:
        sesion = _abrir_sesion(client, caja_id, token)
        response = client.post(
            "/api/v1/caja/transferencias",
            json={
                "cuenta_origen_id": cuenta_origen,
                "cuenta_destino_id": cuenta_destino,
                "monto": "15.25",
                "glosa": "Traspaso interno",
            },
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["numero_operacion"] > 0
        assert body["origen"]["numero"] == numero_origen
        assert body["destino"]["numero"] == numero_destino
        assert body["monto"] == "15.25"
        assert body["saldo_origen_actualizado"] == "84.75"
        assert body["glosa"] == "Traspaso interno"
        with SessionLocal() as db:
            control = db.get(models.ControlCaja, sesion["id"])
            saldos = db.execute(
                text("SELECT saldo_disponible FROM cuenta_ahorro WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": [cuenta_origen, cuenta_destino]},
            ).scalars().all()
            transacciones = db.execute(
                text("SELECT tipo, canal, control_caja_id, transaccion_contraparte_id FROM transaccion WHERE transaccion_contraparte_id = :id OR id = :id ORDER BY tipo"),
                {"id": body["numero_operacion"]},
            ).all()
            assert [str(saldo) for saldo in saldos] == ["84.75", "115.25"]
            assert control.saldo_sistema == 200.00
            assert {row[1] for row in transacciones} == {"VENTANILLA"}
            assert {row[2] for row in transacciones} == {control.id}
    finally:
        _limpiar_operador_y_caja(
            operador_id,
            caja_id,
            [cuenta_origen, cuenta_destino],
            [socio_origen, socio_destino],
        )


def test_transferencias_concurrentes_no_generan_saldo_negativo(client):
    operador_id, caja_id, _, token = _crear_operador_y_caja()
    with SessionLocal() as db:
        cooperativa_id = db.get(models.Usuario, operador_id).cooperativa_id
    socio_origen, cuenta_origen, _, _ = _crear_cuenta(cooperativa_id)
    socios_destino, cuentas_destino, _, _ = zip(
        *[_crear_cuenta(cooperativa_id, saldo="0.00") for _ in range(2)]
    )
    barrier = threading.Barrier(2)
    try:
        _abrir_sesion(client, caja_id, token)

        def transferir(cuenta_destino):
            barrier.wait(timeout=10)
            return client.post(
                "/api/v1/caja/transferencias",
                json={
                    "cuenta_origen_id": cuenta_origen,
                    "cuenta_destino_id": cuenta_destino,
                    "monto": "75.00",
                },
                headers=_auth(token),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(transferir, cuentas_destino))
        assert sorted(response.status_code for response in responses) == [201, 400]
        with SessionLocal() as db:
            saldo = db.get(models.CuentaAhorro, cuenta_origen).saldo_disponible
            assert saldo >= 0
    finally:
        _limpiar_operador_y_caja(
            operador_id,
            caja_id,
            [cuenta_origen, *cuentas_destino],
            [socio_origen, *socios_destino],
        )
