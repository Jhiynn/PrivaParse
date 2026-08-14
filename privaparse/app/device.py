"""Device resolution.

The one rule this module exists to enforce: **an explicit device request never
silently degrades**. If the caller asked for ``cuda`` and CUDA is not usable, we
raise. A pipeline that quietly falls back to CPU is the classic reason behind
"why is this suddenly twenty times slower", and it is invisible in every log.

``compile`` is treated differently, and deliberately so. ``torch.compile`` needs
Triton, which is not part of the PyTorch wheel on Windows — so on this platform
an unavailable compiler is the normal case, not a misconfiguration. More to the
point, it is a pure speed knob: dropping it changes how fast the answer arrives,
never what the answer is. So a missing compiler downgrades to eager mode with a
recorded reason rather than aborting the run. The reason is surfaced by
``privaparse doctor`` and in the log line, so it is visible rather than silent.

Device is a contract. Compile is a hint.
"""

from __future__ import annotations

from dataclasses import dataclass

from privaparse.app.config import Settings
from privaparse.app.logging import get_logger

log = get_logger("device")


class DeviceUnavailableError(RuntimeError):
    """Raised when an explicitly requested device cannot be used."""


@dataclass(frozen=True)
class ResolvedDevice:
    """The concrete execution setup, after ``auto`` has been decided."""

    device: str  # never "auto" — always concrete, e.g. "cpu" or "cuda:0"
    quantize: bool
    compile: bool
    torch_available: bool
    cuda_available: bool
    gpu_name: str | None = None
    vram_total_mb: int | None = None
    #: Set when compilation was asked for but is not usable here.
    compile_disabled_reason: str | None = None

    @property
    def is_cuda(self) -> bool:
        return self.device.startswith("cuda")

    def describe(self) -> str:
        parts = [f"device={self.device}"]
        if self.gpu_name:
            parts.append(f"gpu={self.gpu_name}")
        if self.vram_total_mb:
            parts.append(f"vram={self.vram_total_mb}MiB")
        parts.append(f"dtype={'fp16' if self.quantize else 'fp32'}")
        parts.append(f"compile={'on' if self.compile else 'off'}")
        if self.compile_disabled_reason:
            parts.append(f"({self.compile_disabled_reason})")
        return " ".join(parts)


@dataclass(frozen=True)
class _TorchProbe:
    available: bool
    cuda_available: bool
    device_count: int = 0
    reason: str = ""


def _probe_torch() -> _TorchProbe:
    try:
        import torch
    except ImportError:
        return _TorchProbe(
            available=False,
            cuda_available=False,
            reason=(
                "torch is not installed (pipx: pipx inject privaparse "
                "\"gliner2[local]\"; checkout: pip install -e '.[model]')"
            ),
        )

    if not torch.cuda.is_available():
        return _TorchProbe(
            available=True,
            cuda_available=False,
            reason="torch is installed but reports no usable CUDA device",
        )
    return _TorchProbe(
        available=True,
        cuda_available=True,
        device_count=torch.cuda.device_count(),
    )


def _gpu_info(index: int) -> tuple[str | None, int | None]:
    try:
        import torch

        props = torch.cuda.get_device_properties(index)
        return props.name, int(props.total_memory // (1024 * 1024))
    except Exception:  # noqa: BLE001 -- pragma: no cover, CUDA driver can raise anything
        return None, None


def resolve_device(settings: Settings) -> ResolvedDevice:
    """Turn ``settings.device`` into a concrete device, or raise.

    ``auto``  -> CUDA if usable, otherwise CPU (never raises)
    ``cuda*`` -> CUDA, or :class:`DeviceUnavailableError`
    ``cpu``   -> CPU
    """
    requested = settings.device
    probe = _probe_torch()

    if requested == "cpu":
        return _finalize(settings, "cpu", probe)

    if requested == "auto":
        if probe.cuda_available:
            return _finalize(settings, "cuda:0", probe)
        return _finalize(settings, "cpu", probe)

    if requested in {"cuda", "mps"} or requested.startswith("cuda:"):
        if requested == "mps":
            # Not our target platform; accept it but do not pretend to verify.
            return _finalize(settings, "mps", probe)
        if not probe.cuda_available:
            raise DeviceUnavailableError(
                f"device={requested!r} was requested explicitly, but {probe.reason}. "
                f"Refusing to fall back to CPU silently — set PRIVAPARSE_DEVICE=auto "
                f"if a CPU fallback is acceptable."
            )
        index = int(requested.split(":", 1)[1]) if ":" in requested else 0
        if index >= probe.device_count:
            raise DeviceUnavailableError(
                f"device={requested!r} was requested, but only {probe.device_count} "
                f"CUDA device(s) are visible."
            )
        return _finalize(settings, f"cuda:{index}", probe)

    raise DeviceUnavailableError(f"Unsupported device {requested!r}")


def _finalize(settings: Settings, device: str, probe: _TorchProbe) -> ResolvedDevice:
    on_cuda = device.startswith("cuda")
    gpu_name, vram = _gpu_info(int(device.split(":", 1)[1])) if on_cuda else (None, None)

    # None means "decide from the device": fp16 and torch.compile pay off on GPU
    # and are either useless or actively slower on CPU.
    quantize = settings.quantize if settings.quantize is not None else on_cuda
    compile_ = settings.compile if settings.compile is not None else on_cuda

    reason: str | None = None
    if compile_ and on_cuda:
        reason = _compile_blocker()
        if reason is not None:
            compile_ = False
            log.warning("running without torch.compile: %s", reason)

    return ResolvedDevice(
        device=device,
        quantize=quantize,
        compile=compile_,
        torch_available=probe.available,
        cuda_available=probe.cuda_available,
        gpu_name=gpu_name,
        vram_total_mb=vram,
        compile_disabled_reason=reason,
    )


def _compile_blocker() -> str | None:
    """Why ``torch.compile`` cannot be used here, or ``None`` if it can.

    The inductor backend needs Triton, which the PyTorch wheel does not ship on
    Windows. Finding that out at the first forward pass means the run dies after
    the model is already loaded, so check up front.
    """
    try:
        import triton  # noqa: F401
    except ImportError:
        return "triton is not installed (not shipped with PyTorch on Windows)"
    return None
