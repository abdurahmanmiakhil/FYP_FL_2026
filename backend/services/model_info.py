"""Model card content (thesis results) and model readiness reported by the workers."""

from __future__ import annotations

import json
from typing import Any

from prostate_infer.schemas import PREPROCESSING_VERSION

from ..core.redis import get_redis
from ..schemas import ModelCard

READY_KEY = "worker:ready"
MODEL_INFO_KEY = "model:info"

THESIS_RESULTS = {
    "source": "BS thesis, NUML 2026 - FedAvg 5-seed ensemble, pooled PANDA test set (2,124 slides), "
    "thresholds fitted on validation",
    "fedavg_ensemble": {"csPCa AUC": 0.9705, "cancer AUC": 0.9935, "macro AUC (6-class)": 0.9393, "QWK": 0.9047},
    "fedavg_ensemble_95ci": {
        "csPCa AUC": [0.9641, 0.9766],
        "cancer AUC": [0.9911, 0.9956],
        "macro AUC (6-class)": [0.9327, 0.9455],
        "QWK": [0.8897, 0.9188],
    },
    "per_hospital": {
        "Hospital A - Radboud (n=1,032)": {"csPCa AUC": 0.9654, "cancer AUC": 0.9844, "QWK": 0.8695},
        "Hospital B - Karolinska (n=1,092)": {"csPCa AUC": 0.9691, "cancer AUC": 0.9977, "QWK": 0.9268},
    },
    "single_model_mean_over_seeds": {"csPCa AUC": 0.970, "cancer AUC": 0.993, "QWK": 0.899},
}

TRAINING_DATA = {
    "dataset": "PANDA challenge (Radboud University Medical Center + Karolinska Institutet)",
    "slides": 10614,
    "hospitals": 2,
    "federated": "FedAvg across 2 hospitals (no slide left its hospital); 40 rounds x 1 local epoch; 5 seeds",
    "tile_encoder": "owkin/phikon (frozen ViT-B/16, [CLS] 768-d), 224 px tiles at 20x (~0.5 um/px)",
    "slide_model": "Gated attention MIL (GatedABMIL, 264,582 parameters) with an ordinal 5-logit head",
    "splits": "70% train / 10% validation / 20% test per hospital, near-duplicate slides grouped",
}

INTENDED_USE = (
    "Decision support for pathologists reviewing H&E-stained prostate core-needle biopsy whole-slide images "
    "scanned at 20x (~0.5 um/px). It estimates the ISUP grade group, the probability of cancer and of clinically "
    "significant cancer (ISUP >= 2), and highlights the regions that drove the estimate. Every result is "
    "provisional until a clinician reviews it. It is not a diagnosis and must not be used without a pathologist."
)

LIMITATIONS = [
    "Trained and tested only on PANDA data from two hospitals (Radboud, Karolinska); no external cohort yet.",
    "Only H&E core-needle biopsies at ~0.5 um/px; other tissue, stains, magnifications or scanners are out of scope.",
    "Slide-level labels only; the heatmap shows model attention, not a validated tumour segmentation.",
    "Performance may drop on stain or scanner characteristics outside the PANDA range (the app warns).",
    "Slides with more than 768 tissue tiles are sub-sampled (seeded, reproducible); very large slides are "
    "represented by a sample.",
    "Research prototype: clinical use needs prospective validation, ethics approval and regulatory clearance.",
]


def publish_worker(name: str, version: str, device: str, thresholds: dict[str, Any]) -> None:
    """Called by a worker once its models are loaded and verified."""
    r = get_redis()
    r.hset(READY_KEY, name, json.dumps({"model_version": version, "device": device}))
    r.set(MODEL_INFO_KEY, json.dumps({"model_version": version, "thresholds": thresholds}))


def unpublish_worker(name: str) -> None:
    get_redis().hdel(READY_KEY, name)


def worker_status() -> list[dict[str, str]]:
    try:
        raw = get_redis().hgetall(READY_KEY)
    except Exception:
        return []
    return [{"worker": k.decode() if isinstance(k, bytes) else str(k), **json.loads(v)} for k, v in raw.items()]


def model_card() -> ModelCard:
    workers = worker_status()
    try:
        info = json.loads(get_redis().get(MODEL_INFO_KEY) or b"{}")
    except Exception:
        info = {}
    return ModelCard(
        model_version=info.get("model_version"),
        preprocessing_version=PREPROCESSING_VERSION,
        ready=bool(workers),
        workers=workers,
        thresholds=info.get("thresholds", {}),
        thesis_results=THESIS_RESULTS,
        training_data=TRAINING_DATA,
        intended_use=INTENDED_USE,
        limitations=LIMITATIONS,
    )
