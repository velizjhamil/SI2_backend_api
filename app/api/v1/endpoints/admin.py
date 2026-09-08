"""Endpoints del módulo Admin: estadísticas del dashboard y bitácora de auditoría."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_db, require_admin
from app.core.bitacora import registrar_accion
from app.core.security import hash_password
from app.models.models import Bitacora, Cooperativa, CuentaAhorro, Permiso, Rol, Socio, Usuario
from app.schemas.schemas import (
    AdminStatsOut,
    BitacoraOut,
    PermisoOut,
    RolCreate,
    RolOut,
    RolUpdate,
    UserOut,
    UsuarioCreate,
    UsuarioUpdate,
)

router = APIRouter()


def _usuario_visible(admin: Usuario, usuario: Usuario) -> bool:
    return admin.rol.nombre == "SUPERADMIN" or usuario.cooperativa_id == admin.cooperativa_id


def _get_usuario_visible(db: Session, admin: Usuario, usuario_id: int) -> Usuario:
    usuario = db.execute(
        select(Usuario).options(joinedload(Usuario.rol)).where(Usuario.id == usuario_id)
    ).scalar_one_or_none()
    if usuario is None or not _usuario_visible(admin, usuario):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return usuario


def _validar_rol_y_permisos(db: Session, nombre: str, permiso_ids: list[int] | None = None) -> Rol:
    rol = db.execute(select(Rol).where(Rol.nombre == nombre)).scalar_one_or_none()
    if rol is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El rol indicado no existe")
    if permiso_ids is not None:
        permisos = list(db.execute(select(Permiso).where(Permiso.id.in_(permiso_ids))).scalars())
        if len(permisos) != len(set(permiso_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uno o más permisos no existen")
        rol.permisos = permisos
    return rol


@router.get("/usuarios", response_model=list[UserOut], summary="Listar usuarios")
def listar_usuarios(
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
    estado: str | None = Query(None, pattern="^(ACTIVO|INACTIVO|BLOQUEADO)$"),
):
    query = select(Usuario).options(joinedload(Usuario.rol)).order_by(Usuario.id.desc())
    if admin.rol.nombre != "SUPERADMIN":
        query = query.where(Usuario.cooperativa_id == admin.cooperativa_id)
    if estado:
        query = query.where(Usuario.estado == estado)
    return list(db.execute(query).unique().scalars())


@router.get("/usuarios/{usuario_id}", response_model=UserOut, summary="Consultar usuario")
def obtener_usuario(
    usuario_id: int,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _get_usuario_visible(db, admin, usuario_id)


@router.put("/usuarios/{usuario_id}", response_model=UserOut, summary="Actualizar usuario")
def actualizar_usuario(
    usuario_id: int,
    body: UsuarioUpdate,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    usuario = _get_usuario_visible(db, admin, usuario_id)
    cambios = body.model_dump(exclude_unset=True)
    if usuario.id == admin.id and cambios.get("estado") in {"INACTIVO", "BLOQUEADO"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No puede desactivar su propia cuenta")
    if usuario.rol.nombre == "SUPERADMIN" and admin.rol.nombre != "SUPERADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puede modificar un SUPERADMIN")

    if "correo" in cambios:
        correo = str(cambios["correo"]).lower()
        existente = db.execute(select(Usuario).where(Usuario.correo == correo, Usuario.id != usuario.id)).scalar_one_or_none()
        if existente:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un usuario con ese correo")
        cambios["correo"] = correo
    if "contrasena" in cambios:
        cambios["contrasena"] = hash_password(cambios["contrasena"])
    if "rol" in cambios:
        rol_nombre = cambios.pop("rol").strip().upper()
        if rol_nombre == "SUPERADMIN" and admin.rol.nombre != "SUPERADMIN":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puede asignar SUPERADMIN")
        usuario.rol = _validar_rol_y_permisos(db, rol_nombre)
    for campo, valor in cambios.items():
        setattr(usuario, campo, valor)
    registrar_accion(db, accion="ACTUALIZAR", modulo="USUARIO", usuario_id=admin.id, descripcion=f"Usuario actualizado: {usuario.correo}", request=request)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No se pudo actualizar el usuario")
    db.refresh(usuario)
    return usuario


@router.delete("/usuarios/{usuario_id}", response_model=UserOut, summary="Desactivar usuario")
def desactivar_usuario(
    usuario_id: int,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    usuario = _get_usuario_visible(db, admin, usuario_id)
    if usuario.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No puede desactivar su propia cuenta")
    if usuario.rol.nombre == "SUPERADMIN" and admin.rol.nombre != "SUPERADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puede desactivar un SUPERADMIN")
    usuario.estado = "INACTIVO"
    registrar_accion(db, accion="DESACTIVAR", modulo="USUARIO", usuario_id=admin.id, descripcion=f"Usuario desactivado: {usuario.correo}", request=request)
    db.commit()
    db.refresh(usuario)
    return usuario


@router.post("/usuarios/{usuario_id}/reactivar", response_model=UserOut, summary="Reactivar usuario")
def reactivar_usuario(
    usuario_id: int,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    usuario = _get_usuario_visible(db, admin, usuario_id)
    usuario.estado = "ACTIVO"
    registrar_accion(db, accion="REACTIVAR", modulo="USUARIO", usuario_id=admin.id, descripcion=f"Usuario reactivado: {usuario.correo}", request=request)
    db.commit()
    db.refresh(usuario)
    return usuario


@router.get("/roles", response_model=list[RolOut], summary="Listar roles")
def listar_roles(_: Usuario = Depends(require_admin), db: Session = Depends(get_db)):
    return list(db.execute(select(Rol).options(joinedload(Rol.permisos)).order_by(Rol.nombre)).unique().scalars())


@router.get("/permisos", response_model=list[PermisoOut], summary="Listar permisos")
def listar_permisos(_: Usuario = Depends(require_admin), db: Session = Depends(get_db)):
    return list(db.execute(select(Permiso).order_by(Permiso.nombre)).scalars())


@router.post("/roles", response_model=RolOut, status_code=status.HTTP_201_CREATED, summary="Crear rol")
def crear_rol(body: RolCreate, request: Request, admin: Usuario = Depends(require_admin), db: Session = Depends(get_db)):
    if admin.rol.nombre != "SUPERADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo SUPERADMIN puede administrar roles")
    nombre = body.nombre.strip().upper()
    if db.execute(select(Rol).where(Rol.nombre == nombre)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un rol con ese nombre")
    rol = Rol(nombre=nombre, descripcion=body.descripcion)
    db.add(rol)
    db.flush()
    _validar_rol_y_permisos(db, nombre, body.permiso_ids)
    registrar_accion(db, accion="CREAR", modulo="ROL", usuario_id=admin.id, descripcion=f"Rol creado: {nombre}", request=request)
    db.commit()
    db.refresh(rol)
    return rol


@router.put("/roles/{rol_id}", response_model=RolOut, summary="Actualizar rol")
def actualizar_rol(rol_id: int, body: RolUpdate, request: Request, admin: Usuario = Depends(require_admin), db: Session = Depends(get_db)):
    if admin.rol.nombre != "SUPERADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo SUPERADMIN puede administrar roles")
    rol = db.execute(select(Rol).options(joinedload(Rol.permisos)).where(Rol.id == rol_id)).unique().scalar_one_or_none()
    if rol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado")
    cambios = body.model_dump(exclude_unset=True)
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip().upper()
        existente = db.execute(select(Rol).where(Rol.nombre == cambios["nombre"], Rol.id != rol.id)).scalar_one_or_none()
        if existente:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un rol con ese nombre")
    permiso_ids = cambios.pop("permiso_ids", None)
    for campo, valor in cambios.items():
        setattr(rol, campo, valor)
    if permiso_ids is not None:
        _validar_rol_y_permisos(db, rol.nombre, permiso_ids)
    registrar_accion(db, accion="ACTUALIZAR", modulo="ROL", usuario_id=admin.id, descripcion=f"Rol actualizado: {rol.nombre}", request=request)
    db.commit()
    db.refresh(rol)
    return rol


@router.delete("/roles/{rol_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar rol")
def eliminar_rol(rol_id: int, request: Request, admin: Usuario = Depends(require_admin), db: Session = Depends(get_db)):
    if admin.rol.nombre != "SUPERADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo SUPERADMIN puede administrar roles")
    rol = db.get(Rol, rol_id)
    if rol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado")
    if db.execute(select(func.count(Usuario.id)).where(Usuario.rol_id == rol_id)).scalar_one():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No se puede eliminar un rol que tiene usuarios")
    registrar_accion(db, accion="ELIMINAR", modulo="ROL", usuario_id=admin.id, descripcion=f"Rol eliminado: {rol.nombre}", request=request)
    db.delete(rol)
    db.commit()


@router.post(
    "/usuarios",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar usuario",
)
def crear_usuario(
    body: UsuarioCreate,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Crea un usuario dentro del tenant administrado."""
    correo = str(body.correo).lower()
    if db.execute(select(Usuario).where(Usuario.correo == correo)).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un usuario con ese correo",
        )

    rol_nombre = body.rol.strip().upper()
    rol = db.execute(select(Rol).where(Rol.nombre == rol_nombre)).scalar_one_or_none()
    if rol is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El rol indicado no existe",
        )

    es_superadmin = admin.rol.nombre == "SUPERADMIN"
    if not es_superadmin and rol_nombre == "SUPERADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Un administrador de cooperativa no puede crear SUPERADMIN",
        )

    # El admin puede elegir la cooperativa del nuevo usuario si es SUPERADMIN,
    # o si él mismo todavía no tiene una cooperativa asignada (bootstrap del
    # primer administrador de una cooperativa). Un ADMINISTRADOR que ya
    # pertenece a una cooperativa queda forzado a la suya para no romper el
    # aislamiento entre tenants.
    if es_superadmin or admin.cooperativa_id is None:
        cooperativa_id = body.cooperativa_id
    else:
        cooperativa_id = admin.cooperativa_id
    if rol_nombre != "SUPERADMIN" and cooperativa_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario debe pertenecer a una cooperativa",
        )
    if cooperativa_id is not None:
        cooperativa = db.get(Cooperativa, cooperativa_id)
        if cooperativa is None or not cooperativa.esta_activa:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La cooperativa indicada no existe o está inactiva",
            )

    usuario = Usuario(
        nombre=body.nombre.strip(),
        correo=correo,
        contrasena=hash_password(body.contrasena),
        rol=rol,
        cooperativa_id=cooperativa_id,
        estado="ACTIVO",
    )
    db.add(usuario)
    db.flush()
    registrar_accion(
        db,
        accion="CREAR",
        modulo="USUARIO",
        usuario_id=admin.id,
        descripcion=f"Usuario creado: {usuario.correo} con rol {rol_nombre}",
        request=request,
    )
    db.commit()
    db.refresh(usuario)
    return usuario


@router.get("/stats", response_model=AdminStatsOut, summary="Estadísticas del dashboard")
def get_stats(
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Devuelve métricas de resumen reales para las tarjetas del Dashboard.
    Lee directamente de las tablas `socio` y `cuenta_ahorro`.

    El filtro por cooperativa se hace con comparaciones ORM (`==`), que
    SQLAlchemy traduce a `IS NULL` cuando corresponde — igual que el resto
    de endpoints (listado de socios, listado de usuarios). Usar SQL crudo
    con `= :cooperativa_id` aquí rompía las tarjetas para cooperativas con
    `cooperativa_id IS NULL`, porque `columna = NULL` nunca es verdadero.
    """
    # Total socios (tabla socio real)
    socio_query = select(func.count(Socio.id))
    socio_active_query = select(func.count(Socio.id)).where(Socio.estado == "ACTIVO")
    if admin.rol.nombre != "SUPERADMIN":
        socio_query = socio_query.where(Socio.cooperativa_id == admin.cooperativa_id)
        socio_active_query = socio_active_query.where(Socio.cooperativa_id == admin.cooperativa_id)
    total_socios: int = db.execute(socio_query).scalar_one()
    socios_activos: int = db.execute(socio_active_query).scalar_one()

    # Cuentas de ahorro activas
    cuenta_query = (
        select(func.count(CuentaAhorro.id))
        .join(Socio, Socio.id == CuentaAhorro.socio_id)
        .where(CuentaAhorro.estado == "ACTIVA")
    )
    if admin.rol.nombre != "SUPERADMIN":
        cuenta_query = cuenta_query.where(Socio.cooperativa_id == admin.cooperativa_id)
    cuentas_activas: int = db.execute(cuenta_query).scalar_one()

    # Total usuarios del sistema
    usuario_query = select(func.count(Usuario.id))
    if admin.rol.nombre != "SUPERADMIN":
        usuario_query = usuario_query.where(Usuario.cooperativa_id == admin.cooperativa_id)
    total_usuarios: int = db.execute(usuario_query).scalar_one()

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
    if not es_superadmin:
        q = q.where(Bitacora.cooperativa_id == admin.cooperativa_id)

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
