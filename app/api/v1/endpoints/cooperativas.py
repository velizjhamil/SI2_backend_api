from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.deps import require_superadmin
from app.db.session import get_db
from app.models.models import Cooperativa, Usuario
from app.schemas.schemas import (
    CooperativaCreate,
    CooperativaOut,
    CooperativaUpdate,
)

router = APIRouter(dependencies=[Depends(require_superadmin)])


def _get_cooperativa_or_404(db: Session, cooperativa_id: int) -> Cooperativa:
    cooperativa = db.get(Cooperativa, cooperativa_id)
    if cooperativa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cooperativa no encontrada",
        )
    return cooperativa


def _ensure_nit_disponible(
    db: Session, nit: str | None, exclude_id: int | None = None
) -> None:
    if not nit:
        return
    query = select(Cooperativa).where(Cooperativa.nit == nit)
    if exclude_id is not None:
        query = query.where(Cooperativa.id != exclude_id)
    existente = db.execute(query).scalar_one_or_none()
    if existente is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cooperativa registrada con ese NIT",
        )


@router.get("", response_model=list[CooperativaOut])
def listar_cooperativas(
    estado: str | None = Query(None, pattern="^(ACTIVO|INACTIVO)$"),
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    query = select(Cooperativa).order_by(Cooperativa.id)
    if estado:
        query = query.where(Cooperativa.estado == estado)
    return list(db.execute(query).scalars().all())


@router.post("", response_model=CooperativaOut, status_code=status.HTTP_201_CREATED)
def crear_cooperativa(
    payload: CooperativaCreate,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    _ensure_nit_disponible(db, payload.nit)
    cooperativa = Cooperativa(**payload.model_dump())
    db.add(cooperativa)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cooperativa registrada con ese NIT",
        )
    db.refresh(cooperativa)
    return cooperativa


@router.get("/{cooperativa_id}", response_model=CooperativaOut)
def obtener_cooperativa(
    cooperativa_id: int,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    return _get_cooperativa_or_404(db, cooperativa_id)


@router.put("/{cooperativa_id}", response_model=CooperativaOut)
def actualizar_cooperativa(
    cooperativa_id: int,
    payload: CooperativaUpdate,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    cooperativa = _get_cooperativa_or_404(db, cooperativa_id)
    cambios = payload.model_dump(exclude_unset=True)
    if "nit" in cambios and cambios["nit"] != cooperativa.nit:
        _ensure_nit_disponible(db, cambios["nit"], exclude_id=cooperativa.id)
    for campo, valor in cambios.items():
        setattr(cooperativa, campo, valor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cooperativa registrada con ese NIT",
        )
    db.refresh(cooperativa)
    return cooperativa


@router.delete("/{cooperativa_id}", response_model=CooperativaOut)
def desactivar_cooperativa(
    cooperativa_id: int,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Baja lógica: marca la cooperativa como INACTIVA sin eliminar datos."""
    cooperativa = _get_cooperativa_or_404(db, cooperativa_id)
    if cooperativa.estado == "ACTIVO":
        cooperativa.estado = "INACTIVO"
        cooperativa.fecha_baja = func.now()
        db.commit()
        db.refresh(cooperativa)
    return cooperativa


@router.post("/{cooperativa_id}/reactivar", response_model=CooperativaOut)
def reactivar_cooperativa(
    cooperativa_id: int,
    _: Usuario = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    cooperativa = _get_cooperativa_or_404(db, cooperativa_id)
    if cooperativa.estado == "INACTIVO":
        cooperativa.estado = "ACTIVO"
        cooperativa.fecha_baja = None
        db.commit()
        db.refresh(cooperativa)
    return cooperativa
