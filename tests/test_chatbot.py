"""Pruebas del asistente virtual (chatbot).

Requiere PostgreSQL disponible; si no lo está, la suite se marca como skip.
Se verifica el enrutamiento de intenciones, la estructura de la respuesta y el
uso de datos reales tanto para personal como para socios.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.core.security import create_access_token
from app.db.session import SessionLocal, engine
from app.models.models import Cooperativa, CuentaAhorro, ProductoCredito, SolicitudCredito, Socio, Usuario


def _db_disponible() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_DB_DISPONIBLE = _db_disponible()
pytestmark = pytest.mark.skipif(not _DB_DISPONIBLE, reason="PostgreSQL no disponible")


@pytest.fixture(autouse=True)
def _sin_llm(monkeypatch):
    """Fija las pruebas al modo determinista aunque haya un LLM configurado."""
    for var in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)

if _DB_DISPONIBLE:
    from main import app
else:
    app = None


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def contexto():
    with SessionLocal() as db:
        rol_superadmin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'SUPERADMIN'")
        ).scalar_one()
        rol_admin = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'ADMINISTRADOR'")
        ).scalar_one()
        rol_socio = db.execute(
            text("SELECT id FROM rol WHERE nombre = 'SOCIO'")
        ).scalar_one()
        moneda_id = db.execute(text("SELECT id FROM moneda ORDER BY id LIMIT 1")).scalar_one()

        cooperativa = Cooperativa(nombre="Coop Chatbot Test")
        db.add(cooperativa)
        db.flush()

        superadmin = Usuario(
            correo="chatbot.superadmin@test.com",
            contrasena="x",
            rol_id=rol_superadmin,
            nombre="Super Chatbot",
            estado="ACTIVO",
        )
        admin = Usuario(
            correo="chatbot.admin@test.com",
            contrasena="x",
            rol_id=rol_admin,
            cooperativa_id=cooperativa.id,
            nombre="Admin Chatbot",
            estado="ACTIVO",
        )
        socio_usuario = Usuario(
            correo="chatbot.socio@test.com",
            contrasena="x",
            rol_id=rol_socio,
            cooperativa_id=cooperativa.id,
            nombre="Socio Chatbot",
            estado="ACTIVO",
        )
        db.add_all([superadmin, admin, socio_usuario])
        db.flush()

        socio = Socio(
            cooperativa_id=cooperativa.id,
            ci="99999999-CHAT",
            nombre="Ana",
            apellido="Pérez",
            estado="ACTIVO",
            usuario_id=socio_usuario.id,
        )
        db.add(socio)
        db.flush()

        cuenta = CuentaAhorro(
            numero="CHAT-0001",
            tipo_producto="VISTA",
            saldo_disponible=1500,
            saldo_bloqueado=0,
            estado="ACTIVA",
            fecha_registro=date.today(),
            socio_id=socio.id,
            moneda_id=moneda_id,
        )
        producto = ProductoCredito(
            cooperativa_id=cooperativa.id,
            codigo="CHAT-CREDITO",
            nombre="Crédito Chatbot",
            moneda_id=moneda_id,
            monto_min=100,
            monto_max=50000,
            plazo_min_meses=6,
            plazo_max_meses=36,
            tasa_interes_anual=10,
            tipo_amortizacion="FRANCES",
        )
        db.add_all([cuenta, producto])
        db.commit()

        datos = {
            "cooperativa_id": cooperativa.id,
            "superadmin_id": superadmin.id,
            "admin_id": admin.id,
            "socio_usuario_id": socio_usuario.id,
            "socio_id": socio.id,
        }

    yield datos

    with SessionLocal() as db:
        db.execute(delete(CuentaAhorro).where(CuentaAhorro.numero == "CHAT-0001"))
        db.execute(delete(ProductoCredito).where(ProductoCredito.codigo == "CHAT-CREDITO"))
        db.execute(delete(Socio).where(Socio.ci == "99999999-CHAT"))
        db.execute(
            delete(Usuario).where(
                Usuario.correo.in_(
                    [
                        "chatbot.superadmin@test.com",
                        "chatbot.admin@test.com",
                        "chatbot.socio@test.com",
                    ]
                )
            )
        )
        db.execute(delete(Cooperativa).where(Cooperativa.id == datos["cooperativa_id"]))
        db.commit()


def _token_admin(datos) -> dict:
    token, _ = create_access_token(
        subject=datos["admin_id"],
        role="ADMINISTRADOR",
        cooperativa_id=datos["cooperativa_id"],
    )
    return _auth(token)


def _token_socio(datos) -> dict:
    token, _ = create_access_token(
        subject=datos["socio_usuario_id"],
        role="SOCIO",
        cooperativa_id=datos["cooperativa_id"],
    )
    return _auth(token)


def _assert_savings_total(value):
    assert Decimal("".join(str(value).split())) == Decimal("1500.00")


def test_chat_requiere_autenticacion(client):
    assert client.post("/api/v1/chatbot/chat", json={"mensaje": "hola"}).status_code == 401


def test_chat_admin_responde_mora_con_datos_reales(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cómo está la mora?"},
        headers=_token_admin(contexto),
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["intent"] == "mora"
    assert cuerpo["fuente"] == "PLANTILLA"
    assert isinstance(cuerpo["respuesta"], str) and cuerpo["respuesta"]
    assert "mora" in cuerpo["datos"]
    assert cuerpo["sugerencias"]


def test_chat_admin_cuenta_socios_del_tenant(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuántos socios activos hay?"},
        headers=_token_admin(contexto),
    )
    cuerpo = resp.json()
    assert cuerpo["intent"] == "socios"
    assert cuerpo["datos"]["socios"]["total"] >= 1
    assert cuerpo["datos"]["alcance"] == "COOPERATIVA"


def test_chat_admin_ayuda_lista_capacidades(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "ayuda"},
        headers=_token_admin(contexto),
    )
    assert resp.json()["intent"] == "ayuda"


def test_chat_superadmin_alcance_global(client, contexto):
    token, _ = create_access_token(subject=contexto["superadmin_id"], role="SUPERADMIN")
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "estado de caja"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["datos"]["alcance"] == "GLOBAL"


def test_chat_socio_devuelve_contexto_personal(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuál es mi saldo?"},
        headers=_token_socio(contexto),
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["datos"]["es_socio"] is True
    _assert_savings_total(cuerpo["datos"]["ahorro_total"])
    assert "ci" not in str(cuerpo).lower()
    assert cuerpo["datos"]["socio"]["nombre"] == "Ana Pérez"


def test_chat_socio_precalificacion_usa_su_historial(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿cómo precalifico un crédito?"},
        headers=_token_socio(contexto),
    )
    cuerpo = resp.json()
    assert cuerpo["intent"] == "precalificacion"
    assert "indícame" in cuerpo["respuesta"]
    assert "ingreso mensual" in cuerpo["respuesta"]
    assert "aprobación" in cuerpo["respuesta"]


def test_chat_socio_conserva_intencion_precalificacion_en_turno_siguiente(client, contexto):
    response = client.post(
        "/api/v1/chatbot/chat",
        json={
            "mensaje": "ingreso mensual 3000",
            "historial": [{"role": "user", "content": "Quiero precalificar un crédito"}],
        },
        headers=_token_socio(contexto),
    )

    assert response.status_code == 200, response.text
    assert response.json()["intent"] == "precalificacion"
    assert "producto (código o nombre exacto)" in response.json()["respuesta"]
    assert "monto" in response.json()["respuesta"]


def test_chat_socio_calcula_precalificacion_sin_crear_solicitud(client, contexto):
    with SessionLocal() as db:
        solicitudes_antes = db.query(SolicitudCredito).filter(
            SolicitudCredito.socio_id == contexto["socio_id"]
        ).count()

    response = client.post(
        "/api/v1/chatbot/chat",
        json={
            "mensaje": (
                "Quiero precalificar producto CHAT-CREDITO, monto 1000, plazo 12 meses, "
                "ingreso mensual 3000, egresos mensuales 1000, deudas mensuales 0, "
                "antiguedad laboral 24 meses"
            )
        },
        headers=_token_socio(contexto),
    )

    assert response.status_code == 200, response.text
    assert "/1000 puntos" in response.json()["respuesta"]
    assert "no constituye aprobación" in response.json()["respuesta"]
    assert response.json()["fuente"] == "PLANTILLA"
    with SessionLocal() as db:
        assert db.query(SolicitudCredito).filter(
            SolicitudCredito.socio_id == contexto["socio_id"]
        ).count() == solicitudes_antes


def test_chat_socio_texto_no_relacionado_muestra_ayuda(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Qué tiempo hará mañana?"},
        headers=_token_socio(contexto),
    )

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["intent"] == "general"
    assert "No puedo responder esa consulta" in cuerpo["respuesta"]
    assert "funciones disponibles" in cuerpo["respuesta"]
    assert "no tienes créditos registrados" not in cuerpo["respuesta"]


def test_chat_socio_consulta_creditos_muestra_resumen_personal(client, contexto):
    resp = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuáles son mis créditos?"},
        headers=_token_socio(contexto),
    )

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["intent"] == "creditos"
    assert "Ana Pérez" in cuerpo["respuesta"]
    assert "no tienes créditos registrados" in cuerpo["respuesta"]


def test_chat_socio_lista_condiciones_del_producto_de_su_cooperativa(client, contexto):
    response = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Qué productos de crédito tienen?"},
        headers=_token_socio(contexto),
    )

    assert response.status_code == 200, response.text
    assert "CHAT-CREDITO" in response.json()["respuesta"]
    assert "100.00–50 000.00" in response.json()["respuesta"]
    assert "6–36 meses" in response.json()["respuesta"]


def test_chat_socio_omite_llm_y_pii(monkeypatch, client, contexto):
    from app.services import chatbot

    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    monkeypatch.setattr(
        chatbot.llm,
        "generar_texto",
        lambda *args, **kwargs: pytest.fail("Socio context must not be sent to the LLM"),
    )

    response = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuál es mi saldo?"},
        headers=_token_socio(contexto),
    )

    assert response.status_code == 200, response.text
    assert response.json()["fuente"] == "PLANTILLA"
    assert contexto["socio_id"] and "99999999-CHAT" not in response.text


def test_chat_socio_ignora_identificadores_enviados_por_cliente(client, contexto):
    response = client.post(
        "/api/v1/chatbot/chat",
        json={"mensaje": "¿Cuál es mi saldo? socio_id 999999 cooperativa_id 888888"},
        headers=_token_socio(contexto),
    )

    assert response.status_code == 200, response.text
    _assert_savings_total(response.json()["datos"]["ahorro_total"])
    assert "999999" not in response.text


@pytest.mark.parametrize(
    ("mensaje", "esperado"),
    [
        ("monto 3,000", Decimal("3000")),
        ("monto 3,50", Decimal("3.50")),
        ("monto 3.000,50", Decimal("3000.50")),
        ("monto -500", None),
        ("monto 3,0000", None),
    ],
)
def test_chat_precalificacion_parsea_montos_sin_aceptar_negativos(mensaje, esperado):
    from app.services.chatbot import _numero_de_mensajes

    assert _numero_de_mensajes([mensaje], "monto", "decimal") == esperado


def test_contexto_socio_incluye_detalle_vencido_solo_de_socio_y_cooperativa(monkeypatch):
    from app.services import chatbot

    socio = SimpleNamespace(
        id=12,
        cooperativa_id=34,
        fecha_registro=date(2020, 1, 1),
        nombre="Ana",
        apellido="Pérez",
    )
    cuota = SimpleNamespace(
        numero_credito="CREDITO-PERSONAL",
        numero_cuota=3,
        fecha_vencimiento=date(2024, 1, 15),
        monto_pendiente=Decimal("75.50"),
        estado_pago="PENDIENTE",
    )

    class QueryResult:
        def __init__(self, value=None):
            self.value = value

        def scalars(self):
            return self

        def all(self):
            return self.value or []

        def one(self):
            return (1, Decimal("75.50"))

        def scalar_one_or_none(self):
            return None

    class ReadOnlyDB:
        def __init__(self):
            self.statements = []
            self.results = [QueryResult(), QueryResult(), QueryResult(), QueryResult(), QueryResult([cuota]), QueryResult(), QueryResult()]

        def execute(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    db = ReadOnlyDB()
    monkeypatch.setattr(chatbot, "_socio_de_usuario", lambda db, usuario: socio)

    contexto = chatbot.contexto_socio(db, SimpleNamespace())

    query = str(db.statements[4].compile(compile_kwargs={"literal_binds": True}))
    assert "credito.socio_id = 12" in query
    assert "credito.cooperativa_id = 34" in query
    assert contexto["cuotas_vencidas_detalle"] == [
        {
            "numero_credito": "CREDITO-PERSONAL",
            "numero_cuota": 3,
            "fecha_vencimiento": "2024-01-15",
            "monto_pendiente": 75.5,
            "estado": "PENDIENTE",
        }
    ]


def test_chat_precalificacion_reutiliza_dictamen_sin_crear_solicitud(monkeypatch):
    from app.services import chatbot
    from app.services.scoring import score_application

    producto = SimpleNamespace(
        relacion_cuota_ingreso_max=Decimal("40"),
        tipo_amortizacion="FRANCES",
        requiere_garantia=False,
        cobertura_minima_garantia=Decimal("100"),
        tasa_interes_anual=Decimal("10"),
        moneda_id=1,
    )
    socio = SimpleNamespace(id=12, cooperativa_id=34, fecha_registro=date(2020, 1, 1))

    class QueryResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return []

    class ReadOnlyDB:
        def __init__(self):
            self.results = [QueryResult(producto), QueryResult([]), QueryResult(None)]
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    monkeypatch.setattr(chatbot, "_socio_de_usuario", lambda db, usuario: socio)
    scorer = Mock(wraps=score_application)
    monkeypatch.setattr(chatbot, "score_application", scorer)
    contexto = {
        "productos": [
            {
                "codigo": "PRUEBA",
                "nombre": "Producto de prueba",
                "monto_min": 100,
                "monto_max": 50000,
                "plazo_min_meses": 6,
                "plazo_max_meses": 36,
            }
        ],
        "mora": {"cuotas_vencidas": 0},
    }
    mensaje = (
        "precalificar producto PRUEBA monto 5000 plazo 24 meses ingreso mensual 5000 "
        "egresos mensuales 1000 deudas mensuales 0 antiguedad laboral 24 meses"
    )

    db = ReadOnlyDB()
    respuesta = chatbot._respuesta_precalificacion_socio(db, SimpleNamespace(), mensaje, [], contexto)

    expected = score_application(
        SimpleNamespace(
            evaluacion=SimpleNamespace(
                ingreso_mensual=Decimal("5000"),
                egreso_mensual=Decimal("1000"),
                cuota_deudas_mensual=Decimal("0"),
                antiguedad_laboral_meses=24,
                calificacion_asfi=None,
            ),
            producto=producto,
            monto=Decimal("5000"),
            plazo_meses=24,
            tasa_interes=producto.tasa_interes_anual,
            moneda_id=1,
            socio=socio,
            garantias=(),
        ),
        savings_accounts=(),
        has_credit_history=False,
        has_current_arrears=False,
    )
    assert f"Dictamen del motor: {expected['dictamen']}" in respuesta
    assert f"{expected['score']}/1000 puntos" in respuesta
    assert f"{expected['cuota_estimada']:.2f}" in respuesta
    assert "Factores:" in respuesta
    assert "Reglas de excepción:" in respuesta
    assert "no constituye aprobación ni crea una solicitud" in respuesta
    scorer.assert_called_once()
    assert all(str(statement).lstrip().upper().startswith("SELECT") for statement in db.statements)
