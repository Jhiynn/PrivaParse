"""PrivaParse command line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from privaparse.app.config import load_settings
from privaparse.app.device import DeviceUnavailableError, resolve_device
from privaparse.app.mock_llm import mock_llm_response
from privaparse.engine import PrivaParseEngine

app = typer.Typer(
    add_completion=False,
    help="Local privacy layer: pseudonymise text before an LLM sees it, restore it after.",
    no_args_is_help=True,
)
vault_app = typer.Typer(help="Inspect the local vault.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")
catalog_app = typer.Typer(help="Inspect and check the entity catalogue.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")

_OVERRIDES = "privaparse_overrides"


@app.callback()
def _main(
    ctx: typer.Context,
    device: Optional[str] = typer.Option(
        None, "--device", help="auto | cpu | cuda | cuda:N. Overrides PRIVAPARSE_DEVICE."
    ),
    detector: Optional[str] = typer.Option(
        None, "--detector", help="hybrid | gliner | regex."
    ),
    db: Optional[Path] = typer.Option(None, "--db", help="Path to the vault database."),
    threshold: Optional[float] = typer.Option(
        None, "--threshold", min=0.0, max=1.0,
        help="Score floor for a type with no threshold: of its own in the "
        "catalogue. Most types declare one and are unaffected by this.",
    ),
    batch_size: Optional[int] = typer.Option(None, "--batch-size", min=1),
    scan_code: Optional[bool] = typer.Option(
        None, "--scan-code/--protect-code", help="Also scan code blocks and URLs."
    ),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    _force_utf8_output()
    ctx.ensure_object(dict)
    ctx.obj[_OVERRIDES] = {
        "device": device,
        "detector": detector,
        "db_path": db,
        "threshold": threshold,
        "batch_size": batch_size,
        "scan_code": scan_code,
        "log_level": log_level.upper() if log_level else None,
    }


# --- commands --------------------------------------------------------------


@app.command()
def pseudonymize(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    out: Optional[Path] = typer.Option(None, "-o", "--out", help="Defaults to <file>.pseudo.md"),
    mapping_out: Optional[Path] = typer.Option(
        None, "--mapping-out", help="Write the mapping id to this file as well."
    ),
) -> None:
    """Replace PII in FILE with placeholders."""
    engine = _engine(ctx)
    text = _read(file)
    result = _run(lambda: engine.pseudonymize(text, source_name=str(file)))

    target = out or file.with_suffix(f".pseudo{file.suffix or '.md'}")
    target.write_text(result.text, encoding="utf-8")

    if mapping_out:
        mapping_out.write_text(result.mapping_id, encoding="utf-8")

    typer.echo(f"wrote      {target}")
    typer.echo(f"mapping id {result.mapping_id}")
    typer.echo(f"replaced   {result.replacements} span(s) -> {len(result.placeholders)} placeholder(s)")
    if mapping_out:
        typer.echo(f"mapping id also written to {mapping_out}")


@app.command()
def reverse(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    mapping: Optional[str] = typer.Option(
        None,
        "--mapping",
        "-m",
        help="Mapping id from pseudonymize. Omit to find the session that issued "
        "every placeholder in the file.",
    ),
    out: Optional[Path] = typer.Option(None, "-o", "--out"),
    strict: bool = typer.Option(
        False, "--strict", help="Fail if the text carries placeholders from another session."
    ),
) -> None:
    """Restore original values in FILE.

    With no --mapping, the session that issued every placeholder in the file is
    looked up. Partial coverage matches nothing, so this cannot be used to
    unmask placeholders from a document you did not pseudonymise yourself.
    """
    engine = _engine(ctx)
    text = _read(file)
    result = _run(lambda: engine.reverse(mapping, text, strict=strict))

    target = out or file.with_suffix(f".restored{file.suffix or '.md'}")
    target.write_text(result.text, encoding="utf-8")

    typer.echo(f"wrote     {target}")
    typer.echo(f"restored  {result.restored} placeholder(s)")
    if result.foreign:
        typer.secho(
            f"left in place (issued to another document): {', '.join(result.foreign)}",
            fg=typer.colors.YELLOW,
        )
    if result.unknown:
        typer.secho(
            f"left in place (never issued by this vault): {', '.join(result.unknown)}",
            fg=typer.colors.YELLOW,
        )


@app.command()
def detect(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show what would be detected, without touching the vault."""
    engine = _engine(ctx)
    spans = _run(lambda: engine.detect(_read(file)))

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "start": s.start,
                        "end": s.end,
                        "type": str(s.type),
                        "text": s.text,
                        "score": round(s.score, 4),
                        "source": s.source,
                    }
                    for s in spans
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not spans:
        typer.echo("no entities detected")
        return

    typer.echo(f"{'TYPE':<8} {'RANGE':<14} {'SOURCE':<7} {'SCORE':>6}  TEXT")
    for span in spans:
        span_range = f"{span.start}-{span.end}"
        typer.echo(
            f"{str(span.type):<8} {span_range:<14} {span.source:<7} "
            f"{span.score:>6.2f}  {span.text}"
        )
    typer.echo(f"\n{len(spans)} span(s)")


@app.command()
def demo(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Run the whole round trip on FILE and print every stage."""
    engine = _engine(ctx)
    original = _read(file)

    result = _run(lambda: engine.pseudonymize(original, source_name=str(file)))
    answer = mock_llm_response(result.text)
    restored = _run(lambda: engine.reverse(result.mapping_id, answer))

    _section("1. ORIGINAL", original)
    _section(
        "2. DETECTED",
        "\n".join(
            f"  {str(r.span.type):<8} {r.span.text!r:<32} -> {r.placeholder}"
            for r in result.spans
        )
        or "  (nothing detected)",
    )
    _section("3. PSEUDONYMISED (this is what the LLM sees)", result.text)
    _section("4. MOCK LLM RESPONSE", answer)
    _section("5. RESTORED", restored.text)

    typer.echo(f"mapping id : {result.mapping_id}")
    typer.echo(f"replaced   : {result.replacements} span(s)")
    typer.echo(f"restored   : {restored.restored} placeholder(s)")
    if not restored.is_clean:
        typer.secho("warnings   : see above", fg=typer.colors.YELLOW)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Show the resolved runtime setup: device, dtype, model, vault."""
    settings = load_settings(**ctx.obj[_OVERRIDES])

    typer.echo(f"model      {settings.model_id}")
    typer.echo(f"detector   {settings.detector}")
    typer.echo(f"vault      {settings.db_path.resolve()}")
    typer.echo(f"threshold  {settings.threshold}  (fallback; most types pin their own)")
    typer.echo(f"batch size {settings.batch_size}")
    typer.echo(f"scan code  {settings.scan_code}")

    catalogue = settings.catalogue
    source = settings.catalogue_path or "built-in + discovered"
    typer.echo(f"catalogue  {source}")
    typer.echo(
        f"           {len(catalogue.enabled)} type(s), {len(catalogue.schema())} label(s)"
    )

    try:
        resolved = resolve_device(settings)
    except DeviceUnavailableError as exc:
        typer.secho(f"device     UNAVAILABLE — {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"device     {resolved.describe()}")
    if not resolved.torch_available:
        typer.secho(
            "torch      not installed — install with: pip install -e '.[model]'",
            fg=typer.colors.YELLOW,
        )
    elif not resolved.cuda_available:
        typer.secho("cuda       not available on this machine", fg=typer.colors.YELLOW)


@app.command("eval")
def evaluate(
    ctx: typer.Context,
    model: list[str] = typer.Option(
        [], "--model", help="Model id. Repeat to compare several."
    ),
    mode: list[str] = typer.Option(
        [],
        "--mode",
        help="hybrid | gliner | regex. Repeat to compare several in one run; "
        "defaults to the global --detector.",
    ),
    gold: Optional[Path] = typer.Option(None, "--gold", help="Gold JSONL."),
    report: Optional[Path] = typer.Option(
        None, "--report", help="Write a markdown report here (default docs/eval-report.md)."
    ),
    show: int = typer.Option(15, "--show", help="Mistakes to list per category."),
    sweep_threshold: bool = typer.Option(
        False, "--sweep-threshold",
        help="Score the gold set at 0.3–0.9 from one model pass and write the curve.",
    ),
) -> None:
    """Score detection against the German gold set and decide the fine-tuning question."""
    from privaparse.evaluation import DEFAULT_REPORT_DIR
    from privaparse.evaluation.harness import evaluate as run_eval
    from privaparse.evaluation.harness import format_report, load_gold

    base = load_settings(**ctx.obj[_OVERRIDES])
    documents = _run(lambda: load_gold(gold or _default_gold()))

    if sweep_threshold:
        from privaparse.evaluation.harness import format_sweep, sweep_thresholds

        engine = _engine_with(base)
        try:
            results = sweep_thresholds(engine, documents, catalogue=base.catalogue)
        finally:
            engine.close()

        text = format_sweep(results)
        target = report or (DEFAULT_REPORT_DIR / "sweep-report.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        typer.echo(f"sweep written to {target}")
        return

    models = model or [base.model_id]
    modes = mode or [base.detector]

    reports = []
    for model_id in models:
        for detector_mode in modes:
            settings = base.model_copy(
                update={"model_id": model_id, "detector": detector_mode}
            )
            label = (
                "regex"
                if detector_mode == "regex"
                else f"{detector_mode}/{_short(model_id)}"
            )
            typer.echo(f"running {label} …", err=True)

            engine = _engine_with(settings)
            try:
                reports.append(
                    run_eval(engine, documents, label=label, catalogue=settings.catalogue)
                )
            finally:
                engine.close()

    text = format_report(reports, show_mistakes=show)
    target = report or (DEFAULT_REPORT_DIR / "eval-report.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    typer.echo()
    for entry in reports:
        counts = entry.person_partial
        colour = typer.colors.RED if entry.needs_finetuning else typer.colors.GREEN
        typer.secho(
            f"{entry.label:<28} PERSON partial  P={counts.precision:.3f} "
            f"R={counts.recall:.3f} F1={counts.f1:.3f}",
            fg=colour,
        )
        typer.echo(f"{'':<28} {entry.verdict()}")
    typer.echo(f"\nreport written to {target}")


@app.command()
def bench(
    ctx: typer.Context,
    repeats: int = typer.Option(3, "--repeats", min=1, help="Passes over the gold set."),
    gold: Optional[Path] = typer.Option(None, "--gold"),
    report: Optional[Path] = typer.Option(None, "--report"),
    full_matrix: bool = typer.Option(
        False, "--matrix", help="Sweep device x dtype x compile x batch size."
    ),
) -> None:
    """Measure throughput — with detection quality in the same table."""
    from privaparse.evaluation import DEFAULT_REPORT_DIR
    from privaparse.evaluation.bench import DEFAULT_MATRIX, format_bench_report, run_bench
    from privaparse.evaluation.harness import load_gold

    base = load_settings(**ctx.obj[_OVERRIDES])
    documents = _run(lambda: load_gold(gold or _default_gold()))

    configurations = (
        list(DEFAULT_MATRIX)
        if full_matrix
        else [(base.device, base.quantize, base.compile, base.batch_size)]
    )

    results = []
    for device, quantize, compile_, batch_size in configurations:
        settings = base.model_copy(
            update={
                "device": device,
                "quantize": quantize,
                "compile": compile_,
                "batch_size": batch_size,
            }
        )
        label = f"{device}/{'fp16' if quantize else 'fp32'}/c{int(bool(compile_))}/b{batch_size}"
        typer.echo(f"running {label} …", err=True)

        try:
            engine = PrivaParseEngine(settings)
        except Exception as exc:  # a config being unavailable must not stop the sweep
            from privaparse.evaluation.bench import BenchResult

            results.append(
                BenchResult(
                    label=label,
                    device=device,
                    quantize=bool(quantize),
                    compile=bool(compile_),
                    batch_size=batch_size,
                    error=str(exc).splitlines()[0],
                )
            )
            continue

        try:
            results.append(run_bench(engine, documents, label=label, repeats=repeats))
        finally:
            engine.close()

    text = format_bench_report(results, documents)
    target = report or (DEFAULT_REPORT_DIR / "bench-report.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    typer.echo()
    for result in results:
        colour = typer.colors.GREEN if result.status == "ok" else typer.colors.RED
        if result.error:
            typer.secho(f"{result.label:<28} ERROR: {result.error}", fg=colour)
            continue
        typer.secho(
            f"{result.label:<28} p50 {result.p50_ms:7.1f} ms  "
            f"{result.docs_per_second:6.1f} docs/s  "
            f"PERSON R={result.person_recall:.3f}  {result.status}",
            fg=colour,
        )
    typer.echo(f"\nreport written to {target}")


@vault_app.command("mappings")
def vault_mappings(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", min=1),
    match: Optional[str] = typer.Option(
        None, "--match", help="Only sessions whose source filename contains this."
    ),
) -> None:
    """List recent pseudonymisation sessions and their mapping ids.

    Without this, losing the id printed by `pseudonymize` means the document
    cannot be reversed at all — even though the vault knows exactly which
    placeholders it issued. Prints no stored values.
    """
    engine = _engine(ctx)
    rows = engine.recent_mappings(limit=limit, match=match)

    if not rows:
        typer.echo("no mappings recorded" + (f" matching {match!r}" if match else ""))
        return

    typer.echo(f"{'CREATED':<20} {'PLACEHOLDERS':>12}  {'MAPPING ID':<38} SOURCE")
    for row in rows:
        created = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(
            f"{created:<20} {row.placeholders:>12}  {row.id:<38} {row.source_name or '-'}"
        )
    typer.echo(f"\n{len(rows)} session(s). Reverse with: privaparse reverse FILE -m <MAPPING ID>")


@vault_app.command("stats")
def vault_stats(ctx: typer.Context) -> None:
    """Count what the vault currently holds. Prints no values."""
    engine = _engine(ctx)
    stats = engine.vault_stats()

    typer.echo(f"entities      {stats.entities}")
    typer.echo(f"surface forms {stats.surface_forms}")
    typer.echo(f"mappings      {stats.mappings}")
    for entity_type, count in sorted(stats.by_type.items()):
        typer.echo(f"  {entity_type:<8} {count}")


@catalog_app.command("show")
def catalog_show(ctx: typer.Context) -> None:
    """List the resolved placeholder types. Prints no prompts and no values."""
    settings = load_settings(**ctx.obj[_OVERRIDES])
    catalogue = settings.catalogue

    typer.echo(f"{'TYPE':<18} {'LABELS':>6} {'THRESH':>7} {'REV':>4} {'SWEEP':<6} SOURCE")
    for placeholder in sorted(catalogue.types.values(), key=lambda t: t.name):
        threshold = (
            f"{placeholder.threshold:.2f}" if placeholder.threshold is not None else "—"
        )
        source = catalogue.sources.get(placeholder.name)
        marker = "" if placeholder.enabled else "  (disabled)"
        typer.echo(
            f"{placeholder.name:<18} {len(placeholder.labels):>6} {threshold:>7} "
            f"{'yes' if placeholder.reversible else 'no':>4} {placeholder.sweep:<6} "
            f"{source.name if source else '-'}{marker}"
        )
    enabled = catalogue.enabled
    typer.echo(
        f"\n{len(enabled)} enabled type(s), {len(catalogue.schema())} label(s) "
        f"sent to the model"
    )


@catalog_app.command("validate")
def catalog_validate(
    file: Optional[Path] = typer.Argument(
        None, exists=True, dir_okay=False, readable=True,
        help="Catalogue to check. Omit to check the resolved one.",
    ),
) -> None:
    """Load a catalogue and report what is wrong with it. Changes nothing."""
    from privaparse.app.catalogue import CatalogueError, load_catalogue

    try:
        catalogue = load_catalogue(file)
    except CatalogueError as exc:
        typer.secho(f"invalid: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(
        f"ok — {len(catalogue.enabled)} enabled type(s), "
        f"{len(catalogue.schema())} label(s)",
        fg=typer.colors.GREEN,
    )


# --- helpers ---------------------------------------------------------------


def _engine(ctx: typer.Context) -> PrivaParseEngine:
    return _engine_with(load_settings(**ctx.obj[_OVERRIDES]))


def _engine_with(settings, **kwargs) -> PrivaParseEngine:  # type: ignore[no-untyped-def]
    try:
        return PrivaParseEngine(settings, **kwargs)
    except DeviceUnavailableError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _default_gold() -> Path:
    from privaparse.evaluation import DEFAULT_GOLD_PATH

    return DEFAULT_GOLD_PATH


def _short(model_id: str) -> str:
    return model_id.rsplit("/", 1)[-1]


def _run(action):  # type: ignore[no-untyped-def]
    """Turn expected pipeline failures into clean CLI errors."""
    try:
        return action()
    except (RuntimeError, ValueError, LookupError) as exc:
        typer.secho(f"{type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _read(file: Path) -> str:
    return file.read_text(encoding="utf-8")


def _section(title: str, body: str) -> None:
    typer.secho(f"\n{'=' * 4} {title} {'=' * max(4, 68 - len(title))}", fg=typer.colors.CYAN)
    typer.echo(body.rstrip("\n"))


def _force_utf8_output() -> None:
    """Windows consoles default to a legacy code page; German text breaks on it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached stream
                pass


if __name__ == "__main__":  # pragma: no cover
    app()
