"""Endpoint del asistente virtual (chatbot) de la cooperativa.

Disponible para cualquier usuario autenticado. El contenido de las respuestas
se adapta al rol: el personal recibe métricas de la cooperativa y el socio
recibe información personal (saldos, créditos, precalificación).
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_socio, get_current_user, require_feature
from app.db.session import get_db
from app.models.models import Usuario
from app.schemas.schemas import ChatRequest, ChatResponse, TranscripcionResponse
from app.services.chatbot import responder
from app.services.transcripcion import (
    TranscripcionError,
    TranscripcionNoConfigurada,
    transcribir_audio,
)

router = APIRouter(dependencies=[Depends(require_feature("chatbot_ia"))])

MAX_AUDIO_BYTES = 10 * 1024 * 1024
# Groq identifica el formato por la extensión del archivo.
EXTENSIONES_AUDIO = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def get_chat_user(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Usuario:
    """Validate socio self-service through the shared authenticated dependency."""
    if usuario.rol and usuario.rol.nombre == "SOCIO":
        get_current_socio(usuario=usuario, db=db)
    return usuario


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    usuario: Usuario = Depends(get_chat_user),
    db: Session = Depends(get_db),
):
    """Responde una consulta en lenguaje natural sobre datos reales del sistema."""
    return responder(db, usuario, payload.mensaje, payload.historial)


@router.post("/transcribir", response_model=TranscripcionResponse)
def transcribir(
    audio: UploadFile = File(...),
    usuario: Usuario = Depends(get_chat_user),
):
    """Convierte una consulta dictada en texto; el cliente la envía luego a /chat."""
    content_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
    extension = EXTENSIONES_AUDIO.get(content_type)
    if extension is None:
        raise HTTPException(status_code=400, detail="El tipo de audio no es válido.")

    contenido = audio.file.read(MAX_AUDIO_BYTES + 1)
    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo de audio está vacío.")
    if len(contenido) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="El archivo de audio supera el límite de 10 MB.")

    try:
        return {"texto": transcribir_audio(contenido, f"consulta.{extension}", content_type)}
    except TranscripcionNoConfigurada as exc:
        raise HTTPException(
            status_code=503, detail="El dictado por voz no está configurado en este servidor."
        ) from exc
    except TranscripcionError as exc:
        raise HTTPException(
            status_code=502, detail="No se pudo transcribir el audio. Inténtalo de nuevo."
        ) from exc
