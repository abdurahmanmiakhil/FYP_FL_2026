"""Validation of the website against the thesis (Phase 4).

Part A - model reproduction (always runs, needs only the model bundle):
    recompute the FedAvg seed ensemble on the thesis prediction files exactly like nb03
    (mean of monotone sigmoid probabilities, thresholds tuned on validation) and report
    csPCa AUC, cancer AUC and QWK on the pooled test split with 2,000-resample bootstrap 95% CIs.

Part B - full website pipeline (needs PANDA slides, --panda):
    upload N test slides through the running API (slide_id = PANDA image_id, so the tile
    sampling matches the thesis), wait for each result and compute the same metrics with the
    same code; compare with the thesis ensemble on exactly the same slides, and report per-slide
    agreement (grade, |delta P(csPCa)|).

    python scripts/validate_on_test_split.py --bundle models/bundle --out docs/validation_report.md
    python scripts/validate_on_test_split.py --bundle models/bundle --panda /data/panda --n 200 \
        --api https://localhost --email pathologist@... --password ... --out docs/validation_report.md
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score, roc_auc_score

K = 6
THESIS = {"auc_cspca": (0.9705, 0.9641, 0.9766), "auc_cancer": (0.9935, 0.9911, 0.9956), "qwk": (0.9047, 0.8897, 0.9188)}
NAMES = {"auc_cspca": "csPCa AUC (ISUP >= 2)", "auc_cancer": "Cancer AUC (ISUP >= 1)", "qwk": "Quadratic weighted kappa"}


def mono_sigmoid(logits: np.ndarray) -> np.ndarray:
    return np.minimum.accumulate(1 / (1 + np.exp(-np.asarray(logits, float))), axis=1)


def grade_from_s(s: np.ndarray, thr: list[float]) -> np.ndarray:
    return (np.asarray(s) > np.asarray(thr)).sum(1)


def metrics(y: np.ndarray, s: np.ndarray, thr: list[float]) -> dict[str, float]:
    y = np.asarray(y).astype(int)
    return {
        "auc_cspca": float(roc_auc_score(y >= 2, s[:, 1])),
        "auc_cancer": float(roc_auc_score(y >= 1, s[:, 0])),
        "qwk": float(cohen_kappa_score(y, grade_from_s(s, thr), weights="quadratic")),
    }


def bootstrap(y: np.ndarray, s: np.ndarray, thr: list[float], n: int = 2000, seed: int = 7) -> dict[str, tuple]:
    """Percentile bootstrap (nb03: 2,000 resamples, seed 7)."""
    rng = np.random.default_rng(seed)
    vals: dict[str, list[float]] = {k: [] for k in THESIS}
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(np.unique(y[i] >= 2)) < 2 or len(np.unique(y[i] >= 1)) < 2:
            continue
        for k, v in metrics(y[i], s[i], thr).items():
            vals[k].append(v)
    return {k: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))) for k, v in vals.items()}


def ensemble_table(bundle: Path) -> tuple[pd.DataFrame, list[float]]:
    d = pd.concat(pd.read_csv(bundle / f"calibration/preds__fedavg__s{s}.csv") for s in range(5))
    S = np.stack([mono_sigmoid(g.sort_values("image_id")[[f"logit{k}" for k in range(5)]].values)
                  for _, g in d.groupby("seed")]).mean(0)
    base = d[d.seed == d.seed.min()].sort_values("image_id")[["image_id", "hospital", "isup_grade", "split"]]
    base = base.reset_index(drop=True)
    base[[f"s{k}" for k in range(5)]] = S
    thr = json.loads((bundle / "calibration/ensemble_thresholds.json").read_text())["thresholds"]
    return base, thr


def fmt_row(name: str, pt: float, ci: tuple, ref: tuple) -> str:
    inside = ref[1] <= pt <= ref[2]
    return (f"| {name} | {pt:.4f} [{ci[0]:.4f}, {ci[1]:.4f}] | {ref[0]:.4f} [{ref[1]:.4f}, {ref[2]:.4f}] | "
            f"{pt - ref[0]:+.4f} | {'yes' if inside else 'NO'} |")


def part_a(base: pd.DataFrame, thr: list[float]) -> tuple[list[str], bool]:
    t = base[base.split == "test"]
    y, s = t.isup_grade.values, t[[f"s{k}" for k in range(5)]].values
    m, ci = metrics(y, s, thr), bootstrap(y, s, thr)
    ok = all(THESIS[k][1] <= m[k] <= THESIS[k][2] for k in THESIS)
    lines = [
        "## A. Model reproduction (thesis prediction files -> web ensemble code)",
        "",
        f"Pooled PANDA test split, **n = {len(t)}** slides (Radboud {int((t.hospital == 'A_Radboud').sum())}, "
        f"Karolinska {int((t.hospital == 'B_Karolinska').sum())}). Ensemble thresholds: "
        f"`{[round(x, 3) for x in thr]}`.",
        "",
        "| Metric | Web ensemble [95% CI] | Thesis (Table E03) [95% CI] | Difference | Within thesis CI |",
        "|---|---|---|---|---|",
        *[fmt_row(NAMES[k], m[k], ci[k], THESIS[k]) for k in THESIS],
        "",
        f"Result: {'**PASS** - identical to the thesis' if ok else '**FAIL**'} "
        "(same five FedAvg models, same probability averaging and thresholds as nb03).",
        "",
    ]
    return lines, ok


def part_b(base: pd.DataFrame, thr: list[float], a: argparse.Namespace) -> tuple[list[str], bool | None]:
    import requests
    import urllib3

    urllib3.disable_warnings()
    test = base[base.split == "test"]
    files = {p.stem: p for p in Path(a.panda).rglob("*.tiff")}
    chosen = test[test.image_id.isin(files)].sample(frac=1, random_state=a.seed).head(a.n)
    if chosen.empty:
        return ["## B. Full website pipeline", "", "No PANDA test slides found under --panda.", ""], None
    s = requests.Session()
    s.verify = False
    s.post(f"{a.api}/api/v1/auth/login", json={"email": a.email, "password": a.password}, timeout=60).raise_for_status()
    csrf = {"X-CSRF-Token": s.cookies.get("csrf_token", "")}
    rows = []
    for _, r in chosen.iterrows():
        with open(files[r.image_id], "rb") as f:
            up = s.post(f"{a.api}/api/v1/cases", headers=csrf, timeout=600,
                        files={"file": (f"{r.image_id}.tiff", f, "image/tiff")},
                        data={"pseudonym_code": f"VAL-{r.image_id[:12]}", "slide_id": r.image_id}).json()
        cid = up["case_id"]
        while True:
            c = s.get(f"{a.api}/api/v1/cases/{cid}", timeout=60).json()
            if c["status"] in ("done", "failed"):
                break
            time.sleep(5)
        if c["status"] != "done":
            rows.append({"image_id": r.image_id, "failed": c.get("error")})
            continue
        p = c["prediction"]
        full = s.get(f"{a.api}/api/v1/cases/{cid}/prediction/result.json", timeout=60).json()
        # web ensemble cumulative probabilities, rebuilt from the per-seed logits the API stored
        cum = list(np.stack([mono_sigmoid([v["logits"]]) for v in full["per_seed"].values()]).mean(0)[0])
        rows.append({"image_id": r.image_id, "isup_true": int(r.isup_grade), "web_isup": p["isup_grade"],
                     "thesis_isup": int(grade_from_s(np.array([[r[f"s{k}"] for k in range(5)]]), thr)[0]),
                     "web_p_cspca": p["p_cspca"], "thesis_p_cspca": float(r.s1),
                     **{f"w{k}": cum[k] for k in range(5)}})
    df = pd.DataFrame(rows)
    ok_df = df[df.get("failed").isna()] if "failed" in df else df
    y = ok_df.isup_true.values
    web = ok_df[[f"w{k}" for k in range(5)]].values
    ref = chosen.set_index("image_id").loc[ok_df.image_id][[f"s{k}" for k in range(5)]].values
    mw, mt = metrics(y, web, thr), metrics(y, ref, thr)
    cw = bootstrap(y, web, thr, n=1000)
    agree = float((ok_df.web_isup == ok_df.thesis_isup).mean())
    dp = (ok_df.web_p_cspca - ok_df.thesis_p_cspca).abs()
    ok = all(cw[k][0] <= mt[k] <= cw[k][1] for k in THESIS)
    lines = [
        "## B. Full website pipeline (upload -> tiling -> Phikon -> ensemble)",
        "",
        f"{len(ok_df)} of {len(df)} PANDA test slides processed through the running API.",
        "",
        "| Metric | Website [95% CI] | Thesis ensemble, same slides | Difference |",
        "|---|---|---|---|",
        *[f"| {NAMES[k]} | {mw[k]:.4f} [{cw[k][0]:.4f}, {cw[k][1]:.4f}] | {mt[k]:.4f} | {mw[k] - mt[k]:+.4f} |"
          for k in THESIS],
        "",
        f"- Same ISUP grade as the thesis ensemble on **{agree:.1%}** of slides.",
        f"- |delta P(csPCa)|: median {dp.median():.4f}, 95th percentile {dp.quantile(0.95):.4f}, max {dp.max():.4f}.",
        f"- Result: {'**PASS**' if ok else '**CHECK**'} (thesis values inside the website's 95% CIs).",
        "",
    ]
    return lines, ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("docs/validation_report.md"))
    ap.add_argument("--panda", type=Path)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--api", default="https://localhost")
    ap.add_argument("--email")
    ap.add_argument("--password")
    a = ap.parse_args()

    base, thr = ensemble_table(a.bundle)
    lines = [
        "# Validation report - website vs thesis",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by `scripts/validate_on_test_split.py`. "
        "Metrics use the same code as nb03 (sklearn roc_auc_score, cohen_kappa_score quadratic, "
        "percentile bootstrap with 2,000 resamples, seed 7).",
        "",
    ]
    la, _ = part_a(base, thr)
    lines += la
    if a.panda:
        lb, _ = part_b(base, thr, a)
        lines += lb
    else:
        lines += [
            "## B. Full website pipeline",
            "",
            "**Not run on this machine: no PANDA whole-slide images are available locally** (the Kaggle "
            "`prostate-cancer-grade-assessment` dataset, ~400 GB). To run it:",
            "",
            "```bash",
            "python scripts/validate_on_test_split.py --bundle models/bundle --panda /path/to/panda/train_images \\",
            "    --n 200 --api https://localhost --email <pathologist> --password <password>",
            "```",
            "",
            "Also run the tile-level parity test (`PANDA_DIR=... THESIS_BUNDLE=models/bundle pytest -m slow "
            "inference/tests/test_parity.py`), which compares the web pipeline's logits with the thesis logits "
            "slide by slide (tolerance 0.05).",
            "",
        ]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
