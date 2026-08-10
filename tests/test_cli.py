"""CLI behaviour. The `demo` command is the "can we test this right now" path."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

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
    # Email and phone come from rules, so they are the control group and must
    # score perfectly even without a model.
    assert "| regex | EMAIL | 20 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |" in text
    assert "| regex | PHONE | 18 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |" in text


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
