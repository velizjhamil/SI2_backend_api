"""Cliente genérico de LLM (opcional).

Usa las variables de entorno `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL`
(los mismos que el módulo de créditos). Si no está configurado o falla,
devuelve `None` para que el llamador use su respuesta determinista. Nunca
lanza excepciones hacia arriba.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def llm_configurado() -> bool:
    provider, key, model = _config()
    return provider in {"anthropic", "gemini"} and bool(key) and bool(model)


def _config() -> tuple[str | None, str | None, str | None]:
    return (
        os.getenv("LLM_PROVIDER"),
        os.getenv("LLM_API_KEY"),
        os.getenv("LLM_MODEL"),
    )


def generar_texto(system: str, prompt: str, *, max_tokens: int = 400, timeout: int = 15) -> str | None:
    provider, key, model = _config()
    if provider not in {"anthropic", "gemini"} or not key or not model:
        return None
    try:
        if provider == "anthropic":
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout,
            )
            response.raise_for_status()
            text_out = response.json()["content"][0]["text"]
        else:  # gemini
            response = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": key},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                },
                timeout=timeout,
            )
            response.raise_for_status()
            text_out = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        if isinstance(text_out, str) and text_out.strip():
            return text_out.strip()
        return None
    except Exception as exc:  # pragma: no cover - red externa
        logger.warning("Fallo al invocar el LLM (%s): %s", provider, exc)
        return None
