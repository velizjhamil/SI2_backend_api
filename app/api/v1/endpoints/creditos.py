"""Cooperative credit-product catalog (CU-W20)."""

import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_user, get_db, require_admin
from app.core.bitacora import registrar_accion
from app.models.models import Moneda, ProductoCredito, Usuario
from app.schemas.schemas import (
    ProductoCreditoCreate,
    ProductoCreditoEstadoUpdate,
    ProductoCreditoOut,
    ProductoCreditoUpdate,
)

router = APIRouter()
ROLES_LECTURA_PRODUCTOS = {"ADMINISTRADOR", "CONTADOR", "CAJERO", "OFICIAL_CREDITO"}


def _validar_lector(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None or usuario.rol.nombre not in ROLES_LECTURA_PRODUCTOS:
        raise HTTPException(status_code=403, detail="Operación reservada al personal de la cooperativa")
    return usuario.cooperativa_id


def _validar_admin_cooperativa(usuario: Usuario) -> int:
    if usuario.cooperativa_id is None:
        raise HTTPException(status_code=403, detail="Operación no disponible para este usuario")
    if usuario.rol is None or usuario.rol.nombre != "ADMINISTRADOR":
        raise HTTPException(status_code=403, detail="Operación reservada a administradores de la cooperativa")
    return usuario.cooperativa_id


def _obtener_producto(db: Session, cooperativa_id: int, producto_id: int) -> ProductoCredito:
    producto = db.execute(
        select(ProductoCredito)
        .options(joinedload(ProductoCredito.moneda))
        .where(
            ProductoCredito.id == producto_id,
            ProductoCredito.cooperativa_id == cooperativa_id,
        )
    ).scalar_one_or_none()
    if producto is None:
        raise HTTPException(status_code=404, detail="Producto crediticio no encontrado")
    return producto


def _validar_datos_producto(db: Session, datos: dict) -> None:
    nombre = datos.get("nombre")
    if not isinstance(nombre, str) or not nombre.strip() or len(nombre.strip()) > 100:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio y debe tener hasta 100 caracteres")
    if db.get(Moneda, datos.get("moneda_id")) is None:
        raise HTTPException(status_code=400, detail="La moneda indicada no existe")
    if datos.get("tipo_amortizacion") not in {"FRANCES", "ALEMAN"}:
        raise HTTPException(status_code=400, detail="El tipo de amortización debe ser FRANCES o ALEMAN")

    monto_min = datos.get("monto_min")
    monto_max = datos.get("monto_max")
    if not isinstance(monto_min, Decimal) or not isinstance(monto_max, Decimal):
        raise HTTPException(status_code=400, detail="Los montos deben ser valores decimales válidos")
    if monto_min <= 0 or monto_min > monto_max or monto_max > Decimal("999999999999.99"):
        raise HTTPException(status_code=400, detail="El rango de montos no es válido")

    plazo_min = datos.get("plazo_min_meses")
    plazo_max = datos.get("plazo_max_meses")
    if not isinstance(plazo_min, int) or not isinstance(plazo_max, int) or plazo_min <= 0 or plazo_min > plazo_max:
        raise HTTPException(status_code=400, detail="El rango de plazos debe ser positivo y coherente")

    for field, label in (("tasa_interes_anual", "La tasa anual"), ("tasa_mora_anual", "La tasa de mora")):
        value = datos.get(field)
        if not isinstance(value, Decimal) or value < 0 or value > 100:
            raise HTTPException(status_code=400, detail=f"{label} debe estar entre 0 y 100")
    ratio = datos.get("relacion_cuota_ingreso_max")
    if not isinstance(ratio, Decimal) or ratio <= 0 or ratio > 100:
        raise HTTPException(status_code=400, detail="La relación cuota-ingreso debe ser mayor a 0 y hasta 100")
    grace_days = datos.get("dias_gracia_mora")
    if not isinstance(grace_days, int) or grace_days < 0:
        raise HTTPException(status_code=400, detail="Los días de gracia no pueden ser negativos")

    for value, label in ((monto_min, "El monto mínimo"), (monto_max, "El monto máximo"),
                         (datos["tasa_interes_anual"], "La tasa anual"),
                         (datos["tasa_mora_anual"], "La tasa de mora"), (ratio, "La relación cuota-ingreso")):
        if value != value.quantize(Decimal("0.01")):
            raise HTTPException(status_code=400, detail=f"{label} admite hasta dos decimales")


@router.post("/productos", response_model=ProductoCreditoOut, status_code=status.HTTP_201_CREATED)
def crear_producto_crediticio(
    body: ProductoCreditoCreate,
    request: Request,
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    codigo = body.codigo.strip().upper()
    if re.fullmatch(r"[A-Z0-9-]{2,20}", codigo) is None:
        raise HTTPException(status_code=400, detail="El código debe tener 2 a 20 caracteres: letras, números o guiones")
    datos = body.model_dump(exclude={"codigo"})
    datos["nombre"] = body.nombre.strip()
    _validar_datos_producto(db, datos)
    if db.execute(
        select(ProductoCredito.id).where(
            ProductoCredito.cooperativa_id == cooperativa_id,
            ProductoCredito.codigo == codigo,
        )
    ).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Ya existe un producto con ese código")

    producto = ProductoCredito(cooperativa_id=cooperativa_id, codigo=codigo, **datos)
    producto.fecha_actualizacion = func.now()
    db.add(producto)
    db.flush()
    registrar_accion(
        db,
        accion="CREAR_PRODUCTO",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Producto crediticio creado: {producto.id} ({producto.codigo})",
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_producto_credito_cooperativa_codigo" in str(exc.orig):
            raise HTTPException(status_code=409, detail="Ya existe un producto con ese código") from exc
        raise
    db.refresh(producto)
    return producto


@router.put("/productos/{producto_id}", response_model=ProductoCreditoOut)
def actualizar_producto_crediticio(
    producto_id: int,
    body: ProductoCreditoUpdate,
    request: Request,
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    producto = _obtener_producto(db, cooperativa_id, producto_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return producto
    if any(value is None for key, value in changes.items() if key != "descripcion"):
        raise HTTPException(status_code=400, detail="Los campos enviados no pueden ser nulos")

    values = {
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "moneda_id": producto.moneda_id,
        "monto_min": producto.monto_min,
        "monto_max": producto.monto_max,
        "plazo_min_meses": producto.plazo_min_meses,
        "plazo_max_meses": producto.plazo_max_meses,
        "tasa_interes_anual": producto.tasa_interes_anual,
        "tipo_amortizacion": producto.tipo_amortizacion,
        "dias_gracia_mora": producto.dias_gracia_mora,
        "tasa_mora_anual": producto.tasa_mora_anual,
        "relacion_cuota_ingreso_max": producto.relacion_cuota_ingreso_max,
        "requiere_garantia": producto.requiere_garantia,
    }
    values.update(changes)
    if "nombre" in changes and changes["nombre"] is not None:
        values["nombre"] = changes["nombre"].strip()
    _validar_datos_producto(db, values)

    for field, value in changes.items():
        setattr(producto, field, values[field])
    producto.fecha_actualizacion = func.now()
    registrar_accion(
        db,
        accion="ACTUALIZAR_PRODUCTO",
        modulo="CREDITOS",
        usuario_id=usuario.id,
        cooperativa_id=cooperativa_id,
        descripcion=f"Producto crediticio actualizado: {producto.id} ({producto.codigo})",
        request=request,
    )
    db.commit()
    db.refresh(producto)
    return producto


@router.patch("/productos/{producto_id}/estado", response_model=ProductoCreditoOut)
def actualizar_estado_producto_crediticio(
    producto_id: int,
    body: ProductoCreditoEstadoUpdate,
    request: Request,
    usuario: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_admin_cooperativa(usuario)
    producto = _obtener_producto(db, cooperativa_id, producto_id)
    if body.estado not in {"ACTIVO", "INACTIVO"}:
        raise HTTPException(status_code=400, detail="El estado debe ser ACTIVO o INACTIVO")
    if producto.estado != body.estado:
        producto.estado = body.estado
        producto.fecha_actualizacion = func.now()
        registrar_accion(
            db,
            accion="ACTIVAR_PRODUCTO" if body.estado == "ACTIVO" else "DESACTIVAR_PRODUCTO",
            modulo="CREDITOS",
            usuario_id=usuario.id,
            cooperativa_id=cooperativa_id,
            descripcion=f"Producto crediticio {body.estado.lower()}: {producto.id} ({producto.codigo})",
            request=request,
        )
        db.commit()
        db.refresh(producto)
    return producto


@router.get("/productos", response_model=list[ProductoCreditoOut])
def listar_productos_crediticios(
    estado: str | None = Query(None),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    if estado is not None and estado not in {"ACTIVO", "INACTIVO"}:
        raise HTTPException(status_code=400, detail="El estado debe ser ACTIVO o INACTIVO")
    query = (
        select(ProductoCredito)
        .options(joinedload(ProductoCredito.moneda))
        .where(ProductoCredito.cooperativa_id == cooperativa_id)
        .order_by(ProductoCredito.codigo)
    )
    if estado is not None:
        query = query.where(ProductoCredito.estado == estado)
    return db.execute(query).scalars().all()


@router.get("/productos/{producto_id}", response_model=ProductoCreditoOut)
def obtener_producto_crediticio(
    producto_id: int,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cooperativa_id = _validar_lector(usuario)
    return _obtener_producto(db, cooperativa_id, producto_id)
