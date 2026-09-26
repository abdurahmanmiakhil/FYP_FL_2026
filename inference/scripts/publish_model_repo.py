"""Publish the local model bundle to a clean private Hugging Face model repo (optional).

    HF_TOKEN=<write token> python scripts/publish_model_repo.py --bundle models/bundle

Then create a fine-grained READ-only token for that repo, and run the app with
MODEL_SOURCE=hf HF_MODEL_REPO=... HF_MODEL_REVISION=<printed sha> HF_TOKEN=<read token>.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from prostate_infer.assets import bundle_files, verify_bundle


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--repo", default="Obaidullahmiakhil/prostate-fl-fedavg-phikon")
    a = ap.parse_args()

    from huggingface_hub import HfApi

    verify_bundle(a.bundle)
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(a.repo, repo_type="model", private=True, exist_ok=True)
    api.upload_folder(
        folder_path=str(a.bundle),
        repo_id=a.repo,
        repo_type="model",
        allow_patterns=[*bundle_files(), "manifest.json"],
        commit_message="FedAvg seed ensemble (thesis models) + calibration + manifest",
    )
    print("done; pin this revision in the app (HF_MODEL_REVISION):", api.model_info(a.repo).sha)


if __name__ == "__main__":
    main()
