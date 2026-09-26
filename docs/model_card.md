# Model card - GleasonAI FedAvg ensemble

| | |
|---|---|
| Name | GleasonAI prostate biopsy grader (FedAvg seed ensemble) |
| Source | BS thesis *Federated Deep Learning for Prostate Cancer Gleason Grading via Histopathology Images and Telehealth Integration*, NUML, 2026 |
| Version | `model_version` = sha256(manifest.json + ensemble_thresholds.json), shown in the app and stored with every prediction. Current bundle: `a1e53bfd0d6dbe743257b338188d466a1941a8d431c30f5be84a5db6f1faf343` |
| Preprocessing version | `nb01-v2:otsu-sat15-60/tile224@L0/tissue0.5-0.25-0.1/max768/phikon-cls-fp16;nb02:bag512` |
| Status | Research prototype. **Not a medical device.** |

## Intended use

Decision support for pathologists reviewing **H&E-stained prostate core-needle biopsy** whole-slide
images scanned at **20x (~0.5 µm/px)**. It estimates the ISUP grade group, the probability of cancer
(ISUP ≥ 1) and of clinically significant cancer (csPCa, ISUP ≥ 2), and highlights the regions that
drove the estimate. Every result is provisional until a clinician reviews it.

**Not intended for:** diagnosis without a pathologist, radical prostatectomy specimens, TURP chips,
frozen sections, IHC or non-H&E stains, other organs, magnifications other than ~20x.

## Architecture

1. Tissue mask on the lowest pyramid level (Otsu on saturation clipped 15-60, pen-mark removal, 5x5 close/open).
2. 224x224 px tiles at level 0 with ≥ 50 % tissue (fallback 25 %, 10 %), at most 768 per slide (seeded by slide id).
3. **Phikon** (owkin/phikon, frozen ViT-B/16, rev `057cc029`) [CLS] features, 768-d, stored as fp16.
4. **GatedABMIL** (264,582 parameters): Linear 768→256 + GELU, gated attention (128), LayerNorm + Linear 256→5 cumulative ordinal logits; bags > 512 tiles sub-sampled to 512.
5. **Ensemble**: mean over 5 FedAvg seeds of the monotone cumulative probabilities s_k = min_{j≤k} σ(o_j).
6. Outputs: P(cancer) = s0, P(csPCa) = s1, P(ISUP = c) = s_{c-1} − s_c; ISUP grade from ordinal thresholds tuned once on validation (`[0.300, 0.475, 0.300, 0.450, 0.425]`).

## Training data

PANDA challenge: Radboud University Medical Center (Netherlands) and Karolinska Institutet (Sweden);
10,614 slides after removing label-inconsistent slides; 70 / 10 / 20 % train / validation / test per
hospital with near-duplicate slides grouped. Federated training: FedAvg across the two hospitals, 40
rounds x 1 local epoch, 5 seeds; slides never left their hospital.

## Performance (pooled PANDA test set, 2,124 slides)

| Metric | FedAvg ensemble | 95 % CI |
|---|---|---|
| csPCa AUC (ISUP ≥ 2) | **0.9705** | 0.9641 - 0.9766 |
| Cancer AUC (ISUP ≥ 1) | **0.9935** | 0.9911 - 0.9956 |
| Macro AUC (6 classes, one-vs-rest) | 0.9393 | 0.9327 - 0.9455 |
| Quadratic weighted kappa | **0.9047** | 0.8897 - 0.9188 |

Per hospital: Radboud (n = 1,032) csPCa AUC 0.9654, QWK 0.8695; Karolinska (n = 1,092) csPCa AUC
0.9691, QWK 0.9268. Single models (mean of 5 seeds): csPCa AUC 0.970, cancer AUC 0.993, QWK 0.899.
The web implementation reproduces the ensemble numbers exactly (docs/validation_report.md, part A).

### Clinical operating points (FedAvg, chosen on validation, applied unchanged to test)

| Task | Operating point | Threshold | Sensitivity | Specificity |
|---|---|---|---|---|
| csPCa | Youden | 0.481 | 0.894 | 0.947 |
| csPCa | Sensitivity ≥ 90 % | 0.420 | 0.900 | 0.929 |
| csPCa | Sensitivity ≥ 95 % | 0.173 | 0.942 | 0.811 |
| Cancer | Youden | 0.660 | 0.959 | 0.969 |

## Safety features in the app

- Disclaimer on every result, on the PDF and in the API response; result **provisional** until reviewed.
- **Mandatory review flags**: P(csPCa) between the 95 %-sensitivity and Youden thresholds; fewer than 50
  tissue tiles; disagreement between the 5 seed models (SD of P(csPCa) > 0.10); stain colour outside the
  PANDA range.
- **Out-of-distribution warnings**: missing or non-20x resolution metadata; tissue hue, saturation,
  brightness or optical density outside mean ± 3 SD of both PANDA hospitals (thesis Table P03).
- Weekly drift report (P(csPCa) distribution and stain statistics vs the PANDA reference).

## Limitations

- Trained and tested on PANDA only (two hospitals, two scanners); **no external validation cohort**.
- Slide-level labels; the attention heatmap shows what influenced the model, not a validated tumour outline.
- PANDA reference grades have inter-observer variability; QWK is bounded by label quality.
- Large slides (> 768 tissue tiles) are represented by a reproducible random sample of tiles.
- Performance on other scanners, stains, staining protocols and populations is unknown.
- Clinical use would require prospective validation, ethics approval and medical-device regulatory clearance.
