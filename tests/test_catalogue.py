from __future__ import annotations

from pathlib import Path

import pytest

from privaparse.app.catalogue import (
    CatalogueError,
    discover_catalogue_path,
    load_catalogue,
)

MINIMAL = """
version: 1
placeholder_types:
  PERSON:
    labels: [person]
    prompts: {person: "Namen"}
    normalizer: person
"""


def _write(tmp_path: Path, body: str, name: str = "privaparse.entities.yaml") -> Path:
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return target


def test_default_catalogue_loads_and_has_person():
    catalogue = load_catalogue()
    assert catalogue.version == 1
    assert catalogue.get("PERSON").normalizer == "person"


def test_user_file_is_merged_onto_the_builtin_not_replacing_it(tmp_path):
    override = _write(tmp_path, "version: 1\nplaceholder_types:\n  PERSON:\n    threshold: 0.9\n")
    catalogue = load_catalogue(override)

    # The override changed one field.
    assert catalogue.get("PERSON").threshold == 0.9
    # Everything else survived, including types the user never mentioned.
    assert catalogue.get("PERSON").normalizer == "person"
    assert "EMAIL" in catalogue.types


def test_disabling_a_type_removes_its_labels_from_the_schema(tmp_path):
    override = _write(tmp_path, "version: 1\nplaceholder_types:\n  EMAIL:\n    enabled: false\n")
    catalogue = load_catalogue(override)

    assert "EMAIL" not in {t.name for t in catalogue.enabled}
    assert "email" not in catalogue.schema()
    assert "person" in catalogue.schema()


def test_unknown_normalizer_is_an_error(tmp_path):
    override = _write(tmp_path, "version: 1\nplaceholder_types:\n  PERSON:\n    normalizer: nope\n")
    with pytest.raises(CatalogueError, match="normalizer"):
        load_catalogue(override)


def test_unknown_sweep_mode_is_an_error(tmp_path):
    override = _write(tmp_path, "version: 1\nplaceholder_types:\n  PERSON:\n    sweep: sideways\n")
    with pytest.raises(CatalogueError, match="sweep"):
        load_catalogue(override)


def test_unknown_version_is_an_error(tmp_path):
    with pytest.raises(CatalogueError, match="version"):
        load_catalogue(_write(tmp_path, "version: 99\nplaceholder_types: {}\n"))


def test_unknown_label_warns_but_loads(tmp_path, caplog):
    override = _write(
        tmp_path,
        "version: 1\nplaceholder_types:\n  PERSON:\n    labels: [person, hausnummer]\n",
    )
    catalogue = load_catalogue(override)
    assert "hausnummer" in catalogue.schema()
    assert "hausnummer" in caplog.text


def test_label_to_type_is_many_to_one(tmp_path):
    override = _write(
        tmp_path,
        "version: 1\nplaceholder_types:\n  PERSON:\n    labels: [person, full_name]\n",
    )
    mapping = load_catalogue(override).label_to_type()
    assert mapping["person"] == "PERSON"
    assert mapping["full_name"] == "PERSON"


def test_two_types_claiming_one_label_is_an_error(tmp_path):
    override = _write(
        tmp_path,
        "version: 1\nplaceholder_types:\n"
        "  ALIAS:\n    labels: [person]\n    normalizer: person\n",
    )
    with pytest.raises(CatalogueError, match="person"):
        load_catalogue(override)


def test_discovery_prefers_env_over_cwd(tmp_path):
    from_env = _write(tmp_path, MINIMAL, "from-env.yaml")
    _write(tmp_path, MINIMAL)
    found = discover_catalogue_path(
        env={"PRIVAPARSE_ENTITIES": str(from_env)}, cwd=tmp_path, home=tmp_path
    )
    assert found == from_env


def test_discovery_falls_back_to_cwd(tmp_path):
    in_cwd = _write(tmp_path, MINIMAL)
    assert discover_catalogue_path(env={}, cwd=tmp_path, home=tmp_path / "elsewhere") == in_cwd


def test_discovery_returns_none_when_nothing_is_configured(tmp_path):
    assert discover_catalogue_path(env={}, cwd=tmp_path, home=tmp_path) is None


def test_span_accepts_a_type_outside_the_builtin_three():
    from privaparse.parser.types import Span

    span = Span(start=0, end=4, text="DE89", type="IBAN")
    assert span.type == "IBAN"
    assert str(span.type) == "IBAN"


def test_entity_type_constants_are_plain_strings():
    from privaparse.parser.types import EntityType

    assert EntityType.PERSON == "PERSON"
    assert f"{EntityType.EMAIL}" == "EMAIL"


def test_settings_entity_schema_comes_from_the_catalogue(tmp_path, monkeypatch):
    from privaparse.app.config import load_settings

    override = tmp_path / "privaparse.entities.yaml"
    override.write_text(
        "version: 1\nplaceholder_types:\n  PHONE:\n    enabled: false\n", encoding="utf-8"
    )
    monkeypatch.setenv("PRIVAPARSE_ENTITIES", str(override))

    schema = load_settings().entity_schema
    assert "person" in schema
    assert "phone_number" not in schema
