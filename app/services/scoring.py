"""Deterministic, explainable CU-W23 credit scoring rules."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


VERSION_MODELO = "reglas-v2"
_CERO = Decimal("0")
_CIEN = Decimal("100")

_DESCRIPCIONES = {
    "CAPACIDAD_PAGO": "Relación cuota-ingreso respecto al máximo del producto",
    "CALIFICACION_ASFI": "Calificación de riesgo ASFI",
    "ANTIGUEDAD_LABORAL": "Antigüedad laboral declarada",
    "ENDEUDAMIENTO": "Cuota de deudas respecto al ingreso mensual",
    "ANTIGUEDAD_SOCIO": "Antigüedad como socio de la cooperativa",
    "AHORRO": "Saldo disponible de ahorro en la moneda solicitada",
    "HISTORIAL_INTERNO": "Historial de créditos internos y morosidad",
}

_MOTIVOS = {
    "CUOTA_SUPERA_CAPACIDAD": "La cuota estimada supera la capacidad de pago disponible.",
    "CALIFICACION_ASFI_CRITICA": "La calificación ASFI E o F requiere rechazo.",
    "SIN_EVALUACION": "No existe evaluación socioeconómica válida para calcular el riesgo.",
    "MORA_VIGENTE": "El socio tiene una morosidad vigente en un crédito anterior.",
    "RATIO_SUPERA_MAXIMO": "La relación cuota-ingreso supera el máximo del producto.",
    "GARANTIA_INSUFICIENTE": "La cobertura de garantías verificadas no alcanza el mínimo del producto.",
}


def _decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _ratio(cuota: Decimal, ingreso: Decimal) -> Decimal | None:
    if ingreso <= 0:
        return None
    return cuota / ingreso * _CIEN


def _meses_completos(fecha_inicio: date, fecha_fin: date) -> int:
    meses = (fecha_fin.year - fecha_inicio.year) * 12 + fecha_fin.month - fecha_inicio.month
    if fecha_fin.day < fecha_inicio.day:
        meses -= 1
    return max(0, meses)


def _factor(codigo: str, points: int, maximum: int, value: str, reason: str) -> dict:
    return {
        "codigo": codigo,
        "descripcion": _DESCRIPCIONES[codigo],
        "puntos": points,
        "maximo": maximum,
        "valor": value,
        "motivo": reason,
    }


def _factor_capacidad(ratio: Decimal | None, maximum_ratio: Decimal) -> dict:
    if ratio is None:
        points = 0
        reason = "No hay ingreso mensual positivo para calcular la relación."
        value = "No calculable"
    elif ratio <= maximum_ratio / Decimal("2"):
        points = 300
        reason = "La relación es igual o menor al 50% del máximo permitido."
        value = f"{ratio:.2f}% de {maximum_ratio:.2f}%"
    elif ratio <= maximum_ratio:
        raw = Decimal("300") - (
            (ratio - maximum_ratio / Decimal("2"))
            / (maximum_ratio / Decimal("2"))
            * Decimal("180")
        )
        points = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        reason = "La relación está dentro del máximo, pero supera la mitad del límite."
        value = f"{ratio:.2f}% de {maximum_ratio:.2f}%"
    else:
        points = 0
        reason = "La relación supera el máximo permitido por el producto."
        value = f"{ratio:.2f}% de {maximum_ratio:.2f}%"
    return _factor("CAPACIDAD_PAGO", points, 300, value, reason)


def _factor_asfi(rating: str | None) -> dict:
    points = {"A": 200, "B": 150, "C": 80, "D": 20, "E": 0, "F": 0}.get(rating, 0)
    reason = f"La categoría {rating} asigna {points} puntos." if rating else "No hay calificación ASFI registrada."
    return _factor("CALIFICACION_ASFI", points, 200, rating or "Sin dato", reason)


def _factor_labor(months: int | None) -> dict:
    months = months if months is not None else 0
    points = 100 if months >= 36 else 80 if months >= 24 else 60 if months >= 12 else 30 if months >= 6 else 0
    return _factor(
        "ANTIGUEDAD_LABORAL", points, 100, f"{months} meses",
        f"La antigüedad laboral de {months} meses asigna {points} puntos.",
    )


def _factor_deuda(debts: Decimal, income: Decimal) -> tuple[dict, Decimal | None]:
    ratio = _ratio(debts, income)
    if ratio is None:
        points, value, reason = 0, "No calculable", "No hay ingreso mensual positivo para calcular el endeudamiento."
    elif debts == 0:
        points, value, reason = 100, f"{ratio:.2f}%", "No se declararon cuotas de otras deudas."
    elif ratio <= Decimal("10"):
        points, value, reason = 80, f"{ratio:.2f}%", "Las cuotas de deuda representan hasta el 10% del ingreso."
    elif ratio <= Decimal("20"):
        points, value, reason = 50, f"{ratio:.2f}%", "Las cuotas de deuda representan hasta el 20% del ingreso."
    elif ratio <= Decimal("30"):
        points, value, reason = 20, f"{ratio:.2f}%", "Las cuotas de deuda representan hasta el 30% del ingreso."
    else:
        points, value, reason = 0, f"{ratio:.2f}%", "Las cuotas de deuda superan el 30% del ingreso."
    return _factor("ENDEUDAMIENTO", points, 100, value, reason), ratio


def _factor_member(socio, today: date) -> dict:
    months = _meses_completos(socio.fecha_registro, today)
    points = 100 if months >= 24 else 70 if months >= 12 else 40 if months >= 6 else 10
    return _factor(
        "ANTIGUEDAD_SOCIO", points, 100, f"{months} meses",
        f"La antigüedad como socio de {months} meses asigna {points} puntos.",
    )


def _factor_savings(accounts: Iterable, currency_id: int | None, amount: Decimal) -> tuple[dict, Decimal]:
    total = sum(
        (_decimal(account.saldo_disponible) for account in accounts
         if account.estado == "ACTIVA" and account.moneda_id == currency_id),
        _CERO,
    )
    ratio = total / amount * _CIEN if amount > 0 else _CERO
    points = 100 if ratio >= 20 else 70 if ratio >= 10 else 40 if ratio >= 5 else 20 if ratio > 0 else 0
    reason = f"El saldo elegible representa {ratio:.2f}% del monto solicitado y asigna {points} puntos."
    return _factor("AHORRO", points, 100, f"{total:.2f} ({ratio:.2f}%)", reason), ratio


def _factor_history(has_credit_history: bool, has_current_arrears: bool) -> dict:
    points = 0 if has_current_arrears else 100 if has_credit_history else 50
    if has_current_arrears:
        reason = "Se encontró morosidad vigente en un crédito anterior."
        value = "Morosidad vigente"
    elif has_credit_history:
        reason = "Existen créditos anteriores sin morosidad vigente."
        value = "Créditos previos sin mora"
    else:
        reason = "No existen créditos previos en la cooperativa."
        value = "Sin historial previo"
    return _factor("HISTORIAL_INTERNO", points, 100, value, reason)


def score_application(
    solicitud,
    *,
    savings_accounts: Iterable = (),
    has_credit_history: bool = False,
    has_current_arrears: bool = False,
    today: date | None = None,
) -> dict:
    """Calculate the task's reglas-v2 score and deterministic explanation."""
    from app.api.v1.endpoints.creditos import _cuota_estimada

    today = today or date.today()
    evaluation = solicitud.evaluacion
    income = _decimal(evaluation.ingreso_mensual) if evaluation is not None else _CERO
    expenses = _decimal(evaluation.egreso_mensual) if evaluation is not None else _CERO
    debt_payments = _decimal(evaluation.cuota_deudas_mensual) if evaluation is not None else _CERO
    capacity = income - expenses - debt_payments
    rating = evaluation.calificacion_asfi if evaluation is not None else None
    labor_months = evaluation.antiguedad_laboral_meses if evaluation is not None else None
    maximum_ratio = _decimal(solicitud.producto.relacion_cuota_ingreso_max)
    installment = _cuota_estimada(
        _decimal(solicitud.monto),
        int(solicitud.plazo_meses),
        _decimal(solicitud.tasa_interes),
        solicitud.producto.tipo_amortizacion,
    )
    income_ratio = _ratio(installment, income)
    debt_factor, debt_ratio = _factor_deuda(debt_payments, income)
    savings_factor, savings_ratio = _factor_savings(
        savings_accounts, solicitud.moneda_id, _decimal(solicitud.monto)
    )
    factors = [
        _factor_capacidad(income_ratio, maximum_ratio),
        _factor_asfi(rating),
        _factor_labor(labor_months),
        debt_factor,
        _factor_member(solicitud.socio, today),
        savings_factor,
        _factor_history(has_credit_history, has_current_arrears),
    ]
    knockouts = []
    if evaluation is not None and installment > capacity:
        knockouts.append({
            "codigo": "CUOTA_SUPERA_CAPACIDAD",
            "descripcion": _MOTIVOS["CUOTA_SUPERA_CAPACIDAD"],
            "efecto": "RECHAZADO",
        })
    if rating in {"E", "F"}:
        knockouts.append({
            "codigo": "CALIFICACION_ASFI_CRITICA",
            "descripcion": _MOTIVOS["CALIFICACION_ASFI_CRITICA"],
            "efecto": "RECHAZADO",
        })
    if evaluation is None or income <= 0:
        knockouts.append({
            "codigo": "SIN_EVALUACION",
            "descripcion": _MOTIVOS["SIN_EVALUACION"],
            "efecto": "RECHAZADO",
        })
    if has_current_arrears:
        knockouts.append({
            "codigo": "MORA_VIGENTE",
            "descripcion": _MOTIVOS["MORA_VIGENTE"],
            "efecto": "REVISION_MANUAL",
        })
    if income_ratio is not None and income_ratio > maximum_ratio:
        knockouts.append({
            "codigo": "RATIO_SUPERA_MAXIMO",
            "descripcion": _MOTIVOS["RATIO_SUPERA_MAXIMO"],
            "efecto": "REVISION_MANUAL",
        })
    if getattr(solicitud.producto, "requiere_garantia", False):
        verified = sum((_decimal(garantia.valor_realizable)
            for garantia in getattr(solicitud, "garantias", ()) if garantia.estado == "VERIFICADA"), _CERO)
        amount = _decimal(solicitud.monto)
        coverage = verified / amount * _CIEN if amount > 0 else _CERO
        minimum = _decimal(getattr(solicitud.producto, "cobertura_minima_garantia", _CIEN))
        if coverage < minimum:
            knockouts.append({
                "codigo": "GARANTIA_INSUFICIENTE",
                "descripcion": _MOTIVOS["GARANTIA_INSUFICIENTE"],
                "efecto": "REVISION_MANUAL",
            })

    score = sum(item["puntos"] for item in factors)
    if any(item["efecto"] == "RECHAZADO" for item in knockouts):
        verdict = "RECHAZADO"
    elif knockouts:
        verdict = "REVISION_MANUAL"
    elif score >= 700:
        verdict = "APROBADO"
    elif score >= 500:
        verdict = "REVISION_MANUAL"
    else:
        verdict = "RECHAZADO"

    weakest = sorted(factors, key=lambda item: (item["puntos"], item["codigo"]))[:2]
    strongest = sorted(factors, key=lambda item: (-item["puntos"], item["codigo"]))[:2]
    explanation = f"Dictamen {verdict} con puntaje {score}/1000."
    if knockouts:
        explanation += " Reglas de excepción: " + " ".join(item["descripcion"] for item in knockouts)
    explanation += " Factores más débiles: " + " ".join(
        f"{item['codigo']}: {item['motivo']}" for item in weakest
    )
    explanation += " Factores más fuertes: " + " ".join(
        f"{item['codigo']}: {item['motivo']}" for item in strongest
    )

    return {
        "version_modelo": VERSION_MODELO,
        "score": score,
        "dictamen": verdict,
        "factores": factors,
        "knockouts": knockouts,
        "explicacion": explanation,
        "cuota_estimada": installment,
        "relacion_cuota_ingreso": income_ratio,
    }
