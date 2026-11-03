"""Transcripción de audio a texto con Groq para el dictado del chatbot."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class TranscripcionNoConfigurada(RuntimeError):
    """GROQ_API_KEY no está configurada en el servidor."""


class TranscripcionError(RuntimeError):
    """El proveedor falló o devolvió una respuesta inválida."""


def transcribir_audio(audio: bytes, filename: str, content_type: str) -> str:
    if not settings.GROQ_API_KEY:
        raise TranscripcionNoConfigurada

    try:
        response = httpx.post(
            GROQ_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            data={"model": settings.GROQ_TRANSCRIPTION_MODEL, "language": "es"},
            files={"file": (filename, audio, content_type)},
            timeout=30,
        )
        response.raise_for_status()
        resultado = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Fallo al transcribir el audio con Groq: %s", type(exc).__name__)
        raise TranscripcionError from exc

    texto = resultado.get("text") if isinstance(resultado, dict) else None
    if not isinstance(texto, str):
        logger.warning("Groq devolvió una transcripción inválida")
        raise TranscripcionError
    return texto.strip()
