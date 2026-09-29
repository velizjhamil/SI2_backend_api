"""Shared business rules for staff and mobile credit-request workflows."""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.bitacora import registrar_accion
from app.models.models import SolicitudCredito

DESTINOS_SOLICITUD = {
    "CAPITAL_TRABAJO", "ACTIVO_FIJO", "CONSUMO", "VIVIENDA", "EDUCACION",
    "SALUD", "REFINANCIAMIENTO", "OTRO",
}
ESTADOS_SOLICITUD_EN_CURSO = ("PENDIENTE", "OBSERVADA", "EN_EVALUACION", "EN_COMITE")
ESTADOS_SOLICITUD_ANULABLES_W21 = {"PENDIENTE", "OBSERVADA"}


def validate_base_credit_request(product, *, monto, plazo_meses, destino, destino_detalle):
    """Validate request/product fields shared by W21 create/update and mobile create."""
    if product.estado != "ACTIVO":
        raise HTTPException(status_code=400, detail="El producto crediticio no está activo")
    if monto is None or monto < product.monto_min or monto > product.monto_max:
        raise HTTPException(status_code=400, detail="El monto está fuera del rango permitido para el producto")
    if plazo_meses is None or plazo_meses < product.plazo_min_meses or plazo_meses > product.plazo_max_meses:
        raise HTTPException(status_code=400, detail="El plazo está fuera del rango permitido para el producto")
    if destino not in DESTINOS_SOLICITUD:
        raise HTTPException(status_code=400, detail="El destino de la solicitud no es válido")
    if destino == "OTRO" and (not isinstance(destino_detalle, str) or not destino_detalle.strip()):
        raise HTTPException(status_code=400, detail="Debe detallar el destino cuando selecciona OTRO")


def has_in_progress_request(db: Session, socio_id: int) -> bool:
    return db.execute(select(SolicitudCredito.id).where(
        SolicitudCredito.socio_id == socio_id,
        SolicitudCredito.estado.in_(ESTADOS_SOLICITUD_EN_CURSO),
    )).scalar_one_or_none() is not None


def cancel_credit_request(
    db: Session, solicitud: SolicitudCredito, *, motivo: str, request,
    user_id: int, cooperativa_id: int, require_mobile_eligibility: bool,
) -> None:
    """Apply the shared W21/mobile annulment state transition and audit event."""
    if require_mobile_eligibility:
        eligible = solicitud.estado == "PENDIENTE" and solicitud.evaluacion_campo_id is None
    else:
        eligible = solicitud.estado in ESTADOS_SOLICITUD_ANULABLES_W21
    if not eligible:
        raise HTTPException(status_code=409, detail="La solicitud ya no se puede anular")
    normalized = motivo.strip()
    if len(normalized) < 5:
        raise HTTPException(status_code=400, detail="El motivo de anulación debe tener al menos 5 caracteres")
    solicitud.estado = "ANULADA"
    solicitud.motivo_anulacion = normalized
    solicitud.fecha_actualizacion = func.now()
    description = (
        f"Solicitud móvil anulada: {solicitud.id}"
        if require_mobile_eligibility
        else f"Solicitud de crédito anulada: {solicitud.id} ({solicitud.numero_solicitud})"
    )
    registrar_accion(db, accion="ANULAR_SOLICITUD", modulo="CREDITOS", usuario_id=user_id,
        cooperativa_id=cooperativa_id, descripcion=description, request=request)
