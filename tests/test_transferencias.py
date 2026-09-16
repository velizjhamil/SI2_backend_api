"""Pruebas Sprint 3: transferencias entre cuentas propias.

Cubre las fases 1-4 de `tasks.md` del change `transferencias-entre-cuentas`:
migración 007, `get_current_socio` (autoservicio), el endpoint
`POST /api/v1/ahorros/transferencias` con su locking determinístico y sus
validaciones de negocio (fases 1-3), y la auditoría de ambas patas más la
ausencia de comisión/límites (fase 4).
"""

import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal, engine
from app.models.models import Bitacora, CuentaAhorro, Rol, Socio, Usuario


def _db_disponible() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# `main.py` corre `init_db()` al importarse (falla fuerte si Postgres no está
# disponible). Chequeamos la conexión ANTES de importar `main`, para que el
# skip por "PostgreSQL no disponible" siga funcionando sin que la sola
# recolección del archivo tire abajo la corrida de pytest.
_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(
    not _DB_DISPONIBLE, reason="PostgreSQL no disponible"
)

if _DB_DISPONIBLE:
    from main import app
else:
    app = None

ADMIN = {"correo": "admin@test.com", "contrasena": "Password123"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client, credentials):
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def generate_ci(prefix):
    return f"{prefix}{uuid4().hex[:10]}"


def crear_socio_autenticado(client, admin_token, ci_prefix):
    """Registra un Socio (staff) y lo vincula a un Usuario nuevo con rol SOCIO.

    Devuelve (socio_id, usuario_id, token_jwt_del_socio).
    """
    response = client.post(
        "/api/v1/socios/registro",
        json={
            "ci": generate_ci(ci_prefix),
            "nombre": "Socio",
            "apellido": "Transferencias",
        },
        headers=headers(admin_token),
    )
    assert response.status_code == 201, response.text
    socio_id = response.json()["socio"]["id"]

    with SessionLocal() as db:
        socio = db.get(Socio, socio_id)
        rol_socio = db.execute(
            select(Rol).where(Rol.nombre == "SOCIO")
        ).scalar_one()
        usuario = Usuario(
            correo=f"socio-{uuid4().hex[:12]}@transferencias.test",
            contrasena=hash_password("Password123"),
            rol=rol_socio,
            cooperativa_id=socio.cooperativa_id,
            nombre="Socio Transferencias",
            estado="ACTIVO",
        )
        db.add(usuario)
        db.flush()
        socio.usuario_id = usuario.id
        usuario_id = usuario.id
        cooperativa_id = socio.cooperativa_id
        db.commit()

    token, _ = create_access_token(str(usuario_id), "SOCIO", cooperativa_id)
    return socio_id, usuario_id, token


def abrir_cuenta(client, admin_token, socio_id, *, moneda_id=1, monto="100.00", tipo_producto="VISTA"):
    response = client.post(
        "/api/v1/ahorros/cuentas",
        json={
            "socio_id": socio_id,
            "moneda_id": moneda_id,
            "tipo_producto": tipo_producto,
            "monto_apertura": monto,
        },
        headers=headers(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def cleanup(socio_id, usuario_id, cuenta_ids):
    with SessionLocal() as db:
        if cuenta_ids:
            db.execute(
                text(
                    "UPDATE transaccion SET transaccion_contraparte_id = NULL "
                    "WHERE cuenta_ahorro_id = ANY(:ids)"
                ),
                {"ids": cuenta_ids},
            )
            db.execute(
                text("DELETE FROM transaccion WHERE cuenta_ahorro_id = ANY(:ids)"),
                {"ids": cuenta_ids},
            )
            db.execute(delete(CuentaAhorro).where(CuentaAhorro.id.in_(cuenta_ids)))
        if usuario_id:
            db.execute(delete(Bitacora).where(Bitacora.usuario_id == usuario_id))
        if socio_id:
            db.execute(
                text("UPDATE socio SET usuario_id = NULL WHERE id = :id"),
                {"id": socio_id},
            )
            db.execute(delete(Socio).where(Socio.id == socio_id))
        if usuario_id:
            db.execute(delete(Usuario).where(Usuario.id == usuario_id))
        db.commit()


# ---------------------------------------------------------------------------
# Fase 2 — Autorización de autoservicio (get_current_socio)
# ---------------------------------------------------------------------------


def test_usuario_sin_socio_asociado_recibe_403(client):
    """Staff (sin Socio vinculado) no puede usar el camino de autoservicio."""
    token = login(client, ADMIN)
    response = client.post(
        "/api/v1/ahorros/transferencias",
        json={"cuenta_origen_id": 1, "cuenta_destino_id": 2, "monto": "10.00"},
        headers=headers(token),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Operación disponible solo para socios"


# ---------------------------------------------------------------------------
# Fase 3 — Locking, propiedad y validaciones de negocio
# ---------------------------------------------------------------------------


def test_cuenta_ajena_o_inexistente_responde_404_sin_revelar_existencia(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRF404-")
    otro_socio_id, otro_usuario_id, _ = crear_socio_autenticado(client, admin_token, "TRFOTR-")
    cuenta_propia = abrir_cuenta(client, admin_token, socio_id)
    cuenta_ajena = abrir_cuenta(client, admin_token, otro_socio_id)
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": cuenta_propia,
                "cuenta_destino_id": cuenta_ajena,
                "monto": "10.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Cuenta no encontrada"

        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": 999_999_999,
                "cuenta_destino_id": cuenta_propia,
                "monto": "10.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Cuenta no encontrada"
    finally:
        cleanup(socio_id, usuario_id, [cuenta_propia])
        cleanup(otro_socio_id, otro_usuario_id, [cuenta_ajena])


def test_origen_igual_a_destino_responde_400(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFEQ-")
    cuenta_id = abrir_cuenta(client, admin_token, socio_id)
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": cuenta_id,
                "cuenta_destino_id": cuenta_id,
                "monto": "10.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 400
        assert "distintas" in response.json()["detail"]
    finally:
        cleanup(socio_id, usuario_id, [cuenta_id])


def test_cuenta_inactiva_responde_400(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFINA-")
    origen_id = abrir_cuenta(client, admin_token, socio_id)
    destino_id = abrir_cuenta(client, admin_token, socio_id)
    response = client.patch(
        f"/api/v1/ahorros/cuentas/{destino_id}/estado",
        json={"estado": "BLOQUEADA"},
        headers=headers(admin_token),
    )
    assert response.status_code == 200, response.text
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": origen_id,
                "cuenta_destino_id": destino_id,
                "monto": "10.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 400
        assert "activas" in response.json()["detail"]
    finally:
        cleanup(socio_id, usuario_id, [origen_id, destino_id])


def test_monedas_distintas_responde_400_sin_conversion(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFMON-")
    origen_id = abrir_cuenta(client, admin_token, socio_id, moneda_id=1)
    destino_id = abrir_cuenta(client, admin_token, socio_id, moneda_id=2)
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": origen_id,
                "cuenta_destino_id": destino_id,
                "monto": "10.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 400
        assert "moneda" in response.json()["detail"]
    finally:
        cleanup(socio_id, usuario_id, [origen_id, destino_id])


def test_saldo_insuficiente_responde_400(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFSLD-")
    origen_id = abrir_cuenta(client, admin_token, socio_id, monto="10.00")
    destino_id = abrir_cuenta(client, admin_token, socio_id, monto="10.00")
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": origen_id,
                "cuenta_destino_id": destino_id,
                "monto": "50.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 400
        assert "Saldo insuficiente" in response.json()["detail"]
    finally:
        cleanup(socio_id, usuario_id, [origen_id, destino_id])


def test_transferencias_concurrentes_sin_deadlock_ni_saldo_negativo(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFCC-")
    cuenta_a = abrir_cuenta(client, admin_token, socio_id, monto="100.00")
    cuenta_b = abrir_cuenta(client, admin_token, socio_id, monto="100.00")
    try:
        resultados: list[int] = []

        def transferir(origen, destino, monto):
            response = client.post(
                "/api/v1/ahorros/transferencias",
                json={
                    "cuenta_origen_id": origen,
                    "cuenta_destino_id": destino,
                    "monto": monto,
                },
                headers=headers(token),
            )
            resultados.append(response.status_code)

        hilo_ab = threading.Thread(target=transferir, args=(cuenta_a, cuenta_b, "30.00"))
        hilo_ba = threading.Thread(target=transferir, args=(cuenta_b, cuenta_a, "20.00"))
        hilo_ab.start()
        hilo_ba.start()
        hilo_ab.join(timeout=10)
        hilo_ba.join(timeout=10)

        assert not hilo_ab.is_alive() and not hilo_ba.is_alive(), (
            f"Deadlock: alguna transferencia no terminó (resultados parciales: {resultados})"
        )
        assert resultados == [201, 201], resultados

        with SessionLocal() as db:
            saldo_a = db.get(CuentaAhorro, cuenta_a).saldo_disponible
            saldo_b = db.get(CuentaAhorro, cuenta_b).saldo_disponible
        assert saldo_a >= 0 and saldo_b >= 0
        assert saldo_a + saldo_b == Decimal("200.00")
    finally:
        cleanup(socio_id, usuario_id, [cuenta_a, cuenta_b])


def test_transferencia_exitosa_actualiza_ambos_saldos(client):
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFOK-")
    origen_id = abrir_cuenta(client, admin_token, socio_id, monto="100.00")
    destino_id = abrir_cuenta(client, admin_token, socio_id, monto="20.00")
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": origen_id,
                "cuenta_destino_id": destino_id,
                "monto": "30.00",
                "glosa": "Ahorro mensual",
            },
            headers=headers(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["cuenta_origen"]["saldo_disponible"] == "70.00"
        assert body["cuenta_destino"]["saldo_disponible"] == "50.00"
        assert body["monto"] == "30.00"
        assert body["transaccion_salida_id"] != body["transaccion_entrada_id"]
    finally:
        cleanup(socio_id, usuario_id, [origen_id, destino_id])


# ---------------------------------------------------------------------------
# Fase 4 — Auditoría de ambas patas y ausencia de comisión/límites
# ---------------------------------------------------------------------------


def test_transferencia_exitosa_genera_dos_filas_vinculadas_y_una_bitacora(client):
    """Cada transferencia exitosa crea exactamente 2 filas en `transaccion`
    (SALIDA/ENTRADA) vinculadas entre sí por `transaccion_contraparte_id` en
    ambos sentidos, más exactamente 1 entrada de bitácora nueva en el módulo
    `AHORROS`."""
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFAUD-")
    origen_id = abrir_cuenta(client, admin_token, socio_id, monto="100.00")
    destino_id = abrir_cuenta(client, admin_token, socio_id, monto="20.00")
    try:
        with SessionLocal() as db:
            bitacoras_antes = db.execute(
                select(func.count()).select_from(Bitacora).where(Bitacora.usuario_id == usuario_id)
            ).scalar_one()

        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": origen_id,
                "cuenta_destino_id": destino_id,
                "monto": "30.00",
                "glosa": "Auditoria fase 4",
            },
            headers=headers(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        salida_id = body["transaccion_salida_id"]
        entrada_id = body["transaccion_entrada_id"]

        with SessionLocal() as db:
            filas = db.execute(
                text(
                    "SELECT id, tipo, cuenta_ahorro_id, transaccion_contraparte_id "
                    "FROM transaccion WHERE id IN (:salida, :entrada) ORDER BY id"
                ),
                {"salida": salida_id, "entrada": entrada_id},
            ).all()
            bitacoras_despues = db.execute(
                select(func.count()).select_from(Bitacora).where(Bitacora.usuario_id == usuario_id)
            ).scalar_one()

        assert len(filas) == 2, filas

        por_id = {fila.id: fila for fila in filas}
        salida = por_id[salida_id]
        entrada = por_id[entrada_id]

        assert salida.tipo == "TRANSFERENCIA_SALIDA"
        assert salida.cuenta_ahorro_id == origen_id
        assert salida.transaccion_contraparte_id == entrada_id

        assert entrada.tipo == "TRANSFERENCIA_ENTRADA"
        assert entrada.cuenta_ahorro_id == destino_id
        assert entrada.transaccion_contraparte_id == salida_id

        assert bitacoras_despues - bitacoras_antes == 1
    finally:
        cleanup(socio_id, usuario_id, [origen_id, destino_id])


def test_transferencia_sin_comision_ni_limite_de_monto(client):
    """No se cobra comisión (acreditado == debitado, sin descuentos) y no
    existe límite de monto: una transferencia por el saldo total disponible
    de la cuenta origen se procesa completa, sin importar su magnitud."""
    admin_token = login(client, ADMIN)
    socio_id, usuario_id, token = crear_socio_autenticado(client, admin_token, "TRFLIM-")
    origen_id = abrir_cuenta(client, admin_token, socio_id, monto="500000.00")
    destino_id = abrir_cuenta(client, admin_token, socio_id, monto="10.00")
    try:
        response = client.post(
            "/api/v1/ahorros/transferencias",
            json={
                "cuenta_origen_id": origen_id,
                "cuenta_destino_id": destino_id,
                "monto": "500000.00",
            },
            headers=headers(token),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["monto"] == "500000.00"
        assert body["cuenta_origen"]["saldo_disponible"] == "0.00"
        assert body["cuenta_destino"]["saldo_disponible"] == "500010.00"

        debitado = Decimal("500000.00") - Decimal(body["cuenta_origen"]["saldo_disponible"])
        acreditado = Decimal(body["cuenta_destino"]["saldo_disponible"]) - Decimal("10.00")
        assert debitado == acreditado == Decimal("500000.00")
    finally:
        cleanup(socio_id, usuario_id, [origen_id, destino_id])
