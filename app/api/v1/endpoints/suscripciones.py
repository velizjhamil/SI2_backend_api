"""Endpoints de suscripciones institucionales (SaaS).

- El SUPERADMIN gestiona el catálogo de planes y asigna planes a cooperativas.
- Un ADMINISTRADOR puede consultar la suscripción de su propia cooperativa.
"""

from datetime import date

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import require_admin, require_superadmin
from app.db.session import get_db
from app.models.models import Cooperativa, PlanSuscripcion, Suscripcion, Usuario
from app.schemas.schemas import (
    PlanSuscripcionOut,
    SuscripcionCreate,
    SuscripcionOut,
    SuscripcionRenovar,
    SuscripcionUpdate,
)
from app.services.suscripciones import ensure_suscripcion, get_suscripcion

router = APIRouter()


@router.get("/planes", response_model=list[PlanSuscripcionOut])
def listar_planes(
    _: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Catálogo de planes de suscripción disponibles."""
    planes = db.execute(
        select(PlanSuscripcion).order_by(PlanSuscripcion.precio_mensual)
    ).scalars().all()
    return list(planes)


@router.get("/mi-suscripcion", response_model=SuscripcionOut)
def mi_suscripcion(
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Suscripción de la cooperativa del usuario autenticado.

    Si la cooperativa aún no tuviera suscripción (p. ej. creada justo antes de
    esta consulta), se le asigna el plan por defecto de forma transparente.
    """
    if usuario.cooperativa_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El usuario no está vinculado a una cooperativa",
        )

    suscripcion = get_suscripcion(db, usuario.cooperativa_id)
    if suscripcion is None:
        suscripcion = ensure_suscripcion(db, usuario.cooperativa_id)
        if suscripcion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No hay planes de suscripción configurados",
            )
        db.commit()
        db.refresh(suscripcion)

    return suscripcion


@router.get("/cooperativa/{cooperativa_id}", response_model=SuscripcionOut)
def suscripcion_de_cooperativa(
    cooperativa_id: int,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    suscripcion = get_suscripcion(db, cooperativa_id)
    if suscripcion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La cooperativa no tiene una suscripción asignada",
        )
    return suscripcion


@router.get("", response_model=list[SuscripcionOut])
def listar_suscripciones(
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Listado de suscripciones de todas las cooperativas (SUPERADMIN)."""
    suscripciones = db.execute(
        select(Suscripcion)
        .options(joinedload(Suscripcion.plan), joinedload(Suscripcion.cooperativa))
        .order_by(Suscripcion.cooperativa_id)
    ).unique().scalars().all()
    return list(suscripciones)


@router.post("", response_model=SuscripcionOut, status_code=status.HTTP_201_CREATED)
def crear_suscripcion(
    payload: SuscripcionCreate,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    cooperativa = db.get(Cooperativa, payload.cooperativa_id)
    if cooperativa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cooperativa no encontrada",
        )
    if get_suscripcion(db, payload.cooperativa_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La cooperativa ya tiene una suscripción asignada",
        )
    plan = db.get(PlanSuscripcion, payload.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de suscripción no encontrado",
        )

    suscripcion = Suscripcion(
        cooperativa_id=payload.cooperativa_id,
        plan_id=payload.plan_id,
        estado=payload.estado,
        fecha_inicio=payload.fecha_inicio or date.today(),
        fecha_fin=payload.fecha_fin,
    )
    db.add(suscripcion)
    db.commit()
    db.refresh(suscripcion)
    return suscripcion


@router.put("/{suscripcion_id}", response_model=SuscripcionOut)
def actualizar_suscripcion(
    suscripcion_id: int,
    payload: SuscripcionUpdate,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    suscripcion = db.get(Suscripcion, suscripcion_id)
    if suscripcion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suscripción no encontrada",
        )

    cambios = payload.model_dump(exclude_unset=True)
    if "plan_id" in cambios:
        plan = db.get(PlanSuscripcion, cambios["plan_id"])
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan de suscripción no encontrado",
            )
    for campo, valor in cambios.items():
        setattr(suscripcion, campo, valor)

    db.commit()
    db.refresh(suscripcion)
    return suscripcion


@router.post("/{suscripcion_id}/renovar", response_model=SuscripcionOut)
def renovar_suscripcion(
    suscripcion_id: int,
    payload: SuscripcionRenovar,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Extiende la vigencia de la suscripción `meses` a partir de hoy o del
    vencimiento actual (lo que sea posterior) y la reactiva."""
    suscripcion = db.get(Suscripcion, suscripcion_id)
    if suscripcion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suscripción no encontrada",
        )

    base = date.today()
    if suscripcion.fecha_fin is not None and suscripcion.fecha_fin > base:
        base = suscripcion.fecha_fin
    suscripcion.fecha_fin = base + relativedelta(months=payload.meses)
    suscripcion.estado = "ACTIVA"
    if suscripcion.fecha_inicio is None:
        suscripcion.fecha_inicio = date.today()

    db.commit()
    db.refresh(suscripcion)
    return suscripcion


@router.delete("/{suscripcion_id}", response_model=SuscripcionOut)
def cancelar_suscripcion(
    suscripcion_id: int,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Baja lógica: marca la suscripción como CANCELADA sin eliminar datos."""
    suscripcion = db.get(Suscripcion, suscripcion_id)
    if suscripcion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suscripción no encontrada",
        )
    suscripcion.estado = "CANCELADA"
    db.commit()
    db.refresh(suscripcion)
    return suscripcion
