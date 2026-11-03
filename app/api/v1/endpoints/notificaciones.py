"""Gestionar notificaciones: el personal envía avisos a socios y cada socio los lee en su bandeja."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_db
from app.core.bitacora import registrar_accion
from app.models.models import Notificacion, NotificacionDestinatario, Usuario
from app.services.notificaciones import (
    DESTINOS, DIAS_POR_VENCER, TIPOS, buscar_socios, contar_segmentos, personalizar, resolver_destinatarios,
)

router = APIRouter()
ROLES_ENVIO = {"ADMINISTRADOR", "CAJERO"}


def _tenant_envio(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES_ENVIO:
        raise HTTPException(403, "Operación reservada a ADMINISTRADOR o CAJERO")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


class NotificacionIn(BaseModel):
    tipo: str
    titulo: str = Field(..., min_length=3, max_length=120)
    mensaje: str = Field(..., min_length=3, max_length=2000)
    destino: str
    socio_ids: list[int] = Field(default_factory=list, max_length=200)
    dias: int = Field(DIAS_POR_VENCER, ge=1, le=60)

    @field_validator("tipo")
    @classmethod
    def _tipo(cls, value):
        if value not in TIPOS:
            raise ValueError("El tipo de notificación no es válido")
        return value

    @field_validator("destino")
    @classmethod
    def _destino(cls, value):
        if value not in DESTINOS:
            raise ValueError("El destino de la notificación no es válido")
        return value

    @field_validator("titulo", "mensaje")
    @classmethod
    def _sin_espacios(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Debe tener al menos 3 caracteres")
        return value

    @model_validator(mode="after")
    def _socios(self):
        if self.destino == "SOCIO" and not self.socio_ids:
            raise ValueError("Selecciona al menos un socio")
        return self


# ---------------------------------------------------------------- personal

@router.get("/segmentos")
def segmentos(dias: int = Query(DIAS_POR_VENCER, ge=1, le=60),
              user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cuántos socios recibirían la notificación en cada grupo."""
    return {"dias": dias, "segmentos": contar_segmentos(db, _tenant_envio(user), dias)}


@router.get("/socios")
def socios(q: str = Query(..., min_length=2, max_length=80),
           user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Busca socios de la cooperativa por nombre, apellido o C.I."""
    return [dict(row) for row in buscar_socios(db, _tenant_envio(user), q)]


@router.post("", status_code=status.HTTP_201_CREATED)
def enviar(body: NotificacionIn, request: Request,
           user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant_envio(user)
    destinatarios, omitidos = resolver_destinatarios(db, tenant, body.destino, body.socio_ids, body.dias)
    if not destinatarios:
        raise HTTPException(422, "Ningún socio con cuenta activa coincide con los destinatarios elegidos")
    cooperativa = user.cooperativa.nombre if user.cooperativa else None
    notificacion = Notificacion(cooperativa_id=tenant, tipo=body.tipo, titulo=body.titulo, mensaje=body.mensaje,
                                destino=body.destino, usuario_id=user.id,
                                total_destinatarios=len(destinatarios))
    notificacion.destinatarios = [
        NotificacionDestinatario(socio_id=fila["socio_id"], usuario_id=fila["usuario_id"],
                                 mensaje=personalizar(body.mensaje, fila, cooperativa))
        for fila in destinatarios
    ]
    db.add(notificacion)
    db.flush()
    registrar_accion(db, accion="ENVIAR_NOTIFICACION", modulo="NOTIFICACION", usuario_id=user.id,
                     cooperativa_id=tenant, request=request,
                     descripcion=f"{body.tipo} «{body.titulo}» a {len(destinatarios)} socio(s) ({body.destino})")
    db.commit()
    return {"id": notificacion.id, "total_destinatarios": len(destinatarios), "omitidos_sin_cuenta": omitidos}


@router.get("")
def historial(limit: int = Query(50, ge=1, le=200),
              user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Notificaciones enviadas por la cooperativa, con cuántos socios ya las leyeron."""
    tenant = _tenant_envio(user)
    leidas = (select(NotificacionDestinatario.notificacion_id,
                     func.count(NotificacionDestinatario.leida_en).label("leidas"))
              .group_by(NotificacionDestinatario.notificacion_id).subquery())
    rows = db.execute(
        select(Notificacion, Usuario.nombre, func.coalesce(leidas.c.leidas, 0))
        .join(Usuario, Usuario.id == Notificacion.usuario_id)
        .outerjoin(leidas, leidas.c.notificacion_id == Notificacion.id)
        .where(Notificacion.cooperativa_id == tenant)
        .order_by(Notificacion.fecha_creacion.desc(), Notificacion.id.desc()).limit(limit)
    ).all()
    return [{"id": n.id, "tipo": n.tipo, "titulo": n.titulo, "mensaje": n.mensaje, "destino": n.destino,
             "total_destinatarios": n.total_destinatarios, "leidas": leidas_count,
             "enviado_por": nombre, "fecha_creacion": n.fecha_creacion}
            for n, nombre, leidas_count in rows]


# ---------------------------------------------------------------- bandeja del socio

def _bandeja(user: Usuario):
    return select(NotificacionDestinatario).where(NotificacionDestinatario.usuario_id == user.id)


@router.get("/mias")
def mis_notificaciones(limit: int = Query(50, ge=1, le=200),
                       user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(NotificacionDestinatario, Notificacion)
        .join(Notificacion, Notificacion.id == NotificacionDestinatario.notificacion_id)
        .where(NotificacionDestinatario.usuario_id == user.id)
        .order_by(Notificacion.fecha_creacion.desc(), NotificacionDestinatario.id.desc()).limit(limit)
    ).all()
    no_leidas = db.execute(select(func.count()).select_from(
        _bandeja(user).where(NotificacionDestinatario.leida_en.is_(None)).subquery())).scalar_one()
    return {"no_leidas": no_leidas,
            "items": [{"id": d.id, "tipo": n.tipo, "titulo": n.titulo, "mensaje": d.mensaje,
                       "fecha": n.fecha_creacion, "leida": d.leida_en is not None}
                      for d, n in rows]}


@router.get("/mias/no-leidas")
def no_leidas(user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.execute(select(func.count()).select_from(
        _bandeja(user).where(NotificacionDestinatario.leida_en.is_(None)).subquery())).scalar_one()
    return {"no_leidas": count}


@router.post("/mias/{destinatario_id}/leer")
def marcar_leida(destinatario_id: int, user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    fila = db.execute(_bandeja(user).where(NotificacionDestinatario.id == destinatario_id)).scalar_one_or_none()
    if fila is None:
        raise HTTPException(404, "Notificación no encontrada")
    if fila.leida_en is None:
        fila.leida_en = datetime.now(timezone.utc)
        db.commit()
    return {"id": fila.id, "leida": True}


@router.post("/mias/leer-todas")
def marcar_todas(user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    result = db.execute(update(NotificacionDestinatario)
                        .where(NotificacionDestinatario.usuario_id == user.id,
                               NotificacionDestinatario.leida_en.is_(None))
                        .values(leida_en=datetime.now(timezone.utc)))
    db.commit()
    return {"marcadas": result.rowcount}
