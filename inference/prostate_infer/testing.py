"""Test helpers (used by the inference and backend test suites; never by production code).

- write_fake_bundle: a complete, sha256-verified bundle with RANDOM heads (no private models).
- FakeViT: a tiny deterministic stand-in for Phikon so tests run without the 330 MB download.
- use_fake_models: point prostate_infer at a fake bundle and swap Phikon for FakeViT.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch
import torch.nn as nn

from . import assets as assets_mod
from . import predict as predict_mod
from .assets import METHOD, N_SEEDS, write_manifest
from .bundle import fit_ensemble_thresholds
from .model import GatedABMIL


def write_fake_bundle(root: Path) -> Path:
    rng = np.random.default_rng(0)
    for d in ("models", "calibration", "data", "config"):
        (root / d).mkdir(parents=True, exist_ok=True)
    ids = [f"img{i:04d}" for i in range(300)]
    y = rng.integers(0, 6, len(ids))
    split = np.where(np.arange(len(ids)) < 150, "val", "test")
    for s in range(N_SEEDS):
        torch.manual_seed(s)
        m = GatedABMIL(768)
        thr = [0.5] * 5
        torch.save(
            {
                "state": m.state_dict(),
                "d_in": 768,
                "cfg": {"hid": 256, "attn": 128, "dropout": 0.25},
                "thresholds": thr,
            },
            root / f"models/model__{METHOD}__s{s}.pt",
        )
        (root / f"calibration/res__{METHOD}__s{s}.json").write_text(json.dumps({"thresholds": thr}))
        # logits correlated with the label so the threshold fit has signal
        base = (y[:, None] - np.arange(5)[None, :] - 0.5) * 1.5 + rng.normal(0, 1, (len(ids), 5))
        with open(root / f"calibration/preds__{METHOD}__s{s}.csv", "w", newline="") as f:
            w = csv.writer(f)
            header = ["image_id", "hospital", "isup_grade", "split", *[f"logit{k}" for k in range(5)], "method", "seed"]
            w.writerow(header)
            for i, iid in enumerate(ids):
                w.writerow([iid, "A_Radboud", int(y[i]), split[i], *base[i].round(5), METHOD, s])
    rows = [
        ("Cancer (ISUP ≥ 1)", "Youden (val)", 0.66),
        ("Cancer (ISUP ≥ 1)", "Sensitivity ≥ 90% (val)", 0.93),
        ("Cancer (ISUP ≥ 1)", "Sensitivity ≥ 95% (val)", 0.66),
        ("csPCa (ISUP ≥ 2)", "Youden (val)", 0.48),
        ("csPCa (ISUP ≥ 2)", "Sensitivity ≥ 90% (val)", 0.42),
        ("csPCa (ISUP ≥ 2)", "Sensitivity ≥ 95% (val)", 0.17),
    ]
    with open(root / "calibration/tab_E08_operating_points.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Method", "Task", "Operating point", "Threshold"])
        for method in ("FedProx", "FedAvg"):
            for task, op, t in rows:
                w.writerow([method, task, op, t if method == "FedAvg" else 0.99])
    lines = [f"{i},{s}" for i, s in zip(ids, split, strict=True)]
    (root / "data/splits.csv").write_text("image_id,split\n" + "\n".join(lines))
    (root / "config/config_nb02.json").write_text(json.dumps({"hid": 256, "attn": 128}))
    (root / "calibration/ensemble_thresholds.json").write_text(json.dumps(fit_ensemble_thresholds(root)))
    write_manifest(root)
    return root


class FakeViT(nn.Module):
    """Stands in for Phikon: a deterministic 768-d [CLS] from each tile's colour statistics."""

    def __init__(self) -> None:
        super().__init__()
        g = torch.Generator().manual_seed(0)
        self.proj = nn.Linear(6, 768)
        with torch.no_grad():
            self.proj.weight.copy_(torch.randn(768, 6, generator=g))
            self.proj.bias.copy_(torch.randn(768, generator=g))

    def forward(self, pixel_values: torch.Tensor) -> SimpleNamespace:
        x = pixel_values
        stats = torch.cat([x.mean((2, 3)), x.std((2, 3))], 1)
        return SimpleNamespace(last_hidden_state=self.proj(stats).unsqueeze(1))


def use_fake_models(monkeypatch: Any, bundle: Path) -> None:
    """pytest helper: fake bundle + FakeViT, single-process tile reading, fresh engine."""
    monkeypatch.setenv("MODEL_SOURCE", "local")
    monkeypatch.setenv("MODEL_DIR", str(bundle))
    monkeypatch.setenv("INFER_NUM_WORKERS", "0")
    monkeypatch.setattr(assets_mod, "load_phikon", lambda s: FakeViT())
    monkeypatch.setattr(predict_mod, "load_phikon", lambda s: FakeViT())
    monkeypatch.setattr(predict_mod, "_engine", None)
