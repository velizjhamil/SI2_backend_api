"""Report-generation intents in the chatbot (no database required)."""

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.chatbot import chat
from app.schemas.schemas import ChatRequest
from app.services import chatbot


@pytest.fixture(autouse=True)
def _sin_llm(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def contexto_cooperativa():
    return {
        "alcance": "COOPERATIVA",
        "socios": {"total": 10, "activos": 9},
        "ahorro": {"cuentas_activas": 8, "saldo_total": 5000.0},
        "creditos": {
            "vigentes": 4,
            "cartera_saldo_pendiente": 3000.0,
            "colocaciones_monto_aprobado": 4000.0,
        },
        "mora": {"cuotas_vencidas": 1, "monto_vencido": 250.0, "creditos_en_mora": 1},
        "dpf": {"vigentes": 2, "monto_total": 1000.0},
        "caja": {"sesiones_abiertas": 1},
        "usuarios_activos": 5,
        "productos": [],
    }


@pytest.fixture
def contexto_socio():
    return {
        "es_socio": True,
        "socio": {"nombre": "Ana", "ci": "123", "estado": "ACTIVO", "antiguedad_dias": 30},
        "cuentas": [],
        "ahorro_total": 0.0,
        "creditos": [],
        "mora": {"cuotas_vencidas": 0, "monto_vencido": 0.0},
        "proxima_cuota": None,
        "dpf": [],
    }


@pytest.fixture
def responder(monkeypatch, contexto_cooperativa, contexto_socio):
    monkeypatch.setattr(chatbot, "contexto_cooperativa", lambda db, usuario: contexto_cooperativa)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: contexto_socio)

    def _responder(rol, mensaje, cooperativa_id=1):
        usuario = SimpleNamespace(
            id=1,
            cooperativa_id=cooperativa_id,
            nombre="Usuario de prueba",
            rol=SimpleNamespace(nombre=rol),
        )
        return chatbot.responder(None, usuario, mensaje)

    return _responder


def test_reporte_de_morosidad_incluye_descarga_pdf(responder):
    salida = responder("ADMINISTRADOR", "Genera el reporte de morosidad en PDF")

    assert salida["intent"] == "reporte"
    assert salida["fuente"] == "PLANTILLA"
    reporte = salida["reporte"]
    assert set(reporte) == {"tipo", "nombre", "formato", "url", "filename"}
    assert reporte["tipo"] == "mora"
    assert reporte["nombre"] == "Reporte de morosidad"
    assert reporte["formato"] == "pdf"
    assert reporte["url"].startswith("/api/v1/reportes/mora/export?")
    assert "formato=pdf" in reporte["url"]
    assert reporte["filename"].startswith("morosidad-")
    assert reporte["filename"].endswith(".pdf")
    assert "Reporte de morosidad" in salida["respuesta"]


@pytest.mark.parametrize(
    ("rol", "mensaje", "tipo", "prefijo"),
    [
        ("ADMINISTRADOR", "Genera el reporte de cartera", "cartera", "/api/v1/reportes/cartera/export?"),
        ("OFICIAL_CREDITO", "Reporte de captaciones en pdf", "captaciones", "/api/v1/reportes/captaciones/export?"),
        ("CONTADOR", "Genera el reporte de indicadores", "indicadores", "/api/v1/reportes/indicadores/export?"),
        ("CONTADOR", "Genera el libro diario", "libro-diario", "/api/v1/contabilidad/libro-diario/export?"),
        ("CONTADOR", "Genera el balance de comprobación", "balance-comprobacion",
         "/api/v1/contabilidad/balance-comprobacion/export?"),
        ("ADMINISTRADOR", "Genera el balance general", "balance-general",
         "/api/v1/contabilidad/balance-general/export?"),
        ("ADMINISTRADOR", "Genera el estado de resultados", "estado-resultados",
         "/api/v1/contabilidad/estado-resultados/export?"),
    ],
)
def test_reportes_disponibles_generan_enlace_pdf(responder, rol, mensaje, tipo, prefijo):
    salida = responder(rol, mensaje)

    assert salida["intent"] == "reporte"
    assert salida["reporte"]["tipo"] == tipo
    assert salida["reporte"]["url"].startswith(prefijo)
    assert "formato=pdf" in salida["reporte"]["url"]
    assert salida["reporte"]["filename"].endswith(".pdf")


def test_reporte_generico_lista_opciones_sin_enlace(responder):
    salida = responder("ADMINISTRADOR", "Necesito un reporte")

    assert salida["intent"] == "reporte"
    assert salida["reporte"] is None
    for nombre in ("Reporte de cartera", "Reporte de morosidad",
                   "Reporte de captaciones", "Estado de resultados"):
        assert nombre in salida["respuesta"]
    assert salida["sugerencias"]


def test_oficial_de_credito_no_puede_generar_estados_contables(responder):
    salida = responder("OFICIAL_CREDITO", "Genera el balance general en PDF")

    assert salida["reporte"] is None
    assert "no tiene acceso" in salida["respuesta"].lower()
    assert "Reporte de cartera" in salida["respuesta"]


def test_superadmin_sin_cooperativa_no_genera_reportes(responder):
    salida = responder("SUPERADMIN", "Genera el reporte de cartera")

    assert salida["reporte"] is None
    assert "personal de una cooperativa" in salida["respuesta"].lower()


def test_socio_no_genera_reportes_financieros(responder):
    salida = responder("SOCIO", "Genera el reporte de cartera")

    assert salida["intent"] == "reporte"
    assert salida["reporte"] is None
    assert "socio" in salida["respuesta"].lower()


def test_libro_mayor_requiere_cuenta_y_no_genera_enlace(responder):
    salida = responder("ADMINISTRADOR", "Genera el libro mayor en PDF")

    assert salida["reporte"] is None
    assert "cuenta contable" in salida["respuesta"].lower()


def test_pregunta_normal_de_mora_sigue_siendo_consulta(responder):
    salida = responder("ADMINISTRADOR", "¿Cómo está la mora?")

    assert salida["intent"] == "mora"
    assert salida["reporte"] is None


def test_solicitud_de_credito_no_se_confunde_con_un_reporte(responder):
    salida = responder("OFICIAL_CREDITO", "¿Cómo puedo generar una solicitud de crédito?")

    assert salida["intent"] != "reporte"
    assert salida["reporte"] is None


def test_descarga_de_comprobante_no_se_confunde_con_un_reporte(responder):
    salida = responder("SOCIO", "Quiero descargar mi comprobante en PDF")

    assert salida["intent"] != "reporte"
    assert salida["reporte"] is None


def test_socio_no_recibe_sugerencias_de_reportes(responder):
    salida = responder("SOCIO", "Genera el reporte de cartera")

    assert all("reporte" not in sugerencia.lower() for sugerencia in salida["sugerencias"])


def test_intencion_de_reporte_no_invoca_al_llm(responder, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("LLM_MODEL", "modelo-de-prueba")

    def fallar(*_args, **_kwargs):
        raise AssertionError("El LLM no debe usarse para generar enlaces de reportes")

    monkeypatch.setattr(chatbot.llm, "generar_texto", fallar)

    salida = responder("ADMINISTRADOR", "Genera el reporte de morosidad en PDF")

    assert salida["fuente"] == "PLANTILLA"
    assert salida["reporte"]["tipo"] == "mora"


def test_endpoint_chat_acepta_historial_enviado_por_clientes(responder):
    payload = ChatRequest(
        mensaje="Genera el reporte de morosidad en PDF",
        historial=[{"role": "user", "content": "Hola"}],
    )
    usuario = SimpleNamespace(
        id=1,
        cooperativa_id=1,
        nombre="Usuario de prueba",
        rol=SimpleNamespace(nombre="ADMINISTRADOR"),
    )

    salida = chat(payload, usuario, None)

    assert salida["intent"] == "reporte"
    assert salida["reporte"]["tipo"] == "mora"
