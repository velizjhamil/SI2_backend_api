"""Pruebas de arqueo y conciliación de caja (CU-W15)."""

from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models import models
from app.schemas import schemas


def _db_disponible() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# `main.py` runs `init_db()` on import; check PostgreSQL first so unavailable
# local integration databases are skipped instead of failing during collection.
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


def _crear_operador_y_caja(client, *, abrir=True):
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        cooperativa_id = db.execute(
            text("SELECT cooperativa_id FROM usuario WHERE correo = 'cajero@test.com'")
        ).scalar_one()
        rol_id = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'CAJERO'")
        ).scalar_one()
        operador = models.Usuario(
            correo=f"arqueo-{sufijo}@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=rol_id,
            cooperativa_id=cooperativa_id,
            nombre="Cajero de arqueo",
            estado="ACTIVO",
        )
        caja = models.Caja(
            nombre=f"Arqueo {sufijo}",
            estado="CERRADA",
            cooperativa_id=cooperativa_id,
            monto_maximo_efectivo=50000.00,
            umbral_diferencia_arqueo=50.00,
        )
        db.add_all([operador, caja])
        db.commit()
        operador_id, caja_id, caja_nombre = operador.id, caja.id, caja.nombre
    token, _ = create_access_token(str(operador_id), "CAJERO", cooperativa_id)
    control_id = None
    if abrir:
        apertura = client.post(
            "/api/v1/caja/aperturas",
            json={"caja_id": caja_id, "monto_apertura": "200.00"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert apertura.status_code == 201, apertura.text
        control_id = apertura.json()["id"]
    return operador_id, caja_id, caja_nombre, cooperativa_id, control_id, token


def _crear_cuenta(cooperativa_id: int, codigo_iso: str):
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        socio = models.Socio(
            cooperativa_id=cooperativa_id,
            ci=f"ARQ-{sufijo}",
            nombre="Socio",
            apellido="Arqueo",
            estado="ACTIVO",
        )
        moneda_id = db.execute(
            text("SELECT id FROM moneda WHERE codigo_iso = :codigo"),
            {"codigo": codigo_iso},
        ).scalar_one()
        db.add(socio)
        db.flush()
        cuenta = models.CuentaAhorro(
            numero=f"ARQ-{sufijo}",
            tipo_producto="VISTA",
            saldo_disponible="0.00",
            saldo_bloqueado="0.00",
            estado="ACTIVA",
            fecha_registro=date.today(),
            socio_id=socio.id,
            moneda_id=moneda_id,
        )
        db.add(cuenta)
        db.commit()
        return socio.id, cuenta.id


def _limpiar_arqueo_fixture(
    operador_id, caja_id, control_id, cuenta_ids=(), socio_ids=()
):
    with SessionLocal() as db:
        if control_id is not None:
            db.execute(
                text("DELETE FROM arqueo_caja WHERE control_caja_id = :id"),
                {"id": control_id},
            )
            db.execute(
                text("DELETE FROM transaccion WHERE control_caja_id = :id"),
                {"id": control_id},
            )
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
        if control_id is not None:
            db.execute(text("DELETE FROM control_caja WHERE id = :id"), {"id": control_id})
        db.execute(text("DELETE FROM caja WHERE id = :id"), {"id": caja_id})
        db.execute(text("DELETE FROM usuario WHERE id = :id"), {"id": operador_id})
        db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _moneda_id(codigo_iso: str) -> int:
    with SessionLocal() as db:
        return db.execute(
            text("SELECT id FROM moneda WHERE codigo_iso = :codigo"),
            {"codigo": codigo_iso},
        ).scalar_one()


def _arqueos_persistidos(control_id: int) -> int:
    with SessionLocal() as db:
        return db.execute(
            text("SELECT count(*) FROM arqueo_caja WHERE control_caja_id = :id"),
            {"id": control_id},
        ).scalar_one()


def _detalle_bob(*denominaciones):
    return [
        {"denominacion": denominacion, "cantidad": cantidad}
        for denominacion, cantidad in denominaciones
    ]


def test_migracion_arqueo_crea_columnas_y_tablas():
    inspector = inspect(engine)
    caja_columns = {column["name"] for column in inspector.get_columns("caja")}
    tables = set(inspector.get_table_names())

    assert "umbral_diferencia_arqueo" in caja_columns
    assert {"arqueo_caja", "arqueo_caja_moneda", "arqueo_caja_detalle"} <= tables

    assert {
        "control_caja_id",
        "usuario_id",
        "supervisor_id",
        "fecha",
        "fecha_autorizacion",
        "cierre",
        "requiere_supervisor",
        "observacion",
    } <= {column["name"] for column in inspector.get_columns("arqueo_caja")}
    assert {
        "arqueo_id",
        "moneda_id",
        "saldo_teorico",
        "total_contado",
        "diferencia",
        "resultado",
    } <= {column["name"] for column in inspector.get_columns("arqueo_caja_moneda")}
    assert {
        "arqueo_moneda_id",
        "tipo",
        "denominacion",
        "cantidad",
        "subtotal",
    } <= {column["name"] for column in inspector.get_columns("arqueo_caja_detalle")}


def test_modelos_y_schemas_de_arqueo_estan_disponibles():
    assert hasattr(models, "ArqueoCaja")
    assert hasattr(models, "ArqueoCajaMoneda")
    assert hasattr(models, "ArqueoCajaDetalle")
    assert hasattr(schemas, "ArqueoResumenOut")
    assert hasattr(schemas, "ArqueoCajaCreate")
    assert hasattr(schemas, "ArqueoCajaOut")


def test_resumen_arqueo_devuelve_404_sin_sesion_abierta(client):
    operador_id, caja_id, _, _, _, token = _crear_operador_y_caja(
        client, abrir=False
    )
    try:
        response = client.get(
            "/api/v1/caja/arqueos/resumen", headers=_auth(token)
        )
        assert response.status_code == 404
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, None)


def test_resumen_separa_bob_y_usd_desde_transacciones(client):
    operador_id, caja_id, caja_nombre, cooperativa_id, control_id, token = (
        _crear_operador_y_caja(client)
    )
    socio_bob, cuenta_bob = _crear_cuenta(cooperativa_id, "BOB")
    socio_usd, cuenta_usd = _crear_cuenta(cooperativa_id, "USD")
    try:
        for cuenta_id, monto in ((cuenta_bob, "30.00"), (cuenta_usd, "15.00")):
            response = client.post(
                "/api/v1/caja/depositos",
                json={
                    "cuenta_id": cuenta_id,
                    "monto": monto,
                    "depositante_nombre": "Titular",
                    "depositante_ci": "1234567",
                },
                headers=_auth(token),
            )
            assert response.status_code == 201, response.text

        response = client.get(
            "/api/v1/caja/arqueos/resumen", headers=_auth(token)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["control_caja_id"] == control_id
        assert body["caja_nombre"] == caja_nombre
        monedas = {item["moneda"]["codigo_iso"]: item for item in body["monedas"]}
        assert monedas["BOB"]["saldo_teorico"] == "230.00"
        assert monedas["BOB"]["monto_apertura"] == "200.00"
        assert monedas["BOB"]["total_depositos"] == "30.00"
        assert monedas["BOB"]["cantidad_movimientos"] == 1
        assert monedas["USD"]["saldo_teorico"] == "15.00"
        assert monedas["USD"]["monto_apertura"] == "0.00"
        assert monedas["USD"]["total_depositos"] == "15.00"
        assert monedas["USD"]["cantidad_movimientos"] == 1
        assert [entry["valor"] for entry in monedas["BOB"]["denominaciones"]] == [
            "200.00", "100.00", "50.00", "20.00", "10.00", "5.00",
            "2.00", "1.00", "0.50", "0.20", "0.10",
        ]
        assert [entry["valor"] for entry in monedas["USD"]["denominaciones"]] == [
            "100.00", "50.00", "20.00", "10.00", "5.00", "1.00",
        ]
    finally:
        _limpiar_arqueo_fixture(
            operador_id,
            caja_id,
            control_id,
            [cuenta_bob, cuenta_usd],
            [socio_bob, socio_usd],
        )


@pytest.mark.parametrize(
    ("detalle", "resultado", "total_contado", "diferencia"),
    [
        (_detalle_bob(("200.00", 1)), "CUADRADO", "200.00", "0.00"),
        (
            _detalle_bob(("200.00", 1), ("10.00", 1)),
            "SOBRANTE",
            "210.00",
            "10.00",
        ),
        (
            _detalle_bob(("100.00", 1), ("50.00", 1), ("20.00", 1), ("10.00", 2)),
            "FALTANTE",
            "190.00",
            "-10.00",
        ),
    ],
)
def test_registrar_arqueo_guarda_resultado_por_monto_contado(
    client, detalle, resultado, total_contado, diferencia
):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {"moneda_id": _moneda_id("BOB"), "detalle": detalle}
                ]
            },
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        moneda = response.json()["monedas"][0]
        assert moneda["resultado"] == resultado
        assert moneda["total_contado"] == total_contado
        assert moneda["diferencia"] == diferencia
        assert _arqueos_persistidos(control_id) == 1
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)


def test_diferencia_significativa_sin_supervisor_responde_409_sin_guardar(client):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {
                        "moneda_id": _moneda_id("BOB"),
                        "detalle": _detalle_bob(("100.00", 1)),
                    }
                ]
            },
            headers=_auth(token),
        )
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Diferencia significativa: se requiere autorización del supervisor"
        )
        assert _arqueos_persistidos(control_id) == 0
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)


def test_supervisor_invalido_responde_403_sin_guardar(client):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {
                        "moneda_id": _moneda_id("BOB"),
                        "detalle": _detalle_bob(("100.00", 1)),
                    }
                ],
                "supervisor": {
                    "correo": "missing-supervisor@example.com",
                    "contrasena": "incorrecta",
                },
            },
            headers=_auth(token),
        )
        assert response.status_code == 403
        assert _arqueos_persistidos(control_id) == 0
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)


def test_supervisor_administrador_autoriza_diferencia_significativa(client):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {
                        "moneda_id": _moneda_id("BOB"),
                        "detalle": _detalle_bob(("100.00", 1)),
                    }
                ],
                "supervisor": {
                    "correo": "admin@test.com",
                    "contrasena": "Password123",
                },
            },
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["requiere_supervisor"] is True
        assert body["supervisor"]["id"] > 0
        assert body["fecha_autorizacion"] is not None
        assert body["cierre"] is False
        assert _arqueos_persistidos(control_id) == 1
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)


def test_cerrar_caja_registra_arqueo_y_cierra_sesion(client):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {
                        "moneda_id": _moneda_id("BOB"),
                        "detalle": _detalle_bob(("200.00", 1)),
                    }
                ],
                "cerrar_caja": True,
            },
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        assert response.json()["cierre"] is True
        with SessionLocal() as db:
            control = db.get(models.ControlCaja, control_id)
            caja = db.get(models.Caja, caja_id)
            assert control.estado == "CERRADA"
            assert control.fecha_cierre is not None
            assert control.monto_cierre == 200.00
            assert caja.estado == "CERRADA"
        actual = client.get("/api/v1/caja/sesion-actual", headers=_auth(token))
        assert actual.status_code == 404
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)


@pytest.mark.parametrize(
    "detalle",
    [
        _detalle_bob(("7.00", 1)),
        _detalle_bob(("200.00", -1)),
    ],
)
def test_arqueo_rechaza_denominacion_desconocida_o_cantidad_negativa(
    client, detalle
):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {"moneda_id": _moneda_id("BOB"), "detalle": detalle}
                ]
            },
            headers=_auth(token),
        )
        assert response.status_code == 400
        assert _arqueos_persistidos(control_id) == 0
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)


def test_arqueo_exige_incluir_todas_las_monedas_resumen(client):
    operador_id, caja_id, _, cooperativa_id, control_id, token = (
        _crear_operador_y_caja(client)
    )
    socio_id, cuenta_id = _crear_cuenta(cooperativa_id, "USD")
    try:
        deposito = client.post(
            "/api/v1/caja/depositos",
            json={
                "cuenta_id": cuenta_id,
                "monto": "5.00",
                "depositante_nombre": "Titular",
                "depositante_ci": "1234567",
            },
            headers=_auth(token),
        )
        assert deposito.status_code == 201, deposito.text
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {
                        "moneda_id": _moneda_id("BOB"),
                        "detalle": _detalle_bob(("200.00", 1)),
                    }
                ]
            },
            headers=_auth(token),
        )
        assert response.status_code == 400
        assert _arqueos_persistidos(control_id) == 0
    finally:
        _limpiar_arqueo_fixture(
            operador_id, caja_id, control_id, [cuenta_id], [socio_id]
        )


def test_get_arqueo_devuelve_hoja_y_restringe_por_cooperativa(client):
    operador_id, caja_id, _, _, control_id, token = _crear_operador_y_caja(client)
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        otra_cooperativa = models.Cooperativa(
            nombre=f"Cooperativa arqueo {sufijo}",
            nit=f"ARQ{sufijo[:8]}",
            estado="ACTIVO",
        )
        db.add(otra_cooperativa)
        db.flush()
        rol_id = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'CAJERO'")
        ).scalar_one()
        otro_usuario = models.Usuario(
            correo=f"otro-arqueo-{sufijo}@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=rol_id,
            cooperativa_id=otra_cooperativa.id,
            nombre="Cajero de otra cooperativa",
            estado="ACTIVO",
        )
        db.add(otro_usuario)
        db.commit()
        otra_cooperativa_id, otro_usuario_id = otra_cooperativa.id, otro_usuario.id
    otro_token, _ = create_access_token(
        str(otro_usuario_id), "CAJERO", otra_cooperativa_id
    )
    try:
        created = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {
                        "moneda_id": _moneda_id("BOB"),
                        "detalle": _detalle_bob(("200.00", 1)),
                    }
                ],
                "observacion": "Conteo de prueba",
            },
            headers=_auth(token),
        )
        assert created.status_code == 201, created.text
        arqueo_id = created.json()["id"]
        own = client.get(
            f"/api/v1/caja/arqueos/{arqueo_id}", headers=_auth(token)
        )
        assert own.status_code == 200, own.text
        assert own.json()["id"] == arqueo_id
        assert own.json()["caja_nombre"].startswith("Arqueo ")
        assert own.json()["cajero"]["id"] == operador_id
        assert own.json()["monedas"][0]["resultado"] == "CUADRADO"
        assert own.json()["observacion"] == "Conteo de prueba"

        hidden = client.get(
            f"/api/v1/caja/arqueos/{arqueo_id}", headers=_auth(otro_token)
        )
        assert hidden.status_code == 404
    finally:
        _limpiar_arqueo_fixture(operador_id, caja_id, control_id)
        with SessionLocal() as db:
            db.execute(
                text("DELETE FROM usuario WHERE id = :id"),
                {"id": otro_usuario_id},
            )
            db.execute(
                text("DELETE FROM cooperativa WHERE id = :id"),
                {"id": otra_cooperativa_id},
            )
            db.commit()
