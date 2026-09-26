"""Checks against the real thesis model bundle (skipped when THESIS_BUNDLE is not set)."""

from __future__ import annotations

from pathlib import Path

import torch

from prostate_infer.assets import Assets, Settings, verify_bundle
from prostate_infer.bundle import fit_ensemble_thresholds
from prostate_infer.model import GatedABMIL

# thesis Table E03, FedAvg seed ensemble, pooled test set (2,124 slides)
THESIS = {"auc_cspca": 0.9705, "auc_cancer": 0.9935, "qwk": 0.9047, "n": 2124}


def test_bundle_integrity(thesis_bundle: Path) -> None:
    verify_bundle(thesis_bundle)


def test_ensemble_reproduces_thesis_metrics(thesis_bundle: Path) -> None:
    fit = fit_ensemble_thresholds(thesis_bundle)
    t = fit["test"]
    assert t["n"] == THESIS["n"]
    for k in ("auc_cspca", "auc_cancer", "qwk"):
        assert round(t[k], 4) == THESIS[k], (k, t[k])


def test_heads_load_with_expected_shapes(thesis_bundle: Path) -> None:
    a = Assets(thesis_bundle, "v", Settings())
    for s in range(5):
        ck = torch.load(a.model_path(s), map_location="cpu", weights_only=False)
        assert ck["d_in"] == 768 and len(ck["thresholds"]) == 5
        m = GatedABMIL(768)
        m.load_state_dict(ck["state"])
    assert set(a.operating_points) == {"cspca_youden", "cspca_sens90", "cspca_sens95", "cancer_youden"}
