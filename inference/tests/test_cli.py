from __future__ import annotations

import json
from pathlib import Path

import pytest

from prostate_infer.__main__ import main


def test_cli_fetch_predict_and_fit(
    engine_env: Path, slide_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["fetch"]) == 0
    assert "model_version" in capsys.readouterr().out

    out, hm = tmp_path / "r.json", tmp_path / "h.png"
    assert main(["predict", str(slide_path), "--out", str(out), "--heatmap", str(hm), "--device", "cpu"]) == 0
    res = json.loads(out.read_text())
    assert len(res["p_isup"]) == 6 and hm.stat().st_size > 0
    assert "ISUP" in capsys.readouterr().out

    assert main(["fit-thresholds", "--bundle", str(engine_env)]) == 0
    assert len(json.loads(capsys.readouterr().out)["thresholds"]) == 5
