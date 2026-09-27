"""Hand-calculated tests for the CU-W24 amortization schedule service."""

from datetime import date
from decimal import Decimal
from importlib import import_module, util

import pytest


def _service():
    spec = util.find_spec("app.services.amortizacion")
    assert spec is not None, "app.services.amortizacion has not been implemented"
    return import_module("app.services.amortizacion")


def _plan(tipo: str, *, tasa=Decimal("12.00"), first_due=date(2026, 1, 31)):
    service = _service()
    return service.generar_plan_pagos(
        monto=Decimal("1000.00"),
        tasa_anual=tasa,
        plazo_meses=3,
        tipo_amortizacion=tipo,
        fecha_desembolso=date(2025, 12, 17),
        fecha_primer_vencimiento=first_due,
    )


def test_frances_schedule_has_hand_calculated_values_and_residual_last_installment():
    plan = _plan("FRANCES")
    assert [row["cuota"] for row in plan["cuotas"]] == [
        Decimal("340.02"), Decimal("340.02"), Decimal("340.03")
    ]
    assert [row["capital"] for row in plan["cuotas"]] == [
        Decimal("330.02"), Decimal("333.32"), Decimal("336.66")
    ]
    assert [row["interes"] for row in plan["cuotas"]] == [
        Decimal("10.00"), Decimal("6.70"), Decimal("3.37")
    ]
    assert [row["saldo_inicial"] for row in plan["cuotas"]] == [
        Decimal("1000.00"), Decimal("669.98"), Decimal("336.66")
    ]
    assert [row["saldo_final"] for row in plan["cuotas"]] == [
        Decimal("669.98"), Decimal("336.66"), Decimal("0.00")
    ]
    assert plan["total_capital"] == Decimal("1000.00")
    assert plan["total_interes"] == Decimal("20.07")
    assert plan["total_pagar"] == Decimal("1020.07")


def test_aleman_schedule_has_hand_calculated_fixed_principal_and_rounded_residual():
    plan = _plan("ALEMAN")
    assert [row["capital"] for row in plan["cuotas"]] == [
        Decimal("333.33"), Decimal("333.33"), Decimal("333.34")
    ]
    assert [row["interes"] for row in plan["cuotas"]] == [
        Decimal("10.00"), Decimal("6.67"), Decimal("3.33")
    ]
    assert [row["cuota"] for row in plan["cuotas"]] == [
        Decimal("343.33"), Decimal("340.00"), Decimal("336.67")
    ]
    assert plan["total_capital"] == Decimal("1000.00")
    assert plan["total_interes"] == Decimal("20.00")
    assert plan["total_pagar"] == Decimal("1020.00")


@pytest.mark.parametrize("tipo", ["FRANCES", "ALEMAN"])
def test_zero_rate_and_last_installment_absorb_principal_rounding(tipo):
    plan = _plan(tipo, tasa=Decimal("0.00"))
    assert [row["capital"] for row in plan["cuotas"]] == [
        Decimal("333.33"), Decimal("333.33"), Decimal("333.34")
    ]
    assert [row["interes"] for row in plan["cuotas"]] == [
        Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    ]
    assert plan["total_capital"] == Decimal("1000.00")
    assert plan["total_pagar"] == Decimal("1000.00")


def test_due_dates_keep_the_anchor_day_and_clamp_to_each_month_end():
    plan = _plan("ALEMAN")
    assert [row["fecha_vencimiento"] for row in plan["cuotas"]] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)
    ]


def test_january_31_anchor_is_restored_after_clamping_february():
    plan = _service().generar_plan_pagos(
        monto=Decimal("1000.00"),
        tasa_anual=Decimal("0.00"),
        plazo_meses=5,
        tipo_amortizacion="FRANCES",
        fecha_desembolso=date(2025, 12, 17),
        fecha_primer_vencimiento=date(2026, 1, 31),
    )
    assert [row["fecha_vencimiento"] for row in plan["cuotas"]] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
        date(2026, 4, 30), date(2026, 5, 31),
    ]


def test_first_due_override_on_day_30_keeps_day_30_after_february():
    plan = _service().generar_plan_pagos(
        monto=Decimal("1000.00"),
        tasa_anual=Decimal("0.00"),
        plazo_meses=4,
        tipo_amortizacion="FRANCES",
        fecha_desembolso=date(2025, 12, 17),
        fecha_primer_vencimiento=date(2026, 1, 30),
    )
    assert [row["fecha_vencimiento"] for row in plan["cuotas"]] == [
        date(2026, 1, 30), date(2026, 2, 28), date(2026, 3, 30),
        date(2026, 4, 30),
    ]


def test_default_first_due_date_is_one_clamped_month_after_disbursement():
    plan = _service().generar_plan_pagos(
        monto=Decimal("1000.00"),
        tasa_anual=Decimal("0.00"),
        plazo_meses=1,
        tipo_amortizacion="FRANCES",
        fecha_desembolso=date(2026, 1, 31),
    )
    assert plan["fecha_primer_vencimiento"] == date(2026, 2, 28)


def test_default_due_dates_keep_disbursement_day_after_month_end_clamping():
    plan = _service().generar_plan_pagos(
        monto=Decimal("1000.00"),
        tasa_anual=Decimal("0.00"),
        plazo_meses=4,
        tipo_amortizacion="FRANCES",
        fecha_desembolso=date(2026, 1, 31),
    )
    assert [row["fecha_vencimiento"] for row in plan["cuotas"]] == [
        date(2026, 2, 28), date(2026, 3, 31),
        date(2026, 4, 30), date(2026, 5, 31),
    ]


def test_legacy_installment_estimator_keeps_w21_w23_values():
    from app.api.v1.endpoints.creditos import _cuota_estimada

    assert _cuota_estimada(
        Decimal("1200.00"), 12, Decimal("18.00"), "FRANCES"
    ) == Decimal("110.02")
    assert _cuota_estimada(
        Decimal("1200.00"), 12, Decimal("18.00"), "ALEMAN"
    ) == Decimal("118.00")
