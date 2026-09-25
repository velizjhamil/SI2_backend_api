"""UIF declaration validation and persistence shared by cash operations."""

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

UMBRAL_BOB = Decimal("70000.00")
UMBRAL_USD = Decimal("10000.00")
ORIGENES = {
    "AHORROS_PROPIOS": "Ahorros propios",
    "VENTA_INMUEBLE": "Venta de inmueble",
    "VENTA_VEHICULO": "Venta de vehículo",
    "COBRO_SERVICIOS": "Cobro de servicios",
    "ACTIVIDAD_COMERCIAL": "Actividad comercial",
    "SUELDO": "Sueldo",
    "HERENCIA": "Herencia",
    "PRESTAMO": "Préstamo",
    "OTRO": "Otro",
}
DESTINOS = {
    "CONSTITUCION_DPF": "Constitución de DPF",
    "AHORRO": "Ahorro",
    "PAGO_PROVEEDORES": "Pago a proveedores",
    "COMPRA_BIENES": "Compra de bienes",
    "INVERSION": "Inversión",
    "GASTOS_PERSONALES": "Gastos personales",
    "PAGO_DEUDAS": "Pago de deudas",
    "OTRO": "Otro",
}
ERROR_REQUIERE_UIF = "Se requiere declaración jurada UIF para esta operación"


def umbral_para(moneda_iso: str) -> Decimal | None:
    if moneda_iso == "BOB":
        return UMBRAL_BOB
    if moneda_iso == "USD":
        return UMBRAL_USD
    return None


def requiere_declaracion(
    db: Session, *, socio_id: int, moneda_id: int, moneda_iso: str, monto: Decimal
) -> tuple[bool, bool]:
    """Return (required, same-day structuring) for a ventanilla cash operation."""
    threshold = umbral_para(moneda_iso)
    if threshold is None:
        return False, False
    if monto >= threshold:
        return True, False
    acumulado = db.execute(
        text("""
            SELECT COALESCE(SUM(t.monto), 0)
            FROM transaccion t
            JOIN cuenta_ahorro ca ON ca.id = t.cuenta_ahorro_id
            WHERE ca.socio_id = :socio_id
              AND t.moneda_id = :moneda_id
              AND t.canal = 'VENTANILLA'
              AND t.tipo IN ('DEPOSITO', 'RETIRO')
              AND t.fecha_hora::date = CURRENT_DATE
        """),
        {"socio_id": socio_id, "moneda_id": moneda_id},
    ).scalar_one()
    fraccionada = Decimal(acumulado) + monto >= threshold
    return fraccionada, fraccionada


def validar_declaracion(declaracion) -> None:
    if declaracion is None:
        return
    data = declaracion.model_dump() if hasattr(declaracion, "model_dump") else declaracion
    if data.get("origen") not in ORIGENES or data.get("destino") not in DESTINOS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origen o destino UIF inválido")
    if data.get("origen") == "OTRO" and not (data.get("origen_detalle") or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debe detallar el origen de fondos")
    if data.get("destino") == "OTRO" and not (data.get("destino_detalle") or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debe detallar el destino de fondos")
    if not (data.get("actividad_economica") or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La actividad económica es obligatoria")
    if data.get("realizado_por") not in {"TITULAR", "TERCERO"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Realizado por UIF inválido")
    if data.get("realizado_por") == "TERCERO" and not all(
        (data.get(field) or "").strip()
        for field in ("tercero_nombre", "tercero_ci", "tercero_parentesco")
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Los datos del tercero son obligatorios")
    if data.get("declara_bajo_juramento") is not True:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debe aceptar la declaración bajo juramento")


def registrar_declaracion(
    db: Session,
    *,
    declaracion,
    tipo_operacion: str,
    monto: Decimal,
    moneda_id: int,
    socio_id: int,
    usuario_id: int,
    cooperativa_id: int,
    fraccionada: bool,
) -> int | None:
    if declaracion is None:
        return None
    validar_declaracion(declaracion)
    data = declaracion.model_dump() if hasattr(declaracion, "model_dump") else declaracion
    return db.execute(
        text("""
            INSERT INTO declaracion_jurada_uif (
                origen, destino, tipo_operacion, monto, moneda_id, socio_id,
                realizado_por, tercero_nombre, tercero_ci, tercero_parentesco,
                actividad_economica, origen_detalle, destino_detalle,
                declara_bajo_juramento, fraccionada, usuario_id, cooperativa_id
            ) VALUES (
                :origen, :destino, :tipo_operacion, :monto, :moneda_id, :socio_id,
                :realizado_por, :tercero_nombre, :tercero_ci, :tercero_parentesco,
                :actividad_economica, :origen_detalle, :destino_detalle,
                :declara_bajo_juramento, :fraccionada, :usuario_id, :cooperativa_id
            ) RETURNING id
        """),
        {
            **data,
            "tipo_operacion": tipo_operacion,
            "monto": monto,
            "moneda_id": moneda_id,
            "socio_id": socio_id,
            "fraccionada": fraccionada,
            "usuario_id": usuario_id,
            "cooperativa_id": cooperativa_id,
        },
    ).scalar_one()


def exigir_declaracion_si_corresponde(
    db: Session,
    *,
    declaracion,
    socio_id: int,
    moneda_id: int,
    moneda_iso: str,
    monto: Decimal,
) -> bool:
    requerida, fraccionada = requiere_declaracion(
        db,
        socio_id=socio_id,
        moneda_id=moneda_id,
        moneda_iso=moneda_iso,
        monto=monto,
    )
    if requerida and declaracion is None:
        raise HTTPException(status_code=428, detail=ERROR_REQUIERE_UIF)
    validar_declaracion(declaracion)
    return fraccionada
