from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.models import PlanSuscripcion, Socio, Usuario
from app.services.suscripciones import (
    DETALLE_SUSCRIPCION_NO_VIGENTE,
    NOMBRES_MODULOS,
    get_suscripcion,
    usuario_tiene_suscripcion_vigente,
    usuario_tiene_modulo,
)

bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(Usuario).filter(Usuario.id == int(user_id)).first()
    if user is None or not user.esta_activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not usuario_tiene_suscripcion_vigente(db, user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=DETALLE_SUSCRIPCION_NO_VIGENTE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


ROL_SUPERADMIN = "SUPERADMIN"


def require_superadmin(usuario: Usuario = Depends(get_current_user)) -> Usuario:
    rol_nombre = usuario.rol.nombre if usuario.rol else None
    if rol_nombre != ROL_SUPERADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operación reservada al Super Administrador SaaS",
        )
    return usuario


ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROLES_ADMIN = {ROL_SUPERADMIN, ROL_ADMINISTRADOR}


def require_admin(usuario: Usuario = Depends(get_current_user)) -> Usuario:
    """Permite acceso a SUPERADMIN y ADMINISTRADOR de cooperativa."""
    rol_nombre = usuario.rol.nombre if usuario.rol else None
    if rol_nombre not in ROLES_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operación reservada a administradores",
        )
    return usuario


ROL_CAJERO = "CAJERO"
ROL_OFICIAL_CREDITO = "OFICIAL_CREDITO"
ROLES_OPERACIONES = ROLES_ADMIN | {ROL_CAJERO, ROL_OFICIAL_CREDITO}


def require_operaciones(usuario: Usuario = Depends(get_current_user)) -> Usuario:
    """Permite acceso a administradores y al personal de ventanilla (cajero/oficial de servicios)."""
    rol_nombre = usuario.rol.nombre if usuario.rol else None
    if rol_nombre not in ROLES_OPERACIONES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operación reservada al personal de ventanilla",
        )
    return usuario


def require_feature(modulo: str):
    """Build a reusable API dependency for a plan-restricted module."""
    def dependency(
        usuario: Usuario = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Usuario:
        if usuario_tiene_modulo(db, usuario, modulo):
            return usuario
        suscripcion = get_suscripcion(db, usuario.cooperativa_id)
        plan = db.get(PlanSuscripcion, suscripcion.plan_id) if suscripcion else None
        nombre_plan = plan.nombre if plan else "desconocido"
        nombre_modulo = NOMBRES_MODULOS.get(modulo, modulo)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"El módulo {nombre_modulo} no está incluido en el plan {nombre_plan}.",
        )
    return dependency


def get_current_socio(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
    _feature_access: Usuario = Depends(require_feature("banca_movil")),
) -> Socio:
    """Dependencia de autoservicio: resuelve el `Socio` propio del usuario autenticado.

    Distinta de `require_operaciones` — no otorga el acceso que otorgan los
    roles de staff. Un usuario sin `Socio` vinculado (p. ej. personal de
    ventanilla) o cuyo `Socio` no está `ACTIVO` recibe 403 sin detalle interno.
    La propiedad de recursos concretos (p. ej. cuentas) se valida aparte, en
    el `WHERE` del endpoint que la consume — no aquí.
    """
    socio = db.execute(
        select(Socio).where(Socio.usuario_id == usuario.id)
    ).scalar_one_or_none()
    if socio is None or socio.estado != "ACTIVO":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operación disponible solo para socios",
        )
    return socio
