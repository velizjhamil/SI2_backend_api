"""Public, privacy-preserving receipt verification endpoint."""

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.models.models import ComprobanteTransaccion, Cooperativa, CuentaAhorro
from app.services.comprobantes_movil import mask_account, verify_receipt

router = APIRouter()


def _public_data(db: Session, receipt: ComprobanteTransaccion) -> dict:
    cooperative = db.get(Cooperativa, receipt.cooperativa_id)
    source = db.get(CuentaAhorro, receipt.cuenta_origen_id)
    destination = db.get(CuentaAhorro, receipt.cuenta_destino_id) if receipt.cuenta_destino_id else None
    return {
        "estado": "VÁLIDO" if verify_receipt(receipt) else "INVÁLIDO",
        "numero": receipt.numero,
        "tipo": receipt.tipo,
        "emitido_en": receipt.emitido_en.isoformat(),
        "monto": str(receipt.monto),
        "moneda": receipt.moneda,
        "cooperativa": cooperative.nombre if cooperative else "",
        "cuenta_origen": mask_account(source.numero if source else None),
        "cuenta_destino": mask_account(destination.numero if destination else None),
    }


@router.get("/comprobantes/{codigo}")
def verificar_comprobante(
    codigo: str,
    formato: str | None = Query(None),
    db: Session = Depends(get_db),
):
    receipt = db.execute(select(ComprobanteTransaccion).where(
        ComprobanteTransaccion.codigo_verificacion == codigo,
    )).scalar_one_or_none()
    if receipt is None:
        if formato == "json":
            raise HTTPException(status_code=404, detail="No encontrado")
        return HTMLResponse(
            "<!doctype html><html lang='es'><meta charset='utf-8'>"
            "<title>Verificación de comprobante</title><main><h1>Verificación de comprobante</h1>"
            "<p>No encontrado</p></main></html>", status_code=404,
        )
    data = _public_data(db, receipt)
    if formato == "json":
        return data
    labels = {
        "estado": "Estado", "numero": "Número", "tipo": "Tipo", "emitido_en": "Fecha",
        "monto": "Monto", "moneda": "Moneda", "cooperativa": "Cooperativa",
        "cuenta_origen": "Cuenta origen", "cuenta_destino": "Cuenta destino",
    }
    values = "".join(
        f"<dt>{labels[key]}</dt><dd>{escape(value or '—')}</dd>"
        for key, value in data.items()
    )
    return HTMLResponse(
        "<!doctype html><html lang='es'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Verificación de comprobante</title><main><h1>Verificación de comprobante</h1>"
        f"<p>{escape(data['estado'])}</p><dl>{values}</dl></main></html>"
    )
