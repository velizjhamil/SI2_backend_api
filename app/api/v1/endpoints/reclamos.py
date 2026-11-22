"""Customer claims (Punto de Reclamo, Ley 393): socios file and accept or escalate; staff attend and respond."""

from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.deps import get_current_socio, get_current_user, get_db
from app.api.v1.endpoints.ahorros import _siguiente_secuencia
from app.core.bitacora import registrar_accion
from app.models.models import Credito, Reclamo, Socio, Usuario
from app.services.reclamos import PLAZO_RESPUESTA_DIAS_HABILES, sumar_dias_habiles

router = APIRouter()
socio_router = APIRouter()
ROLES_PERSONAL = {"ADMINISTRADOR", "OFICIAL_CREDITO", "ASESOR_CREDITO", "CAJERO", "CONTADOR"}
TipoReclamo = Literal["CREDITO", "AHORRO", "CAJA", "COBRO_INDEBIDO", "ATENCION", "OTRO"]
EstadoReclamo = Literal["REGISTRADO", "EN_ATENCION", "RESPONDIDO", "CERRADO", "ESCALADO_ASFI"]


def _texto(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class ReclamoIn(BaseModel):
    tipo: TipoReclamo
    descripcion: str = Field(min_length=1, max_length=2000)
    credito_id: int | None = None

    @field_validator("descripcion")
    @classmethod
    def _descripcion(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La descripción es obligatoria")
        return value


class ReclamoVentanillaIn(ReclamoIn):
    socio_id: int


class RespuestaIn(BaseModel):
    respuesta: str = Field(min_length=1, max_length=2000)
    procedente: bool

    @field_validator("respuesta")
    @classmethod
    def _respuesta(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La respuesta es obligatoria")
        return value


class ConformidadIn(BaseModel):
    conforme: bool
    motivo: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _motivo_si_disconforme(self):
        self.motivo = _texto(self.motivo)
        if not self.conforme and not self.motivo:
            raise ValueError("Debe indicar el motivo de la disconformidad")
        return self


def _vencido(row: Reclamo) -> bool:
    return row.estado in {"REGISTRADO", "EN_ATENCION"} and date.today() > row.fecha_limite_respuesta


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def _out(row: Reclamo) -> dict:
    return {
        "id": row.id, "numero_reclamo": row.numero_reclamo, "socio_id": row.socio_id,
        "socio": f"{row.socio.nombre} {row.socio.apellido}".strip(), "credito_id": row.credito_id,
        "tipo": row.tipo, "descripcion": row.descripcion, "canal": row.canal, "estado": row.estado,
        "fecha_registro": _iso(row.fecha_registro), "fecha_limite_respuesta": _iso(row.fecha_limite_respuesta),
        "vencido": _vencido(row), "usuario_atencion_id": row.usuario_atencion_id,
        "respuesta": row.respuesta, "procedente": row.procedente, "fecha_respuesta": _iso(row.fecha_respuesta),
        "conforme": row.conforme, "motivo_disconformidad": row.motivo_disconformidad,
        "fecha_cierre": _iso(row.fecha_cierre), "fecha_escalamiento": _iso(row.fecha_escalamiento),
    }


def _tenant_personal(user: Usuario) -> int:
    if user.rol is None or user.rol.nombre not in ROLES_PERSONAL:
        raise HTTPException(403, "Operación reservada al personal de la cooperativa")
    if user.cooperativa_id is None:
        raise HTTPException(403, "El usuario no pertenece a una cooperativa")
    return user.cooperativa_id


def _registrar(db: Session, *, cooperativa_id: int, socio_id: int, body: ReclamoIn, canal: str,
               usuario_id: int, request: Request) -> Reclamo:
    if body.credito_id is not None and db.execute(select(Credito.id).where(
        Credito.id == body.credito_id, Credito.socio_id == socio_id,
    )).scalar_one_or_none() is None:
        raise HTTPException(404, "Crédito no encontrado")
    hoy = date.today()
    numero = _siguiente_secuencia(db, cooperativa_id, f"RECLAMO_{hoy.year}")
    row = Reclamo(
        cooperativa_id=cooperativa_id, socio_id=socio_id, credito_id=body.credito_id,
        numero_reclamo=f"PR-{hoy.year}-{numero:06d}", tipo=body.tipo, descripcion=body.descripcion,
        canal=canal, estado="REGISTRADO",
        fecha_limite_respuesta=sumar_dias_habiles(hoy, PLAZO_RESPUESTA_DIAS_HABILES),
    )
    db.add(row)
    db.flush()
    registrar_accion(db, accion="REGISTRAR_RECLAMO", modulo="RECLAMOS", usuario_id=usuario_id,
                     cooperativa_id=cooperativa_id, descripcion=f"Reclamo registrado: {row.numero_reclamo}",
                     request=request)
    db.commit()
    return _obtener(db, cooperativa_id, row.id)


def _obtener(db: Session, cooperativa_id: int, reclamo_id: int, socio_id: int | None = None,
             lock: bool = False) -> Reclamo:
    query = (select(Reclamo).options(joinedload(Reclamo.socio))
             .where(Reclamo.id == reclamo_id, Reclamo.cooperativa_id == cooperativa_id))
    if socio_id is not None:
        query = query.where(Reclamo.socio_id == socio_id)
    if lock:
        query = query.with_for_update(of=Reclamo)
    row = db.execute(query).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Reclamo no encontrado")
    return row


# --- Socio self-service (/socio/reclamos) ----------------------------------

@socio_router.post("/reclamos", status_code=status.HTTP_201_CREATED)
def registrar_reclamo_socio(body: ReclamoIn, request: Request, socio: Socio = Depends(get_current_socio),
                            db: Session = Depends(get_db)):
    return _out(_registrar(db, cooperativa_id=socio.cooperativa_id, socio_id=socio.id, body=body, canal="MOVIL",
                           usuario_id=socio.usuario_id, request=request))


@socio_router.get("/reclamos")
def listar_reclamos_socio(socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    rows = db.execute(select(Reclamo).options(joinedload(Reclamo.socio)).where(
        Reclamo.socio_id == socio.id, Reclamo.cooperativa_id == socio.cooperativa_id,
    ).order_by(Reclamo.fecha_registro.desc(), Reclamo.id.desc())).unique().scalars().all()
    return [_out(row) for row in rows]


@socio_router.post("/reclamos/{reclamo_id}/conformidad")
def registrar_conformidad(reclamo_id: int, body: ConformidadIn, request: Request,
                          socio: Socio = Depends(get_current_socio), db: Session = Depends(get_db)):
    row = _obtener(db, socio.cooperativa_id, reclamo_id, socio_id=socio.id, lock=True)
    ahora = datetime.now(timezone.utc)
    if body.conforme:
        if row.estado != "RESPONDIDO":
            raise HTTPException(409, "Solo puede dar conformidad a un reclamo respondido")
        row.estado, row.conforme, row.fecha_cierre = "CERRADO", True, ahora
        accion = "CERRAR_RECLAMO"
    else:
        if row.estado != "RESPONDIDO" and not _vencido(row):
            raise HTTPException(409, "Solo puede escalar a ASFI un reclamo respondido o con el plazo de respuesta vencido")
        row.estado, row.conforme = "ESCALADO_ASFI", False
        row.motivo_disconformidad, row.fecha_escalamiento = body.motivo, ahora
        accion = "ESCALAR_RECLAMO_ASFI"
    registrar_accion(db, accion=accion, modulo="RECLAMOS", usuario_id=socio.usuario_id,
                     cooperativa_id=socio.cooperativa_id, descripcion=f"Reclamo {row.numero_reclamo}: {row.estado}",
                     request=request)
    db.commit()
    return _out(_obtener(db, socio.cooperativa_id, reclamo_id))


# --- Staff (/reclamos) -----------------------------------------------------

@router.get("")
def listar_reclamos(estado: EstadoReclamo | None = Query(None), vencido: bool | None = Query(None),
                    usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant_personal(usuario)
    query = select(Reclamo).options(joinedload(Reclamo.socio)).where(Reclamo.cooperativa_id == tenant)
    if estado:
        query = query.where(Reclamo.estado == estado)
    rows = db.execute(query.order_by(Reclamo.fecha_registro.desc(), Reclamo.id.desc())).unique().scalars().all()
    if vencido is not None:
        rows = [row for row in rows if _vencido(row) == vencido]
    return [_out(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def registrar_reclamo_ventanilla(body: ReclamoVentanillaIn, request: Request,
                                 usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant_personal(usuario)
    if db.execute(select(Socio.id).where(Socio.id == body.socio_id, Socio.cooperativa_id == tenant)).scalar_one_or_none() is None:
        raise HTTPException(404, "Socio no encontrado")
    return _out(_registrar(db, cooperativa_id=tenant, socio_id=body.socio_id, body=body, canal="VENTANILLA",
                           usuario_id=usuario.id, request=request))


@router.get("/{reclamo_id}")
def obtener_reclamo(reclamo_id: int, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(_obtener(db, _tenant_personal(usuario), reclamo_id))


@router.post("/{reclamo_id}/atender")
def atender_reclamo(reclamo_id: int, request: Request, usuario: Usuario = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    tenant = _tenant_personal(usuario)
    row = _obtener(db, tenant, reclamo_id, lock=True)
    if row.estado != "REGISTRADO":
        raise HTTPException(409, "Solo se puede atender un reclamo registrado")
    row.estado, row.usuario_atencion_id = "EN_ATENCION", usuario.id
    registrar_accion(db, accion="ATENDER_RECLAMO", modulo="RECLAMOS", usuario_id=usuario.id, cooperativa_id=tenant,
                     descripcion=f"Reclamo en atención: {row.numero_reclamo}", request=request)
    db.commit()
    return _out(_obtener(db, tenant, reclamo_id))


@router.post("/{reclamo_id}/responder")
def responder_reclamo(reclamo_id: int, body: RespuestaIn, request: Request,
                      usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = _tenant_personal(usuario)
    row = _obtener(db, tenant, reclamo_id, lock=True)
    if row.estado not in {"REGISTRADO", "EN_ATENCION"}:
        raise HTTPException(409, "El reclamo ya fue respondido")
    row.estado, row.respuesta, row.procedente = "RESPONDIDO", body.respuesta, body.procedente
    row.fecha_respuesta = datetime.now(timezone.utc)
    row.usuario_atencion_id = row.usuario_atencion_id or usuario.id
    registrar_accion(db, accion="RESPONDER_RECLAMO", modulo="RECLAMOS", usuario_id=usuario.id, cooperativa_id=tenant,
                     descripcion=f"Reclamo respondido: {row.numero_reclamo}", request=request)
    db.commit()
    return _out(_obtener(db, tenant, reclamo_id))
