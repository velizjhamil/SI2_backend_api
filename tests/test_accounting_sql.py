"""Shared SQL expressions for accounting-book and statement calculations."""


def test_nature_balance_delta_sql_produces_one_shared_rule():
    from app.api.v1.endpoints.accounting_sql import nature_balance_delta_sql

    assert nature_balance_delta_sql("pc.naturaleza", "SUM(d.debe)", "SUM(d.haber)") == (
        "CASE WHEN pc.naturaleza='DEUDORA' THEN (SUM(d.debe))-(SUM(d.haber)) "
        "ELSE (SUM(d.haber))-(SUM(d.debe)) END"
    )
