"""Endpoints del módulo Socios — KYC (registro e identidad de miembros)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, require_admin
from app.core.bitacora import registrar_accion
from app.models.models import Socio, Usuario
from app.schemas.schemas import SocioCreate, SocioOut, SocioRegistroResponse

router = APIRouter()


@router.post(
    "/registro",
    response_model=SocioRegistroResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nuevo socio (KYC)",
)
def registrar_socio(
    body: SocioCreate,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Registra un nuevo Socio en el sistema (proceso KYC).
    - Verifica que la CI no esté duplicada.
    - Persiste el socio en la tabla `socio`.
    - Registra automáticamente la acción en la `bitacora`.
    - Solo accesible para SUPERADMIN y ADMINISTRADOR.
    """
    # Verificar CI única
    existente = db.execute(
        select(Socio).where(Socio.ci == body.ci)
    ).scalar_one_or_none()

    if existente:
        # Igualmente registramos el intento en bitácora
        bit = registrar_accion(
            db,
            accion="CREAR",
            modulo="SOCIO",
            usuario_id=admin.id,
            descripcion=f"Intento duplicado — CI {body.ci} ya existe (id={existente.id})",
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un socio con la CI '{body.ci}'",
        )

    # Crear el socio
    nuevo_socio = Socio(
        ci=body.ci,
        nombre=body.nombre,
        apellido=body.apellido,
        direccion=body.direccion,
        telefono=body.telefono,
        correo=str(body.correo) if body.correo else None,
        estado="ACTIVO",
    )
    db.add(nuevo_socio)
    db.flush()  # Obtener el id sin commitear

    # Registrar en bitácora (mismo transaction)
    bit = registrar_accion(
        db,
        accion="CREAR",
        modulo="SOCIO",
        usuario_id=admin.id,
        descripcion=(
            f"Registro KYC — {body.nombre} {body.apellido} "
            f"(CI: {body.ci}) — registrado por {admin.nombre}"
        ),
        request=request,
    )
    db.flush()

    db.commit()
    db.refresh(nuevo_socio)

    return SocioRegistroResponse(
        socio=SocioOut.model_validate(nuevo_socio),
        bitacora_id=bit.id,
    )


@router.get(
    "/",
    response_model=list[SocioOut],
    summary="Listar socios",
)
def listar_socios(
    _admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
    limite: int = 50,
    offset: int = 0,
):
    """Lista todos los socios registrados, del más reciente al más antiguo."""
    socios = db.execute(
        select(Socio)
        .order_by(Socio.fecha_registro.desc(), Socio.id.desc())
        .limit(limite)
        .offset(offset)
    ).scalars().all()
    return socios
