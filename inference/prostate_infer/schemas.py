from __future__ import annotations

from pydantic import BaseModel, Field

DISCLAIMER = "AI decision support only - final diagnosis requires a pathologist."

PREPROCESSING_VERSION = "nb01-v2:otsu-sat15-60/tile224@L0/tissue0.5-0.25-0.1/max768/phikon-cls-fp16;nb02:bag512"

# ISUP grade group -> Gleason pattern(s) (Epstein et al. 2016)
GLEASON_HINT = {0: "benign", 1: "3+3", 2: "3+4", 3: "4+3", 4: "4+4 / 3+5 / 5+3", 5: "4+5 / 5+4 / 5+5"}


class OperatingPointFlags(BaseModel):
    """True when the ensemble probability is at or above the tab_E08 FedAvg threshold."""

    cspca_youden: bool
    cspca_sens90: bool
    cspca_sens95: bool
    cancer_youden: bool


class PerSeedOutput(BaseModel):
    logits: list[float]
    p_cancer: float
    p_cspca: float
    p_isup: list[float]
    isup_grade: int


class SlideQC(BaseModel):
    """Out-of-distribution checks against the PANDA training data (thesis Table 4.4)."""

    mpp: float | None = None
    tissue_fraction: float
    mean_rgb: list[float]
    colour: dict[str, float] | None = None  # hue, saturation, brightness, optical_density (nb01 P03)
    warnings: list[str] = []


class SlidePrediction(BaseModel):
    p_cancer: float
    p_cspca: float
    p_isup: list[float] = Field(..., min_length=6, max_length=6)
    isup_grade: int
    gleason_hint: str
    operating_point_flags: OperatingPointFlags
    thresholds: dict[str, float]
    n_tiles: int  # tiles encoded (<= 768)
    n_tiles_total: int  # tissue tiles found before the 768 cap
    n_tiles_model: int  # tiles in the bag given to the heads (<= 512)
    tile_coords: list[list[int]]  # level-0 (x, y) of each encoded tile
    attention: list[float]  # per encoded tile, mean over the 5 seeds
    per_seed: dict[str, PerSeedOutput]
    seed_std_p_cspca: float
    low_confidence_reasons: list[str]
    qc: SlideQC
    slide_width: int
    slide_height: int
    model_version: str
    preprocessing_version: str
    runtime_seconds: float
    device: str
    disclaimer: str = DISCLAIMER
