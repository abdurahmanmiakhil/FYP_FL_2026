"""Parity test (slow): the web pipeline must reproduce the thesis logits on real PANDA slides.

Needs:
  PANDA_DIR       folder with PANDA train_images/<image_id>.tiff (Kaggle prostate-cancer-grade-assessment)
  THESIS_BUNDLE   the real model bundle (models/bundle)
  Phikon cached in MODEL_CACHE_DIR (python -m prostate_infer fetch)

Picks up to PARITY_N (default 10) test slides from data/splits.csv that exist in PANDA_DIR and
have <= 512 tissue tiles (larger bags were sub-sampled with an unseeded randperm in nb02, so
they cannot be compared exactly), runs each seed head and compares its logits with
calibration/preds__fedavg__s{k}.csv. Pass: |delta logit| < 0.05 for every slide/seed/logit.

    PANDA_DIR=... THESIS_BUNDLE=models/bundle pytest -m slow tests/test_parity.py -s
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import openslide
import pandas as pd
import pytest
import torch

TOL = 0.05


@pytest.mark.slow
def test_parity_with_thesis_logits(thesis_bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panda = os.environ.get("PANDA_DIR")
    if not panda:
        pytest.skip("PANDA_DIR not set")
    img_dir = Path(panda) / "train_images" if (Path(panda) / "train_images").exists() else Path(panda)
    monkeypatch.setenv("MODEL_DIR", str(thesis_bundle))
    monkeypatch.setenv("MODEL_SOURCE", "local")

    from prostate_infer.assets import METHOD
    from prostate_infer.predict import encode_tiles, get_engine
    from prostate_infer.preprocess import MAX_BAG, tile_coords

    preds = pd.concat(pd.read_csv(thesis_bundle / f"calibration/preds__{METHOD}__s{s}.csv") for s in range(5))
    test_ids = preds[preds.split == "test"].image_id.unique()
    engine = get_engine("auto")
    rows, want = [], int(os.environ.get("PARITY_N", "10"))
    for iid in test_ids:
        path = img_dir / f"{iid}.tiff"
        if not path.exists():
            continue
        with openslide.OpenSlide(str(path)) as sl:
            coords, _ = tile_coords(sl, iid)
        if not 0 < len(coords) <= MAX_BAG:
            continue
        feats = encode_tiles(engine, str(path), coords, lambda *a: None)
        X = feats.float().unsqueeze(0).to(engine.device)
        M = torch.ones(1, len(coords), dtype=torch.bool, device=engine.device)
        for k, head in enumerate(engine.heads):
            with torch.no_grad():
                ours = head.model(X, M)[0][0].float().cpu().numpy()
            ref = preds[(preds.image_id == iid) & (preds.seed == k)][[f"logit{j}" for j in range(5)]].values[0]
            rows.append(
                {"image_id": iid, "seed": k, "n_tiles": len(coords), "max_abs_delta": float(np.abs(ours - ref).max())}
            )
        if len({r["image_id"] for r in rows}) >= want:
            break
    if not rows:
        pytest.skip("no PANDA test slides with <= 512 tiles found in PANDA_DIR")
    table = pd.DataFrame(rows)
    print("\n" + table.to_string(index=False))
    print(f"\nworst |delta logit| = {table.max_abs_delta.max():.4f} (tolerance {TOL})")
    assert (table.max_abs_delta < TOL).all()
