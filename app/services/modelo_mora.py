"""Pure-Python advisory inference for the committed synthetic mora model."""
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

_VERSION = "mora-logit-v1"
_FEATURES = ("ratio_cuota_ingreso", "asfi", "antiguedad_laboral_meses", "endeudamiento", "antiguedad_socio_meses", "ahorro_ratio", "atrasos_previos")

@lru_cache(maxsize=1)
def _modelo():
    return json.loads((Path(__file__).parents[1] / "ml" / "modelo_mora_v1.json").read_text(encoding="utf-8"))

def _asfi(value):
    if isinstance(value, str):
        return float(max(0, min(5, ord(value.upper()[:1]) - ord("A"))))
    return float(value)

def predecir_mora(features):
    model = _modelo()
    values = [float(_asfi(features[name]) if name == "asfi" else features[name]) for name in _FEATURES]
    z = model["intercepto"] + sum(c * ((v - mean) / std) for c, v, mean, std in zip(model["coeficientes"], values, model["medias"], model["desviaciones"]))
    p = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))
    probability = Decimal(str(p)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {"probabilidad_mora": probability, "nivel_riesgo": "BAJO" if probability < Decimal("0.15") else "MEDIO" if probability < Decimal("0.35") else "ALTO", "version_modelo": _VERSION}

def ficha_modelo():
    model = _modelo()
    return {"version": model["version"], "dataset": model["dataset"], "descripcion_dataset": model["descripcion_dataset"] + " ahorro_ratio = ahorro activo elegible dividido por principal solicitado × 100 (factor AHORRO de reglas-v1).", "n_muestras": model["n_muestras"], "semilla": model["semilla"], "metricas": model["metricas"], "features": [{"nombre": name, "descripcion": desc, "coeficiente": coef} for name, desc, coef in zip(model["features"], model["descripciones_features"], model["coeficientes"])], "umbrales": {"bajo": 0.15, "medio": 0.35}, "advertencia": "Modelo experimental entrenado exclusivamente con datos sintéticos; probabilidad informativa. No altera score, knock-outs ni dictamen."}
