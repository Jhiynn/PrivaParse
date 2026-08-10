from __future__ import annotations

from pathlib import Path

import pytest

from privaparse.app.catalogue import (
    DEFAULT_CATALOGUE_PATH,
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
    # Pass the built-in path explicitly rather than calling load_catalogue()
    # bare. With no argument, loading falls through to discover_catalogue_path(),
    # which reads the real PRIVAPARSE_ENTITIES env var, the real cwd and the
    # real home directory — any of which could hold a stray catalogue file on
    # the machine running the test. Passing the path merges the default onto
    # itself, which is a no-op, and skips discovery entirely.
    catalogue = load_catalogue(DEFAULT_CATALOGUE_PATH)
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


def test_enabled_must_be_a_real_boolean(tmp_path):
    # "false" is a non-empty string, and bool("false") is True — a YAML author
    # who quotes the word gets a type that silently stays on. Catalogue
    # loading is fail-closed, so this must be a load error, not a truthy 1.
    override = _write(
        tmp_path, 'version: 1\nplaceholder_types:\n  PERSON:\n    enabled: "false"\n'
    )
    with pytest.raises(CatalogueError, match="enabled"):
        load_catalogue(override)


def test_reversible_must_be_a_real_boolean(tmp_path):
    override = _write(
        tmp_path, 'version: 1\nplaceholder_types:\n  PERSON:\n    reversible: "false"\n'
    )
    with pytest.raises(CatalogueError, match="reversible"):
        load_catalogue(override)


def test_type_name_must_satisfy_the_placeholder_grammar(tmp_path):
    # "1PERSON".isupper() is True — digits are not cased characters, so
    # .isupper() only looks at "PERSON" — but PLACEHOLDER_RE requires a
    # letter first. The label must not collide with a built-in one (e.g.
    # "person"), or the pre-existing duplicate-label check would raise
    # first and the test would pass for the wrong reason.
    override = _write(
        tmp_path,
        "version: 1\nplaceholder_types:\n  1PERSON:\n    labels: [made_up_label]\n"
        "    normalizer: person\n",
    )
    with pytest.raises(CatalogueError, match="1PERSON"):
        load_catalogue(override)


def test_type_name_may_contain_an_underscore(tmp_path):
    # Widening PLACEHOLDER_RE's type group to [A-Z][A-Z0-9_]* (rather than
    # closing up multi-word names like NATIONAL_ID) is the point of this
    # test: a name with an internal underscore must load, not be rejected by
    # the same grammar check that rejects a leading digit.
    override = _write(
        tmp_path,
        "version: 1\nplaceholder_types:\n  NATIONAL_ID:\n    labels: [made_up_label_2]\n"
        "    normalizer: person\n",
    )
    catalogue = load_catalogue(override)
    assert "NATIONAL_ID" in catalogue.types


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
    from enum import Enum

    from privaparse.parser.types import EntityType

    assert EntityType.PERSON == "PERSON"
    assert f"{EntityType.EMAIL}" == "EMAIL"
    # The old EntityType(str, Enum) also satisfied both assertions above -
    # str-mixin equality and str.__format__ don't distinguish it from a plain
    # string. This is the assertion only the refactor makes true.
    assert not issubclass(EntityType, Enum)


def test_settings_entity_schema_comes_from_the_catalogue(tmp_path, monkeypatch):
    from privaparse.app.config import load_settings

    override = tmp_path / "privaparse.entities.yaml"
    override.write_text(
        "version: 1\nplaceholder_types:\n"
        "  PHONE:\n    enabled: false\n"
        "  IBAN:\n    labels: [iban]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRIVAPARSE_ENTITIES", str(override))

    schema = load_settings().entity_schema
    assert "person" in schema
    assert "phone_number" not in schema
    # The hardcoded dict this used to return could never produce this key
    # under any input - only a real catalogue read can. This is what makes
    # the test discriminate the new behaviour from the old.
    assert "iban" in schema


# --- Task 9: the full catalogue --------------------------------------------

ALL_42 = 42


def test_default_catalogue_defines_every_model_label():
    """Every one of the 42 documented labels has a placeholder type
    somewhere in the catalogue file — nothing is silently missing or
    mistyped. Deliberately checked against ``catalogue.types`` (every
    defined type), not ``catalogue.enabled`` — "has a home in the
    catalogue" and "is currently sent to the model" are different claims
    since Task 12's sweep, which is exactly what the next test is for.
    """
    from privaparse.app.catalogue import MODEL_LABELS, load_catalogue

    catalogue = load_catalogue()
    defined = {label for placeholder in catalogue.types.values() for label in placeholder.labels}
    assert defined == set(MODEL_LABELS)
    assert len(defined) == ALL_42


def test_four_low_value_types_are_disabled_and_their_labels_stay_unrouted():
    """CITY, REGION, COUNTRY and DATE ship disabled. Each has its own
    measured false-positive count on the Task 12 gold set (91 documents) —
    DATE 19, CITY 13, REGION 4, COUNTRY 0 — not a single combined figure;
    see each type's own comment in entities.default.yaml for why summing
    them was wrong the first time this was written down. COUNTRY's 0 false
    positives means its disabling rests on judgement, not this measurement
    — its comment says so explicitly, unlike the other three.
    ``label_to_type()`` only walks ``catalogue.enabled``, so their 7 labels
    fall out of routing without being un-defined (previous test).
    """
    from privaparse.app.catalogue import load_catalogue

    catalogue = load_catalogue()
    routed = set(catalogue.label_to_type())
    disabled_labels = {
        "city", "state_or_region", "country",
        "sensitive_date", "document_date", "expiration_date", "transaction_date",
    }
    assert len(routed) == ALL_42 - len(disabled_labels)
    assert routed.isdisjoint(disabled_labels)


def test_default_catalogue_has_25_types():
    assert len(load_catalogue().types) == 25


def test_secrets_are_irreversible_by_default():
    assert load_catalogue().get("SECRET").reversible is False


def test_every_declared_validator_and_backstop_resolves():
    from privaparse.parser import registry

    catalogue = load_catalogue()
    for placeholder in catalogue.types.values():
        if placeholder.validator:
            registry.get_validator(placeholder.validator)
        if placeholder.backstop:
            registry.get_backstop(placeholder.backstop)
        registry.get_normalizer(placeholder.normalizer)


def test_every_registered_validator_and_backstop_is_used():
    """Mirror of the test above: a validator or backstop that no type names
    is dead code, not a safety net. Ten validators and six backstops are
    registered; the shipped catalogue is what is supposed to make every one
    of them reachable."""
    from privaparse.parser import registry

    catalogue = load_catalogue()
    used_validators = {t.validator for t in catalogue.types.values() if t.validator}
    used_backstops = {t.backstop for t in catalogue.types.values() if t.backstop}
    assert used_validators == set(registry.known_validators())
    assert used_backstops == set(registry.known_backstops())
