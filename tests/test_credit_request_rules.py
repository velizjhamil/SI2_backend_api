from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.services import credit_request_rules
from app.services.credit_request_rules import (
    cancel_credit_request,
    has_in_progress_request,
    validate_base_credit_request,
)


def product(**changes):
    values = {"estado": "ACTIVO", "monto_min": 100, "monto_max": 5000,
        "plazo_min_meses": 1, "plazo_max_meses": 24}
    values.update(changes)
    return SimpleNamespace(**values)


def test_shared_validation_preserves_w21_product_rules():
    with pytest.raises(HTTPException) as error:
        validate_base_credit_request(product(), monto=99, plazo_meses=12,
            destino="CONSUMO", destino_detalle=None)
    assert error.value.status_code == 400
    assert error.value.detail == "El monto está fuera del rango permitido para el producto"


def test_shared_in_progress_check_uses_existing_active_states():
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = 17
    assert has_in_progress_request(db, socio_id=3) is True


def test_mobile_cancel_requires_pending_without_officer_evaluation(monkeypatch):
    monkeypatch.setattr(credit_request_rules, "registrar_accion", lambda *args, **kwargs: None)
    row = SimpleNamespace(id=12, numero_solicitud="SOL-000012", estado="PENDIENTE", evaluacion_campo_id=None)
    db = Mock()
    cancel_credit_request(db, row, motivo="Reason enough", request=object(), user_id=5,
        cooperativa_id=7, require_mobile_eligibility=True)
    assert row.estado == "ANULADA"
    assert row.motivo_anulacion == "Reason enough"


def test_w21_cancel_keeps_observed_requests_editable(monkeypatch):
    monkeypatch.setattr(credit_request_rules, "registrar_accion", lambda *args, **kwargs: None)
    row = SimpleNamespace(id=12, numero_solicitud="SOL-000012", estado="OBSERVADA", evaluacion_campo_id=4)
    db = Mock()
    cancel_credit_request(db, row, motivo="Reason enough", request=object(), user_id=5,
        cooperativa_id=7, require_mobile_eligibility=False)
    assert row.estado == "ANULADA"
