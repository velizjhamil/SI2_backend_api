"""Pure calculations for installment debt and arrears."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP


CENTAVO = Decimal("0.01")
CIEN = Decimal("100")
DIAS_ANIO = Decimal("360")


def calcular_mora(
    *,
    capital_cuota: Decimal,
    tasa_mora_anual: Decimal,
    fecha_vencimiento: date,
    fecha_pago: date,
    dias_gracia_mora: int,
) -> dict[str, Decimal | int | bool]:
    """Calculate days late and simple 360-day late fee after the grace limit."""
    dias_reales = max((fecha_pago - fecha_vencimiento).days, 0)
    if dias_reales <= dias_gracia_mora:
        return {"dias_atraso": 0, "en_mora": False, "mora": Decimal("0.00")}

    mora = (
        Decimal(capital_cuota)
        * Decimal(tasa_mora_anual)
        / CIEN
        / DIAS_ANIO
        * Decimal(dias_reales)
    ).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    return {"dias_atraso": dias_reales, "en_mora": True, "mora": mora}


def resumir_morosidad(
    *,
    cuotas,
    fecha: date,
    tasa_mora_anual: Decimal,
    dias_gracia_mora: int,
) -> dict[str, Decimal | int | str]:
    """Summarize unpaid installments that have exceeded their grace period."""
    vencidas = []
    for cuota in cuotas:
        if (cuota.estado_pago or "PENDIENTE") in {"PAGADA", "PAGADO"}:
            continue
        calculo = calcular_mora(
            capital_cuota=cuota.monto_capital,
            tasa_mora_anual=tasa_mora_anual,
            fecha_vencimiento=cuota.fecha_vencimiento,
            fecha_pago=fecha,
            dias_gracia_mora=dias_gracia_mora,
        )
        if calculo["en_mora"]:
            vencidas.append((cuota, calculo))
    return {
        "estado": "EN_MORA" if vencidas else "AL_DIA",
        "dias_de_retaso": max(
            (calculo["dias_atraso"] for _, calculo in vencidas), default=0
        ),
        "monto_penalizado": sum(
            (calculo["mora"] for _, calculo in vencidas), Decimal("0.00")
        ).quantize(CENTAVO, rounding=ROUND_HALF_UP),
        "cuotas_vencidas": len(vencidas),
        "monto_vencido": sum(
            (cuota.monto_cuota_total for cuota, _ in vencidas), Decimal("0.00")
        ).quantize(CENTAVO, rounding=ROUND_HALF_UP),
    }
