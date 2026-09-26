"""Fast unit tests: preprocessing, model maths, assets, validation, predict (fake encoder)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import openslide
import pytest
import torch

from prostate_infer.assets import AssetIntegrityError, Assets, Settings, model_version, verify_bundle
from prostate_infer.model import (
    GatedABMIL,
    class_probs,
    class_probs_from_s,
    grade_from_s,
    mono_sigmoid,
    tune_thresholds,
)
from prostate_infer.preprocess import MAX_TILES, TILE_PX, id_seed, lowres_rgb, tile_coords, tissue_mask
from prostate_infer.qc import NoTissueError, SlideValidationError, colour_stats, validate_slide_file
from prostate_infer.synthetic import write_synthetic_slide

# ---------------------------------------------------------------- model maths


def test_gated_abmil_param_count_matches_thesis() -> None:
    assert sum(p.numel() for p in GatedABMIL(768).parameters()) == 264_582


def test_mono_sigmoid_is_monotone_and_class_probs_sum_to_one() -> None:
    logits = np.array([[-1.0, 2.0, 0.5, 3.0, -4.0], [5.0, 4.0, 3.0, 2.0, 1.0]])
    s = mono_sigmoid(logits)
    assert np.all(np.diff(s, axis=1) <= 0)
    p = class_probs(logits)
    assert p.shape == (2, 6)
    np.testing.assert_allclose(p.sum(1), 1.0)
    np.testing.assert_allclose(class_probs_from_s(s), p)


def test_grade_counts_thresholds_exceeded() -> None:
    s = np.array([[0.9, 0.8, 0.2, 0.1, 0.0]])
    assert grade_from_s(s, [0.5] * 5)[0] == 2
    assert grade_from_s(s, [0.95, 0.5, 0.5, 0.5, 0.5])[0] == 1


def test_tune_thresholds_returns_five_thresholds() -> None:
    rng = np.random.default_rng(0)
    y = rng.integers(0, 6, 400)
    s = mono_sigmoid((y[:, None] - np.arange(5) - 0.5) * 2 + rng.normal(0, 1, (400, 5)))
    thr, q = tune_thresholds(y, s)
    assert len(thr) == 5 and 0 < q <= 1


def test_attention_scores_match_forward_softmax() -> None:
    torch.manual_seed(0)
    m = GatedABMIL(768).eval()
    x = torch.randn(1, 30, 768)
    _, a = m(x, torch.ones(1, 30, dtype=torch.bool))
    torch.testing.assert_close(torch.softmax(m.attention_scores(x)[0], 0), a[0])


# ---------------------------------------------------------------- preprocessing


def test_tissue_mask_finds_tissue_not_background() -> None:
    rgb = np.full((100, 100, 3), 245, np.uint8)
    rgb[20:80, 20:80] = [226, 150, 196]
    m = tissue_mask(rgb)
    assert m[50, 50] == 1 and m[5, 5] == 0


def test_tissue_mask_removes_pen_marks() -> None:
    rgb = np.full((100, 100, 3), 245, np.uint8)
    rgb[20:80, 20:80] = [40, 160, 60]  # green pen
    assert tissue_mask(rgb).sum() == 0


def test_tile_coords_on_synthetic_slide(slide_path: Path) -> None:
    with openslide.OpenSlide(str(slide_path)) as sl:
        coords, n_all = tile_coords(sl, "synthetic")
        W, H = sl.level_dimensions[0]
    assert coords.ndim == 2 and coords.shape[1] == 2 and len(coords) == n_all > 0
    assert (coords % TILE_PX == 0).all()
    assert (coords[:, 0] + TILE_PX <= W).all() and (coords[:, 1] + TILE_PX <= H).all()


def test_tile_cap_is_seeded_by_slide_id(tmp_path: Path) -> None:
    p = write_synthetic_slide(tmp_path / "big.tiff", 17920, 8960, seed=2, n_cores=5)
    with openslide.OpenSlide(str(p)) as sl:
        a, n = tile_coords(sl, "slide-a")
        b, _ = tile_coords(sl, "slide-a")
        c, _ = tile_coords(sl, "slide-b")
    assert n > MAX_TILES and len(a) == MAX_TILES
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)
    assert id_seed("slide-a") != id_seed("slide-b")


def test_lowres_uses_smallest_level(slide_path: Path) -> None:
    with openslide.OpenSlide(str(slide_path)) as sl:
        rgb, ds = lowres_rgb(sl)
        assert ds == 16.0 and rgb.shape[:2] == sl.level_dimensions[-1][::-1]


# ---------------------------------------------------------------- validation / QC


def test_validate_rejects_wrong_suffix(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG")
    with pytest.raises(SlideValidationError, match="unsupported"):
        validate_slide_file(p)


def test_validate_rejects_garbage_tiff(tmp_path: Path) -> None:
    p = tmp_path / "x.tiff"
    p.write_bytes(b"not a tiff at all" * 10)
    with pytest.raises(SlideValidationError):
        validate_slide_file(p)


def test_validate_rejects_single_level_tiff(tmp_path: Path) -> None:
    import tifffile

    p = tmp_path / "flat.tiff"
    tifffile.imwrite(p, np.full((512, 512, 3), 200, np.uint8), tile=(256, 256), photometric="rgb")
    with pytest.raises(SlideValidationError, match="pyramidal"):
        validate_slide_file(p)


def test_validate_accepts_synthetic_slide_and_reads_mpp(slide_path: Path) -> None:
    from prostate_infer.qc import slide_mpp

    sl = validate_slide_file(slide_path)
    assert sl.level_count == 3
    assert slide_mpp(sl) == pytest.approx(0.486, abs=1e-3)
    sl.close()


def test_colour_stats_flag_non_he_stain() -> None:
    from prostate_infer.qc import PANDA_COLOUR

    rgb = np.full((50, 50, 3), [60, 200, 90], np.uint8)  # green "tissue"
    st = colour_stats(rgb, np.ones((50, 50), np.uint8))
    assert st is not None
    lo = min(m - 3 * s for m, s in PANDA_COLOUR["hue"])
    hi = max(m + 3 * s for m, s in PANDA_COLOUR["hue"])
    assert not lo <= st["hue"] <= hi


# ---------------------------------------------------------------- assets


def test_verify_bundle_passes_and_detects_tampering(fake_bundle: Path, tmp_path: Path) -> None:
    verify_bundle(fake_bundle)
    bad = tmp_path / "bad"
    shutil.copytree(fake_bundle, bad)
    with open(bad / "models/model__fedavg__s2.pt", "ab") as f:
        f.write(b"x")
    with pytest.raises(AssetIntegrityError, match="sha256 mismatch"):
        verify_bundle(bad)
    (bad / "models/model__fedavg__s2.pt").unlink()
    with pytest.raises(AssetIntegrityError, match="missing"):
        verify_bundle(bad)


def test_model_version_changes_with_thresholds(fake_bundle: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    shutil.copytree(fake_bundle, other)
    t = json.loads((other / "calibration/ensemble_thresholds.json").read_text())
    t["thresholds"][0] += 0.025
    (other / "calibration/ensemble_thresholds.json").write_text(json.dumps(t))
    assert model_version(fake_bundle) != model_version(other)


def test_operating_points_read_fedavg_rows_only(fake_bundle: Path) -> None:
    a = Assets(fake_bundle, "v", Settings())
    assert a.operating_points == {
        "cspca_youden": 0.48,
        "cspca_sens90": 0.42,
        "cspca_sens95": 0.17,
        "cancer_youden": 0.66,
    }
    assert len(a.ensemble_thresholds) == 5


def test_hf_token_is_never_printed() -> None:
    s = Settings(HF_TOKEN="hf_secret_value")  # type: ignore[arg-type]
    assert "hf_secret_value" not in repr(s) and "hf_secret_value" not in str(s.model_dump())


# ---------------------------------------------------------------- predict (fake Phikon)


def test_predict_slide_end_to_end(engine_env: Path, slide_path: Path) -> None:
    from prostate_infer.predict import make_heatmap, predict_slide, top_tiles

    stages: list[str] = []
    pred = predict_slide(slide_path, device="cpu", progress=lambda s, d, t: stages.append(s))
    assert {"reading slide", "tiling", "encoding", "predicting"} <= set(stages)
    assert len(pred.p_isup) == 6 and abs(sum(pred.p_isup) - 1) < 1e-6
    assert 0 <= pred.isup_grade <= 5
    assert pred.p_cancer >= pred.p_cspca  # monotone cumulative probabilities
    assert len(pred.attention) == len(pred.tile_coords) == pred.n_tiles
    assert abs(sum(pred.attention) - 1) < 1e-4
    assert set(pred.per_seed) == {f"s{k}" for k in range(5)}
    assert pred.model_version == model_version(engine_env)
    assert "pathologist" in pred.disclaimer
    with openslide.OpenSlide(str(slide_path)) as sl:
        hm = make_heatmap(pred, sl)
        assert hm.mode == "RGBA" and hm.size == sl.level_dimensions[-1]
        alpha = np.asarray(hm)[..., 3]
        assert 0 < (alpha > 0).mean() < 1  # transparent outside tissue
        tops = top_tiles(pred, sl, k=8)
    assert len(tops) == min(8, pred.n_tiles)
    assert tops[0].image.size == (TILE_PX, TILE_PX)
    assert [t.attention for t in tops] == sorted([t.attention for t in tops], reverse=True)


def test_predict_is_deterministic(engine_env: Path, slide_path: Path) -> None:
    from prostate_infer.predict import predict_slide

    a = predict_slide(slide_path, device="cpu")
    b = predict_slide(slide_path, device="cpu")
    assert a.p_isup == b.p_isup and a.attention == b.attention and a.isup_grade == b.isup_grade


def test_predict_flags_few_tiles(engine_env: Path, slide_path: Path) -> None:
    from prostate_infer.predict import predict_slide

    pred = predict_slide(slide_path, device="cpu")
    assert pred.n_tiles < 50
    assert any("tissue tiles" in r for r in pred.low_confidence_reasons)


def test_predict_raises_no_tissue(engine_env: Path, tmp_path: Path) -> None:
    import tifffile

    from prostate_infer.predict import predict_slide

    p = tmp_path / "blank.tiff"
    blank = np.full((2048, 2048, 3), 245, np.uint8)
    with tifffile.TiffWriter(p) as tif:
        tif.write(blank, tile=(256, 256), photometric="rgb")
        tif.write(blank[::16, ::16].copy(), subfiletype=1, tile=(128, 128), photometric="rgb")
    with pytest.raises(NoTissueError, match="No tissue"):
        predict_slide(p, device="cpu")


def test_bag_is_subsampled_to_512_deterministically() -> None:
    from prostate_infer.predict import bag_indices

    a = bag_indices(768, "x")
    assert len(a) == 512 and (np.diff(a) > 0).all()
    np.testing.assert_array_equal(a, bag_indices(768, "x"))
    np.testing.assert_array_equal(bag_indices(300, "x"), np.arange(300))
