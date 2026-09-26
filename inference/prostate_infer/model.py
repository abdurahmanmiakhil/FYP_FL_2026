"""Model maths copied verbatim from the thesis notebooks.

- Encoder, MEAN, STD: nb01_features.ipynb, Cell 7 (Phikon 'hf_vit' branch).
- GatedABMIL, mono_sigmoid, class_probs, grade: nb02_federated.ipynb, Cell 5.
- class_probs_from_s, grade_from_s, tune_thresholds: nb03_evaluation.ipynb, Cell 3
  (the seed ensemble works on averaged probabilities, not logits).

Do not change anything here without re-running the parity test (tests/test_parity.py).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import cohen_kappa_score

K = 6  # ISUP 0..5

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


class Encoder(nn.Module):
    """Phikon [CLS] encoder (nb01 Cell 7, loader='hf_vit'); `m` is a transformers ViTModel."""

    mean: torch.Tensor
    std: torch.Tensor

    def __init__(self, m: nn.Module):
        super().__init__()
        self.m = m
        self.register_buffer("mean", MEAN.clone())
        self.register_buffer("std", STD.clone())

    def forward(self, x_uint8_nhwc: torch.Tensor) -> torch.Tensor:
        x = x_uint8_nhwc.permute(0, 3, 1, 2).float().div_(255.0)
        x = (x - self.mean) / self.std
        # mixed precision: matmuls in fp16 on GPU, LayerNorm/softmax kept in fp32 (as in NB01)
        with torch.autocast("cuda", dtype=torch.float16, enabled=x.is_cuda):
            out = self.m(pixel_values=x).last_hidden_state[:, 0]  # CLS token
        return out.float()


class GatedABMIL(nn.Module):
    """Gated attention MIL (Ilse et al., ICML 2018) with an ordinal (cumulative-logit) head."""

    def __init__(self, d_in: int, hid: int = 256, attn: int = 128, drop: float = 0.25, n_out: int = K - 1):
        super().__init__()
        self.fc = nn.Sequential(nn.Linear(d_in, hid), nn.GELU(), nn.Dropout(drop))
        self.att_a = nn.Sequential(nn.Linear(hid, attn), nn.Tanh())
        self.att_b = nn.Sequential(nn.Linear(hid, attn), nn.Sigmoid())
        self.att_w = nn.Linear(attn, 1)
        self.head = nn.Sequential(nn.LayerNorm(hid), nn.Dropout(drop), nn.Linear(hid, n_out))

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.fc(x)  # B,N,H
        s = self.att_w(self.att_a(h) * self.att_b(h)).squeeze(-1)  # B,N
        s = s.masked_fill(~mask, -1e4)
        a = torch.softmax(s, dim=1)
        z = torch.bmm(a.unsqueeze(1), h).squeeze(1)  # B,H
        return self.head(z), a

    @torch.no_grad()
    def attention_scores(self, x: torch.Tensor) -> torch.Tensor:
        """Un-normalised gated-attention score per tile (same layers as forward, no softmax).

        Used only for the heatmap, so every kept tile gets a score even when the bag
        was sub-sampled to 512 tiles for the prediction itself.
        """
        h = self.fc(x)
        return self.att_w(self.att_a(h) * self.att_b(h)).squeeze(-1)


def mono_sigmoid(logits: Any) -> np.ndarray:
    s = 1 / (1 + np.exp(-np.asarray(logits, dtype=np.float64)))
    return np.minimum.accumulate(s, axis=1)  # enforce P(y>0) >= P(y>1) >= ...


def class_probs_from_s(s: Any) -> np.ndarray:
    s = np.asarray(s, dtype=np.float64)
    pge = np.concatenate([np.ones((len(s), 1)), s, np.zeros((len(s), 1))], 1)
    p = np.clip(pge[:, :-1] - pge[:, 1:], 0, 1)
    return p / p.sum(1, keepdims=True)


def class_probs(logits: Any) -> np.ndarray:
    return class_probs_from_s(mono_sigmoid(logits))


def grade_from_s(s: Any, thr: Any) -> np.ndarray:
    return (np.asarray(s) > np.asarray(thr)).sum(1)


def grade(logits: Any, thr: Any = None) -> np.ndarray:
    thr = np.full(K - 1, 0.5) if thr is None else np.asarray(thr)
    return grade_from_s(mono_sigmoid(logits), thr)


def tune_thresholds(y: Any, s: Any, passes: int = 3) -> tuple[list[float], float]:
    """Coordinate search of the 5 ordinal thresholds maximising validation QWK (nb03 form)."""
    thr = np.full(K - 1, 0.5)
    grid = np.arange(0.05, 0.951, 0.025)
    best = cohen_kappa_score(y, grade_from_s(s, thr), weights="quadratic")
    for _ in range(passes):
        for k in range(K - 1):
            for t in grid:
                c = thr.copy()
                c[k] = t
                q = cohen_kappa_score(y, grade_from_s(s, c), weights="quadratic")
                if q > best + 1e-9:
                    best, thr = q, c
    return [float(v) for v in thr], float(best)
