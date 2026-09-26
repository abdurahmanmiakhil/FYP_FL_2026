# prostate-infer

Single-slide inference that returns exactly what the thesis FedAvg ensemble returns.
Code is copied verbatim from `artifacts/nb01_features.ipynb` (tiling, Phikon encoder),
`nb02_federated.ipynb` (GatedABMIL, ordinal maths) and `nb03_evaluation.ipynb` (seed ensemble).

## Model assets
A *bundle* holds the 5 FedAvg heads, calibration files and a sha256 `manifest.json`:

```bash
python -m prostate_infer build-bundle --fl-dir .../fl_outputs_phikon --results-dir .../results_phikon --out models/bundle
python -m prostate_infer fetch            # verify every sha256, cache owkin/phikon, print the model version
```

Settings (env / `.env`): `MODEL_SOURCE` (`local` | `hf`), `MODEL_DIR`, `HF_MODEL_REPO`, `HF_MODEL_REPO_TYPE`,
`HF_MODEL_REVISION` (pin a commit), `HF_TOKEN` or `/run/secrets/hf_token`, `PHIKON_REPO`, `PHIKON_REVISION`,
`MODEL_CACHE_DIR`, `OFFLINE`. The worker refuses to start if any file fails its checksum.
`model_version` = sha256(manifest.json + ensemble_thresholds.json).

`scripts/fit_ensemble_thresholds.py` re-fits the ensemble ISUP thresholds on validation (nb03) and can upload
them to the HF repo; `scripts/publish_model_repo.py` publishes the bundle as a private HF model repo.

## Use
```bash
python -m prostate_infer predict slide.tiff --out result.json --heatmap heatmap.png [--image-id PANDA_ID]
```
```python
from prostate_infer.predict import predict_slide, make_heatmap, top_tiles

pred = predict_slide("slide.tiff", progress=lambda stage, done, total: ...)
pred.isup_grade, pred.p_cancer, pred.p_cspca, pred.p_isup, pred.operating_point_flags, pred.low_confidence_reasons
```
`image_id` seeds the 768-tile cap and the 512-tile bag sub-sample (pass the PANDA image_id to match the thesis).

Measured on CPU (Apple M3, 4 cores): ~24 s for 112 tiles including model loading; ~1.5-2 min for 768 tiles.
On a T4 GPU (fp16 autocast, batch 256) a slide takes seconds.

## Tests
```bash
pytest                                   # fast tests: synthetic slides, fake bundle, fake encoder
THESIS_BUNDLE=models/bundle pytest       # + checks that the ensemble reproduces the thesis metrics
PANDA_DIR=... THESIS_BUNDLE=models/bundle pytest -m slow tests/test_parity.py -s   # logits vs thesis, |delta| < 0.05
```
