"""Credit rescheduling (reprogramación): staff and socio requests, preview, approval and rejection."""

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.v1.deps import get_current_socio, get_current_user, get_db
from app.core.bitacora import registrar_accion
from app.models.models import Credito, ReprogramacionCredito, SolicitudCredito, Socio, TablaAmortizacion, Usuario
from app.api.v1.endpoints.creditos import _actualizar_morosidad_credito
from app.schemas.schemas import MonedaOut
from app.services.cobro_cuotas import CUOTAS_CERRADAS
from app.services.reprogramacion import plan_reprogramacion

router = APIRouter()
socio_router = APIRouter()
ROLES_GESTION = {"ADMINISTRADOR", "OFICIAL_CREDITO"}
ROLES_LECTURA = ROLES_GESTION | {"ASESOR_CREDITO", "CONTADOR"}


def _texto_obligatorio(value: str, mensaje: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(mensaje)
    return value


class PlazoIn(BaseModel):
    nuevo_plazo_meses: int


class ReprogramacionIn(PlazoIn):
    motivo: str = Field(min_length=1, max_length=2000)

    @field_validator("motivo")
    @classmethod
    def _motivo(cls, value: str) -> str:
        return _texto_obligatorio(value, "El motivo es obligatorio")


class RechazoIn(BaseModel):
    motivo: str = Field(min_length=1, max_length=2000)

    @field_validator("motivo")
    @classmethod
    def _motivo(cls, value: str) -> str:
        return _texto_obligatorio(value, "El motivo del rechazo es obligatorio")


def _json(value):
    """Money as fixed two-decimal strings, like the response models of the rest of the API."""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def _out(row: ReprogramacionCredito) -> dict:
    def iso(value):
        return None if value is None else value.isoformat()

    return _json({
        "id": row.id, "credito_id": row.credito_id,
        "numero_credito": row.credito.numero_credito if row.credito else None,
        "estado": row.estado, "canal": row.canal, "motivo": row.motivo,
        "nuevo_plazo_meses": row.nuevo_plazo_meses, "saldo_capital": row.saldo_capital,
        "interes_diferido": row.interes_diferido, "mora_condonada": row.mora_condonada,
        "plazo_anterior": row.plazo_anterior, "cuotas_reprogramadas": row.cuotas_reprogramadas,
        "motivo_rechazo": row.motivo_rechazo, "usuario_solicitud_id": row.usuario_solicitud_id,
        "usuario_resolucion_id": row.usuario_resolucion_id,
        "fecha_solicitud": iso(row.fecha_solicitud), "fecha_resolucion": iso(row.fecha_resolucion),
    })


def _tenant(user: Usuario, roles: set[str]) -> int:
    if user.rol is None or user.rol.nombre not in roles:
        raise HTTPException(403, "Operación reservada a oficiales de crédito y administradores")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _credito(db: Session, cooperativa_id: int, credito_id: int, socio_id: int | None = None,
             lock: bool = False) -> Credito:
    query = (select(Credito)
             .options(joinedload(Credito.producto), joinedload(Credito.moneda),
                      joinedload(Credito.solicitud).joinedload(SolicitudCredito.producto),
                      joinedload(Credito.solicitud).joinedload(SolicitudCredito.moneda),
                      selectinload(Credito.cronograma))
             .where(Credito.id == credito_id, Credito.cooperativa_id == cooperativa_id))
    if socio_id is not None:
        query = query.where(Credito.socio_id == socio_id)
    if lock:
        query = query.with_for_update(of=Credito)
    credito = db.execute(query).unique().scalar_one_or_none()
    if credito is None:
        raise HTTPException(404, "Crédito no encontrado")
    return credito


def _producto(credito: Credito):
    return credito.producto or credito.solicitud.producto


def _validar_reprogramable(credito: Credito, nuevo_plazo_meses: int) -> None:
    if credito.estado != "VIGENTE" or not credito.saldo_pendiente or credito.saldo_pendiente <= 0:
        raise HTTPException(409, "Solo se puede reprogramar un crédito VIGENTE con saldo pendiente")
    producto = _producto(credito)
    if producto is None:
        raise HTTPException(409, "El crédito no tiene producto crediticio")
    if nuevo_plazo_meses < 1:
        raise HTTPException(400, "El nuevo plazo debe ser de al menos 1 mes")
    if nuevo_plazo_meses > producto.plazo_max_meses:
        raise HTTPException(400, f"El nuevo plazo no puede superar {producto.plazo_max_meses} meses")


def _calcular(credito: Credito, nuevo_plazo_meses: int) -> dict:
    producto = _producto(credito)
    return plan_reprogramacion(
        cuotas=credito.cronograma, saldo_capital=credito.saldo_pendiente,
        tasa_anual=credito.tasa_interes if credito.tasa_interes is not None else producto.tasa_interes_anual,
        tipo_amortizacion=credito.tipo_amortizacion or producto.tipo_amortizacion,
        tasa_seguro_desgravamen=producto.tasa_seguro_desgravamen, nuevo_plazo_meses=nuevo_plazo_meses,
        hoy=date.today(), tasa_mora_anual=producto.tasa_mora_anual, dias_gracia_mora=producto.dias_gracia_mora,
    )


def _solicitar(db: Session, credito: Credito, body: ReprogramacionIn, *, canal: str, usuario_id: int,
               request: Request) -> dict:
    _validar_reprogramable(credito, body.nuevo_plazo_meses)
    if db.execute(select(ReprogramacionCredito.id).where(
        ReprogramacionCredito.credito_id == credito.id, ReprogramacionCredito.estado == "SOLICITADA",
    )).scalar_one_or_none() is not None:
        raise HTTPException(409, "El crédito ya tiene una reprogramación solicitada")
    row = ReprogramacionCredito(
        cooperativa_id=credito.cooperativa_id, credito_id=credito.id, estado="SOLICITADA", canal=canal,
        motivo=body.motivo, nuevo_plazo_meses=body.nuevo_plazo_meses, saldo_capital=credito.saldo_pendiente,
        plazo_anterior=credito.plazo_meses, usuario_solicitud_id=usuario_id,
    )
    row.credito = credito
    db.add(row)
    db.flush()
    registrar_accion(db, accion="SOLICITAR_REPROGRAMACION", modulo="CREDITOS", usuario_id=usuario_id,
                     cooperativa_id=credito.cooperativa_id,
                     descripcion=f"Reprogramación solicitada: crédito {credito.numero_credito} a {body.nuevo_plazo_meses} meses",
                     request=request)
    db.commit()
    db.refresh(row)
    return _out(row)


def _historial(db: Session, credito_id: int) -> list[dict]:
    rows = db.execute(select(ReprogramacionCredito).options(joinedload(ReprogramacionCredito.credito))
                      .where(ReprogramacionCredito.credito_id == credito_id)
                      .order_by(ReprogramacionCredito.fecha_solicitud.desc(), ReprogramacionCredito.id.desc())
                      ).scalars().all()
    return [_out(row) for row in rows]


def _reprogramacion(db: Session, cooperativa_id: int, reprogramacion_id: int) -> ReprogramacionCredito:
    row = db.execute(select(ReprogramacionCredito).where(
        ReprogramacionCredito.id == reprogramacion_id, ReprogramacionCredito.cooperativa_id == cooperativa_id,
    ).with_for_update()).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Reprogramación no encontrada")
    if row.estado != "SOLICITADA":
        raise HTTPException(409, "La reprogramación ya fue resuelta")
    return row


# --- Staff (mounted under /creditos) ---------------------------------------

@router.post("/creditos/{credito_id}/reprogramaciones/simulacion")
def simular_reprogramacion(credito_id: int, body: PlazoIn, usuario: Usuario = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    credito = _credito(db, _tenant(usuario, ROLES_LECTURA), credito_id)
    _validar_reprogramable(credito, body.nuevo_plazo_meses)
    result = _calcular(credito, body.nuevo_plazo_meses)
    moneda = credito.moneda or credito.solicitud.moneda
    return _json({
        **result["plan"],
        "moneda": None if moneda is None else MonedaOut.model_validate(moneda).model_dump(mode="json"),
        "saldo_capital": result["saldo_capital"], "interes_diferido": result["interes_diferido"],
        "mora_condonada": result["mora_condonada"], "cuotas_reprogramadas": result["cuotas_reprogramadas"],
    })


@router.post("/creditos/{credito_id}/reprogramaciones", status_code=status.HTTP_201_CREATED)
def solicitar_reprogramacion(credito_id: int, body: ReprogramacionIn, request: Request,
                             usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    credito = _credito(db, _tenant(usuario, ROLES_GESTION), credito_id, lock=True)
    return _solicitar(db, credito, body, canal="VENTANILLA", usuario_id=usuario.id, request=request)


@router.get("/creditos/{credito_id}/reprogramaciones")
def listar_reprogramaciones_credito(credito_id: int, usuario: Usuario = Depends(get_current_user),
                                    db: Session = Depends(get_db)):
    credito = _credito(db, _tenant(usuario, ROLES_LECTURA), credito_id)
    return _historial(db, credito.id)


@router.get("/reprogramaciones")
def listar_reprogramaciones(estado: str | None = Query(None), usuario: Usuario = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    tenant = _tenant(usuario, ROLES_LECTURA)
    query = (select(ReprogramacionCredito).options(joinedload(ReprogramacionCredito.credito))
             .where(ReprogramacionCredito.cooperativa_id == tenant))
    if estado:
        query = query.where(ReprogramacionCredito.estado == estado)
    rows = db.execute(query.order_by(ReprogramacionCredito.fecha_solicitud.desc(),
                                     ReprogramacionCredito.id.desc())).scalars().all()
    return [_out(row) for row in rows]


@router.post("/reprogramaciones/{reprogramacion_id}/aprobar")
def aprobar_reprogramacion(reprogramacion_id: int, request: Request, usuario: Usuario = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    tenant = _tenant(usuario, ROLES_GESTION)
    row = _reprogramacion(db, tenant, reprogramacion_id)
    credito = _credito(db, tenant, row.credito_id, lock=True)
    _validar_reprogramable(credito, row.nuevo_plazo_meses)
    result = _calcular(credito, row.nuevo_plazo_meses)

    ultimo = max((cuota.numero_cuota for cuota in credito.cronograma), default=0)
    reprogramadas = 0
    for cuota in credito.cronograma:
        if (cuota.estado_pago or "PENDIENTE") not in CUOTAS_CERRADAS:
            cuota.estado_pago = "REPROGRAMADA"
            reprogramadas += 1
    for nueva in result["plan"]["cuotas"]:
        db.add(TablaAmortizacion(
            credito_id=credito.id, numero_cuota=ultimo + nueva["numero"],
            fecha_vencimiento=nueva["fecha_vencimiento"], monto_capital=nueva["capital"],
            monto_interes=nueva["interes"], monto_seguro=nueva["seguro"], monto_cuota_total=nueva["cuota"],
            saldo_inicial=nueva["saldo_inicial"], saldo_final=nueva["saldo_final"],
            estado_pago="PENDIENTE", monto_pagado=0,
        ))
    # The new rows are not in credito.cronograma yet, so this counts the installments kept (paid ones).
    conservadas = sum(cuota.estado_pago != "REPROGRAMADA" for cuota in credito.cronograma)
    credito.plazo_meses = conservadas + row.nuevo_plazo_meses
    credito.numero_reprogramaciones = (credito.numero_reprogramaciones or 0) + 1
    # Refresh the cached arrears record: the overdue installments are now closed.
    db.flush()
    db.expire(credito, ["cronograma"])
    _actualizar_morosidad_credito(db, credito, date.today())

    row.estado = "APROBADA"
    row.saldo_capital = result["saldo_capital"]
    row.interes_diferido = result["interes_diferido"]
    row.mora_condonada = result["mora_condonada"]
    row.plazo_anterior = row.plazo_anterior or len(credito.cronograma)
    row.cuotas_reprogramadas = reprogramadas
    row.usuario_resolucion_id = usuario.id
    row.fecha_resolucion = datetime.now(timezone.utc)
    registrar_accion(db, accion="APROBAR_REPROGRAMACION", modulo="CREDITOS", usuario_id=usuario.id,
                     cooperativa_id=tenant,
                     descripcion=(f"Reprogramación aprobada: crédito {credito.numero_credito}, "
                                  f"{reprogramadas} cuotas reprogramadas a {row.nuevo_plazo_meses} meses"),
                     request=request)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.post("/reprogramaciones/{reprogramacion_id}/rechazar")
def rechazar_reprogramacion(reprogramacion_id: int, body: RechazoIn, request: Request,
                            usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant(usuario, ROLES_GESTION)
    row = _reprogramacion(db, tenant, reprogramacion_id)
    row.estado, row.motivo_rechazo = "RECHAZADA", body.motivo
    row.usuario_resolucion_id, row.fecha_resolucion = usuario.id, datetime.now(timezone.utc)
    registrar_accion(db, accion="RECHAZAR_REPROGRAMACION", modulo="CREDITOS", usuario_id=usuario.id,
                     cooperativa_id=tenant, descripcion=f"Reprogramación rechazada: {row.id}", request=request)
    db.commit()
    db.refresh(row)
    return _out(row)


# --- Socio self-service (mounted under /socio) -----------------------------

@socio_router.post("/creditos/{credito_id}/reprogramaciones", status_code=status.HTTP_201_CREATED)
def solicitar_reprogramacion_socio(credito_id: int, body: ReprogramacionIn, request: Request,
                                   socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    credito = _credito(db, socio.cooperativa_id, credito_id, socio_id=socio.id, lock=True)
    return _solicitar(db, credito, body, canal="MOVIL", usuario_id=socio.usuario_id, request=request)


@socio_router.get("/creditos/{credito_id}/reprogramaciones")
def listar_reprogramaciones_socio(credito_id: int, socio: Socio = Depends(get_current_socio),
                                  db: Session = Depends(get_db)):
    credito = _credito(db, socio.cooperativa_id, credito_id, socio_id=socio.id)
    return _historial(db, credito.id)
