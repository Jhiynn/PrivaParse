"""Device resolution — the point of these tests is the *refusal* to fall back."""

from __future__ import annotations

import pytest

from privaparse.app import device as device_mod
from privaparse.app.config import Settings
from privaparse.app.device import DeviceUnavailableError, resolve_device


@pytest.fixture()
def no_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        device_mod,
        "_probe_torch",
        lambda: device_mod._TorchProbe(
            available=True, cuda_available=False, reason="no CUDA in this test"
        ),
    )


@pytest.fixture()
def with_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        device_mod,
        "_probe_torch",
        lambda: device_mod._TorchProbe(available=True, cuda_available=True, device_count=1),
    )
    monkeypatch.setattr(device_mod, "_gpu_info", lambda _index: ("Test GPU", 8188))
    monkeypatch.setattr(device_mod, "_compile_blocker", lambda: None)


@pytest.fixture()
def with_cuda_no_triton(with_cuda: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_mod, "_compile_blocker", lambda: "triton is not installed")


def test_cpu_disables_fp16_and_compile(no_cuda: None) -> None:
    resolved = resolve_device(Settings(device="cpu"))
    assert resolved.device == "cpu"
    assert resolved.quantize is False
    assert resolved.compile is False


def test_auto_picks_cpu_when_cuda_missing(no_cuda: None) -> None:
    resolved = resolve_device(Settings(device="auto"))
    assert resolved.device == "cpu"


def test_auto_picks_cuda_when_available(with_cuda: None) -> None:
    resolved = resolve_device(Settings(device="auto"))
    assert resolved.device == "cuda:0"
    assert resolved.quantize is True
    assert resolved.compile is True
    assert resolved.gpu_name == "Test GPU"
    assert resolved.vram_total_mb == 8188


def test_explicit_cuda_without_cuda_raises_instead_of_degrading(no_cuda: None) -> None:
    """A silent CPU fallback is invisible in logs and looks like a mystery
    slowdown weeks later. Fail loudly instead."""
    with pytest.raises(DeviceUnavailableError) as excinfo:
        resolve_device(Settings(device="cuda"))
    assert "Refusing to fall back to CPU" in str(excinfo.value)


def test_out_of_range_cuda_index_raises(with_cuda: None) -> None:
    with pytest.raises(DeviceUnavailableError):
        resolve_device(Settings(device="cuda:3"))


def test_explicit_overrides_beat_the_device_default(with_cuda: None) -> None:
    resolved = resolve_device(Settings(device="cuda", quantize=False, compile=False))
    assert resolved.device == "cuda:0"
    assert resolved.quantize is False
    assert resolved.compile is False


def test_invalid_device_string_is_rejected_at_config_time() -> None:
    with pytest.raises(ValueError):
        Settings(device="gpu0")


def test_describe_is_human_readable(with_cuda: None) -> None:
    text = resolve_device(Settings(device="auto")).describe()
    assert "device=cuda:0" in text
    assert "dtype=fp16" in text


def test_missing_triton_downgrades_compile_instead_of_aborting(
    with_cuda_no_triton: None,
) -> None:
    """torch.compile needs Triton, which PyTorch does not ship on Windows.

    Unlike the device, this is a pure speed knob: dropping it changes how fast
    the answer arrives, never what the answer is. Aborting the run over it would
    make CUDA unusable on Windows for no safety gain.
    """
    resolved = resolve_device(Settings(device="cuda"))

    assert resolved.device == "cuda:0"
    assert resolved.compile is False
    assert resolved.compile_disabled_reason is not None


def test_a_downgraded_compile_is_reported_not_hidden(with_cuda_no_triton: None) -> None:
    text = resolve_device(Settings(device="cuda")).describe()
    assert "compile=off" in text
    assert "triton" in text


def test_compile_is_not_probed_on_cpu(monkeypatch: pytest.MonkeyPatch, no_cuda: None) -> None:
    """There is nothing to downgrade — compile is already off on CPU."""
    called = {"n": 0}

    def counting() -> str | None:
        called["n"] += 1
        return "should not be consulted"

    monkeypatch.setattr(device_mod, "_compile_blocker", counting)
    resolved = resolve_device(Settings(device="cpu"))

    assert resolved.compile is False
    assert resolved.compile_disabled_reason is None
    assert called["n"] == 0
