"""PrivaParse command line interface."""

from __future__ import annotations

import ipaddress
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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
catalog_app = typer.Typer(help="Inspect and check the catalogue.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
gateway_app = typer.Typer(help="Inspect the running gateway.", no_args_is_help=True)
app.add_typer(gateway_app, name="gateway")

_OVERRIDES = "privaparse_overrides"

#: Where `serve` listens and `run` looks, unless told otherwise.
DEFAULT_PORT = 8787

#: Host names that resolve to this machine and nowhere else.
_LOOPBACK_NAMES = frozenset({"localhost"})


@app.callback()
def _main(
    ctx: typer.Context,
    device: str | None = typer.Option(
        None, "--device", help="auto | cpu | cuda | cuda:N. Overrides PRIVAPARSE_DEVICE."
    ),
    detector: str | None = typer.Option(
        None, "--detector", help="hybrid | gliner | regex."
    ),
    db: Path | None = typer.Option(None, "--db", help="Path to the vault database."),
    threshold: float | None = typer.Option(
        None, "--threshold", min=0.0, max=1.0,
        help="Score floor for a type with no threshold of its own in the "
        "catalogue. Most types declare one and are unaffected by this.",
    ),
    batch_size: int | None = typer.Option(None, "--batch-size", min=1),
    scan_code: bool | None = typer.Option(
        None, "--scan-code/--protect-code", help="Also scan code blocks and URLs."
    ),
    log_level: str | None = typer.Option(None, "--log-level"),
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
    out: Path | None = typer.Option(None, "-o", "--out", help="Defaults to <file>.pseudo.md"),
    mapping_out: Path | None = typer.Option(
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
    mapping: str | None = typer.Option(
        None,
        "--mapping",
        "-m",
        help="Mapping id from pseudonymize. Omit to find the mapping that issued "
        "every placeholder in the file.",
    ),
    out: Path | None = typer.Option(None, "-o", "--out"),
    strict: bool = typer.Option(
        False, "--strict", help="Fail if the text carries placeholders from another mapping."
    ),
) -> None:
    """Restore original values in FILE.

    With no --mapping, the mapping that issued every placeholder in the file is
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
            f"{span.type!s:<8} {span_range:<14} {span.source:<7} "
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
            f"  {r.span.type!s:<8} {r.span.text!r:<32} -> {r.placeholder}"
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
            "torch      not installed — pipx: pipx inject privaparse "
            "\"gliner2[local]\"; checkout: pip install -e '.[model]'",
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
    gold: Path | None = typer.Option(None, "--gold", help="Gold JSONL."),
    report: Path | None = typer.Option(
        None, "--report", help="Write a markdown report here (default docs/benchmarks/detection-quality.md)."
    ),
    show: int = typer.Option(15, "--show", help="Mistakes to list per category."),
    sweep_threshold: bool = typer.Option(
        False, "--sweep-threshold",
        help="Score the gold set at 0.3–0.9 from one model pass and write the curve.",
    ),
) -> None:
    """Score detection against the German gold set and decide the fine-tuning question."""
    from privaparse.evaluation import DEFAULT_REPORT_DIR
    from privaparse.evaluation.harness import detect_for_scoring, format_report, load_gold
    from privaparse.evaluation.harness import evaluate as run_eval

    base = load_settings(**ctx.obj[_OVERRIDES])
    documents = _run(lambda: load_gold(gold or _default_gold()))

    if sweep_threshold:
        from privaparse.evaluation.harness import format_sweep, sweep_thresholds

        engine = _engine_with(base)
        try:
            results = sweep_thresholds(
                engine.detection_pass(), documents, catalogue=base.catalogue
            )
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
                    run_eval(
                        detect_for_scoring(
                            engine.detection_pass(), documents, batched=False
                        ),
                        documents,
                        label=label,
                        catalogue=settings.catalogue,
                    )
                )
            finally:
                engine.close()

    text = format_report(reports, show_mistakes=show)
    target = report or (DEFAULT_REPORT_DIR / "benchmarks" / "detection-quality.md")
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
    gold: Path | None = typer.Option(None, "--gold"),
    report: Path | None = typer.Option(None, "--report"),
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
        except Exception as exc:  # noqa: BLE001 -- config unavailable must not stop the sweep
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
    target = report or (DEFAULT_REPORT_DIR / "benchmarks" / "throughput.md")
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
    match: str | None = typer.Option(
        None, "--match", help="Only mappings whose source filename contains this."
    ),
) -> None:
    """List recent mappings and their ids.

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
    typer.echo(f"\n{len(rows)} mapping(s). Reverse with: privaparse reverse FILE -m <MAPPING ID>")


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


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Loopback only. See below."),
    port: int = typer.Option(DEFAULT_PORT, "--port"),
    upstream: str | None = typer.Option(
        None, "--upstream", help="Origin to forward to, e.g. https://api.openai.com. "
        "Overrides PRIVAPARSE_GATEWAY_UPSTREAM.",
    ),
) -> None:
    """Run the OpenAI-compatible gateway on this machine.

    Point any client that accepts a base URL at it and nothing else changes:
    OPENAI_BASE_URL=http://127.0.0.1:8787/v1
    """
    settings = load_settings(**ctx.obj[_OVERRIDES], gateway_upstream=upstream)
    _check_bind_address(host, api_key=settings.api_key)
    engine = _engine_with(settings)

    from privaparse.gateway.server import create_app

    typer.echo(f"privaparse gateway  http://{host}:{port}  ->  {settings.gateway_upstream}")
    typer.echo(f"point a client at it with OPENAI_BASE_URL=http://{host}:{port}/v1")
    _serve(create_app(settings, engine=engine), host=host, port=port)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: typer.Context,
    port: int = typer.Option(DEFAULT_PORT, "--port"),
    wait: float = typer.Option(60.0, "--wait", help="Seconds to wait for a gateway to start."),
) -> None:
    """Run a command with its OpenAI client pointed at the gateway.

    privaparse run -- <command>

    Starts a gateway if none is already listening, sets OPENAI_BASE_URL in the
    child's environment, and exits with the child's own exit code.
    """
    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        typer.secho(
            "nothing to run. Usage: privaparse run -- <command>",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=2)

    daemon: subprocess.Popen | None = None
    if not _gateway_ready(port):
        if _gateway_requires_key(port):
            # Something is already listening on this port, keyed, and this
            # command has no way to speak to it: the child gets an OpenAI
            # client, and that wire protocol has nowhere to carry
            # X-PrivaParse-Key. Starting a second gateway would also just
            # fail to bind the port, so this fails now with a message that
            # says what is actually wrong instead of timing out later.
            typer.secho(
                f"a gateway is already listening on 127.0.0.1:{port} but requires "
                "a key. The child this command starts speaks the OpenAI wire "
                "protocol, which has nowhere to carry X-PrivaParse-Key, so it "
                "cannot use that gateway. Stop it, or point --port at a "
                "different one.",
                fg=typer.colors.RED, err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"starting a gateway on 127.0.0.1:{port} ...")
        daemon = _start_gateway(port)
        if not _await_gateway(port, wait):
            daemon.terminate()
            typer.secho(
                f"the gateway did not come up within {wait:g}s. Run `privaparse serve` "
                "in another terminal to see why.",
                fg=typer.colors.RED, err=True,
            )
            raise typer.Exit(code=1)

    environment = dict(os.environ)
    environment["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    # OPENAI_API_KEY is deliberately untouched. It is the caller's credential,
    # the gateway forwards it to the provider and stores none of its own, so
    # there is nothing here to substitute and nothing to keep.

    try:
        code = _run_child(command, environment)
    finally:
        if daemon is not None:
            # Only a gateway this command started. One that was already
            # running belongs to whoever started it.
            daemon.terminate()
    raise typer.Exit(code=code)


@gateway_app.command("stats")
def gateway_stats(
    ctx: typer.Context,
    port: int = typer.Option(DEFAULT_PORT, "--port"),
) -> None:
    """Counters from a running gateway. Prints no content, ever."""
    settings = load_settings(**ctx.obj[_OVERRIDES])
    body = _fetch_stats(port, api_key=settings.api_key)
    cache = body.get("cache", {})

    typer.echo(f"requests          {body['requests']}")
    typer.echo(f"entities/request  {body['entities_per_request']}")
    typer.echo(f"pseudonymise p50  {body['pseudonymize_p50_ms']} ms")
    typer.echo(
        f"cache hit rate    {cache['hit_rate']} "
        f"({cache['hits']} hit / {cache['misses']} miss)"
    )
    typer.echo(f"cache blocks      {cache['blocks']} / {cache['capacity']}")


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
    file: Path | None = typer.Argument(
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


# --- gateway helpers ---------------------------------------------------------


def _check_bind_address(host: str, *, api_key: str) -> None:
    """Refuse a bind that would expose the vault with nothing in front of it.

    Loopback is always allowed. Anything wider is allowed only when a key is
    configured, because the vault holds plaintext values and
    `/privaparse/reverse` reads them back for whoever asks. This deliberately
    does not try to detect a container: the heuristics for that are unreliable,
    and being wrong means either blocking a valid deployment or silently
    publishing a vault on somebody's network.
    """
    if host in _LOOPBACK_NAMES:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass

    if api_key:
        return

    typer.secho(
        f"refusing to bind {host}: the vault behind this gateway stores plaintext "
        "values, and /privaparse/reverse turns placeholders back into them for "
        "whoever asks. Set PRIVAPARSE_API_KEY to a secret of your choosing and "
        "callers must then present it as the X-PrivaParse-Key header. Or bind "
        "127.0.0.1 and reach it through an SSH tunnel.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


def _serve(application, *, host: str, port: int) -> None:  # pragma: no cover - real server
    import uvicorn

    uvicorn.run(application, host=host, port=port, log_level="info")


def _gateway_ready(port: int) -> bool:
    """True only if a gateway is listening on `port` AND usable by `run`.

    Deliberately probes `/privaparse/stats`, not `/healthz`. `/healthz` is
    the one route the auth middleware exempts -- the container healthcheck
    needs it to work without a key -- so it reads as ready on a keyed
    gateway even though the child `run` starts has no way to present that
    key. Any other route tells the truth: a keyed gateway 401s it, and this
    returns False, same as nothing listening at all. See
    `_gateway_requires_key` for telling those two apart.
    """
    import httpx

    from privaparse.gateway.server import STATS_PATH

    try:
        response = httpx.get(f"http://127.0.0.1:{port}{STATS_PATH}", timeout=1.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _gateway_requires_key(port: int) -> bool:
    """True if something is already listening on `port` but rejects an
    unauthenticated request with a 401.

    Called only after `_gateway_ready` says no, to tell "nothing is
    listening, start one" apart from "a keyed gateway already occupies this
    port" -- the second case is not fixable by starting a second gateway,
    since that would just fail to bind.
    """
    import httpx

    from privaparse.gateway.server import STATS_PATH

    try:
        response = httpx.get(f"http://127.0.0.1:{port}{STATS_PATH}", timeout=1.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 401


def _start_gateway(port: int) -> subprocess.Popen:  # pragma: no cover - spawns a process
    # This gateway is reachable only from this process tree -- `run` starts
    # it for a child process that gets OPENAI_BASE_URL and nothing else. A
    # key would buy nothing here (the only caller is this process's own
    # child) and cannot work anyway: the child speaks the OpenAI wire
    # protocol, which has no header for X-PrivaParse-Key. So the gateway
    # `run` spawns for itself always comes up keyless on loopback,
    # regardless of PRIVAPARSE_API_KEY in this process's own environment.
    environment = dict(os.environ)
    environment.pop("PRIVAPARSE_API_KEY", None)
    return subprocess.Popen(  # noqa: S603 -- fixed argv, this process's own entry point
        [sys.executable, "-m", "privaparse.app.main", "serve", "--port", str(port)],
        env=environment,
    )


def _await_gateway(port: int, seconds: float) -> bool:  # pragma: no cover - timing
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _gateway_ready(port):
            return True
        time.sleep(0.25)
    return False


def _run_child(command: list[str], environment: dict[str, str]) -> int:
    """Run the child to completion and hand back its exit code.

    There is no `exec` on Windows, so the child is a subprocess and this
    process stays alive in front of it. That makes signal forwarding this
    command's job: a Ctrl-C aimed at `privaparse run` has to reach the program
    the user actually launched, not stop at the wrapper.
    """
    child = subprocess.Popen(command, env=environment)  # noqa: S603 -- operator-supplied command by design
    previous = _forward_signals_to(child)
    try:
        return child.wait()
    finally:
        for received, handler in previous.items():
            try:
                signal.signal(received, handler)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass


def _forward_signals_to(child: subprocess.Popen) -> dict:
    def relay(received, frame) -> None:  # pragma: no cover - needs a real signal
        child.terminate()

    previous: dict = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        received = getattr(signal, name, None)
        if received is None:
            continue
        try:
            previous[received] = signal.signal(received, relay)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            continue
    return previous


def _fetch_stats(port: int, *, api_key: str = "") -> dict:
    import httpx

    from privaparse.gateway.auth import HEADER
    from privaparse.gateway.server import STATS_PATH

    headers = {HEADER: api_key} if api_key else {}
    try:
        response = httpx.get(
            f"http://127.0.0.1:{port}{STATS_PATH}", timeout=2.0, headers=headers
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # `HTTPStatusError` is also an `HTTPError`, so this has to come
        # first: a 401 means a gateway *is* there and answering, just not to
        # this request, which is a different problem than nothing listening
        # and deserves a different message.
        if exc.response.status_code == 401:
            typer.secho(
                f"the gateway on 127.0.0.1:{port} requires a key, and "
                "PRIVAPARSE_API_KEY is not set in this shell. Set it to the "
                "same value the gateway was started with.",
                fg=typer.colors.RED,
                err=True,
            )
        else:
            typer.secho(
                f"the gateway on 127.0.0.1:{port} answered with "
                f"{exc.response.status_code}: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
        raise typer.Exit(code=1)
    except httpx.HTTPError:
        typer.secho(
            f"no privaparse gateway answering on 127.0.0.1:{port}. "
            "Start one with: privaparse serve",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return response.json()


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
