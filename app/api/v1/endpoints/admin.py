"""Endpoints del módulo Admin: estadísticas del dashboard y bitácora de auditoría."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_db, require_admin
from app.models.models import Bitacora, Socio, Usuario
from app.schemas.schemas import AdminStatsOut, BitacoraOut

router = APIRouter()


@router.get("/stats", response_model=AdminStatsOut, summary="Estadísticas del dashboard")
def get_stats(
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Devuelve métricas de resumen reales para las tarjetas del Dashboard.
    Lee directamente de las tablas `socio` y `cuenta_ahorro`.
    """
    # Total socios (tabla socio real)
    total_socios: int = db.execute(
        select(func.count()).select_from(text("socio"))
    ).scalar_one()

    socios_activos: int = db.execute(
        select(func.count()).select_from(text("socio")).where(text("estado = 'ACTIVO'"))
    ).scalar_one()

    # Cuentas de ahorro activas
    cuentas_activas: int = db.execute(
        select(func.count()).select_from(text("cuenta_ahorro")).where(text("estado = 'ACTIVA'"))
    ).scalar_one()

    # Total usuarios del sistema
    total_usuarios: int = db.execute(
        select(func.count(Usuario.id))
    ).scalar_one()

    return AdminStatsOut(
        total_socios=total_socios,
        socios_activos=socios_activos,
        cuentas_activas=cuentas_activas,
        total_usuarios=total_usuarios,
    )


# Módulos válidos según los datos reales de la BD
MODULOS_VALIDOS = ["CAJA", "CONTABILIDAD", "CREDITO", "DPF", "REPORTE",
                   "ROL", "TRANSACCION", "USUARIO", "SOCIO"]


@router.get(
    "/bitacora",
    response_model=list[BitacoraOut],
    summary="Bitácora de auditoría",
)
def get_bitacora(
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    modulo: Annotated[str | None, Query()] = None,
):
    """
    Devuelve los registros de la bitácora, del más reciente al más antiguo.
    SUPERADMIN: ve todos los registros.
    ADMINISTRADOR: ve los registros de usuarios de su cooperativa.
    """
    q = (
        select(Bitacora)
        .options(joinedload(Bitacora.usuario))
        .order_by(Bitacora.fecha_hora.desc())
        .limit(limite)
        .offset(offset)
    )

    if modulo and modulo.upper() in MODULOS_VALIDOS:
        q = q.where(Bitacora.modulo == modulo.upper())

    # ADMINISTRADOR solo ve registros de usuarios de su cooperativa
    es_superadmin = admin.rol.nombre == "SUPERADMIN"
    if not es_superadmin and admin.cooperativa_id:
        subq = select(Usuario.id).where(Usuario.cooperativa_id == admin.cooperativa_id)
        q = q.where(Bitacora.usuario_id.in_(subq))

    registros = db.execute(q).unique().scalars().all()

    result = []
    for r in registros:
        item = BitacoraOut.model_validate(r)
        if r.usuario:
            item.usuario_nombre = r.usuario.nombre
        result.append(item)

    return result


@router.get(
    "/modulos",
    response_model=list[str],
    summary="Lista de módulos disponibles en la bitácora",
)
def get_modulos(
    _admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Devuelve los módulos que tienen registros en la bitácora (dinámico)."""
    rows = db.execute(
        select(Bitacora.modulo).distinct().order_by(Bitacora.modulo)
    ).scalars().all()
    return list(rows)
