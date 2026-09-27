"""Side-effect-free helpers for CU-W28 alert classification."""

from datetime import date
from decimal import Decimal


def construir_candidatos_alerta(cuotas, fecha: date, *, dias_gracia_mora: int, en_mora: bool):
    """Return one active-condition candidate per unpaid installment and risk signal."""
    candidatos = []
    for cuota in cuotas:
        if (cuota.estado_pago or "PENDIENTE") in {"PAGADA", "PAGADO"}:
            continue
        diferencia = (cuota.fecha_vencimiento - fecha).days
        if 0 <= diferencia <= 5:
            tipo, severidad = "CUOTA_POR_VENCER", "INFO"
        elif diferencia < 0 and -diferencia <= dias_gracia_mora:
            tipo, severidad = "CUOTA_VENCIDA", "ADVERTENCIA"
        elif diferencia < 0 and en_mora:
            tipo, severidad = "MORA", "CRITICA"
        else:
            continue
        candidatos.append({
            "tipo": tipo,
            "severidad": severidad,
            "tabla_amortizacion_id": cuota.id,
            "mensaje": f"{tipo.replace('_', ' ').title()} — cuota {cuota.numero_cuota}",
            "datos": {"numero_cuota": cuota.numero_cuota,
                      "dias": abs(diferencia),
                      "monto_cuota": str(Decimal(cuota.monto_cuota_total))},
        })
    return candidatos


def reconciliar_alertas(alertas, candidatos):
    """Compute active rows to retain/resolve and candidate rows safe to create."""
    key = lambda row: (row.tipo, row.tabla_amortizacion_id)
    candidate_by_key = {(row["tipo"], row.get("tabla_amortizacion_id")): row for row in candidatos}
    active = [row for row in alertas if row.estado == "ACTIVA"]
    terminal = {(row.tipo, row.tabla_amortizacion_id) for row in alertas
                if row.estado in {"ATENDIDA", "DESCARTADA"}}
    active_keys = {key(row) for row in active}
    kept = [row for row in active if key(row) in candidate_by_key]
    resolved = [row for row in active if key(row) not in candidate_by_key]
    created = [row for row in candidatos
               if (row["tipo"], row.get("tabla_amortizacion_id")) not in active_keys
               and (row["tipo"], row.get("tabla_amortizacion_id")) not in terminal]
    return kept, resolved, created


def candidato_riesgo_alto(nivel_riesgo: str, *, eligible: bool, probability):
    """Build the synthetic model warning only for credits eligible for W23 prediction."""
    if not eligible or nivel_riesgo != "ALTO":
        return None
    return {"tipo": "RIESGO_ALTO", "severidad": "ADVERTENCIA",
            "tabla_amortizacion_id": None,
            "mensaje": "Riesgo alto según modelo sintético (informativo)",
            "datos": {"probabilidad_mora": str(probability), "nivel_riesgo": nivel_riesgo}}
