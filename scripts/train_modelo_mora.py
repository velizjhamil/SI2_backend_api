"""Train the declared deterministic, fully synthetic mora model (seed 2026)."""
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

SEED = 2026
FEATURES = ["ratio_cuota_ingreso", "asfi", "antiguedad_laboral_meses", "endeudamiento", "antiguedad_socio_meses", "ahorro_ratio", "atrasos_previos"]
DESCRIPTIONS = ["Cuota/ingreso (%)", "ASFI codificada A=0…F=5", "Antigüedad laboral (meses)", "Cuotas de deuda/ingreso (%)", "Antigüedad como socio (meses)", "Ahorro activo elegible / principal solicitado (%)", "Cuotas previas pagadas fuera de gracia"]

def train():
    rng = np.random.default_rng(SEED)
    n = 5000
    x = np.column_stack((rng.uniform(0, 80, n), rng.integers(0, 6, n), rng.integers(0, 481, n), rng.uniform(0, 60, n), rng.integers(0, 361, n), rng.uniform(0, 100, n), rng.integers(0, 9, n)))
    raw = -3.1 + .035*x[:,0] + .42*x[:,1] - .003*x[:,2] + .025*x[:,3] - .002*x[:,4] - .012*x[:,5] + .48*x[:,6]
    p = 1 / (1 + np.exp(-raw))
    y = (rng.random(n) < p).astype(int)
    x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=.2, random_state=SEED, stratify=y)
    scaler = StandardScaler().fit(x_train)
    model = LogisticRegression(random_state=SEED, max_iter=1000).fit(scaler.transform(x_train), y_train)
    probs = model.predict_proba(scaler.transform(x_test))[:,1]
    result = {"version":"mora-logit-v1", "dataset":"SINTETICO", "descripcion_dataset":"5000 registros completamente sintéticos generados con numpy.default_rng(2026); etiqueta Bernoulli obtenida de regla logística conocida con ruido. No contiene datos reales.", "n_muestras":n,"semilla":SEED,"features":FEATURES,"descripciones_features":DESCRIPTIONS,"medias":scaler.mean_.tolist(),"desviaciones":scaler.scale_.tolist(),"coeficientes":model.coef_[0].tolist(),"intercepto":float(model.intercept_[0]),"metricas":{"auc":float(roc_auc_score(y_test,probs)),"accuracy":float(accuracy_score(y_test,probs>=.5))},"ground_truth":"logit(p) = -3.1 + 0.035*ratio_cuota_ingreso + 0.42*ASFI - 0.003*antiguedad_laboral + 0.025*endeudamiento - 0.002*antiguedad_socio - 0.012*ahorro_ratio + 0.48*atrasos_previos; labels sampled as Bernoulli(p)."}
    path=Path(__file__).parents[1]/"app/ml/modelo_mora_v1.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return result
if __name__ == "__main__":
    print(json.dumps(train()["metricas"], indent=2))
