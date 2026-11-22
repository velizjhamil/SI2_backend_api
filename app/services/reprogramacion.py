"""Pure rescheduling (reprogramación) calculation over a credit's unpaid installments."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.amortizacion import calcular_teac, generar_plan_pagos
from app.services.cobro_cuotas import CUOTAS_CERRADAS, resumir_morosidad

_CENT = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def plan_reprogramacion(
    *,
    cuotas,
    saldo_capital: Decimal,
    tasa_anual: Decimal,
    tipo_amortizacion: str,
    tasa_seguro_desgravamen: Decimal,
    nuevo_plazo_meses: int,
    hoy: date,
    tasa_mora_anual: Decimal,
    dias_gracia_mora: int,
) -> dict:
    """New schedule for the unpaid capital.

    Interest of overdue unpaid installments is deferred into the first new
    installment, never capitalized (ASFI forbids capitalizing interest). The
    late fee accrued until today is reported as forgiven.
    """
    abiertas = [row for row in cuotas if (row.estado_pago or "PENDIENTE") not in CUOTAS_CERRADAS]
    interes_diferido = _cents(sum(
        (Decimal(row.monto_interes) for row in abiertas if row.fecha_vencimiento < hoy), Decimal("0"),
    ))
    mora = resumir_morosidad(cuotas=cuotas, fecha=hoy, tasa_mora_anual=Decimal(tasa_mora_anual),
                             dias_gracia_mora=dias_gracia_mora)
    plan = generar_plan_pagos(
        monto=Decimal(saldo_capital), tasa_anual=Decimal(tasa_anual), plazo_meses=nuevo_plazo_meses,
        tipo_amortizacion=tipo_amortizacion, fecha_desembolso=hoy,
        tasa_seguro_desgravamen=Decimal(tasa_seguro_desgravamen),
    )
    if interes_diferido > 0:
        primera = plan["cuotas"][0]
        primera["interes"] = _cents(primera["interes"] + interes_diferido)
        primera["cuota"] = _cents(primera["cuota"] + interes_diferido)
        plan["total_interes"] = _cents(plan["total_interes"] + interes_diferido)
        plan["total_pagar"] = _cents(plan["total_pagar"] + interes_diferido)
        plan["teac"] = calcular_teac(Decimal(saldo_capital), [row["cuota"] for row in plan["cuotas"]])
    return {
        "plan": plan,
        "saldo_capital": _cents(saldo_capital),
        "interes_diferido": interes_diferido,
        "mora_condonada": _cents(mora["monto_penalizado"]),
        "cuotas_reprogramadas": len(abiertas),
    }
