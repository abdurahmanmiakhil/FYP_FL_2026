"""Fit the seed-ensemble ISUP thresholds on validation and (optionally) upload them to HF.

    python scripts/fit_ensemble_thresholds.py --bundle models/bundle            # local
    python scripts/fit_ensemble_thresholds.py --bundle models/bundle --upload   # + new HF commit

Averages the 5 preds__fedavg CSVs (split=='val', sigmoid + monotone), runs tune_thresholds
once (nb03 Cell 4), writes calibration/ensemble_thresholds.json and refreshes manifest.json.
--upload pushes both files to HF_MODEL_REPO as a new commit (needs a write HF_TOKEN).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from prostate_infer.assets import write_manifest
from prostate_infer.bundle import fit_ensemble_thresholds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--upload", action="store_true")
    a = ap.parse_args()

    fit = fit_ensemble_thresholds(a.bundle)
    (a.bundle / "calibration/ensemble_thresholds.json").write_text(json.dumps(fit, indent=2))
    write_manifest(a.bundle)
    print(json.dumps(fit, indent=2))

    if a.upload:
        from huggingface_hub import HfApi

        repo = os.environ.get("HF_MODEL_REPO", "Obaidullahmiakhil/prostate-fl-fedavg-phikon")
        api = HfApi(token=os.environ["HF_TOKEN"])
        for rel in ("calibration/ensemble_thresholds.json", "manifest.json"):
            api.upload_file(
                path_or_fileobj=str(a.bundle / rel),
                path_in_repo=rel,
                repo_id=repo,
                repo_type="model",
                commit_message="ensemble thresholds (FedAvg, 5 seeds)",
            )
        print("uploaded; pin this revision:", api.model_info(repo).sha)


if __name__ == "__main__":
    main()
