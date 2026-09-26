"""Tissue detection and tiling, copied verbatim from nb01_features.ipynb Cell 6.

CFG values from nb01 Cell 2 are inlined as module constants. Do not change the maths
without re-running the parity test.
"""

from __future__ import annotations

import hashlib

import cv2
import numpy as np
import openslide
import torch
from torch.utils.data import Dataset

TILE_PX = 224  # tile size at level 0 (20x)
TISSUE_MIN = 0.50  # min tissue fraction for a tile to be kept
MAX_TILES = 768  # cap per slide (random, seeded by slide id)
MAX_BAG = 512  # nb02: bags larger than this are sub-sampled


def id_seed(image_id: str) -> int:
    """Deterministic seed from a slide id (the md5 rule nb01 uses for the tile cap)."""
    return int(hashlib.md5(image_id.encode()).hexdigest()[:8], 16)


def read_rgb(slide: openslide.OpenSlide, loc: tuple[int, int], level: int, size: tuple[int, int]) -> np.ndarray:
    """read_region -> RGB, compositing transparent pixels onto white."""
    rgba = np.asarray(slide.read_region(loc, level, size))
    rgb = rgba[..., :3].copy()
    rgb[rgba[..., 3] == 0] = 255
    return rgb


def lowres_rgb(slide: openslide.OpenSlide, target_ds: int = 16) -> tuple[np.ndarray, float]:
    """Low-resolution RGB image + its exact downsample factor. Uses the smallest pyramid level
    (PANDA: 16x); if a slide has no small level, falls back to an aspect-correct thumbnail."""
    low = slide.level_count - 1
    if slide.level_downsamples[low] >= 8:
        return read_rgb(slide, (0, 0), low, slide.level_dimensions[low]), float(slide.level_downsamples[low])
    W, H = slide.level_dimensions[0]
    th = np.asarray(slide.get_thumbnail((max(1, W // target_ds), max(1, H // target_ds))).convert("RGB"))
    return th, W / th.shape[1]


def tissue_mask(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1]
    otsu, _ = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = float(np.clip(otsu, 15, 60))
    gray = rgb.mean(-1)
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    pen = (g > r + 15) | ((b > r + 25) & (b > g + 15)) | (gray < 40)  # green/blue/black pen marks
    m: np.ndarray = ((sat > thr) & (gray < 235) & ~pen).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    return m


def tile_coords(slide: openslide.OpenSlide, image_id: str) -> tuple[np.ndarray, int]:
    """Grid of level-0 tiles whose tissue fraction >= TISSUE_MIN (computed on the lowest level)."""
    T = TILE_PX
    W, H = slide.level_dimensions[0]
    low_rgb, ds = lowres_rgb(slide)
    lh, lw = low_rgb.shape[:2]
    mask = tissue_mask(low_rgb).astype(np.float32)
    ncol, nrow = W // T, H // T
    if ncol == 0 or nrow == 0:
        return np.zeros((0, 2), np.int32), 0
    cw, ch = min(lw, int(round(ncol * T / ds))), min(lh, int(round(nrow * T / ds)))
    frac = cv2.resize(mask[:ch, :cw], (ncol, nrow), interpolation=cv2.INTER_AREA)
    for thr in (TISSUE_MIN, 0.25, 0.10):  # graceful fallback for tiny biopsies
        ys, xs = np.where(frac >= thr)
        if len(xs):
            break
    coords = np.stack([xs * T, ys * T], 1).astype(np.int32)
    n_all = len(coords)
    if n_all > MAX_TILES:
        rng = np.random.default_rng(id_seed(image_id))
        coords = coords[np.sort(rng.choice(n_all, MAX_TILES, replace=False))]
    return coords, n_all


class SlideTiles(Dataset):  # type: ignore[type-arg]
    """Reads the kept level-0 tiles of one slide. nb01 reads a whole slide per item; here
    each item is one tile so a DataLoader can read in parallel and report progress."""

    def __init__(self, path: str, coords: np.ndarray):
        self.path = path
        self.coords = coords
        self._slide: openslide.OpenSlide | None = None

    def __len__(self) -> int:
        return len(self.coords)

    def __getitem__(self, i: int) -> torch.Tensor:
        if self._slide is None:  # opened lazily, once per worker process
            self._slide = openslide.OpenSlide(self.path)
        x, y = self.coords[i]
        return torch.from_numpy(read_rgb(self._slide, (int(x), int(y)), 0, (TILE_PX, TILE_PX)))
