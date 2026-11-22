"""Shared business rules for staff and mobile credit-request workflows."""

from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.bitacora import registrar_accion
from app.core.config import settings
from app.models.models import SolicitudCredito

DESTINOS_SOLICITUD = {
    "CAPITAL_TRABAJO", "ACTIVO_FIJO", "CONSUMO", "VIVIENDA", "EDUCACION",
    "SALUD", "REFINANCIAMIENTO", "OTRO",
}
ESTADOS_SOLICITUD_EN_CURSO = ("PENDIENTE", "OBSERVADA", "EN_EVALUACION", "EN_COMITE")
ESTADOS_SOLICITUD_ANULABLES_W21 = {"PENDIENTE", "OBSERVADA"}


def requires_field_evaluation(solicitud: SolicitudCredito) -> bool:
    """Return whether a request is both unevaluated and still in progress."""
    return (
        solicitud.evaluacion_campo_id is None
        and solicitud.estado in ESTADOS_SOLICITUD_EN_CURSO
    )


def requires_field_evaluation_clause():
    """SQL form of the shared field-evaluation eligibility predicate."""
    return and_(
        SolicitudCredito.evaluacion_campo_id.is_(None),
        SolicitudCredito.estado.in_(ESTADOS_SOLICITUD_EN_CURSO),
    )


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


# Social housing (vivienda de interés social, DS 1842): value limits in UFV and
# maximum annual rate by value bracket.
VIS_TIPO = "VIVIENDA_SOCIAL"
VIS_TASA_MAXIMA_PRODUCTO = Decimal("6.50")
VIS_LIMITE_UFV = {"CASA": Decimal("460000"), "DEPARTAMENTO": Decimal("400000")}
VIS_TRAMOS_TASA = ((Decimal("255000"), Decimal("5.50")), (Decimal("380000"), Decimal("6.00")))
_CENT = Decimal("0.01")


def validate_vis_product_rate(tipo_credito_asfi, tasa_interes_anual) -> None:
    if tipo_credito_asfi == VIS_TIPO and Decimal(tasa_interes_anual) > VIS_TASA_MAXIMA_PRODUCTO:
        raise HTTPException(
            status_code=400,
            detail="La tasa de un producto de vivienda de interés social no puede superar 6.50% anual",
        )


def validate_vivienda_social(product, vivienda_social, tasa_interes) -> dict | None:
    """Validate a VIS request and return the housing data to store, or None for other products."""
    if product.tipo_credito_asfi != VIS_TIPO:
        return None
    if not vivienda_social:
        raise HTTPException(
            status_code=400,
            detail="La solicitud de vivienda de interés social requiere los datos de la vivienda",
        )
    data = vivienda_social if isinstance(vivienda_social, dict) else vivienda_social.model_dump()
    if not data.get("unica_vivienda"):
        raise HTTPException(status_code=400, detail="La vivienda de interés social debe ser la única vivienda del socio")
    valor = Decimal(str(data["valor_vivienda"])).quantize(_CENT, rounding=ROUND_HALF_UP)
    valor_ufv = (valor / Decimal(str(settings.UFV_BOB))).quantize(_CENT, rounding=ROUND_HALF_UP)
    if valor_ufv > VIS_LIMITE_UFV[data["tipo_inmueble"]]:
        raise HTTPException(status_code=400, detail="El valor de la vivienda supera el límite de vivienda de interés social")
    tasa_maxima = next((tasa for limite, tasa in VIS_TRAMOS_TASA if valor_ufv <= limite), VIS_TASA_MAXIMA_PRODUCTO)
    if Decimal(tasa_interes) > tasa_maxima:
        raise HTTPException(
            status_code=400,
            detail=f"La tasa del producto supera la tasa máxima de vivienda de interés social ({tasa_maxima}% anual)",
        )
    return {
        "valor_vivienda": str(valor),
        "tipo_inmueble": data["tipo_inmueble"],
        "unica_vivienda": True,
        "valor_ufv": str(valor_ufv),
        "tasa_maxima": str(tasa_maxima),
    }
