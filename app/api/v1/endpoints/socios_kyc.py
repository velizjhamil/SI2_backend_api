"""Endpoints del módulo Socios — KYC (registro e identidad de miembros)."""

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, require_admin, require_operaciones
from app.core.bitacora import registrar_accion
from app.models.models import PlanSuscripcion, Socio, Suscripcion, Usuario
from app.schemas.schemas import SocioCreate, SocioOut, SocioRegistroResponse, SocioUpdate

router = APIRouter()
LIMITE_SOCIOS_BASICO = 500


def _validar_limite_socios(db: Session, admin: Usuario) -> None:
    if admin.cooperativa_id is None or admin.rol.nombre == "SUPERADMIN":
        return
    suscripcion = db.execute(
        select(Suscripcion)
        .where(Suscripcion.cooperativa_id == admin.cooperativa_id)
        .with_for_update()
    ).scalar_one_or_none()
    plan = db.get(PlanSuscripcion, suscripcion.plan_id) if suscripcion else None
    if plan is None or plan.codigo != "BASICO":
        return
    activos = db.execute(
        select(func.count(Socio.id)).where(
            Socio.cooperativa_id == admin.cooperativa_id,
            Socio.estado == "ACTIVO",
        )
    ).scalar_one()
    if activos >= LIMITE_SOCIOS_BASICO:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Se alcanzó el límite de {LIMITE_SOCIOS_BASICO} socios activos del plan {plan.nombre}.",
        )


def _socio_visible(admin: Usuario, socio: Socio) -> bool:
    return admin.rol.nombre == "SUPERADMIN" or socio.cooperativa_id == admin.cooperativa_id


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

    _validar_limite_socios(db, admin)

    # Crear el socio
    nuevo_socio = Socio(
        cooperativa_id=admin.cooperativa_id,
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


@router.put("/{socio_id}", response_model=SocioOut, summary="Actualizar socio")
def actualizar_socio(
    socio_id: int,
    body: SocioUpdate,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    socio = db.get(Socio, socio_id)
    if socio is None or not _socio_visible(admin, socio):
        raise HTTPException(status_code=404, detail="Socio no encontrado")
    cambios = body.model_dump(exclude_unset=True)
    if socio.estado != "ACTIVO" and cambios.get("estado") == "ACTIVO":
        _validar_limite_socios(db, admin)
    for campo, valor in cambios.items():
        setattr(socio, campo, valor)
    registrar_accion(db, accion="ACTUALIZAR", modulo="SOCIO", usuario_id=admin.id, descripcion=f"Socio actualizado: {socio.id}", request=request)
    db.commit()
    db.refresh(socio)
    return socio


@router.delete("/{socio_id}", response_model=SocioOut, summary="Dar de baja socio")
def desactivar_socio(
    socio_id: int,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    socio = db.get(Socio, socio_id)
    if socio is None or not _socio_visible(admin, socio):
        raise HTTPException(status_code=404, detail="Socio no encontrado")
    socio.estado = "INACTIVO"
    socio.fecha_baja = date.today()
    registrar_accion(db, accion="BAJA", modulo="SOCIO", usuario_id=admin.id, descripcion=f"Socio dado de baja: {socio.id}", request=request)
    db.commit()
    db.refresh(socio)
    return socio


@router.get(
    "/",
    response_model=list[SocioOut],
    summary="Listar socios",
)
def listar_socios(
    admin: Usuario = Depends(require_operaciones),
    db: Session = Depends(get_db),
    limite: int = 50,
    offset: int = 0,
    estado: str | None = None,
):
    """Lista todos los socios registrados, del más reciente al más antiguo."""
    query = select(Socio)
    if admin.rol.nombre != "SUPERADMIN":
        query = query.where(Socio.cooperativa_id == admin.cooperativa_id)
    if estado:
        query = query.where(Socio.estado == estado)
    socios = db.execute(
        query
        .order_by(Socio.fecha_registro.desc(), Socio.id.desc())
        .limit(limite)
        .offset(offset)
    ).scalars().all()
    return socios
