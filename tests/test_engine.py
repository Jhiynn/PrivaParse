"""Engine wiring: the model cache location and the shared-engine lifecycle."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from privaparse.app.config import Settings
from privaparse.engine import PrivaParseEngine, _point_hf_cache_at


@pytest.fixture(autouse=True)
def clean_hf_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    yield


def test_model_dir_becomes_the_hugging_face_cache(tmp_path: Path) -> None:
    """It was a setting that read nicely and did nothing — weights landed in
    ~/.cache/huggingface regardless, costing a second 1.2 GB download."""
    target = tmp_path / "weights"
    _point_hf_cache_at(target)

    assert os.environ["HF_HOME"] == str(target.resolve())
    assert target.exists()


def test_an_explicit_hf_home_is_respected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone who set HF_HOME meant it."""
    monkeypatch.setenv("HF_HOME", "/somewhere/deliberate")
    _point_hf_cache_at(tmp_path / "weights")

    assert os.environ["HF_HOME"] == "/somewhere/deliberate"


def test_engine_sets_the_cache_before_the_model_could_load(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "vault.db",
        model_dir=tmp_path / "weights",
        device="cpu",
        detector="regex",
    )
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        assert os.environ["HF_HOME"] == str((tmp_path / "weights").resolve())
    finally:
        engine.close()


def test_offline_mode_cuts_out_the_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that promises the document never leaves the machine should be
    able to prove it does not phone home either."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    settings = Settings(
        db_path=tmp_path / "vault.db", device="cpu", detector="regex", offline=True
    )
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    finally:
        engine.close()


def test_offline_is_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first run has to be able to fetch the weights."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    settings = Settings(db_path=tmp_path / "vault.db", device="cpu", detector="regex")
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        assert "HF_HUB_OFFLINE" not in os.environ
    finally:
        engine.close()


def test_engine_is_reusable_across_calls(tmp_path: Path) -> None:
    """A service builds one engine at startup; the vault must survive calls."""
    settings = Settings(db_path=tmp_path / "vault.db", device="cpu", detector="regex")
    engine = PrivaParseEngine(settings, configure_logs=False)
    try:
        first = engine.pseudonymize("Mail an max@test.de", source_name="a.md")
        second = engine.pseudonymize("Wieder max@test.de", source_name="b.md")

        assert first.spans[0].placeholder == second.spans[0].placeholder
        assert engine.vault_stats().mappings == 2
    finally:
        engine.close()
