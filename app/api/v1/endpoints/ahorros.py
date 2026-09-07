"""Cuentas de ahorro y certificados de aportacion."""

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_user, get_db, require_admin
from app.core.bitacora import registrar_accion
from app.models.models import CertificadoAportacion, CuentaAhorro, Moneda, Socio, Usuario
from app.schemas.schemas import (
    CertificadoAportacionCreate,
    CertificadoAportacionOut,
    CuentaAhorroCreate,
    CuentaAhorroOut,
    MonedaOut,
)

router = APIRouter()


def _socio_visible(admin: Usuario, socio: Socio) -> bool:
    return admin.rol.nombre == "SUPERADMIN" or socio.cooperativa_id == admin.cooperativa_id


def _get_socio(db: Session, admin: Usuario, socio_id: int) -> Socio:
    socio = db.get(Socio, socio_id)
    if socio is None or not _socio_visible(admin, socio):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Socio no encontrado")
    if socio.estado != "ACTIVO":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El socio no está activo")
    return socio


def _get_moneda(db: Session, moneda_id: int) -> Moneda:
    moneda = db.get(Moneda, moneda_id)
    if moneda is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Moneda no encontrada")
    return moneda


def _numero_cuenta(cooperativa_id: int | None) -> str:
    tenant = cooperativa_id or 0
    return f"CA-{tenant:03d}-{uuid4().hex[:12].upper()}"


@router.get("/monedas", response_model=list[MonedaOut], summary="Listar monedas")
def listar_monedas(_: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.execute(select(Moneda).order_by(Moneda.codigo_iso)).scalars())


@router.get("/cuentas", response_model=list[CuentaAhorroOut], summary="Listar cuentas de ahorro")
def listar_cuentas(
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
    socio_id: int | None = Query(None, ge=1),
    estado: str | None = Query(None, pattern="^(ACTIVA|INACTIVA|BLOQUEADA)$"),
):
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(joinedload(CuentaAhorro.moneda))
        .order_by(CuentaAhorro.id.desc())
    )
    if admin.rol.nombre != "SUPERADMIN":
        query = query.where(Socio.cooperativa_id == admin.cooperativa_id)
    if socio_id:
        query = query.where(CuentaAhorro.socio_id == socio_id)
    if estado:
        query = query.where(CuentaAhorro.estado == estado)
    return list(db.execute(query).unique().scalars())


@router.post("/cuentas", response_model=CuentaAhorroOut, status_code=status.HTTP_201_CREATED, summary="Abrir cuenta de ahorro")
def abrir_cuenta(
    body: CuentaAhorroCreate,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    socio = _get_socio(db, admin, body.socio_id)
    moneda = _get_moneda(db, body.moneda_id)
    cuenta = CuentaAhorro(
        numero=_numero_cuenta(socio.cooperativa_id),
        fecha_registro=date.today(),
        socio_id=socio.id,
        moneda_id=moneda.id,
        estado="ACTIVA",
    )
    db.add(cuenta)
    db.flush()
    registrar_accion(
        db,
        accion="APERTURA",
        modulo="AHORROS",
        usuario_id=admin.id,
        cooperativa_id=socio.cooperativa_id,
        descripcion=f"Cuenta {cuenta.numero} abierta para socio {socio.id} ({moneda.codigo_iso})",
        request=request,
    )
    db.commit()
    db.refresh(cuenta)
    return db.execute(
        select(CuentaAhorro).options(joinedload(CuentaAhorro.moneda)).where(CuentaAhorro.id == cuenta.id)
    ).scalar_one()


@router.get("/certificados", response_model=list[CertificadoAportacionOut], summary="Listar certificados")
def listar_certificados(
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
    socio_id: int | None = Query(None, ge=1),
):
    query = (
        select(CertificadoAportacion)
        .join(Socio, CertificadoAportacion.socio_id == Socio.id)
        .options(joinedload(CertificadoAportacion.moneda))
        .order_by(CertificadoAportacion.id.desc())
    )
    if admin.rol.nombre != "SUPERADMIN":
        query = query.where(Socio.cooperativa_id == admin.cooperativa_id)
    if socio_id:
        query = query.where(CertificadoAportacion.socio_id == socio_id)
    return list(db.execute(query).unique().scalars())


@router.post("/certificados", response_model=CertificadoAportacionOut, status_code=status.HTTP_201_CREATED, summary="Emitir certificado de aportación")
def emitir_certificado(
    body: CertificadoAportacionCreate,
    request: Request,
    admin: Usuario = Depends(require_admin),
    db: Session = Depends(get_db),
):
    socio = _get_socio(db, admin, body.socio_id)
    moneda = _get_moneda(db, body.moneda_id)
    certificado = CertificadoAportacion(
        monto=body.monto,
        fecha_emision=date.today(),
        estado="EMITIDO",
        socio_id=socio.id,
        moneda_id=moneda.id,
    )
    db.add(certificado)
    db.flush()
    registrar_accion(
        db,
        accion="EMISION",
        modulo="APORTES",
        usuario_id=admin.id,
        cooperativa_id=socio.cooperativa_id,
        descripcion=f"Certificado de aportación {certificado.id} emitido para socio {socio.id}",
        request=request,
    )
    db.commit()
    db.refresh(certificado)
    return db.execute(
        select(CertificadoAportacion)
        .options(joinedload(CertificadoAportacion.moneda))
        .where(CertificadoAportacion.id == certificado.id)
    ).scalar_one()


@router.get("/mis-cuentas", response_model=list[CuentaAhorroOut], summary="Consultar mis cuentas")
def mis_cuentas(usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(CuentaAhorro)
        .join(Socio, CuentaAhorro.socio_id == Socio.id)
        .options(joinedload(CuentaAhorro.moneda))
        .where(Socio.usuario_id == usuario.id)
        .order_by(CuentaAhorro.id.desc())
    )
    return list(db.execute(query).unique().scalars())


@router.get("/mis-certificados", response_model=list[CertificadoAportacionOut], summary="Consultar mis certificados")
def mis_certificados(usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(CertificadoAportacion)
        .join(Socio, CertificadoAportacion.socio_id == Socio.id)
        .options(joinedload(CertificadoAportacion.moneda))
        .where(Socio.usuario_id == usuario.id)
        .order_by(CertificadoAportacion.id.desc())
    )
    return list(db.execute(query).unique().scalars())
