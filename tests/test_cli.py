"""CLI behaviour. The `demo` command is the "can we test this right now" path."""

from __future__ import annotations

import contextlib
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from privaparse.app import main
from privaparse.app.main import app
from privaparse.engine import PrivaParseEngine
from tests.conftest import DATA_DIR, NameListDetector

runner = CliRunner()


@pytest.fixture(name="runner")
def _runner_fixture() -> CliRunner:
    """The catalog tests below take ``runner`` as a fixture argument rather
    than closing over the module global the way ``_run()`` does. Registering
    under a different def name than ``runner`` matters: a fixture literally
    named ``def runner(...)`` would rebind the module-level ``runner`` above
    to the fixture function itself, and every existing test that goes through
    ``_run()`` -> the module global would start calling ``.invoke`` on a
    function object instead of a ``CliRunner``."""
    return runner


@pytest.fixture(autouse=True)
def fake_model(monkeypatch: pytest.MonkeyPatch):
    """Give the CLI the fake person detector instead of loading GLiNER2."""
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.detector import CompositeDetector, RegexDetector

    monkeypatch.setattr(
        PrivaParseEngine,
        "_build_detector",
        lambda self: CompositeDetector([NameListDetector(), RegexDetector(load_catalogue())]),
    )


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    shutil.copy(DATA_DIR / "beispiel.md", tmp_path / "beispiel.md")
    shutil.copy(DATA_DIR / "mit_code.md", tmp_path / "mit_code.md")
    return tmp_path


def _run(workspace: Path, *args: str):
    return runner.invoke(app, ["--db", str(workspace / "vault.db"), *args])


def test_pseudonymize_writes_a_file_and_prints_the_mapping_id(workspace: Path) -> None:
    result = _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))

    assert result.exit_code == 0, result.output
    out = workspace / "beispiel.pseudo.md"
    assert out.exists()

    text = out.read_text(encoding="utf-8")
    assert "[[PERSON_A1]]" in text
    assert "Max Mustermann" not in text
    assert "mapping id" in result.output


def test_round_trip_through_the_cli(workspace: Path) -> None:
    original = (workspace / "beispiel.md").read_text(encoding="utf-8")

    first = _run(
        workspace,
        "pseudonymize",
        str(workspace / "beispiel.md"),
        "--mapping-out",
        str(workspace / "id.txt"),
    )
    assert first.exit_code == 0, first.output

    mapping_id = (workspace / "id.txt").read_text(encoding="utf-8").strip()
    second = _run(
        workspace,
        "reverse",
        str(workspace / "beispiel.pseudo.md"),
        "--mapping",
        mapping_id,
        "-o",
        str(workspace / "zurueck.md"),
    )
    assert second.exit_code == 0, second.output
    assert (workspace / "zurueck.md").read_text(encoding="utf-8") == original


def test_demo_prints_every_stage(workspace: Path) -> None:
    result = _run(workspace, "demo", str(workspace / "beispiel.md"))

    assert result.exit_code == 0, result.output
    for stage in ("1. ORIGINAL", "2. DETECTED", "3. PSEUDONYMISED", "4. MOCK LLM", "5. RESTORED"):
        assert stage in result.output
    assert "[[PERSON_A1]]" in result.output
    assert "Max Mustermann" in result.output


def test_detect_json_output_is_parseable(workspace: Path) -> None:
    import json

    result = _run(workspace, "detect", str(workspace / "beispiel.md"), "--json")

    assert result.exit_code == 0, result.output
    spans = json.loads(result.stdout)
    assert {s["type"] for s in spans} == {"PERSON", "EMAIL", "PHONE"}
    assert all("start" in s and "end" in s for s in spans)


def test_diagnostics_go_to_stderr_so_stdout_stays_pipeable(workspace: Path) -> None:
    """`privaparse detect --json | jq` has to work."""
    result = _run(workspace, "detect", str(workspace / "beispiel.md"), "--json")

    assert result.stdout.lstrip().startswith("[")
    assert "engine ready" in result.stderr


def test_detect_does_not_write_to_the_vault(workspace: Path) -> None:
    _run(workspace, "detect", str(workspace / "beispiel.md"))
    stats = _run(workspace, "vault", "stats")
    assert "entities      0" in stats.output


def test_vault_mappings_lists_sessions_so_a_lost_id_is_recoverable(
    workspace: Path,
) -> None:
    """Without this, losing the id printed by `pseudonymize` makes the document
    permanently unreversible — even though the vault knows its placeholders."""
    _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))
    result = _run(workspace, "vault", "mappings")

    assert result.exit_code == 0, result.output
    assert "MAPPING ID" in result.output
    assert "beispiel.md" in result.output
    assert "privaparse reverse FILE -m" in result.output
    # A listing must not expose what was pseudonymised.
    assert "Max Mustermann" not in result.output


def test_vault_mappings_can_be_filtered_by_source(workspace: Path) -> None:
    _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))
    _run(workspace, "pseudonymize", str(workspace / "mit_code.md"))

    result = _run(workspace, "vault", "mappings", "--match", "mit_code")
    assert "mit_code.md" in result.output
    assert "beispiel.md" not in result.output


def test_vault_mappings_on_an_empty_vault_says_so(workspace: Path) -> None:
    result = _run(workspace, "vault", "mappings")
    assert result.exit_code == 0
    assert "no mappings recorded" in result.output


def test_an_unknown_mapping_id_points_at_the_listing(workspace: Path) -> None:
    _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))
    result = _run(
        workspace,
        "reverse",
        str(workspace / "beispiel.pseudo.md"),
        "--mapping",
        "00000000-0000-0000-0000-000000000000",
    )
    assert result.exit_code == 1
    assert "privaparse vault mappings" in result.output


def test_vault_stats_reports_counts_without_values(workspace: Path) -> None:
    _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))
    result = _run(workspace, "vault", "stats")

    assert result.exit_code == 0, result.output
    assert "entities      3" in result.output
    assert "Max Mustermann" not in result.output


def test_doctor_reports_the_resolved_setup(workspace: Path) -> None:
    result = _run(workspace, "--device", "cpu", "doctor")

    assert result.exit_code == 0, result.output
    assert "device     device=cpu" in result.output
    assert "dtype=fp32" in result.output


def test_doctor_fails_when_an_explicit_device_is_unavailable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from privaparse.app import device as device_mod

    monkeypatch.setattr(
        device_mod,
        "_probe_torch",
        lambda: device_mod._TorchProbe(available=True, cuda_available=False, reason="no CUDA"),
    )
    result = _run(workspace, "--device", "cuda", "doctor")

    assert result.exit_code == 1
    assert "UNAVAILABLE" in result.output


def test_pseudonymising_twice_exits_with_an_error(workspace: Path) -> None:
    _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))
    result = _run(workspace, "pseudonymize", str(workspace / "beispiel.pseudo.md"))

    assert result.exit_code == 1
    assert "AlreadyPseudonymizedError" in result.output


def test_reverse_with_an_unknown_mapping_exits_with_an_error(workspace: Path) -> None:
    _run(workspace, "pseudonymize", str(workspace / "beispiel.md"))
    result = _run(
        workspace,
        "reverse",
        str(workspace / "beispiel.pseudo.md"),
        "--mapping",
        "00000000-0000-0000-0000-000000000000",
    )
    assert result.exit_code == 1


def test_scan_code_flag_reaches_the_pipeline(workspace: Path) -> None:
    protected = _run(workspace, "detect", str(workspace / "mit_code.md"), "--json")
    scanned = _run(workspace, "--scan-code", "detect", str(workspace / "mit_code.md"), "--json")

    import json

    assert len(json.loads(scanned.stdout)) > len(json.loads(protected.stdout))


def test_missing_file_is_reported_by_the_argument_parser(workspace: Path) -> None:
    result = _run(workspace, "pseudonymize", str(workspace / "gibtsnicht.md"))
    assert result.exit_code != 0


def test_eval_writes_a_report_and_prints_a_verdict(workspace: Path) -> None:
    report = workspace / "eval.md"
    result = _run(workspace, "--device", "cpu", "eval", "--mode", "regex", "--report", str(report))

    assert result.exit_code == 0, result.output
    assert report.exists()

    text = report.read_text(encoding="utf-8")
    assert "| Run | Type | Support |" in text
    # Regex mode has no PERSON backstop, so it must miss every PERSON gold
    # entity — proof the per-type verdict actually catches a type that misses
    # its bar, not just one hand-fed a passing Counts in a unit test.
    assert "PERSON [FAIL]" in text
    # Email comes from rules, so it is the control group and must score
    # perfectly even without a model.
    assert "| regex | EMAIL | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |" in text
    # Phone is 0.947, not 1.000 — one deliberate exception, not a regression.
    # de-047 keeps its Steuer-ID in the bare, ungrouped digit form
    # generate_decidable() actually produces; the other three TAX_ID gold
    # entities are re-spaced into the official "XX XXX XXX XXX" grouping.
    # An 11-digit run with no separators is indistinguishable, to the phone
    # backstop's shape check, from a German mobile number, so that one gold
    # document is a known, kept collision — restoring it is what makes
    # TAX_ID's low measured recall on the grouped form visible at all. See
    # eval/gold/de_gold_source.md's Batch A note and detection-quality.md's
    # "One defect, two numbers" section.
    assert "| regex | PHONE | 18 | 0.947 | 1.000 | 0.947 | 1.000 | 0.973 |" in text


def test_eval_can_compare_several_modes_in_one_run(workspace: Path) -> None:
    report = workspace / "eval.md"
    result = _run(
        workspace, "--device", "cpu", "eval", "--mode", "regex", "--report", str(report), "--show", "3"
    )
    assert result.exit_code == 0, result.output
    assert "PERSON partial" in result.output


def test_bench_reports_speed_and_quality_together(workspace: Path) -> None:
    report = workspace / "bench.md"
    result = _run(
        workspace, "--device", "cpu", "--detector", "regex", "bench",
        "--repeats", "1", "--report", str(report),
    )

    assert result.exit_code == 0, result.output
    text = report.read_text(encoding="utf-8")
    assert "PERSON P | PERSON R" in text
    # Regex alone is very fast and finds no names — it must be marked failed,
    # not recommended.
    assert "FAILED QUALITY" in text
    assert "No configuration met the quality floor" in text


def test_catalog_show_lists_types(runner):
    result = runner.invoke(app, ["catalog", "show"])
    assert result.exit_code == 0
    assert "PERSON" in result.stdout
    assert "SECRET" in result.stdout


def test_catalog_show_prints_no_prompts_or_values(runner):
    result = runner.invoke(app, ["catalog", "show"])
    # exit_code is checked here, not just the absence of the prompt text:
    # without it, this assertion holds vacuously before the `catalog` command
    # exists at all (a "no such command" error prints no prompt text either).
    assert result.exit_code == 0
    assert "Vor- und Nachnamen" not in result.stdout


def test_catalog_validate_reports_a_bad_file(runner, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nplaceholder_types:\n  X:\n    normalizer: nope\n", encoding="utf-8")
    result = runner.invoke(app, ["catalog", "validate", str(bad)])
    assert result.exit_code == 1
    assert "normalizer" in result.stdout


def test_doctor_shows_the_catalogue(runner):
    result = runner.invoke(app, ["doctor"])
    assert "catalogue" in result.stdout


# --- the gateway commands ---------------------------------------------------


def _said(result) -> str:
    """Everything the command printed, whichever stream it chose."""
    return result.output + result.stderr


def test_serve_refuses_a_host_that_is_not_loopback(workspace: Path, monkeypatch) -> None:
    """The vault beside the gateway is plaintext and has no per-user access
    control, so a reachable port is a readable vault."""
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))

    result = _run(workspace, "serve", "--host", "0.0.0.0")  # noqa: S104 -- the value the guard must reject

    assert result.exit_code == 1
    assert "plaintext" in _said(result)
    assert started == []


def test_serve_binds_loopback_on_the_port_it_was_given(workspace: Path, monkeypatch) -> None:
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))

    result = _run(workspace, "serve", "--port", "8123")

    assert result.exit_code == 0, _said(result)
    assert started == [{"host": "127.0.0.1", "port": 8123}]


def test_serve_accepts_localhost_by_name(workspace: Path, monkeypatch) -> None:
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))

    result = _run(workspace, "serve", "--host", "localhost")

    assert result.exit_code == 0, _said(result)
    assert started


def test_run_injects_the_base_url_and_propagates_the_exit_code(
    workspace: Path, monkeypatch
) -> None:
    monkeypatch.setattr(main, "_gateway_ready", lambda port: True)
    probe = (
        "import os, sys; "
        "sys.exit(3 if os.environ.get('OPENAI_BASE_URL') == 'http://127.0.0.1:8791/v1' else 1)"
    )

    result = _run(workspace, "run", "--port", "8791", "--", sys.executable, "-c", probe)

    assert result.exit_code == 3, _said(result)


def test_run_leaves_the_api_key_exactly_as_it_found_it(workspace: Path, monkeypatch) -> None:
    """The key belongs to the caller and reaches the provider unchanged; the
    gateway stores no credential of its own."""
    monkeypatch.setattr(main, "_gateway_ready", lambda port: True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-child")
    probe = (
        "import os, sys; "
        "sys.exit(0 if os.environ.get('OPENAI_API_KEY') == 'sk-child' else 1)"
    )

    result = _run(workspace, "run", "--", sys.executable, "-c", probe)

    assert result.exit_code == 0, _said(result)


def test_run_with_no_command_says_so(workspace: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "_gateway_ready", lambda port: True)

    result = _run(workspace, "run")

    assert result.exit_code == 2
    assert "privaparse run --" in _said(result)


def test_gateway_stats_prints_the_counters(workspace: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "_fetch_stats", lambda port, api_key="": {
        "requests": 2,
        "entities_per_request": 1.5,
        "pseudonymize_p50_ms": 12.3,
        "cache": {"hits": 1, "misses": 1, "hit_rate": 0.5, "blocks": 1, "capacity": 2048},
    })

    result = _run(workspace, "gateway", "stats")

    assert result.exit_code == 0, _said(result)
    assert "12.3" in result.stdout
    assert "1.5" in result.stdout


def test_gateway_stats_says_when_nothing_is_listening(workspace: Path) -> None:
    result = _run(workspace, "gateway", "stats", "--port", "1")

    assert result.exit_code == 1
    assert "no privaparse gateway" in _said(result).lower()


@contextlib.contextmanager
def _live_gateway(settings):
    """Serve `settings` on a real loopback socket, not an in-process ASGI
    transport.

    `_fetch_stats` does a real `httpx.get` against `127.0.0.1:<port>` -- the
    defect this exists to catch (a keyed gateway 401ing a request that never
    sent `X-PrivaParse-Key`) only exists on the wire. `TestClient`, used
    everywhere else in this suite, talks to the ASGI app directly and cannot
    stand in for it; the wholesale mock on `_fetch_stats` in the sibling test
    above is exactly what let this go unnoticed in the first place.
    """
    import uvicorn

    from privaparse.gateway.server import create_app

    engine = PrivaParseEngine(settings, configure_logs=False)
    application = create_app(settings, engine=engine)
    config = uvicorn.Config(application, host="127.0.0.1", port=0, log_level="critical")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_gateway_stats_works_against_a_keyed_gateway_with_the_key_set(
    settings, monkeypatch
) -> None:
    """The real `_fetch_stats` against a real keyed gateway, not the
    wholesale mock `test_gateway_stats_prints_the_counters` uses. `main.py`
    used to send no `X-PrivaParse-Key` at all, so this 401ed against any
    keyed gateway -- and the mocked test above never could have caught it."""
    key = "s3cret-not-a-real-key"
    keyed_settings = settings.model_copy(update={"api_key": key})
    monkeypatch.setenv("PRIVAPARSE_API_KEY", key)

    with _live_gateway(keyed_settings) as port:
        result = runner.invoke(app, ["gateway", "stats", "--port", str(port)])

    assert result.exit_code == 0, _said(result)
    assert "requests" in result.stdout


def test_gateway_stats_reports_a_key_requirement_not_nothing_listening(settings) -> None:
    """A keyed gateway that 401s a request is not the same problem as no
    gateway at all, and used to be reported as exactly that -- `HTTPStatusError`
    subclasses `httpx.HTTPError`, so the old blanket `except` swallowed the
    401 into the "nothing is listening" message even though the gateway was
    up, healthy, and answering."""
    keyed_settings = settings.model_copy(update={"api_key": "s3cret-not-a-real-key"})

    with _live_gateway(keyed_settings) as port:
        result = runner.invoke(app, ["gateway", "stats", "--port", str(port)])

    assert result.exit_code == 1
    said = _said(result).lower()
    assert "requires a key" in said
    assert "no privaparse gateway" not in said


def test_serve_refuses_a_public_bind_without_a_key(workspace: Path, monkeypatch) -> None:
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))

    result = _run(workspace, "serve", "--host", "0.0.0.0")  # noqa: S104 -- the value the guard must reject

    assert result.exit_code == 1
    # The message must name the setting, so the reader knows what to do next.
    assert "PRIVAPARSE_API_KEY" in _said(result)
    assert started == []


def test_serve_still_refuses_a_public_bind_when_the_key_is_empty(
    workspace: Path, monkeypatch
) -> None:
    # An empty string is not a key. This is the case a .env file produces when
    # someone writes `PRIVAPARSE_API_KEY=` and moves on.
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))
    monkeypatch.setenv("PRIVAPARSE_API_KEY", "")

    result = _run(workspace, "serve", "--host", "0.0.0.0")  # noqa: S104 -- the value the guard must reject

    assert result.exit_code == 1
    assert started == []


def test_serve_allows_a_public_bind_once_a_key_is_set(workspace: Path, monkeypatch) -> None:
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))
    monkeypatch.setenv("PRIVAPARSE_API_KEY", "s3cret")

    result = _run(workspace, "serve", "--host", "0.0.0.0")  # noqa: S104 -- allowed once a key guards it

    assert result.exit_code == 0, _said(result)
    assert started == [{"host": "0.0.0.0", "port": 8787}]  # noqa: S104


def test_serve_allows_loopback_without_a_key(workspace: Path, monkeypatch) -> None:
    # The default path for every existing user, and it must not change.
    started: list[dict] = []
    monkeypatch.setattr(main, "_serve", lambda application, **kw: started.append(kw))

    result = _run(workspace, "serve", "--host", "127.0.0.1")

    assert result.exit_code == 0, _said(result)
    assert started == [{"host": "127.0.0.1", "port": 8787}]
