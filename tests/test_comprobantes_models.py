"""Schema contract for the first CU-W30 persistence slice."""

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
