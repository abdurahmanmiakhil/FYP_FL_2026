# Validation report - website vs thesis

Generated 2026-09-26 00:06 UTC by `scripts/validate_on_test_split.py`. Metrics use the same code as nb03 (sklearn roc_auc_score, cohen_kappa_score quadratic, percentile bootstrap with 2,000 resamples, seed 7).

## A. Model reproduction (thesis prediction files -> web ensemble code)

Pooled PANDA test split, **n = 2124** slides (Radboud 1032, Karolinska 1092). Ensemble thresholds: `[0.3, 0.475, 0.3, 0.45, 0.425]`.

| Metric | Web ensemble [95% CI] | Thesis (Table E03) [95% CI] | Difference | Within thesis CI |
|---|---|---|---|---|
| csPCa AUC (ISUP >= 2) | 0.9705 [0.9641, 0.9766] | 0.9705 [0.9641, 0.9766] | -0.0000 | yes |
| Cancer AUC (ISUP >= 1) | 0.9935 [0.9911, 0.9956] | 0.9935 [0.9911, 0.9956] | +0.0000 | yes |
| Quadratic weighted kappa | 0.9047 [0.8897, 0.9188] | 0.9047 [0.8897, 0.9188] | -0.0000 | yes |

Result: **PASS** - identical to the thesis (same five FedAvg models, same probability averaging and thresholds as nb03).

## B. Full website pipeline

**Not run on this machine: no PANDA whole-slide images are available locally** (the Kaggle `prostate-cancer-grade-assessment` dataset, ~400 GB). To run it:

```bash
python scripts/validate_on_test_split.py --bundle models/bundle --panda /path/to/panda/train_images \
    --n 200 --api https://localhost --email <pathologist> --password <password>
```

Also run the tile-level parity test (`PANDA_DIR=... THESIS_BUNDLE=models/bundle pytest -m slow inference/tests/test_parity.py`), which compares the web pipeline's logits with the thesis logits slide by slide (tolerance 0.05).
