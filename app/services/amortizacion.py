"""Decimal amortization helpers for credit simulation and payment schedules."""

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext


_CENTAVO = Decimal("0.01")
_CIEN = Decimal("100")
_DOCE = Decimal("12")


def _redondear_monto(monto: Decimal) -> Decimal:
    return monto.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _tasa_mensual(tasa_anual: Decimal) -> Decimal:
    return tasa_anual / (_DOCE * _CIEN)


def calcular_cuota_inicial(
    monto: Decimal,
    plazo_meses: int,
    tasa_anual: Decimal,
    tipo_amortizacion: str,
) -> Decimal:
    """Return the installment amount currently used by CU-W21/W23."""
    if plazo_meses <= 0:
        raise ValueError("plazo_meses debe ser mayor a cero")
    if monto <= 0:
        raise ValueError("monto debe ser mayor a cero")
    if tasa_anual < 0:
        raise ValueError("tasa_anual no puede ser negativa")
    if tipo_amortizacion not in {"FRANCES", "ALEMAN"}:
        raise ValueError("tipo_amortizacion debe ser FRANCES o ALEMAN")

    with localcontext() as context:
        context.prec = 32
        tasa = _tasa_mensual(tasa_anual)
        if tipo_amortizacion == "ALEMAN":
            cuota = monto / Decimal(plazo_meses) + monto * tasa
        elif tasa == 0:
            cuota = monto / Decimal(plazo_meses)
        else:
            cuota = monto * tasa / (
                Decimal(1) - (Decimal(1) + tasa) ** (-plazo_meses)
            )
        return _redondear_monto(cuota)


def _sumar_meses(fecha: date, meses: int, dia_ancla: int) -> date:
    total_meses = fecha.year * 12 + (fecha.month - 1) + meses
    year, month_index = divmod(total_meses, 12)
    month = month_index + 1
    day = min(dia_ancla, monthrange(year, month)[1])
    return date(year, month, day)


def generar_plan_pagos(
    *,
    monto: Decimal,
    tasa_anual: Decimal,
    plazo_meses: int,
    tipo_amortizacion: str,
    fecha_desembolso: date,
    fecha_primer_vencimiento: date | None = None,
) -> dict:
    """Generate a full French/German amortization schedule using Decimal only."""
    cuota_fija = calcular_cuota_inicial(
        monto, plazo_meses, tasa_anual, tipo_amortizacion
    )
    if fecha_primer_vencimiento is None:
        dia_ancla = fecha_desembolso.day
        primer_vencimiento = _sumar_meses(
            fecha_desembolso, 1, dia_ancla
        )
    else:
        primer_vencimiento = fecha_primer_vencimiento
        dia_ancla = primer_vencimiento.day
    tasa = _tasa_mensual(tasa_anual)

    with localcontext() as context:
        context.prec = 32
        saldo = _redondear_monto(monto)
        capital_aleman = _redondear_monto(monto / Decimal(plazo_meses))
        cuotas: list[dict] = []
        for numero in range(1, plazo_meses + 1):
            saldo_inicial = saldo
            interes = _redondear_monto(saldo_inicial * tasa)
            if numero == plazo_meses:
                capital = saldo_inicial
            elif tipo_amortizacion == "ALEMAN":
                capital = capital_aleman
            else:
                capital = cuota_fija - interes
            capital = min(_redondear_monto(capital), saldo_inicial)
            saldo_final = _redondear_monto(saldo_inicial - capital)
            if numero == plazo_meses:
                saldo_final = Decimal("0.00")
            cuota = _redondear_monto(capital + interes)
            cuotas.append({
                "numero": numero,
                "fecha_vencimiento": _sumar_meses(primer_vencimiento, numero - 1, dia_ancla),
                "saldo_inicial": saldo_inicial,
                "capital": capital,
                "interes": interes,
                "cuota": cuota,
                "saldo_final": saldo_final,
                "estado_pago": "PENDIENTE",
            })
            saldo = saldo_final

    total_capital = _redondear_monto(sum((row["capital"] for row in cuotas), Decimal("0.00")))
    total_interes = _redondear_monto(sum((row["interes"] for row in cuotas), Decimal("0.00")))
    total_pagar = _redondear_monto(total_capital + total_interes)
    return {
        "tipo_amortizacion": tipo_amortizacion,
        "monto": _redondear_monto(monto),
        "tasa_interes": tasa_anual,
        "plazo_meses": plazo_meses,
        "fecha_desembolso": fecha_desembolso,
        "fecha_primer_vencimiento": primer_vencimiento,
        "cuotas": cuotas,
        "total_capital": total_capital,
        "total_interes": total_interes,
        "total_pagar": total_pagar,
    }
