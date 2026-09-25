"""Pruebas de retiros en ventanilla y cierre de turno (CU-W12/CU-W16)."""

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


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(_DB_DISPONIBLE is False, reason="PostgreSQL no disponible")

if _DB_DISPONIBLE:
    from main import app
else:
    app = None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _crear_operador_y_caja(client, *, apertura="200.00", abrir=True):
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        cooperativa_id = db.execute(
            text("SELECT cooperativa_id FROM usuario WHERE correo = 'cajero@test.com'")
        ).scalar_one()
        rol_id = db.execute(text("SELECT id FROM rol WHERE nombre = 'CAJERO'")).scalar_one()
        operador = models.Usuario(
            correo=f"retiro-{sufijo}@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=rol_id,
            cooperativa_id=cooperativa_id,
            nombre="Cajero retiro",
            estado="ACTIVO",
        )
        caja = models.Caja(
            nombre=f"Retiro {sufijo}",
            estado="CERRADA",
            cooperativa_id=cooperativa_id,
            monto_maximo_efectivo=50000.00,
            umbral_diferencia_arqueo=50.00,
        )
        db.add_all([operador, caja])
        db.commit()
        operador_id, caja_id = operador.id, caja.id
    token, _ = create_access_token(str(operador_id), "CAJERO", cooperativa_id)
    control_id = None
    if abrir:
        response = client.post(
            "/api/v1/caja/aperturas",
            json={"caja_id": caja_id, "monto_apertura": apertura},
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        control_id = response.json()["id"]
    return operador_id, caja_id, cooperativa_id, control_id, token


def _crear_cuenta(cooperativa_id, *, saldo="100.00", tipo_producto="VISTA", moneda="BOB"):
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        socio = models.Socio(
            cooperativa_id=cooperativa_id,
            ci=f"RET-{sufijo}",
            nombre="Titular",
            apellido="Retiro",
            estado="ACTIVO",
        )
        moneda_id = db.execute(
            text("SELECT id FROM moneda WHERE codigo_iso = :codigo"),
            {"codigo": moneda},
        ).scalar_one()
        db.add(socio)
        db.flush()
        cuenta = models.CuentaAhorro(
            numero=f"RET-{sufijo}",
            tipo_producto=tipo_producto,
            saldo_disponible=saldo,
            saldo_bloqueado="0.00",
            estado="ACTIVA",
            fecha_registro=date.today(),
            socio_id=socio.id,
            moneda_id=moneda_id,
        )
        db.add(cuenta)
        db.commit()
        return socio.id, socio.ci, cuenta.id


def _detalle_bob_total(monto):
    restante = int(monto)
    detalle = []
    for valor in (200, 100, 50, 20, 10, 5, 2, 1):
        cantidad, restante = divmod(restante, valor)
        if cantidad:
            detalle.append({"denominacion": f"{valor:.2f}", "cantidad": cantidad})
    assert restante == 0
    return detalle


def _registrar_arqueo(client, token, *, contado=200):
    return client.post(
        "/api/v1/caja/arqueos",
        json={
            "monedas": [
                {"moneda_id": _moneda_id("BOB"), "detalle": _detalle_bob_total(contado)}
            ]
        },
        headers=_auth(token),
    )


def _limpiar_fixture(operador_id, caja_id, control_id, cuenta_ids=(), socio_ids=()):
    with SessionLocal() as db:
        if control_id is not None:
            db.execute(text("DELETE FROM cierre_caja WHERE control_caja_id = :id"), {"id": control_id})
            db.execute(text("DELETE FROM arqueo_caja WHERE control_caja_id = :id"), {"id": control_id})
            db.execute(
                text("UPDATE transaccion SET transaccion_contraparte_id = NULL WHERE control_caja_id = :id"),
                {"id": control_id},
            )
            db.execute(text("DELETE FROM transaccion WHERE control_caja_id = :id"), {"id": control_id})
        if cuenta_ids:
            db.execute(
                text("UPDATE transaccion SET transaccion_contraparte_id = NULL WHERE cuenta_ahorro_id = ANY(:ids) OR transaccion_contraparte_id = ANY(:ids)"),
                {"ids": list(cuenta_ids)},
            )
            db.execute(text("DELETE FROM transaccion WHERE cuenta_ahorro_id = ANY(:ids)"), {"ids": list(cuenta_ids)})
            db.execute(text("DELETE FROM cuenta_ahorro WHERE id = ANY(:ids)"), {"ids": list(cuenta_ids)})
        if socio_ids:
            db.execute(text("DELETE FROM socio WHERE id = ANY(:ids)"), {"ids": list(socio_ids)})
        if operador_id is not None:
            db.execute(text("DELETE FROM bitacora WHERE usuario_id = :id"), {"id": operador_id})
        if control_id is not None:
            db.execute(text("DELETE FROM control_caja WHERE id = :id"), {"id": control_id})
        if caja_id is not None:
            db.execute(text("DELETE FROM caja WHERE id = :id"), {"id": caja_id})
        if operador_id is not None:
            db.execute(text("DELETE FROM usuario WHERE id = :id"), {"id": operador_id})
        db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _moneda_id(codigo_iso):
    with SessionLocal() as db:
        return db.execute(
            text("SELECT id FROM moneda WHERE codigo_iso = :codigo"),
            {"codigo": codigo_iso},
        ).scalar_one()


def _retiro(client, token, cuenta_id, ci, *, monto="20.00", retirante=None):
    return client.post(
        "/api/v1/caja/retiros",
        json={
            "cuenta_id": cuenta_id,
            "monto": monto,
            "retirante": retirante or {"tipo": "TITULAR", "nombre": "Titular Retiro", "ci": ci},
        },
        headers=_auth(token),
    )


def test_migracion_retiros_cierre_crea_columnas_y_tablas():
    inspector = inspect(engine)
    transaccion_columns = {
        column["name"] for column in inspector.get_columns("transaccion")
    }
    tables = set(inspector.get_table_names())

    assert {"retirante_tipo", "retirante_nombre", "retirante_ci"} <= transaccion_columns
    assert {"cierre_caja", "cierre_caja_moneda"} <= tables
    assert {
        "control_caja_id",
        "arqueo_id",
        "usuario_id",
        "fecha",
        "observacion",
    } <= {column["name"] for column in inspector.get_columns("cierre_caja")}
    assert {
        "cierre_id",
        "moneda_id",
        "monto_apertura",
        "total_depositos",
        "cantidad_depositos",
        "total_retiros",
        "cantidad_retiros",
        "cantidad_transferencias",
        "saldo_teorico",
        "total_contado",
        "diferencia",
        "traspaso_boveda",
    } <= {column["name"] for column in inspector.get_columns("cierre_caja_moneda")}


def test_modelos_y_schemas_de_retiro_y_cierre_estan_disponibles():
    assert hasattr(models, "CierreCaja")
    assert hasattr(models, "CierreCajaMoneda")
    assert hasattr(schemas, "RetiroVentanillaCreate")
    assert hasattr(schemas, "RetiroVentanillaOut")
    assert hasattr(schemas, "CierreVerificacionOut")
    assert hasattr(schemas, "CierreCajaCreate")
    assert hasattr(schemas, "CierreCajaOut")


def test_retiro_titular_actualiza_saldo_caja_y_registra_datos(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id)
    try:
        response = _retiro(client, token, cuenta_id, ci)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["cuenta_numero"].startswith("RET-")
        assert body["monto"] == "20.00"
        assert body["saldo_actualizado"] == "80.00"
        assert body["saldo_minimo"] == "10.00"
        assert body["efectivo_caja_restante"] == "180.00"
        assert body["retirante"] == {"tipo": "TITULAR", "nombre": "Titular Retiro", "ci": ci}
        with SessionLocal() as db:
            cuenta = db.get(models.CuentaAhorro, cuenta_id)
            control = db.get(models.ControlCaja, control_id)
            row = db.execute(
                text("SELECT tipo, canal, monto, retirante_tipo, retirante_nombre, retirante_ci FROM transaccion WHERE control_caja_id = :control AND cuenta_ahorro_id = :cuenta"),
                {"control": control_id, "cuenta": cuenta_id},
            ).one()
            assert cuenta.saldo_disponible == 80
            assert control.saldo_sistema == 180
            assert tuple(row) == ("RETIRO", "VENTANILLA", 20, "TITULAR", "Titular Retiro", ci)
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_retiro_aplica_saldo_minimo_del_producto_programado(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id, saldo="60.00", tipo_producto="PROGRAMADO")
    try:
        response = _retiro(client, token, cuenta_id, ci, monto="11.00")
        assert response.status_code == 400
        assert response.json()["detail"] == "Saldo insuficiente: debe mantener un saldo mínimo de 50.00"
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_retiro_rechaza_ci_de_titular_incorrecta(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id)
    try:
        response = _retiro(client, token, cuenta_id, ci, retirante={"tipo": "TITULAR", "nombre": "Titular Retiro", "ci": "CI-INCORRECTO"})
        assert response.status_code == 400
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_retiro_registra_apoderado_y_exige_datos(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id)
    try:
        missing = _retiro(client, token, cuenta_id, ci, retirante={"tipo": "APODERADO", "nombre": "", "ci": ""})
        assert missing.status_code == 400
        response = _retiro(client, token, cuenta_id, ci, retirante={"tipo": "APODERADO", "nombre": "Apoderada", "ci": "CI-APO-1"})
        assert response.status_code == 201, response.text
        assert response.json()["retirante"] == {"tipo": "APODERADO", "nombre": "Apoderada", "ci": "CI-APO-1"}
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_retiro_rechaza_efectivo_insuficiente_y_refleja_retiro_en_resumen(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client, apertura="50.00")
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id, saldo="500.00")
    try:
        insufficient = _retiro(client, token, cuenta_id, ci, monto="60.00")
        assert insufficient.status_code == 400
        assert insufficient.json()["detail"] == "Efectivo insuficiente en caja"
        success = _retiro(client, token, cuenta_id, ci, monto="20.00")
        assert success.status_code == 201, success.text
        summary = client.get("/api/v1/caja/arqueos/resumen", headers=_auth(token))
        assert summary.status_code == 200, summary.text
        bob = next(item for item in summary.json()["monedas"] if item["moneda"]["codigo_iso"] == "BOB")
        assert bob["total_retiros"] == "20.00"
        assert bob["saldo_teorico"] == "30.00"
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_verificacion_acepta_diferencia_autorizada(client):
    operador_id, caja_id, _, control_id, token = _crear_operador_y_caja(client)
    try:
        arqueo = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {"moneda_id": _moneda_id("BOB"), "detalle": _detalle_bob_total(260)}
                ],
                "supervisor": {"correo": "admin@test.com", "contrasena": "Password123"},
            },
            headers=_auth(token),
        )
        assert arqueo.status_code == 201, arqueo.text
        verification = client.get("/api/v1/caja/cierres/verificacion", headers=_auth(token))
        assert verification.status_code == 200, verification.text
        assert verification.json()["puede_cerrar"] is True
        assert verification.json()["arqueo"]["supervisor"]["id"]
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id)


def test_retiro_y_verificacion_exigen_sesion_abierta(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(
        client, abrir=False
    )
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id)
    try:
        withdrawal = _retiro(client, token, cuenta_id, ci)
        assert withdrawal.status_code == 400
        verification = client.get("/api/v1/caja/cierres/verificacion", headers=_auth(token))
        assert verification.status_code == 404
        closing = client.post("/api/v1/caja/cierres", json={}, headers=_auth(token))
        assert closing.status_code == 400
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_retiro_usa_efectivo_teorico_de_su_moneda(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id, moneda="USD")
    try:
        deposito = client.post(
            "/api/v1/caja/depositos",
            json={"cuenta_id": cuenta_id, "monto": "100.00", "depositante_nombre": "Titular", "depositante_ci": ci},
            headers=_auth(token),
        )
        assert deposito.status_code == 201, deposito.text
        withdrawal = _retiro(client, token, cuenta_id, ci, monto="20.00")
        assert withdrawal.status_code == 201, withdrawal.text
        assert withdrawal.json()["moneda"]["codigo_iso"] == "USD"
        assert withdrawal.json()["efectivo_caja_restante"] == "80.00"
        summary = client.get("/api/v1/caja/arqueos/resumen", headers=_auth(token))
        balances = {
            item["moneda"]["codigo_iso"]: item["saldo_teorico"]
            for item in summary.json()["monedas"]
        }
        assert balances["BOB"] == "200.00"
        assert balances["USD"] == "80.00"
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_arqueo_no_puede_cerrar_turno(client):
    operador_id, caja_id, _, control_id, token = _crear_operador_y_caja(client)
    try:
        response = client.post(
            "/api/v1/caja/arqueos",
            json={
                "monedas": [
                    {"moneda_id": _moneda_id("BOB"), "detalle": [{"denominacion": "200.00", "cantidad": 1}]}
                ],
                "cerrar_caja": True,
            },
            headers=_auth(token),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "El cierre de caja se realiza desde el cierre de turno"
        with SessionLocal() as db:
            assert db.get(models.ControlCaja, control_id).estado == "ABIERTA"
            assert db.execute(
                text("SELECT count(*) FROM arqueo_caja WHERE control_caja_id = :id"),
                {"id": control_id},
            ).scalar_one() == 0
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id)


def test_verificacion_cierre_avisa_si_no_hay_arqueo_y_post_rechaza(client):
    operador_id, caja_id, _, control_id, token = _crear_operador_y_caja(client)
    try:
        verification = client.get("/api/v1/caja/cierres/verificacion", headers=_auth(token))
        assert verification.status_code == 200, verification.text
        body = verification.json()
        assert body["puede_cerrar"] is False
        assert body["arqueo"] is None
        assert body["alertas"] == ["No hay un arqueo registrado para esta sesión"]
        response = client.post("/api/v1/caja/cierres", json={}, headers=_auth(token))
        assert response.status_code == 409
        assert response.json()["detail"] == body["alertas"][0]
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id)


def test_verificacion_rechaza_diferencia_no_autorizada(client):
    operador_id, caja_id, _, control_id, token = _crear_operador_y_caja(client)
    try:
        arqueo = _registrar_arqueo(client, token, contado=210)
        assert arqueo.status_code == 201, arqueo.text
        verification = client.get("/api/v1/caja/cierres/verificacion", headers=_auth(token))
        assert verification.status_code == 200, verification.text
        body = verification.json()
        assert body["puede_cerrar"] is False
        assert body["arqueo"]["id"] == arqueo.json()["id"]
        assert body["arqueo"]["monedas"][0]["resultado"] == "SOBRANTE"
        assert body["alertas"] == ["El último arqueo tiene diferencias sin autorización del supervisor"]
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id)


def test_verificacion_detecta_transacciones_posteriores_al_arqueo(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio_id, ci, cuenta_id = _crear_cuenta(cooperativa_id)
    try:
        arqueo = _registrar_arqueo(client, token)
        assert arqueo.status_code == 201, arqueo.text
        deposito = client.post(
            "/api/v1/caja/depositos",
            json={"cuenta_id": cuenta_id, "monto": "5.00", "depositante_nombre": "Titular", "depositante_ci": ci},
            headers=_auth(token),
        )
        assert deposito.status_code == 201, deposito.text
        verification = client.get("/api/v1/caja/cierres/verificacion", headers=_auth(token))
        assert verification.status_code == 200, verification.text
        assert verification.json()["puede_cerrar"] is False
        assert verification.json()["alertas"] == ["Hay transacciones posteriores al último arqueo"]
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta_id], [socio_id])


def test_cierre_turno_persiste_resumen_y_bloquea_operaciones(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    socio1_id, ci1, cuenta1_id = _crear_cuenta(cooperativa_id, saldo="100.00")
    socio2_id, ci2, cuenta2_id = _crear_cuenta(cooperativa_id, saldo="100.00")
    try:
        deposito = client.post(
            "/api/v1/caja/depositos",
            json={"cuenta_id": cuenta1_id, "monto": "50.00", "depositante_nombre": "Titular", "depositante_ci": ci1},
            headers=_auth(token),
        )
        assert deposito.status_code == 201, deposito.text
        transferencia = client.post(
            "/api/v1/caja/transferencias",
            json={
                "cuenta_origen_id": cuenta1_id,
                "cuenta_destino_id": cuenta2_id,
                "monto": "5.00",
            },
            headers=_auth(token),
        )
        assert transferencia.status_code == 201, transferencia.text
        retiro = _retiro(client, token, cuenta1_id, ci1, monto="20.00")
        assert retiro.status_code == 201, retiro.text
        arqueo = _registrar_arqueo(client, token, contado=230)
        assert arqueo.status_code == 201, arqueo.text
        verification = client.get("/api/v1/caja/cierres/verificacion", headers=_auth(token))
        assert verification.status_code == 200, verification.text
        assert verification.json()["puede_cerrar"] is True

        close = client.post(
            "/api/v1/caja/cierres",
            json={"observacion": "Turno finalizado"},
            headers=_auth(token),
        )
        assert close.status_code == 201, close.text
        body = close.json()
        assert body["arqueo_id"] == arqueo.json()["id"]
        assert body["observacion"] == "Turno finalizado"
        bob = next(item for item in body["monedas"] if item["moneda"]["codigo_iso"] == "BOB")
        assert bob["total_depositos"] == "50.00"
        assert bob["cantidad_depositos"] == 1
        assert bob["total_retiros"] == "20.00"
        assert bob["cantidad_retiros"] == 1
        assert bob["cantidad_transferencias"] == 1
        assert bob["total_contado"] == "230.00"
        assert bob["traspaso_boveda"] == "230.00"
        with SessionLocal() as db:
            assert db.get(models.ControlCaja, control_id).estado == "CERRADA"
            assert db.get(models.Caja, caja_id).estado == "CERRADA"
            assert db.execute(
                text("SELECT count(*) FROM cierre_caja WHERE id = :id"), {"id": body["id"]}
            ).scalar_one() == 1

        post_close_requests = [
            client.post("/api/v1/caja/depositos", json={"cuenta_id": cuenta1_id, "monto": "1.00", "depositante_nombre": "Titular", "depositante_ci": ci1}, headers=_auth(token)),
            _retiro(client, token, cuenta1_id, ci1, monto="1.00"),
            client.post("/api/v1/caja/transferencias", json={"cuenta_origen_id": cuenta1_id, "cuenta_destino_id": cuenta2_id, "monto": "1.00"}, headers=_auth(token)),
            _registrar_arqueo(client, token),
        ]
        assert [item.status_code for item in post_close_requests] == [400, 400, 400, 400]
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id, [cuenta1_id, cuenta2_id], [socio1_id, socio2_id])


def test_get_cierre_devuelve_planilla_y_restringe_por_cooperativa(client):
    operador_id, caja_id, cooperativa_id, control_id, token = _crear_operador_y_caja(client)
    sufijo = uuid4().hex[:12]
    with SessionLocal() as db:
        otra_cooperativa = models.Cooperativa(
            nombre=f"Cooperativa cierre {sufijo}",
            nit=f"CER{sufijo[:8]}",
            estado="ACTIVO",
        )
        db.add(otra_cooperativa)
        db.flush()
        rol_id = db.execute(text("SELECT id FROM rol WHERE nombre = 'CAJERO'")).scalar_one()
        otro_usuario = models.Usuario(
            correo=f"otro-cierre-{sufijo}@test.invalid",
            contrasena=hash_password("Password123"),
            rol_id=rol_id,
            cooperativa_id=otra_cooperativa.id,
            nombre="Cajero otro cierre",
            estado="ACTIVO",
        )
        db.add(otro_usuario)
        db.commit()
        otra_cooperativa_id, otro_usuario_id = otra_cooperativa.id, otro_usuario.id
    otro_token, _ = create_access_token(str(otro_usuario_id), "CAJERO", otra_cooperativa_id)
    try:
        arqueo = _registrar_arqueo(client, token)
        assert arqueo.status_code == 201, arqueo.text
        cierre = client.post("/api/v1/caja/cierres", json={}, headers=_auth(token))
        assert cierre.status_code == 201, cierre.text
        cierre_id = cierre.json()["id"]
        own = client.get(f"/api/v1/caja/cierres/{cierre_id}", headers=_auth(token))
        assert own.status_code == 200, own.text
        assert own.json()["id"] == cierre_id
        assert own.json()["caja_nombre"].startswith("Retiro ")
        assert own.json()["arqueo_id"] == arqueo.json()["id"]
        assert own.json()["cajero"]["id"] == operador_id
        hidden = client.get(f"/api/v1/caja/cierres/{cierre_id}", headers=_auth(otro_token))
        assert hidden.status_code == 404
    finally:
        _limpiar_fixture(operador_id, caja_id, control_id)
        with SessionLocal() as db:
            db.execute(text("DELETE FROM usuario WHERE id = :id"), {"id": otro_usuario_id})
            db.execute(text("DELETE FROM cooperativa WHERE id = :id"), {"id": otra_cooperativa_id})
            db.commit()
