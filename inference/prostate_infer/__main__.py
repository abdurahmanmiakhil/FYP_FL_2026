"""CLI: python -m prostate_infer {fetch,predict,build-bundle,fit-thresholds}"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m prostate_infer", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fetch", help="download (if MODEL_SOURCE=hf) and verify all model files, cache Phikon")

    p = sub.add_parser("predict", help="run the FedAvg ensemble on one slide")
    p.add_argument("slide")
    p.add_argument("--out", required=True, help="result JSON path")
    p.add_argument("--heatmap", help="attention heatmap PNG path")
    p.add_argument("--image-id", help="slide id used for seeding (PANDA image_id); default: file stem")
    p.add_argument("--device", default="auto")

    b = sub.add_parser("build-bundle", help="package thesis outputs into a verified model bundle")
    b.add_argument("--fl-dir", required=True, type=Path, help="…/submission_files/fl_outputs_phikon")
    b.add_argument("--results-dir", required=True, type=Path, help="…/submission_files/results_phikon")
    b.add_argument("--out", required=True, type=Path)

    f = sub.add_parser("fit-thresholds", help="re-fit ensemble thresholds on validation and print test metrics")
    f.add_argument("--bundle", required=True, type=Path)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "fetch":
        from .assets import fetch_assets

        a = fetch_assets()
        print(f"model_version: {a.version}\nbundle: {a.bundle}\nensemble thresholds: {a.ensemble_thresholds}")
        print(f"operating points: {a.operating_points}")
    elif args.cmd == "predict":
        import openslide

        from .predict import make_heatmap, predict_slide

        def show(stage: str, done: int, total: int) -> None:
            print(f"\r{stage}: {done}/{total}   ", end="", file=sys.stderr, flush=True)

        pred = predict_slide(args.slide, device=args.device, image_id=args.image_id, progress=show)
        print(file=sys.stderr)
        Path(args.out).write_text(pred.model_dump_json(indent=2))
        if args.heatmap:
            with openslide.OpenSlide(args.slide) as sl:
                make_heatmap(pred, sl).save(args.heatmap)
        print(
            f"ISUP {pred.isup_grade} ({pred.gleason_hint}) | P(cancer) {pred.p_cancer:.3f} | "
            f"P(csPCa) {pred.p_cspca:.3f} | {pred.n_tiles} tiles | {pred.runtime_seconds:.1f}s on {pred.device}"
        )
    elif args.cmd == "build-bundle":
        from .bundle import build_bundle

        print(json.dumps(build_bundle(args.fl_dir, args.results_dir, args.out), indent=2))
    elif args.cmd == "fit-thresholds":
        from .bundle import fit_ensemble_thresholds

        print(json.dumps(fit_ensemble_thresholds(args.bundle), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
