"""Shared fixtures: a synthetic pyramidal slide, a fake model bundle and a fake Phikon.

The fake bundle has randomly initialised heads, so tests never need the private models.
Tests that need the real thesis bundle read THESIS_BUNDLE and are skipped without it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prostate_infer.synthetic import write_synthetic_slide
from prostate_infer.testing import use_fake_models, write_fake_bundle


@pytest.fixture(scope="session")
def slide_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_synthetic_slide(tmp_path_factory.mktemp("slides") / "synthetic.tiff", 4480, 2240, seed=1)


@pytest.fixture(scope="session")
def fake_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_fake_bundle(tmp_path_factory.mktemp("bundle"))


@pytest.fixture
def engine_env(fake_bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point prostate_infer at the fake bundle and replace Phikon with FakeViT."""
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path / "cache"))
    use_fake_models(monkeypatch, fake_bundle)
    return fake_bundle


@pytest.fixture
def thesis_bundle() -> Path:
    p = os.environ.get("THESIS_BUNDLE")
    if not p or not Path(p, "manifest.json").exists():
        pytest.skip("THESIS_BUNDLE (the real model bundle) not available")
    return Path(p)
