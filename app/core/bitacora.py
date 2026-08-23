"""Utilidades para registrar acciones en la Bitácora del sistema.

Estructura real de la tabla bitacora en la BD:
  id, usuario_id (NOT NULL), modulo, accion, descripcion, ip (inet), user_agent, fecha_hora
"""

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.models import Bitacora


def registrar_accion(
    db: Session,
    *,
    accion: str,
    modulo: str,
    usuario_id: int,
    descripcion: str | None = None,
    request: Request | None = None,
) -> Bitacora:
    """
    Registra una acción en la Bitácora de auditoría.

    Args:
        db:           Sesión de base de datos activa.
        accion:       Descripción breve de la acción (ej. "LOGIN_EXITOSO").
        modulo:       Módulo origen de la acción (ej. "USUARIO", "SOCIO", "CAJA").
        usuario_id:   ID del usuario que realizó la acción (obligatorio).
        descripcion:  Información adicional en texto libre.
        request:      Request de FastAPI para extraer IP y User-Agent.

    Returns:
        El registro Bitacora recién creado (ya agregado a la sesión, no commiteado).
    """
    ip = None
    user_agent = None

    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else request.client.host
        user_agent = request.headers.get("User-Agent")

    registro = Bitacora(
        accion=accion,
        modulo=modulo,
        usuario_id=usuario_id,
        descripcion=descripcion,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(registro)
    return registro
