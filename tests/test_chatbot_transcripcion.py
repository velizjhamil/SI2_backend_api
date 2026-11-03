"""Voice transcription endpoint for the chatbot (Groq provider mocked)."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import chatbot as chatbot_endpoint
from app.core.config import settings
from app.services import chatbot, transcripcion

CLAVE = "clave-secreta-de-prueba"
URL = "/chatbot/transcribir"


class _Respuesta:
    def __init__(self, cuerpo=None, *, status_code=200):
        self.cuerpo = cuerpo
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", transcripcion.GROQ_TRANSCRIPTIONS_URL)
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )

    def json(self):
        if self.cuerpo is None:
            raise ValueError("invalid json")
        return self.cuerpo


@pytest.fixture
def app():
    aplicacion = FastAPI()
    aplicacion.include_router(chatbot_endpoint.router, prefix="/chatbot")
    return aplicacion


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", CLAVE)
    app.dependency_overrides[chatbot_endpoint.get_chat_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


@pytest.fixture
def groq(monkeypatch):
    llamadas = []
    estado = SimpleNamespace(respuesta=_Respuesta({"text": " Genera el reporte de morosidad. "}), llamadas=llamadas)

    def post(url, **kwargs):
        llamadas.append((url, kwargs))
        if isinstance(estado.respuesta, Exception):
            raise estado.respuesta
        return estado.respuesta

    monkeypatch.setattr(transcripcion.httpx, "post", post)
    return estado


def _subir(client, contenido=b"audio", tipo="audio/webm;codecs=opus"):
    return client.post(URL, files={"audio": ("cualquiera.exe", contenido, tipo)})


def test_transcribe_audio_con_groq_y_devuelve_texto(client, groq):
    respuesta = _subir(client)

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"texto": "Genera el reporte de morosidad."}
    url, kwargs = groq.llamadas[0]
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert kwargs["headers"] == {"Authorization": f"Bearer {CLAVE}"}
    assert kwargs["data"] == {"model": settings.GROQ_TRANSCRIPTION_MODEL, "language": "es"}
    assert kwargs["files"] == {"file": ("consulta.webm", b"audio", "audio/webm")}


def test_transcripcion_requiere_autenticacion(app, groq, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", CLAVE)

    respuesta = _subir(TestClient(app))

    assert respuesta.status_code == 401
    assert groq.llamadas == []


def test_rechaza_tipo_de_archivo_no_soportado(client, groq):
    respuesta = _subir(client, tipo="application/pdf")

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "El tipo de audio no es válido."
    assert groq.llamadas == []


def test_rechaza_audio_vacio(client, groq):
    respuesta = _subir(client, contenido=b"")

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "El archivo de audio está vacío."
    assert groq.llamadas == []


def test_rechaza_audio_mayor_al_limite(client, groq, monkeypatch):
    monkeypatch.setattr(chatbot_endpoint, "MAX_AUDIO_BYTES", 4)

    respuesta = _subir(client, contenido=b"12345")

    assert respuesta.status_code == 413
    assert respuesta.json()["detail"] == "El archivo de audio supera el límite de 10 MB."
    assert groq.llamadas == []


def test_sin_clave_responde_503_sin_llamar_al_proveedor(client, groq, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")

    respuesta = _subir(client)

    assert respuesta.status_code == 503
    assert respuesta.json()["detail"] == "El dictado por voz no está configurado en este servidor."
    assert groq.llamadas == []


@pytest.mark.parametrize(
    "fallo",
    [
        _Respuesta(status_code=401),
        _Respuesta(None),
        _Respuesta({"text": 123}),
        _Respuesta(["sin objeto"]),
        httpx.ConnectTimeout("sin conexión"),
    ],
)
def test_fallo_del_proveedor_responde_502_sin_exponer_la_clave(client, groq, fallo):
    groq.respuesta = fallo

    respuesta = _subir(client)

    assert respuesta.status_code == 502
    assert respuesta.json()["detail"] == "No se pudo transcribir el audio. Inténtalo de nuevo."
    assert CLAVE not in respuesta.text


def test_texto_transcrito_genera_reporte_con_el_flujo_del_chatbot(monkeypatch):
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: False)
    monkeypatch.setattr(chatbot, "contexto_cooperativa", lambda db, usuario: {})
    usuario = SimpleNamespace(id=1, cooperativa_id=1, nombre="Ana", rol=SimpleNamespace(nombre="ADMINISTRADOR"))

    salida = chatbot.responder(None, usuario, "Genera el reporte de morosidad.")

    assert salida["intent"] == "reporte"
    assert salida["reporte"]["tipo"] == "mora"
