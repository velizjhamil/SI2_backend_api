"""Schema contract for the first CU-W30 persistence slice."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import models
from app.schemas import schemas


def test_comprobante_model_exposes_persisted_header_contract():
    columns = models.ComprobanteContable.__table__.columns

    assert {
        "cooperativa_id",
        "numero",
        "gestion",
        "fecha_contable",
        "moneda_id",
        "estado",
        "usuario_id",
        "origen",
        "comprobante_reversion_id",
        "revierte_a_id",
        "motivo_anulacion",
        "fecha_registro",
    } <= set(columns.keys())
    assert any(
        constraint.name == "uq_comprobante_coop_tipo_gestion_numero"
        for constraint in models.ComprobanteContable.__table__.constraints
    )


def test_detalle_asiento_model_has_exactly_one_nonzero_side_constraint():
    table = models.DetalleAsiento.__table__

    assert {"glosa", "orden"} <= set(table.columns.keys())
    checks = {constraint.name: str(constraint.sqltext) for constraint in table.constraints if constraint.__class__.__name__ == "CheckConstraint"}
    assert "chk_detalle_asiento_un_solo_lado" in checks


def test_parametro_contable_model_is_unique_per_tenant_and_key():
    table = models.ParametroContable.__table__

    assert {"cooperativa_id", "clave", "plan_cuenta_id"} <= set(table.columns.keys())
    assert any(
        isinstance(constraint, models.UniqueConstraint)
        and set(constraint.columns.keys()) == {"cooperativa_id", "clave"}
        for constraint in table.constraints
    )


def test_parameter_schemas_require_positive_account_id_and_expose_defaults():
    request = schemas.ParametroContableUpdate(plan_cuenta_id=12)
    assert request.plan_cuenta_id == 12
    with pytest.raises(ValidationError):
        schemas.ParametroContableUpdate(plan_cuenta_id=0)

    item = schemas.ParametroContableOut(
        clave="CAJA", plan_cuenta_id=12, codigo="111.01", nombre="Billetes y monedas"
    )
    assert item.model_dump() == {
        "clave": "CAJA",
        "plan_cuenta_id": 12,
        "codigo": "111.01",
        "nombre": "Billetes y monedas",
    }


def _line(debe="0", haber="0"):
    return {"plan_cuenta_id": 1, "debe": debe, "haber": haber}


def _voucher(lineas):
    return {"tipo": "TRASPASO", "fecha_contable": "2026-09-27", "glosa": "Asiento de prueba", "moneda_id": 1, "lineas": lineas}


def test_voucher_line_requires_exactly_one_positive_side_and_rounds_half_up():
    assert schemas.ComprobanteLineaCreate.model_validate(_line(debe="10.005")).debe == Decimal("10.01")
    for bad in (_line(), _line(debe="5", haber="5")):
        with pytest.raises(ValidationError, match="exactamente uno de debe o haber"):
            schemas.ComprobanteLineaCreate.model_validate(bad)
    with pytest.raises(ValidationError):
        schemas.ComprobanteLineaCreate.model_validate(_line(debe="-1"))


def test_manual_voucher_requires_two_balanced_lines():
    ok = schemas.ComprobanteManualCreate.model_validate(_voucher([_line(debe="10.00"), _line(haber="10.00")]))
    assert sum(l.debe for l in ok.lineas) == sum(l.haber for l in ok.lineas) == Decimal("10.00")
    with pytest.raises(ValidationError, match="al menos dos líneas"):
        schemas.ComprobanteManualCreate.model_validate(_voucher([_line(debe="10.00")]))
    with pytest.raises(ValidationError, match="iguales y mayores que cero"):
        schemas.ComprobanteManualCreate.model_validate(_voucher([_line(debe="10.00"), _line(haber="9.99")]))
    with pytest.raises(ValidationError, match="cinco caracteres"):
        schemas.ComprobanteManualCreate.model_validate({**_voucher([_line(debe="1"), _line(haber="1")]), "glosa": " ab "})
