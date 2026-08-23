from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.models import Usuario

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