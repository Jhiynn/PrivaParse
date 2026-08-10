"""Throughput benchmark — and the reason it never reports speed on its own.

fp16, INT8 and aggressive batching can all cost recall. A pipeline that gets
faster because it stops noticing names is not optimised, it is broken: the
missed name goes to the LLM. That is a disclosure with a better benchmark
attached.

So every configuration measured here is scored for quality in the same pass, and
anything that drops below the PERSON recall floor is marked FAILED regardless of
how fast it ran.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Sequence

from privaparse.app.logging import get_logger
from privaparse.engine import PrivaParseEngine
from privaparse.evaluation.harness import (
    PERSON_PRECISION_FLOOR,
    PERSON_RECALL_FLOOR,
    GoldDocument,
    evaluate,
)

log = get_logger("bench")

__all__ = ["BenchResult", "run_bench", "format_bench_report", "DEFAULT_MATRIX"]

#: (device, quantize, compile, batch_size). ``None`` means "leave as configured".
DEFAULT_MATRIX: tuple[tuple[str, bool | None, bool | None, int], ...] = (
    ("cuda", True, True, 8),
    ("cuda", True, False, 8),
    ("cuda", False, False, 8),
    ("cuda", True, True, 1),
    ("cuda", True, True, 16),
    ("cpu", False, False, 8),
)


@dataclass
class BenchResult:
    label: str
    device: str
    quantize: bool
    compile: bool
    batch_size: int

    p50_ms: float = 0.0
    p95_ms: float = 0.0
    ms_per_kb: float = 0.0
    docs_per_second: float = 0.0
    vram_peak_mb: int | None = None

    person_precision: float = 0.0
    person_recall: float = 0.0
    email_recall: float = 0.0
    phone_recall: float = 0.0

    error: str | None = field(default=None)
    #: Set when this run asked for torch.compile and did not get it.
    compile_disabled_reason: str | None = field(default=None)
    #: Set when the GPU was clocked far below its maximum during the run.
    throttle_warning: str | None = field(default=None)

    @property
    def meets_quality_floor(self) -> bool:
        return (
            self.person_recall >= PERSON_RECALL_FLOOR
            and self.person_precision >= PERSON_PRECISION_FLOOR
        )

    @property
    def status(self) -> str:
        if self.error:
            return "ERROR"
        return "ok" if self.meets_quality_floor else "FAILED QUALITY"


def run_bench(
    engine: PrivaParseEngine,
    documents: Sequence[GoldDocument],
    *,
    label: str,
    repeats: int = 3,
    warmup_docs: int = 3,
) -> BenchResult:
    """Time ``engine.detect`` over the gold set and score its output."""
    result = BenchResult(
        label=label,
        device=engine.device.device,
        quantize=engine.device.quantize,
        compile=engine.device.compile,
        batch_size=engine.settings.batch_size,
        compile_disabled_reason=engine.device.compile_disabled_reason,
    )

    # Warm up outside the measurement: torch.compile and CUDA kernel init are
    # one-off costs a long-running service pays once, not per document.
    for document in documents[:warmup_docs]:
        engine.detect(document.text)

    _reset_vram_peak(engine)

    timings: list[float] = []
    total_bytes = 0
    started = time.perf_counter()

    for _ in range(repeats):
        for document in documents:
            document_started = time.perf_counter()
            engine.detect(document.text)
            timings.append((time.perf_counter() - document_started) * 1000)
            total_bytes += len(document.text.encode("utf-8"))

    wall = time.perf_counter() - started

    result.p50_ms = statistics.median(timings)
    result.p95_ms = _percentile(timings, 0.95)
    result.ms_per_kb = (sum(timings) / (total_bytes / 1024)) if total_bytes else 0.0
    result.docs_per_second = len(timings) / wall if wall else 0.0
    result.vram_peak_mb = _vram_peak(engine)
    # Sampled at the end of the timed section, while the GPU is still hot.
    result.throttle_warning = _throttle_check(engine)

    quality = evaluate(engine, documents, label=label)
    result.person_precision = quality.partial["PERSON"].precision
    result.person_recall = quality.partial["PERSON"].recall
    result.email_recall = quality.partial["EMAIL"].recall
    result.phone_recall = quality.partial["PHONE"].recall

    return result


#: Below this fraction of the maximum clock, timings say more about the power
#: policy than about the code.
_THROTTLE_RATIO = 0.5


def _throttle_check(engine: PrivaParseEngine) -> str | None:
    """Is the GPU actually running at speed?

    Worth its weight: a laptop GPU pinned in P8 at 210 MHz out of 3105 still
    reports 100% utilisation, so every timing looks plausible and every
    conclusion drawn from it is wrong. Ask the clock, not the utilisation.
    """
    if not engine.device.is_cuda:
        return None

    current_mhz, max_mhz, pstate = _nvidia_smi_clocks()
    if current_mhz is None or not max_mhz:
        return None

    if current_mhz < max_mhz * _THROTTLE_RATIO:
        return (
            f"GPU ran at {current_mhz} MHz of {max_mhz} MHz"
            + (f" (pstate {pstate})" if pstate else "")
            + " — these timings measure the power policy, not the code"
        )
    return None


def _nvidia_smi_clocks() -> tuple[int | None, int | None, str | None]:
    import shutil
    import subprocess

    if shutil.which("nvidia-smi") is None:
        return None, None, None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=clocks.current.graphics,clocks.max.graphics,pstate",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip().splitlines()[0]
        current, maximum, pstate = (part.strip() for part in out.split(","))
        return int(current), int(maximum), pstate
    except Exception:  # pragma: no cover - diagnostics must never break a run
        return None, None, None


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _reset_vram_peak(engine: PrivaParseEngine) -> None:
    if not engine.device.is_cuda:
        return
    try:
        import torch

        torch.cuda.reset_peak_memory_stats()
    except Exception:  # pragma: no cover - diagnostics only
        pass


def _vram_peak(engine: PrivaParseEngine) -> int | None:
    if not engine.device.is_cuda:
        return None
    try:
        import torch

        return int(torch.cuda.max_memory_allocated() // (1024 * 1024))
    except Exception:  # pragma: no cover - diagnostics only
        return None


def format_bench_report(
    results: Sequence[BenchResult],
    documents: Sequence[GoldDocument] | None = None,
) -> str:
    lines = ["# Benchmark report", ""]
    lines.append(
        "Latency and quality are reported together on purpose. A configuration "
        "that is faster because it detects fewer names is not an optimisation."
    )
    lines.append("")
    lines.append(
        f"Quality floor (fixed in advance): PERSON partial recall >= "
        f"{PERSON_RECALL_FLOOR}, precision >= {PERSON_PRECISION_FLOOR}."
    )
    lines.append("")

    if documents:
        sizes = sorted(len(d.text.encode("utf-8")) for d in documents)
        median = sizes[len(sizes) // 2]
        lines.append(
            f"Corpus: {len(documents)} documents, median {median} bytes "
            f"(range {sizes[0]}–{sizes[-1]}). These are short letters and file "
            f"notes, so per-document overhead dominates and **ms/KB reads much "
            f"worse than it would on real multi-page documents** — compare p50 "
            f"across configurations, not ms/KB across corpora."
        )
        lines.append("")

    lines.append(
        "| Config | Device | dtype | compile | batch | p50 ms | p95 ms | ms/KB | docs/s "
        "| VRAM MB | PERSON P | PERSON R | Status |"
    )
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")

    for result in results:
        if result.error:
            lines.append(
                f"| {result.label} | {result.device} | – | – | {result.batch_size} "
                f"| – | – | – | – | – | – | – | ERROR: {result.error} |"
            )
            continue
        lines.append(
            f"| {result.label} | {result.device} "
            f"| {'fp16' if result.quantize else 'fp32'} "
            f"| {'on' if result.compile else 'off'} | {result.batch_size} "
            f"| {result.p50_ms:.1f} | {result.p95_ms:.1f} | {result.ms_per_kb:.1f} "
            f"| {result.docs_per_second:.1f} "
            f"| {result.vram_peak_mb if result.vram_peak_mb is not None else '–'} "
            f"| {result.person_precision:.3f} | {result.person_recall:.3f} "
            f"| {result.status} |"
        )

    throttled = {r.throttle_warning for r in results if r.throttle_warning}
    if throttled:
        lines.append("")
        lines.append(
            "> ⚠️ **The GPU was throttled during this run** — "
            + "; ".join(sorted(throttled))
            + ". Every GPU row below is invalid as a performance measurement. A "
            "capped GPU still reports 100% utilisation, so nothing else in the "
            "table gives this away. Fix the power policy and re-run before "
            "drawing any conclusion from these numbers."
        )

    downgraded = {r.compile_disabled_reason for r in results if r.compile_disabled_reason}
    if downgraded:
        lines.append("")
        lines.append(
            "> **torch.compile was requested but not applied** — "
            + "; ".join(sorted(downgraded))
            + ". Rows whose label says `c1` therefore ran in eager mode, and are "
            "duplicates of the matching `c0` row rather than a separate "
            "measurement. Compilation is a speed knob only; it does not change "
            "what gets detected."
        )

    lines.append("")
    lines.append("## Recommendation")
    lines.append("")

    usable = [r for r in results if not r.error and r.meets_quality_floor]
    if not usable:
        lines.append(
            "No configuration met the quality floor. Speed is not the problem to "
            "solve yet — see the evaluation report."
        )
    else:
        best = min(usable, key=lambda r: r.p50_ms)
        lines.append(
            f"Fastest configuration that still meets the quality floor: "
            f"**{best.label}** — p50 {best.p50_ms:.1f} ms, "
            f"{best.docs_per_second:.1f} docs/s, PERSON recall {best.person_recall:.3f}."
        )
        rejected = [r for r in results if not r.error and not r.meets_quality_floor]
        if rejected:
            lines.append("")
            lines.append("Rejected despite their speed:")
            for result in rejected:
                lines.append(
                    f"- {result.label} — p50 {result.p50_ms:.1f} ms but PERSON recall "
                    f"{result.person_recall:.3f}"
                )
    lines.append("")
    return "\n".join(lines)
