"""Issuance and verification of tamper-evident mobile transaction receipts."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.bitacora import registrar_accion
from app.core.config import settings
from app.models.models import ComprobanteTransaccion

logger = logging.getLogger(__name__)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _crockford_code(signature: bytes) -> str:
    value = int.from_bytes(signature[:10], "big")
    chars = "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(75, -1, -5))
    return "-".join(chars[index:index + 4] for index in range(0, 16, 4))


def canonical_receipt_string(receipt: ComprobanteTransaccion) -> str:
    issued = receipt.emitido_en
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)
    issued = issued.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    fields = (
        receipt.numero, receipt.tipo, str(receipt.cooperativa_id), str(receipt.socio_id),
        format(Decimal(receipt.monto), ".2f"), receipt.moneda,
        str(receipt.cuenta_origen_id), str(receipt.cuenta_destino_id or ""),
        str(receipt.credito_id or ""), str(receipt.numero_cuota or ""), issued,
    )
    return "|".join(fields)


def sign_receipt(receipt: ComprobanteTransaccion, key: str | None = None) -> tuple[str, str]:
    signing_key = key or settings.COMPROBANTE_SIGNING_KEY
    if not signing_key:
        raise RuntimeError("COMPROBANTE_SIGNING_KEY is not configured")
    digest = hmac.new(signing_key.encode(), canonical_receipt_string(receipt).encode(), hashlib.sha256).digest()
    return digest.hex(), _crockford_code(digest)


def verify_receipt(receipt: ComprobanteTransaccion, key: str | None = None) -> bool:
    try:
        signature, code = sign_receipt(receipt, key)
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(signature, receipt.firma) and hmac.compare_digest(code, receipt.codigo_verificacion)


def next_receipt_number(db: Session, cooperative_id: int, receipt_type: str, *, now: datetime | None = None) -> str:
    year = (now or datetime.now(timezone.utc)).year
    last = db.execute(text("""
        INSERT INTO comprobante_secuencia (cooperativa_id, tipo, anio, ultimo)
        VALUES (:cooperativa_id, :tipo, :anio, 1)
        ON CONFLICT (cooperativa_id, tipo, anio)
        DO UPDATE SET ultimo = comprobante_secuencia.ultimo + 1
        RETURNING ultimo
    """), {"cooperativa_id": cooperative_id, "tipo": receipt_type, "anio": year}).scalar_one()
    prefix = "TRF" if receipt_type == "TRANSFERENCIA" else "PAG"
    return f"{prefix}-{year}-{last:06d}"


def issue_receipt(
    db: Session, *, receipt_type: str, cooperative_id: int, member_id: int, user_id: int,
    amount: Decimal, currency: str, source_account_id: int, destination_account_id: int | None = None,
    credit_id: int | None = None, installment_number: int | None = None,
    outgoing_transaction_id: int | None = None, incoming_transaction_id: int | None = None,
    payment_id: int | None = None, description: str | None = None, request=None,
) -> ComprobanteTransaccion:
    now = datetime.now(timezone.utc)
    receipt = ComprobanteTransaccion(
        cooperativa_id=cooperative_id,
        numero=next_receipt_number(db, cooperative_id, receipt_type, now=now),
        tipo=receipt_type, canal="MOVIL", socio_id=member_id, usuario_id=user_id,
        monto=amount, moneda=currency, cuenta_origen_id=source_account_id,
        cuenta_destino_id=destination_account_id, credito_id=credit_id,
        numero_cuota=installment_number, transaccion_salida_id=outgoing_transaction_id,
        transaccion_entrada_id=incoming_transaction_id, pago_cuota_id=payment_id,
        glosa=description, emitido_en=now,
    )
    receipt.firma, receipt.codigo_verificacion = sign_receipt(receipt)
    db.add(receipt)
    db.flush()
    registrar_accion(
        db, accion="EMITIR_COMPROBANTE_MOVIL", modulo="COMPROBANTES",
        usuario_id=user_id, cooperativa_id=cooperative_id,
        descripcion=f"Comprobante {receipt.numero} emitido para {receipt_type}", request=request,
    )
    return receipt


def mask_account(account_number: str | None) -> str | None:
    if account_number is None:
        return None
    if len(account_number) <= 4:
        return "****"
    return f"****{account_number[-4:]}"


def receipt_payload(receipt: ComprobanteTransaccion, *, source_number: str, destination_number: str | None = None) -> dict:
    return {
        "id": receipt.id, "numero": receipt.numero, "tipo": receipt.tipo, "canal": receipt.canal,
        "monto": str(receipt.monto), "moneda": receipt.moneda,
        "cuenta_origen": mask_account(source_number),
        "cuenta_destino": mask_account(destination_number), "credito_id": receipt.credito_id,
        "numero_cuota": receipt.numero_cuota, "glosa": receipt.glosa,
        "emitido_en": receipt.emitido_en.isoformat(), "codigo_verificacion": receipt.codigo_verificacion,
        "url_verificacion": f"{settings.PUBLIC_API_URL.rstrip('/')}/verificacion/comprobantes/{receipt.codigo_verificacion}",
    }
