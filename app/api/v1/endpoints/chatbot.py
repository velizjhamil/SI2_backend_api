"""Endpoint del asistente virtual (chatbot) de la cooperativa.

Disponible para cualquier usuario autenticado. El contenido de las respuestas
se adapta al rol: el personal recibe métricas de la cooperativa y el socio
recibe información personal (saldos, créditos, precalificación).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.db.session import get_db
from app.models.models import Usuario
from app.schemas.schemas import ChatRequest, ChatResponse
from app.services.chatbot import responder

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Responde una consulta en lenguaje natural sobre datos reales del sistema."""
    return responder(db, usuario, payload.mensaje)
