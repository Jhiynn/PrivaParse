"""The benchmark harness must never report speed without quality beside it."""

from __future__ import annotations

from privaparse.evaluation.bench import BenchResult, format_bench_report, run_bench
from privaparse.evaluation.harness import GoldDocument, GoldEntity
from privaparse.parser.detector import StaticDetector
from privaparse.parser.types import SOURCE_GLINER, EntityType, Span


def _documents() -> list[GoldDocument]:
    text = "Max Mustermann kam."
    return [
        GoldDocument(
            id=f"b-{i}",
            kind="test",
            text=text,
            entities=(GoldEntity(0, 14, "PERSON", "Max Mustermann"),),
        )
        for i in range(4)
    ]


def _result(label: str, *, recall: float, precision: float, p50: float) -> BenchResult:
    return BenchResult(
        label=label,
        device="cuda:0",
        quantize=True,
        compile=True,
        batch_size=8,
        p50_ms=p50,
        p95_ms=p50 * 1.4,
        ms_per_kb=p50 / 2,
        docs_per_second=1000 / p50,
        person_precision=precision,
        person_recall=recall,
    )


def test_bench_measures_latency_and_quality_in_one_pass(settings) -> None:
    from privaparse.engine import PrivaParseEngine

    text = "Max Mustermann kam."
    detector = StaticDetector(
        [Span(0, 14, text[0:14], EntityType.PERSON, 0.99, SOURCE_GLINER)]
    )
    engine = PrivaParseEngine(settings, detector=detector, configure_logs=False)
    try:
        result = run_bench(engine, _documents(), label="stub", repeats=2, warmup_docs=1)
    finally:
        engine.close()

    assert result.p50_ms >= 0
    assert result.docs_per_second > 0
    assert result.person_recall == 1.0
    assert result.meets_quality_floor is True
    assert result.status == "ok"


def test_a_fast_but_inaccurate_configuration_is_marked_failed() -> None:
    """Speed bought with recall is a disclosure with a better benchmark."""
    fast_and_wrong = _result("fast", recall=0.40, precision=0.99, p50=5.0)
    assert fast_and_wrong.meets_quality_floor is False
    assert fast_and_wrong.status == "FAILED QUALITY"


def test_recommendation_picks_the_fastest_that_still_meets_the_floor() -> None:
    results = [
        _result("fast-but-wrong", recall=0.50, precision=0.99, p50=5.0),
        _result("slow-and-right", recall=0.96, precision=0.95, p50=90.0),
        _result("quick-and-right", recall=0.94, precision=0.92, p50=40.0),
    ]
    report = format_bench_report(results)

    assert "**quick-and-right**" in report
    assert "Rejected despite their speed" in report
    assert "fast-but-wrong" in report


def test_report_warns_that_ms_per_kb_is_corpus_dependent() -> None:
    """The gold documents are short letters, so fixed per-document cost inflates
    ms/KB. Without the caveat the number invites a wrong comparison."""
    report = format_bench_report(
        [_result("a", recall=0.95, precision=0.95, p50=100.0)], _documents()
    )
    assert "median" in report
    assert "ms/KB reads much worse" in report


def test_report_works_without_corpus_statistics() -> None:
    report = format_bench_report([_result("a", recall=0.95, precision=0.95, p50=100.0)])
    assert "# Benchmark report" in report
    assert "Corpus:" not in report


def test_report_flags_rows_where_compile_was_requested_but_not_applied() -> None:
    """Otherwise a `c1` label next to a `c0` column reads as a contradiction,
    and the duplicate rows look like a measurement that was never taken."""
    requested = _result("cuda/fp16/c1/b8", recall=0.95, precision=0.95, p50=40.0)
    requested.compile = False
    requested.compile_disabled_reason = "triton is not installed"

    report = format_bench_report([requested])
    assert "torch.compile was requested but not applied" in report
    assert "triton is not installed" in report
    assert "eager mode" in report


def test_report_stays_quiet_when_compile_worked() -> None:
    report = format_bench_report([_result("a", recall=0.95, precision=0.95, p50=40.0)])
    assert "was requested but not applied" not in report


def test_report_shouts_when_the_gpu_was_throttled() -> None:
    """A capped GPU still reports 100% utilisation, so the timings look
    plausible and the conclusions drawn from them are wrong. This is the only
    signal that catches it."""
    result = _result("cuda/fp16/b8", recall=0.95, precision=0.95, p50=87.0)
    result.throttle_warning = "GPU ran at 210 MHz of 3105 MHz (pstate P8)"

    report = format_bench_report([result])
    assert "throttled during this run" in report
    assert "210 MHz of 3105 MHz" in report
    assert "invalid as a performance measurement" in report


def test_report_stays_quiet_when_the_gpu_ran_at_speed() -> None:
    report = format_bench_report([_result("a", recall=0.95, precision=0.95, p50=87.0)])
    assert "throttled" not in report


def test_report_says_so_when_nothing_meets_the_floor() -> None:
    results = [_result("a", recall=0.10, precision=0.99, p50=5.0)]
    report = format_bench_report(results)
    assert "No configuration met the quality floor" in report
    assert "Speed is not the problem to solve yet" in report


def test_a_failed_configuration_does_not_stop_the_table() -> None:
    results = [
        BenchResult(
            label="cuda/fp16",
            device="cuda",
            quantize=True,
            compile=True,
            batch_size=8,
            error="no CUDA device visible",
        ),
        _result("cpu", recall=0.95, precision=0.95, p50=300.0),
    ]
    report = format_bench_report(results)

    assert "ERROR: no CUDA device visible" in report
    assert "**cpu**" in report
