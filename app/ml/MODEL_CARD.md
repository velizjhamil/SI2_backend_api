# mora-logit-v1

Dataset: 5,000 **synthetic** examples generated deterministically with NumPy `default_rng(2026)`. Labels are noisy Bernoulli samples from the documented logistic ground-truth rule embedded in `modelo_mora_v1.json`; stratified 80/20 split. Reported AUC and accuracy are held-out metrics. Reproduce using `python scripts/train_modelo_mora.py`.

Features follow reglas-v1 inputs. `ahorro_ratio` means eligible active savings in the request currency divided by requested principal, multiplied by 100 (the AHORRO factor's ratio). Runtime inference loads committed JSON and uses only Python; scikit-learn is training-only. Model output is informational and does not change rules-v1 score, knock-outs, or dictamen.
