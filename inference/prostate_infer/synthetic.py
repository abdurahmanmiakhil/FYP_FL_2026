"""Synthetic pyramidal TIFF slides for tests and demos (NOT real tissue - never for validation).

Writes a tiled, multi-resolution RGB TIFF that OpenSlide opens as a 'generic-tiff' slide:
white background with H&E-coloured core-biopsy-like strips (pink stroma, purple nuclei).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def _tissue_image(width: int, height: int, seed: int, n_cores: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((height, width, 3), 244, np.uint8)
    mask = np.zeros((height, width), np.uint8)
    for i in range(n_cores):  # elongated needle-core shapes
        cy = int(height * (i + 1) / (n_cores + 1))
        x0, x1 = int(width * 0.08), int(width * rng.uniform(0.75, 0.92))
        thick = max(8, int(height * rng.uniform(0.07, 0.11)))
        pts = np.array([[x, cy + int(thick * 0.25 * np.sin(x / width * 6 + i))] for x in range(x0, x1, 16)], np.int32)
        cv2.polylines(mask, [pts], False, 1, thickness=thick)
    # colour only the tissue pixels (keeps memory at ~1 byte per pixel for large slides)
    seeds = (rng.random((height, width), dtype=np.float32) < 0.02).astype(np.uint8)
    nuclei = cv2.dilate(seeds, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    ys, xs = np.nonzero(mask)
    noise = rng.normal(0, 9, len(ys)).astype(np.float32)[:, None]
    tissue = np.clip(np.array([226, 150, 196], np.float32) + noise, 0, 255).astype(np.uint8)  # eosin pink
    tissue[nuclei[ys, xs].astype(bool)] = (118, 64, 158)  # haematoxylin purple
    img[ys, xs] = tissue
    return img


def write_synthetic_slide(
    path: str | Path,
    width: int = 4480,
    height: int = 2240,
    seed: int = 0,
    n_cores: int = 2,
    mpp: float = 0.486,
) -> Path:
    """Write a 3-level (1x, 4x, 16x) tiled pyramidal TIFF. Returns the path."""
    import tifffile

    path = Path(path)
    level0 = _tissue_image(width, height, seed, n_cores)
    res = (1e4 / mpp, 1e4 / mpp)  # pixels per centimetre
    with tifffile.TiffWriter(path, bigtiff=False) as tif:
        opts = dict(tile=(256, 256), photometric="rgb", compression="zlib")
        tif.write(level0, resolution=res, resolutionunit="CENTIMETER", **opts)  # type: ignore[arg-type]
        for ds in (4, 16):
            lvl = cv2.resize(level0, (width // ds, height // ds), interpolation=cv2.INTER_AREA)
            tif.write(lvl, subfiletype=1, resolution=(res[0] / ds, res[1] / ds), resolutionunit="CENTIMETER", **opts)  # type: ignore[arg-type]
    return path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="write a synthetic pyramidal test slide")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=4480)
    ap.add_argument("--height", type=int, default=2240)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    print(write_synthetic_slide(a.out, a.width, a.height, a.seed))
