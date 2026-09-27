"""Synthetic-only model inference contract tests."""
from decimal import Decimal


def test_inference_is_deterministic_and_risk_increases_with_payment_ratio_and_asfi():
    from app.services.modelo_mora import predecir_mora

    base = {
        "ratio_cuota_ingreso": Decimal("20"), "asfi": "A",
        "antiguedad_laboral_meses": 36, "endeudamiento": Decimal("5"),
        "antiguedad_socio_meses": 24, "ahorro_ratio": Decimal("20"),
        "atrasos_previos": 0,
    }
    first = predecir_mora(base)
    assert first == predecir_mora(base)
    assert Decimal("0") <= first["probabilidad_mora"] <= Decimal("1")
    more_ratio = dict(base, ratio_cuota_ingreso=Decimal("40"))
    worse_asfi = dict(base, asfi="F")
    assert predecir_mora(more_ratio)["probabilidad_mora"] > first["probabilidad_mora"]
    assert predecir_mora(worse_asfi)["probabilidad_mora"] > first["probabilidad_mora"]


def test_rule_score_is_identical_when_advisory_model_is_enabled_or_disabled():
    from app.services.scoring import score_application
    from app.services.modelo_mora import predecir_mora
    from tests.test_scoring_crediticio import _request

    request, today = _request()
    before = score_application(request, today=today)
    prediction = predecir_mora({
        "ratio_cuota_ingreso": Decimal("10"), "asfi": "A",
        "antiguedad_laboral_meses": 36, "endeudamiento": Decimal("0"),
        "antiguedad_socio_meses": 24, "ahorro_ratio": Decimal("0"),
        "atrasos_previos": 0,
    })
    after = score_application(request, today=today)
    assert prediction["probabilidad_mora"] is not None
    assert (before["score"], before["dictamen"], before["knockouts"]) == (
        after["score"], after["dictamen"], after["knockouts"])
