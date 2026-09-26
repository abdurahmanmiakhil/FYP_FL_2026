"""Build the model bundle from the thesis output folders and fit the ensemble thresholds."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score, roc_auc_score

from .assets import METHOD, N_SEEDS, bundle_files, verify_bundle, write_manifest
from .model import K, grade_from_s, mono_sigmoid, tune_thresholds

LOGIT_COLS = [f"logit{k}" for k in range(K - 1)]


def ensemble_table(bundle: Path) -> pd.DataFrame:
    """nb03 Cell 4: per-slide ensemble cumulative probabilities s0..s4 (mean over seeds)."""
    d = pd.concat([pd.read_csv(bundle / f"calibration/preds__{METHOD}__s{s}.csv") for s in range(N_SEEDS)])
    S = np.stack([mono_sigmoid(g.sort_values("image_id")[LOGIT_COLS].values) for _, g in d.groupby("seed")])
    base = d[d.seed == d.seed.min()].sort_values("image_id")[["image_id", "hospital", "isup_grade", "split"]]
    base = base.reset_index(drop=True)
    base[[f"s{k}" for k in range(K - 1)]] = S.mean(0)
    return base


def fit_ensemble_thresholds(bundle: Path) -> dict:  # type: ignore[type-arg]
    """tune_thresholds once on the validation split of the seed ensemble; also report test metrics."""
    b = ensemble_table(bundle)
    sc = [f"s{k}" for k in range(K - 1)]
    v, t = b[b.split == "val"], b[b.split == "test"]
    thr, val_qwk = tune_thresholds(v.isup_grade.values, v[sc].values)
    y, s = t.isup_grade.values, t[sc].values
    return {
        "method": METHOD,
        "n_seeds": N_SEEDS,
        "thresholds": thr,
        "val_qwk": val_qwk,
        "test": {
            "n": int(len(t)),
            "auc_cspca": float(roc_auc_score(y >= 2, s[:, 1])),
            "auc_cancer": float(roc_auc_score(y >= 1, s[:, 0])),
            "qwk": float(cohen_kappa_score(y, grade_from_s(s, thr), weights="quadratic")),
        },
    }


def build_bundle(fl_dir: Path, results_dir: Path, out: Path) -> dict:  # type: ignore[type-arg]
    """Copy the FedAvg files into the bundle layout, fit thresholds, write manifest.json."""
    out.mkdir(parents=True, exist_ok=True)
    src = {
        **{f"models/model__{METHOD}__s{s}.pt": fl_dir / f"model__{METHOD}__s{s}.pt" for s in range(N_SEEDS)},
        **{f"calibration/res__{METHOD}__s{s}.json": fl_dir / f"res__{METHOD}__s{s}.json" for s in range(N_SEEDS)},
        **{f"calibration/preds__{METHOD}__s{s}.csv": fl_dir / f"preds__{METHOD}__s{s}.csv" for s in range(N_SEEDS)},
        "calibration/tab_E08_operating_points.csv": results_dir / "tab_E08_operating_points.csv",
        "data/splits.csv": fl_dir / "splits.csv",
        "config/config_nb02.json": fl_dir / "config_nb02.json",
    }
    for rel, p in src.items():
        if not p.exists():
            raise FileNotFoundError(f"{p} (needed for {rel})")
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out / rel)
    fit = fit_ensemble_thresholds(out)
    (out / "calibration/ensemble_thresholds.json").write_text(json.dumps(fit, indent=2))
    assert set(bundle_files()) <= {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    write_manifest(out)
    verify_bundle(out)
    return fit
