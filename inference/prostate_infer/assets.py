"""Model assets: locate the FedAvg bundle, verify it against manifest.json, load Phikon.

A *bundle* is a folder with the layout of the clean HF model repo:

    models/model__fedavg__s{0..4}.pt
    calibration/res__fedavg__s{0..4}.json, preds__fedavg__s{0..4}.csv,
                tab_E08_operating_points.csv, ensemble_thresholds.json
    data/splits.csv
    config/config_nb02.json
    manifest.json            {relative path: sha256} for every file above

Sources (MODEL_SOURCE):
- local: MODEL_DIR already holds a bundle (built by scripts/build_bundle.py from the
  thesis output folder). No network, no token.
- hf:    snapshot_download of HF_MODEL_REPO at the pinned HF_MODEL_REVISION. With
  HF_MODEL_REPO_TYPE=dataset the flat training repo is mapped into the bundle layout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

N_SEEDS = 5
METHOD = "fedavg"
TAB_E08_METHOD = "FedAvg"


class AssetIntegrityError(RuntimeError):
    """A model file is missing or its sha256 does not match manifest.json."""


class Settings(BaseSettings):
    MODEL_SOURCE: Literal["local", "hf"] = "local"
    MODEL_DIR: Path = Path("/models/bundle")
    HF_MODEL_REPO: str = "Obaidullahmiakhil/prostate-fl-fedavg-phikon"
    HF_MODEL_REPO_TYPE: Literal["model", "dataset"] = "model"
    HF_MODEL_REVISION: str = "main"
    HF_TOKEN: SecretStr = SecretStr("")
    PHIKON_REPO: str = "owkin/phikon"
    PHIKON_REVISION: str = "main"
    MODEL_CACHE_DIR: Path = Path("/models/cache")
    OFFLINE: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def get_settings() -> Settings:
    s = Settings()
    if not s.HF_TOKEN.get_secret_value():
        # Docker secret (never an image layer or env var): /run/secrets/hf_token
        secret_file = Path(os.environ.get("HF_TOKEN_FILE", "/run/secrets/hf_token"))
        if secret_file.is_file():
            s.HF_TOKEN = SecretStr(secret_file.read_text().strip())
    if s.OFFLINE:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return s


def bundle_files() -> list[str]:
    """Every file a complete bundle must contain (manifest.json excluded)."""
    files = []
    for s in range(N_SEEDS):
        files += [
            f"models/model__{METHOD}__s{s}.pt",
            f"calibration/res__{METHOD}__s{s}.json",
            f"calibration/preds__{METHOD}__s{s}.csv",
        ]
    return files + [
        "calibration/tab_E08_operating_points.csv",
        "calibration/ensemble_thresholds.json",
        "data/splits.csv",
        "config/config_nb02.json",
    ]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(bundle: Path) -> dict[str, str]:
    manifest = {rel: sha256_file(bundle / rel) for rel in bundle_files() if (bundle / rel).exists()}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def verify_bundle(bundle: Path) -> dict[str, str]:
    """Check every required file exists and matches manifest.json; raise otherwise."""
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        raise AssetIntegrityError(f"manifest.json not found in {bundle}")
    manifest: dict[str, str] = json.loads(manifest_path.read_text())
    for rel in bundle_files():
        if rel not in manifest:
            raise AssetIntegrityError(f"{rel} is not listed in manifest.json")
        p = bundle / rel
        if not p.exists():
            raise AssetIntegrityError(f"missing model file: {rel}")
        actual = sha256_file(p)
        if actual != manifest[rel]:
            raise AssetIntegrityError(f"sha256 mismatch for {rel}: expected {manifest[rel][:12]}, got {actual[:12]}")
    return manifest


def model_version(bundle: Path) -> str:
    """sha256 over manifest.json + the ensemble thresholds file (identifies the exact model)."""
    h = hashlib.sha256()
    h.update((bundle / "manifest.json").read_bytes())
    h.update((bundle / "calibration/ensemble_thresholds.json").read_bytes())
    return h.hexdigest()


# Flat file names in the dataset fallback repo -> bundle layout
def _flat_to_bundle(name: str) -> str | None:
    if name.startswith(f"model__{METHOD}__s") and name.endswith(".pt"):
        return f"models/{name}"
    if name.startswith((f"res__{METHOD}__s", f"preds__{METHOD}__s")):
        return f"calibration/{name}"
    return {
        "tab_E08_operating_points.csv": "calibration/tab_E08_operating_points.csv",
        "ensemble_thresholds.json": "calibration/ensemble_thresholds.json",
        "splits.csv": "data/splits.csv",
        "config_nb02.json": "config/config_nb02.json",
    }.get(name)


def _download_hf_bundle(s: Settings) -> Path:
    from huggingface_hub import snapshot_download

    token = s.HF_TOKEN.get_secret_value() or None
    if s.HF_MODEL_REVISION == "main":
        logger.warning("HF_MODEL_REVISION is 'main'; pin a commit hash for production")
    if s.HF_MODEL_REPO_TYPE == "model":
        snapshot = snapshot_download(
            repo_id=s.HF_MODEL_REPO,
            repo_type="model",
            revision=s.HF_MODEL_REVISION,
            token=token,
            cache_dir=s.MODEL_CACHE_DIR / "hf",
            allow_patterns=["models/*", "calibration/*", "data/*", "config/*", "manifest.json"],
            local_files_only=s.OFFLINE,
        )
        return Path(snapshot)
    # dataset fallback: flat names at the top level; map them into a local bundle copy
    patterns = [
        f"model__{METHOD}__s*.pt",
        f"res__{METHOD}__s*.json",
        f"preds__{METHOD}__s*.csv",
        "splits.csv",
        "config_nb02.json",
        "tab_E08_operating_points.csv",
        "ensemble_thresholds.json",
    ]
    path = Path(
        snapshot_download(
            repo_id=s.HF_MODEL_REPO,
            repo_type="dataset",
            revision=s.HF_MODEL_REVISION,
            token=token,
            cache_dir=s.MODEL_CACHE_DIR / "hf",
            allow_patterns=patterns,
            local_files_only=s.OFFLINE,
        )
    )
    bundle = s.MODEL_CACHE_DIR / f"bundle-{s.HF_MODEL_REVISION[:12]}"
    for f in path.iterdir():
        rel = _flat_to_bundle(f.name)
        if rel:
            (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, bundle / rel)
    if not (bundle / "manifest.json").exists():
        write_manifest(bundle)  # the dataset repo has no manifest: pin what was downloaded
    return bundle


def resolve_bundle(s: Settings | None = None) -> Path:
    s = s or get_settings()
    return Path(s.MODEL_DIR) if s.MODEL_SOURCE == "local" else _download_hf_bundle(s)


@dataclass
class Assets:
    bundle: Path
    version: str
    settings: Settings

    @cached_property
    def ensemble_thresholds(self) -> list[float]:
        return list(json.loads((self.bundle / "calibration/ensemble_thresholds.json").read_text())["thresholds"])

    @cached_property
    def operating_points(self) -> dict[str, float]:
        """Clinical thresholds from tab_E08 rows with Method == 'FedAvg' (fitted on validation)."""
        import csv

        rows = list(csv.DictReader(open(self.bundle / "calibration/tab_E08_operating_points.csv", encoding="utf-8")))
        want = {
            ("csPCa (ISUP ≥ 2)", "Youden (val)"): "cspca_youden",
            ("csPCa (ISUP ≥ 2)", "Sensitivity ≥ 90% (val)"): "cspca_sens90",
            ("csPCa (ISUP ≥ 2)", "Sensitivity ≥ 95% (val)"): "cspca_sens95",
            ("Cancer (ISUP ≥ 1)", "Youden (val)"): "cancer_youden",
        }
        out = {
            want[(r["Task"], r["Operating point"])]: float(r["Threshold"])
            for r in rows
            if r["Method"] == TAB_E08_METHOD and (r["Task"], r["Operating point"]) in want
        }
        missing = set(want.values()) - set(out)
        if missing:
            raise AssetIntegrityError(f"tab_E08 has no FedAvg rows for {sorted(missing)}")
        return out

    def model_path(self, seed: int) -> Path:
        return self.bundle / f"models/model__{METHOD}__s{seed}.pt"


def fetch_assets(s: Settings | None = None) -> Assets:
    """Locate/download the bundle, verify every sha256, and make sure Phikon is cached."""
    s = s or get_settings()
    bundle = resolve_bundle(s)
    verify_bundle(bundle)
    load_phikon(s)  # downloads once into MODEL_CACHE_DIR; later runs can use OFFLINE=1
    version = model_version(bundle)
    logger.info("model assets verified", extra={"model_version": version[:12]})
    return Assets(bundle=bundle, version=version, settings=s)


def load_phikon(s: Settings):  # type: ignore[no-untyped-def]
    from transformers import ViTModel

    return ViTModel.from_pretrained(
        s.PHIKON_REPO,
        revision=s.PHIKON_REVISION,
        add_pooling_layer=False,
        cache_dir=str(s.MODEL_CACHE_DIR / "hf"),
        local_files_only=s.OFFLINE,
    )
