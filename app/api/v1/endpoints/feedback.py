"""Feedback CRM: socios rate a service; staff read their cooperative's answers and statistics."""

from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_socio, get_current_user, get_db
from app.core.bitacora import registrar_accion
from app.models.models import EncuestaSatisfaccion, Socio, Usuario

router = APIRouter()
SERVICIOS = ("CREDITO", "AHORRO", "CAJA", "ATENCION", "APP_MOVIL", "OTRO")
ROLES_LECTURA = {"ADMINISTRADOR", "OFICIAL_CREDITO", "ASESOR_CREDITO", "CAJERO", "CONTADOR"}
ULTIMOS_COMENTARIOS = 10


class EncuestaIn(BaseModel):
    servicio: Literal["CREDITO", "AHORRO", "CAJA", "ATENCION", "APP_MOVIL", "OTRO"]
    calificacion: int = Field(..., ge=1, le=5)
    recomendaria: int | None = Field(None, ge=0, le=10)
    comentario: str | None = Field(None, max_length=1000)
    canal: Literal["WEB", "MOVIL"] = "WEB"

    @field_validator("comentario")
    @classmethod
    def _comentario(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None


def _out(row: EncuestaSatisfaccion, with_socio: bool = False) -> dict:
    data = {
        "id": row.id, "servicio": row.servicio, "calificacion": row.calificacion,
        "recomendaria": row.recomendaria, "comentario": row.comentario, "canal": row.canal,
        "fecha": row.fecha_creacion.isoformat(),
    }
    if with_socio:
        data["socio_id"] = row.socio_id
        data["socio"] = f"{row.socio.nombre} {row.socio.apellido}".strip()
    return data


def _tenant_lectura(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES_LECTURA:
        raise HTTPException(403, "Operación reservada al personal de la cooperativa")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _filtrar(db: Session, tenant: int, servicio: str | None, calificacion: int | None,
             desde: date | None, hasta: date | None) -> list[EncuestaSatisfaccion]:
    if desde and hasta and desde > hasta:
        raise HTTPException(422, "desde no puede ser posterior a hasta")
    query = (select(EncuestaSatisfaccion).options(joinedload(EncuestaSatisfaccion.socio))
             .where(EncuestaSatisfaccion.cooperativa_id == tenant))
    if servicio:
        query = query.where(EncuestaSatisfaccion.servicio == servicio)
    if calificacion:
        query = query.where(EncuestaSatisfaccion.calificacion == calificacion)
    if desde:
        query = query.where(EncuestaSatisfaccion.fecha_creacion >= desde)
    if hasta:
        query = query.where(EncuestaSatisfaccion.fecha_creacion < hasta + timedelta(days=1))
    query = query.order_by(EncuestaSatisfaccion.fecha_creacion.desc(), EncuestaSatisfaccion.id.desc())
    return list(db.execute(query).unique().scalars().all())


def _round1(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _avg(values: list[int]) -> float | None:
    return _round1(Decimal(sum(values)) / len(values)) if values else None


ServicioQ = Query(None, pattern="^(" + "|".join(SERVICIOS) + ")$")


# ---------------------------------------------------------------- socio

@router.post("", status_code=status.HTTP_201_CREATED)
def calificar(body: EncuestaIn, request: Request, socio: Socio = Depends(get_current_socio),
              db: Session = Depends(get_db)):
    row = EncuestaSatisfaccion(cooperativa_id=socio.cooperativa_id, socio_id=socio.id, servicio=body.servicio,
                               calificacion=body.calificacion, recomendaria=body.recomendaria,
                               comentario=body.comentario, canal=body.canal)
    db.add(row)
    db.flush()
    registrar_accion(db, accion="CALIFICAR_SERVICIO", modulo="FEEDBACK", usuario_id=socio.usuario_id,
                     cooperativa_id=socio.cooperativa_id, request=request,
                     descripcion=f"{body.servicio}: {body.calificacion}/5")
    db.commit()
    db.refresh(row)
    return _out(row)


@router.get("/mias")
def mis_calificaciones(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    rows = db.execute(
        select(EncuestaSatisfaccion).where(EncuestaSatisfaccion.socio_id == socio.id)
        .order_by(EncuestaSatisfaccion.fecha_creacion.desc(), EncuestaSatisfaccion.id.desc())
    ).scalars().all()
    return [_out(row) for row in rows]


# ---------------------------------------------------------------- personal

@router.get("")
def listar(servicio: str | None = ServicioQ, calificacion: int | None = Query(None, ge=1, le=5),
           desde: date | None = None, hasta: date | None = None, limit: int = Query(200, ge=1, le=1000),
           user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = _filtrar(db, _tenant_lectura(user), servicio, calificacion, desde, hasta)
    return [_out(row, with_socio=True) for row in rows[:limit]]


@router.get("/estadisticas")
def estadisticas(servicio: str | None = ServicioQ, calificacion: int | None = Query(None, ge=1, le=5),
                 desde: date | None = None, hasta: date | None = None,
                 user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = _filtrar(db, _tenant_lectura(user), servicio, calificacion, desde, hasta)
    ratings = [row.calificacion for row in rows]
    counts = Counter(ratings)

    nps_answers = [row.recomendaria for row in rows if row.recomendaria is not None]
    promoters = sum(1 for value in nps_answers if value >= 9)
    detractors = sum(1 for value in nps_answers if value <= 6)
    score = (_round1(Decimal(promoters - detractors) * 100 / len(nps_answers)) if nps_answers else None)

    by_service: dict[str, list[int]] = {}
    by_month: dict[str, list[int]] = {}
    for row in rows:
        by_service.setdefault(row.servicio, []).append(row.calificacion)
        by_month.setdefault(row.fecha_creacion.strftime("%Y-%m"), []).append(row.calificacion)

    return {
        "total": len(rows),
        "promedio": _avg(ratings),
        "distribucion": {str(value): counts.get(value, 0) for value in range(1, 6)},
        "nps": {"respuestas": len(nps_answers), "promotores": promoters,
                "pasivos": len(nps_answers) - promoters - detractors, "detractores": detractors, "score": score},
        "por_servicio": [{"servicio": name, "total": len(values), "promedio": _avg(values)}
                         for name, values in sorted(by_service.items())],
        "tendencia_mensual": [{"mes": month, "total": len(values), "promedio": _avg(values)}
                              for month, values in sorted(by_month.items())],
        "ultimos_comentarios": [_out(row, with_socio=True) for row in rows if row.comentario][:ULTIMOS_COMENTARIOS],
    }
