"""Single-slide inference with the FedAvg seed ensemble.

Pipeline (identical to the thesis notebooks):
  nb01: tissue mask -> 224 px level-0 tiles (<= 768, seeded by slide id) -> Phikon [CLS]
        -> features stored as fp16
  nb02: bag of <= 512 tiles (sub-sample seeded by slide id; the notebook used an unseeded
        randperm, so only bags <= 512 are bit-comparable) -> GatedABMIL -> 5 ordinal logits
  nb03: ensemble = mean over the 5 seeds of the monotone cumulative probabilities;
        ISUP grade from thresholds tuned once on validation; operating points from tab_E08.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import openslide
import torch
from PIL import Image
from torch.utils.data import DataLoader

from .assets import N_SEEDS, Assets, fetch_assets, get_settings, load_phikon
from .model import Encoder, GatedABMIL, class_probs_from_s, grade_from_s, mono_sigmoid
from .preprocess import MAX_BAG, TILE_PX, SlideTiles, id_seed, lowres_rgb, read_rgb, tile_coords, tissue_mask
from .qc import NoTissueError, slide_qc, validate_slide_file
from .schemas import GLEASON_HINT, PREPROCESSING_VERSION, OperatingPointFlags, PerSeedOutput, SlidePrediction

logger = logging.getLogger(__name__)

D_IN = 768
N_PARAMS = 264_582  # GatedABMIL(768) parameter count reported by nb02

# Clinical-safety rules (Phase 4). A result is flagged for mandatory review when:
MIN_TILES = 50  # fewer tissue tiles than this
SEED_STD_MAX = 0.10  # the 5 seed models disagree on P(csPCa) by more than this (std)

ProgressFn = Callable[[str, int, int], None]


def _noop(stage: str, done: int, total: int) -> None:
    pass


@dataclass
class Head:
    model: GatedABMIL
    thresholds: list[float]  # per-seed tuned thresholds (res__fedavg__s{k}.json)


class Engine:
    """Phikon + the 5 FedAvg heads, loaded once per process (thread-safe)."""

    def __init__(self, assets: Assets, device: torch.device):
        self.assets = assets
        self.device = device
        self.encoder = Encoder(load_phikon(assets.settings)).to(device).eval()
        self.heads: list[Head] = []
        for seed in range(N_SEEDS):
            ckpt = _load_ckpt(assets.model_path(seed))
            if int(ckpt["d_in"]) != D_IN:
                raise ValueError(f"seed {seed}: d_in {ckpt['d_in']} != {D_IN}")
            cfg = ckpt.get("cfg", {})
            m = GatedABMIL(D_IN, cfg.get("hid", 256), cfg.get("attn", 128), cfg.get("dropout", 0.25))
            m.load_state_dict(ckpt["state"])
            n = sum(p.numel() for p in m.parameters())
            if n != N_PARAMS:
                raise ValueError(f"seed {seed}: {n} parameters, expected {N_PARAMS}")
            self.heads.append(Head(m.to(device).eval(), [float(t) for t in ckpt["thresholds"]]))
        self.batch = 256 if device.type == "cuda" else 64
        self.num_workers = int(os.environ.get("INFER_NUM_WORKERS", "2"))


def _load_ckpt(path: Path) -> dict:  # type: ignore[type-arg]
    # sha256 of every file was verified against manifest.json before we get here
    return torch.load(path, map_location="cpu", weights_only=False)  # type: ignore[no-any-return]


_engine: Engine | None = None
_engine_lock = threading.Lock()


def resolve_device(device: str = "auto") -> torch.device:
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device)


def get_engine(device: str = "auto") -> Engine:
    global _engine
    with _engine_lock:
        dev = resolve_device(device)
        if _engine is None or _engine.device != dev:
            _engine = Engine(fetch_assets(get_settings()), dev)
        return _engine


@torch.no_grad()
def encode_tiles(engine: Engine, path: str, coords: np.ndarray, progress: ProgressFn) -> torch.Tensor:
    """Phikon [CLS] features, stored as fp16 exactly like nb01 (`.float().cpu().half()`)."""
    dl = DataLoader(
        SlideTiles(path, coords),
        batch_size=engine.batch,
        shuffle=False,
        num_workers=engine.num_workers,
        persistent_workers=False,
    )
    feats, done = [], 0
    progress("encoding", 0, len(coords))
    for x in dl:
        feats.append(engine.encoder(x.to(engine.device, non_blocking=True)).float().cpu().half())
        done += len(x)
        progress("encoding", done, len(coords))
    f = torch.cat(feats)
    if not torch.isfinite(f.float()).all():
        raise RuntimeError("non-finite tile features")
    return f


def bag_indices(n: int, image_id: str) -> np.ndarray:
    """nb02 collate: bags > 512 tiles are sub-sampled to 512 (seeded here for determinism)."""
    if n <= MAX_BAG:
        return np.arange(n)
    rng = np.random.default_rng(id_seed(image_id))
    return np.sort(rng.choice(n, MAX_BAG, replace=False))


def predict_slide(
    path: str | Path,
    *,
    device: str = "auto",
    image_id: str | None = None,
    progress: ProgressFn | None = None,
) -> SlidePrediction:
    """Run the thesis FedAvg ensemble on one whole-slide image.

    image_id seeds the tile cap and bag sub-sample; pass the original PANDA image_id for
    parity with the notebooks (defaults to the file stem).
    """
    t0 = time.time()
    progress = progress or _noop
    path = str(path)
    image_id = image_id or Path(path).stem

    progress("reading slide", 0, 1)
    slide = validate_slide_file(path)
    try:
        progress("tiling", 0, 1)
        low_rgb, _ = lowres_rgb(slide)
        qc = slide_qc(slide, low_rgb, tissue_mask(low_rgb))
        coords, n_all = tile_coords(slide, image_id)
        W, H = slide.level_dimensions[0]
    finally:
        slide.close()
    if len(coords) == 0:
        raise NoTissueError("No tissue found in slide")
    progress("tiling", 1, 1)

    engine = get_engine(device)
    feats = encode_tiles(engine, path, coords, progress)

    progress("predicting", 0, 1)
    idx = bag_indices(len(feats), image_id)
    dev = engine.device
    X = feats[idx].float().unsqueeze(0).to(dev)
    M = torch.ones(1, len(idx), dtype=torch.bool, device=dev)
    all_x = feats.float().unsqueeze(0).to(dev)

    per_seed: dict[str, PerSeedOutput] = {}
    S_seeds, att_seeds = [], []
    with torch.no_grad():
        for k, head in enumerate(engine.heads):
            logits = head.model(X, M)[0].float().cpu().numpy()  # (1, 5)
            s = mono_sigmoid(logits)
            S_seeds.append(s)
            att_seeds.append(torch.softmax(head.model.attention_scores(all_x)[0], 0).cpu().numpy())
            per_seed[f"s{k}"] = PerSeedOutput(
                logits=[float(v) for v in logits[0]],
                p_cancer=float(s[0, 0]),
                p_cspca=float(s[0, 1]),
                p_isup=[float(v) for v in class_probs_from_s(s)[0]],
                isup_grade=int(grade_from_s(s, head.thresholds)[0]),
            )

    S = np.mean(S_seeds, axis=0)  # (1, 5) ensemble cumulative probabilities
    p_isup = class_probs_from_s(S)[0]
    isup = int(grade_from_s(S, engine.assets.ensemble_thresholds)[0])
    p_cancer, p_cspca = float(S[0, 0]), float(S[0, 1])
    op = engine.assets.operating_points
    flags = OperatingPointFlags(
        cspca_youden=p_cspca >= op["cspca_youden"],
        cspca_sens90=p_cspca >= op["cspca_sens90"],
        cspca_sens95=p_cspca >= op["cspca_sens95"],
        cancer_youden=p_cancer >= op["cancer_youden"],
    )
    seed_std = float(np.std([p.p_cspca for p in per_seed.values()]))

    reasons: list[str] = []
    lo, hi = sorted((op["cspca_sens95"], op["cspca_youden"]))
    if lo <= p_cspca < hi:
        reasons.append(
            f"P(csPCa) {p_cspca:.2f} lies between the 95%-sensitivity ({lo:.2f}) and Youden ({hi:.2f}) thresholds."
        )
    if len(coords) < MIN_TILES:
        reasons.append(f"Only {len(coords)} tissue tiles were found (fewer than {MIN_TILES}).")
    if seed_std > SEED_STD_MAX:
        reasons.append(f"The 5 federated models disagree on P(csPCa) (SD {seed_std:.2f} > {SEED_STD_MAX:.2f}).")
    if any("Stain colour" in w for w in qc.warnings):
        reasons.append("The slide's stain colour is outside the range of the training data.")

    progress("predicting", 1, 1)
    pred = SlidePrediction(
        p_cancer=p_cancer,
        p_cspca=p_cspca,
        p_isup=[float(v) for v in p_isup],
        isup_grade=isup,
        gleason_hint=GLEASON_HINT[isup],
        operating_point_flags=flags,
        thresholds={**op, **{f"isup_{k + 1}": t for k, t in enumerate(engine.assets.ensemble_thresholds)}},
        n_tiles=len(coords),
        n_tiles_total=int(n_all),
        n_tiles_model=len(idx),
        tile_coords=coords.tolist(),
        attention=[float(v) for v in np.mean(att_seeds, axis=0)],
        per_seed=per_seed,
        seed_std_p_cspca=seed_std,
        low_confidence_reasons=reasons,
        qc=qc,
        slide_width=int(W),
        slide_height=int(H),
        model_version=engine.assets.version,
        preprocessing_version=PREPROCESSING_VERSION,
        runtime_seconds=round(time.time() - t0, 2),
        device=str(dev),
    )
    logger.info("prediction done", extra={"n_tiles": pred.n_tiles, "runtime_s": pred.runtime_seconds})
    return pred


def rank_normalise(att: list[float] | np.ndarray) -> np.ndarray:
    a = np.asarray(att, dtype=np.float64)
    if len(a) < 2:
        return np.ones_like(a)
    ranks = np.empty(len(a))
    ranks[a.argsort(kind="stable")] = np.arange(len(a))
    return ranks / (len(a) - 1)


def make_heatmap(pred: SlidePrediction, slide: openslide.OpenSlide) -> Image.Image:
    """RGBA attention heatmap at the low-resolution level (per-slide rank-normalised,
    transparent outside the encoded tissue tiles). Covers the full level-0 extent, so the
    viewer can stretch it over the slide."""
    low_rgb, ds = lowres_rgb(slide)
    h, w = low_rgb.shape[:2]
    val = np.zeros((h, w), np.float32)
    cover = np.zeros((h, w), bool)
    r = rank_normalise(pred.attention)
    t = TILE_PX / ds
    for (x, y), v in zip(pred.tile_coords, r, strict=True):
        x0, y0 = int(round(x / ds)), int(round(y / ds))
        x1, y1 = max(x0 + 1, int(round(x / ds + t))), max(y0 + 1, int(round(y / ds + t)))
        val[y0:y1, x0:x1] = v
        cover[y0:y1, x0:x1] = True
    rgb = cv2.cvtColor(cv2.applyColorMap((val * 255).astype(np.uint8), cv2.COLORMAP_TURBO), cv2.COLOR_BGR2RGB)
    alpha = np.where(cover, 255, 0).astype(np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


@dataclass
class TopTile:
    image: Image.Image
    x: int
    y: int
    attention: float
    rank: int


def top_tiles(pred: SlidePrediction, slide: openslide.OpenSlide, k: int = 8) -> list[TopTile]:
    """The k highest-attention tiles as 224x224 RGB crops from level 0, with coordinates."""
    att = np.asarray(pred.attention)
    order = att.argsort(kind="stable")[::-1][:k]
    out = []
    for rank, i in enumerate(order):
        x, y = pred.tile_coords[int(i)]
        img = Image.fromarray(read_rgb(slide, (int(x), int(y)), 0, (TILE_PX, TILE_PX)))
        out.append(TopTile(img, int(x), int(y), float(att[i]), rank))
    return out
