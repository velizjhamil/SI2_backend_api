from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.models import Rol, Usuario
from app.schemas.schemas import (
    LoginRequest,
    LogoutResponse,
    TokenResponse,
    UserOut,
)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    usuario = db.execute(
        select(Usuario)
        .options(joinedload(Usuario.rol))
        .where(Usuario.correo == body.correo.lower())
    ).scalar_one_or_none()

    if usuario is None or not verify_password(body.contrasena, usuario.contrasena):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )
    if not usuario.esta_activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo o bloqueado",
        )

    token, expires_in = create_access_token(subject=usuario.id, role=usuario.rol.nombre)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/logout", response_model=LogoutResponse)
def logout(_: Usuario = Depends(get_current_user)):
    return LogoutResponse(message="Sesión cerrada correctamente")


@router.get("/me", response_model=UserOut)
def me(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.execute(
        select(Usuario)
        .options(joinedload(Usuario.rol).joinedload(Rol.permisos))
        .where(Usuario.id == usuario.id)
    ).unique().scalar_one()
    return user