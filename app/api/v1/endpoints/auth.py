import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_user
from app.core.email import send_password_reset_email
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    decode_password_reset_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.models import Rol, Usuario
from app.schemas.schemas import (
    LoginRequest,
    LogoutResponse,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    TokenResponse,
    UserOut,
)
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


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

    token, expires_in = create_access_token(
        subject=usuario.id, role=usuario.rol.nombre, cooperativa_id=usuario.cooperativa_id
    )
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


@router.post("/password-reset/request", response_model=PasswordResetRequestResponse)
def request_password_reset(
    body: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Solicita el envío del correo de recuperación.

    Por seguridad siempre se responde con el mismo mensaje neutral, sin
    revelar si el correo existe. La respuesta indica además `delivered`
    (si el servidor SMTP aceptó el envío) y — sólo en modo desarrollo,
    cuando SMTP no está configurado — `debug_token` para poder probar el
    flujo end-to-end sin esperar al correo.
    """
    correo_norm = body.correo.strip().lower()
    usuario = db.execute(
        select(Usuario).where(Usuario.correo == correo_norm)
    ).scalar_one_or_none()

    neutral_message = (
        "Si el correo está registrado, enviaremos un enlace de "
        "recuperación en los próximos minutos."
    )

    if usuario is None or not usuario.esta_activo:
        # Correo inexistente o inactivo — respuesta neutra, sin token.
        return PasswordResetRequestResponse(
            message=neutral_message,
            delivered=settings.RESEND_API_KEY != "",
            debug_token=None,
        )

    token = create_password_reset_token(usuario.id)
    email_configured = bool(settings.RESEND_API_KEY)

    if not email_configured:
        # Modo "log only": no llega correo real. Devolvemos el token en la
        # respuesta solo en este caso, para que el equipo pueda probar.
        logger.warning(
            "PASSWORD RESET solicitados en modo sin RESEND_API_KEY. "
            "Usuario=%s token=%s",
            correo_norm, token,
        )
        return PasswordResetRequestResponse(
            message=neutral_message,
            delivered=False,
            debug_token=token,
        )

    # El envío se dispara en background: smtplib puede tardar/colgarse (ver
    # timeout en app/core/email.py) y no queremos bloquear la respuesta HTTP
    # esperando a Gmail. `delivered` aquí solo indica que quedó encolado, no
    # que Gmail ya lo entregó (los errores reales quedan en los logs).
    background_tasks.add_task(send_password_reset_email, correo_norm, token)
    return PasswordResetRequestResponse(
        message=neutral_message,
        delivered=True,
        debug_token=None,
    )


@router.post("/password-reset/confirm", response_model=MessageResponse)
def confirm_password_reset(
    body: PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    """Restablece la contraseña usando el token recibido por correo."""
    user_id = decode_password_reset_token(body.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace es inválido o ha expirado.",
        )

    try:
        usuario = db.get(Usuario, int(user_id))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace es inválido o ha expirado.",
        )

    if usuario is None or not usuario.esta_activo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo restablecer la contraseña.",
        )

    usuario.contrasena = hash_password(body.nueva_contrasena)
    db.add(usuario)
    db.commit()

    return MessageResponse(message="Contraseña actualizada correctamente.")