"""Slide validation and out-of-distribution (OOD) checks.

Colour statistics are computed exactly like nb01 `p_colour_shift` (tissue pixels of the
low-resolution level). Reference ranges come from thesis Table P03 (150 random slides per
hospital): a slide is flagged when a statistic is outside mean +/- 3 SD of *both* hospitals.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import openslide

from .schemas import SlideQC

MAX_SLIDE_BYTES = 2 * 1024**3
ALLOWED_SUFFIXES = {".tif", ".tiff", ".svs"}

# thesis Table P03: (mean, sd) per hospital
PANDA_COLOUR = {
    "hue": [(307.074, 5.708), (323.320, 6.845)],
    "saturation": [(0.406, 0.116), (0.274, 0.047)],
    "brightness": [(0.742, 0.052), (0.832, 0.038)],
    "optical_density": [(1.631, 0.502), (1.083, 0.221)],
}
PANDA_MPP_RANGE = (0.35, 0.65)  # PANDA level 0 is ~0.45-0.49 um/px (20x)
MIN_TISSUE_PIXELS = 50  # nb01: slides with fewer tissue pixels are skipped


class SlideValidationError(ValueError):
    """The file is not a readable pyramidal whole-slide image."""


class NoTissueError(SlideValidationError):
    """No tissue tiles were found (nb01 logs this as 'no tissue tiles')."""


def validate_slide_file(path: str | Path) -> openslide.OpenSlide:
    """Reject unsupported, oversize, unreadable or non-pyramidal files. Returns the open slide."""
    p = Path(path)
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise SlideValidationError(f"unsupported file type {p.suffix!r}; use .tif, .tiff or .svs")
    size = p.stat().st_size
    if size > MAX_SLIDE_BYTES:
        raise SlideValidationError("slide is larger than 2 GB")
    if size == 0:
        raise SlideValidationError("file is empty")
    try:
        slide = openslide.OpenSlide(str(p))
    except Exception as e:  # openslide raises several unrelated types
        raise SlideValidationError(f"not a readable whole-slide image ({type(e).__name__})") from e
    if slide.level_count < 2:
        slide.close()
        raise SlideValidationError("slide is not pyramidal (only one resolution level)")
    return slide


def slide_mpp(slide: openslide.OpenSlide) -> float | None:
    for key in (openslide.PROPERTY_NAME_MPP_X, "tiff.XResolution"):
        v = slide.properties.get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        if key == "tiff.XResolution":  # pixels per resolution unit
            unit = slide.properties.get("tiff.ResolutionUnit", "")
            if f <= 0 or unit not in ("centimeter", "inch"):
                continue
            f = (1e4 if unit == "centimeter" else 25400.0) / f
        if 0.05 < f < 20:
            return f
    return None


def colour_stats(rgb: np.ndarray, mask: np.ndarray) -> dict[str, float] | None:
    m = mask.astype(bool)
    if m.sum() < MIN_TISSUE_PIXELS:
        return None
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[m]
    od = -np.log(np.clip(rgb[m].astype(float) / 255, 1e-3, 1))
    return {
        "hue": float(hsv[:, 0].mean() * 2),
        "saturation": float(hsv[:, 1].mean() / 255),
        "brightness": float(hsv[:, 2].mean() / 255),
        "optical_density": float(od.sum(1).mean()),
    }


def slide_qc(slide: openslide.OpenSlide, low_rgb: np.ndarray, mask: np.ndarray) -> SlideQC:
    warnings: list[str] = []
    mpp = slide_mpp(slide)
    if mpp is None:
        warnings.append(
            "Resolution (microns per pixel) is not recorded in the file; the model expects ~0.5 um/px (20x)."
        )
    elif not PANDA_MPP_RANGE[0] <= mpp <= PANDA_MPP_RANGE[1]:
        warnings.append(f"Resolution {mpp:.2f} um/px differs from the ~0.5 um/px (20x) the model was trained on.")

    stats = colour_stats(low_rgb, mask)
    if stats is not None:
        for name, refs in PANDA_COLOUR.items():
            v = stats[name]
            if not any(mu - 3 * sd <= v <= mu + 3 * sd for mu, sd in refs):
                warnings.append(
                    f"Stain colour ({name.replace('_', ' ')} {v:.2f}) is outside the range of the PANDA "
                    "training slides; this may not be an H&E prostate biopsy or the stain differs."
                )
    m = mask.astype(bool)
    mean_rgb = low_rgb[m].mean(0).tolist() if m.any() else low_rgb.reshape(-1, 3).mean(0).tolist()
    return SlideQC(
        mpp=mpp, tissue_fraction=float(m.mean()), mean_rgb=[float(x) for x in mean_rgb], colour=stats, warnings=warnings
    )
