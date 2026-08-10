# Entity Catalogue and Measurement Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move PrivaParse's entity types out of source code into a YAML catalogue that routes all 42 labels of `fastino/gliner2-privacy-filter-PII-multi` onto 25 placeholder types, and extend the evaluation so the resulting thresholds are measured rather than guessed.

**Architecture:** A leaf registry module holds named normalizers, validators and backstops. A catalogue module loads and deep-merges YAML into frozen dataclasses and validates every registry reference at load time. `EntityType` stops being a closed enum; `Span.type` becomes a plain string whose legality is checked at the resolver boundary, immediately before anything reaches the vault. Regex keeps two jobs — recall backstop and checksum veto — but merge precedence flips so the model wins overlaps.

**Tech Stack:** Python 3.11+, pydantic v2 / pydantic-settings, SQLAlchemy 2.0, typer, phonenumbers, PyYAML (new), pytest.

## Global Constraints

- Python `>=3.11`. `from __future__ import annotations` at the top of every module.
- Line length 100 (`[tool.ruff]`, `target-version = "py311"`).
- Default test run is `pytest` and must stay under a few seconds. Anything needing model weights is marked `@pytest.mark.model`.
- **Anything that loads model weights runs in a lab sandbox, not on the development machine.** Locally: `pytest` only — the fast, weightless suite. In a sandbox: `pytest -m model`, `privaparse bench`, `privaparse eval`, `privaparse eval --sweep-threshold`. Steps that need one say so and are marked **[lab]**. Provision it with the `lab` skill, install with `pip install -e ".[dev,model]"`, and copy `docs/*-report.md` back before tearing the sandbox down — the reports are the deliverable, and the sandbox is not.
- **No database migration and no placeholder format change.** `entities.type` is already `String(32)` and `PLACEHOLDER_RE` already matches `[A-Z][A-Z0-9]*`. If a task appears to need an Alembic revision, stop — the design is wrong.
- Fail-closed on configuration: an unknown `normalizer`, `validator`, `backstop`, `sweep` or `version` value is an error, never a warning. An unknown *label* warns, because GLiNER is zero-shot and custom labels are legitimate.
- Never log or `repr()` an entity value. `register_secret()` from `privaparse.app.logging` is the existing mechanism.
- Comments explain *why*, matching the density and voice already in `parser/` and `database/`. No comment restates what the line does.
- Every task ends with a commit. Commit messages are Conventional Commits, written in normal English prose.
- **No attribution trailers.** No `Co-Authored-By`, no "Generated with", no mention of or link to Claude, Claude Code or Anthropic — not in commit messages, not in code comments, not in docs. The repository reads as its author's work.

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `privaparse/parser/registry.py` | Leaf module. Three name→callable dicts, registration decorators, `load_builtins()`. Imports nothing from `parser` or `app`. Tasks 4 and 5 each extend its import line as their module appears — no task ships a stub. |
| `privaparse/app/catalogue.py` | Catalogue dataclasses, YAML loading, discovery order, deep-merge, strict validation. |
| `privaparse/app/entities.default.yaml` | The shipped catalogue. Package data. |
| `privaparse/parser/validators.py` | Checksum and syntax vetoes over model spans. |
| `privaparse/parser/backstops.py` | Regex finders run alongside the model. |
| `tests/test_catalogue.py` | Loader, discovery, merge, validation. |
| `tests/test_validators.py` | Valid/invalid vectors per builtin validator. |
| `tests/test_backstops.py` | Each backstop finds its type and nothing else. |
| `tests/test_irreversible.py` | Secrets never reach the vault in restorable form. |
| `tests/test_batch.py` | `pseudonymize_batch` and `detect_many`. |
| `tests/test_sweep.py` | Threshold sweep reuses one model pass. |

**Modified:**

| File | Change |
| --- | --- |
| `privaparse/parser/types.py:9-17` | `EntityType` enum → constants class; `Span.type` becomes `str`. |
| `privaparse/parser/normalizer.py:68-74` | `normalize()` dispatches on a registry name, not an entity type. |
| `privaparse/parser/merge.py:39-50,155-179,195-203` | Precedence flip, catalogue-driven validators, catalogue-driven sweep. |
| `privaparse/parser/detector.py:97-148,179-191` | `RegexDetector` becomes catalogue-driven; `Detector` protocol gains `detect_many`. |
| `privaparse/parser/gliner_detector.py:70,116-175` | Schema from catalogue; `detect_many`; spans carry their model label. |
| `privaparse/parser/entity_resolver.py:57-91` | Catalogue lookup, type validation, irreversible handling. |
| `privaparse/parser/pseudonymizer.py` | `pseudonymize_batch` alongside `pseudonymize_text`. |
| `privaparse/app/config.py:17-31,129-131` | Catalogue-backed `entity_schema`; `catalogue_path` setting. |
| `privaparse/engine.py` | `pseudonymize_batch`, `detect_raw`, catalogue on the engine. |
| `privaparse/app/main.py` | `catalog show`, `catalog validate`, `doctor` additions, `eval --sweep-threshold`. |
| `privaparse/evaluation/harness.py` | Catalogue-driven types, per-label table, per-type bar verdicts, sweep. |
| `privaparse/evaluation/build_gold.py` | Generators for decidable types and negative documents. |
| `pyproject.toml` | `pyyaml>=6.0`; package data for the YAML. |

---

### Task 1: Registry and catalogue loader

The catalogue is the foundation every later task reads from. It ships with the
current three types only, so behaviour is unchanged when this task lands.

**Files:**
- Create: `privaparse/parser/registry.py`
- Create: `privaparse/app/catalogue.py`
- Create: `privaparse/app/entities.default.yaml`
- Modify: `pyproject.toml`
- Test: `tests/test_catalogue.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `registry.register_normalizer(name)`, `register_validator(name)`, `register_backstop(name)` — decorators returning the function unchanged.
  - `registry.get_normalizer(name) -> Callable[[str], str]`, `get_validator(name) -> Callable[[str], bool]`, `get_backstop(name) -> Callable[[str], list[Span]]`
  - `registry.known_normalizers() -> frozenset[str]` and the two siblings.
  - `registry.load_builtins() -> None` — idempotent; imports the implementation modules so the dicts are populated.
  - `catalogue.PlaceholderType`, `catalogue.Bar`, `catalogue.Catalogue`, `catalogue.CatalogueError`
  - `catalogue.load_catalogue(path: Path | None = None) -> Catalogue`
  - `catalogue.discover_catalogue_path(env, cwd, home) -> Path | None`
  - `Catalogue.schema() -> dict[str, str]` (label → prompt), `Catalogue.label_to_type() -> dict[str, str]`, `Catalogue.get(name) -> PlaceholderType`, `Catalogue.enabled -> tuple[PlaceholderType, ...]`

- [ ] **Step 1: Add the dependency and package data**

In `pyproject.toml`, add `"pyyaml>=6.0",` to `[project].dependencies`, and add
after `[tool.setuptools.packages.find]`:

```toml
[tool.setuptools.package-data]
privaparse = ["app/*.yaml"]
```

Then run `pip install -e ".[dev]"` to pick up the new dependency.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_catalogue.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_catalogue.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'privaparse.app.catalogue'`

- [ ] **Step 4: Write the registry**

Create `privaparse/parser/registry.py`:

```python
"""Name-to-callable registries for the entity catalogue.

A leaf module on purpose: it imports nothing from ``parser`` or ``app``, so the
catalogue can validate the names a config file uses without dragging the
detection pipeline — and torch — into config loading.

The implementation modules register into it at import time; ``load_builtins``
is what makes that happen without the catalogue importing them by name at
module scope.
"""

from __future__ import annotations

from typing import Callable, TypeVar

__all__ = [
    "register_normalizer",
    "register_validator",
    "register_backstop",
    "get_normalizer",
    "get_validator",
    "get_backstop",
    "known_normalizers",
    "known_validators",
    "known_backstops",
    "load_builtins",
]

F = TypeVar("F", bound=Callable)

_NORMALIZERS: dict[str, Callable] = {}
_VALIDATORS: dict[str, Callable] = {}
_BACKSTOPS: dict[str, Callable] = {}


def _make_register(table: dict[str, Callable], kind: str):
    def register(name: str) -> Callable[[F], F]:
        def decorate(function: F) -> F:
            if name in table and table[name] is not function:
                raise ValueError(f"{kind} {name!r} is already registered")
            table[name] = function
            return function

        return decorate

    return register


register_normalizer = _make_register(_NORMALIZERS, "normalizer")
register_validator = _make_register(_VALIDATORS, "validator")
register_backstop = _make_register(_BACKSTOPS, "backstop")


def _make_getter(table: dict[str, Callable], kind: str):
    def get(name: str) -> Callable:
        load_builtins()
        try:
            return table[name]
        except KeyError:
            raise KeyError(f"unknown {kind} {name!r}") from None

    return get


get_normalizer = _make_getter(_NORMALIZERS, "normalizer")
get_validator = _make_getter(_VALIDATORS, "validator")
get_backstop = _make_getter(_BACKSTOPS, "backstop")


def known_normalizers() -> frozenset[str]:
    load_builtins()
    return frozenset(_NORMALIZERS)


def known_validators() -> frozenset[str]:
    load_builtins()
    return frozenset(_VALIDATORS)


def known_backstops() -> frozenset[str]:
    load_builtins()
    return frozenset(_BACKSTOPS)


_loaded = False


def load_builtins() -> None:
    """Import the modules that populate the registries. Idempotent.

    Tasks 4 and 5 add ``validators`` and ``backstops`` to the import list when
    those modules exist. Importing a module that has not been written yet would
    mean shipping a stub, and a stub that returns nothing is indistinguishable
    from a finder that found nothing.
    """
    global _loaded
    if _loaded:
        return
    # Set first: the import below reaches back into this module to register, and
    # a re-entrant call must not recurse.
    _loaded = True
    from privaparse.parser import normalizer  # noqa: F401
```

- [ ] **Step 5: Write the catalogue module**

Create `privaparse/app/catalogue.py`:

```python
"""The entity catalogue: which placeholder types exist and how they behave.

Everything about an entity type lives in one YAML file — the model labels that
feed it, how its values are normalised, what may veto the model, its detection
threshold and the quality bar the evaluation holds it to. Splitting those
across code and config was the thing that made adding a fourth type a
five-module edit.

A user file is deep-merged **onto** the built-in catalogue rather than
replacing it. Removing a type takes ``enabled: false``. Without that rule an
upgrade that adds a type would quietly skip anyone who had ever written a
config, which is the opposite of what a privacy tool should do on upgrade.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from privaparse.app.logging import get_logger
from privaparse.parser import registry

log = get_logger("catalogue")

__all__ = [
    "Bar",
    "Catalogue",
    "CatalogueError",
    "PlaceholderType",
    "DEFAULT_CATALOGUE_PATH",
    "MODEL_LABELS",
    "discover_catalogue_path",
    "load_catalogue",
]

CATALOGUE_VERSION = 1
DEFAULT_CATALOGUE_PATH = Path(__file__).with_name("entities.default.yaml")
SWEEP_MODES = frozenset({"word", "icase", "exact", "off"})

#: The 42 labels documented by fastino/gliner2-privacy-filter-PII-multi. Used
#: only to warn about typos — GLiNER is zero-shot, so a label outside this set
#: is unusual rather than wrong.
MODEL_LABELS = frozenset(
    {
        "person", "full_name", "first_name", "middle_name", "last_name",
        "date_of_birth", "email", "phone_number", "address", "street_address",
        "city", "state_or_region", "postal_code", "country", "government_id",
        "national_id_number", "passport_number", "drivers_license_number",
        "license_number", "tax_id", "tax_number", "bank_account",
        "account_number", "routing_number", "iban", "payment_card",
        "card_number", "card_expiry", "card_cvv", "username", "ip_address",
        "account_id", "sensitive_account_id", "password", "secret", "api_key",
        "access_token", "recovery_code", "sensitive_date", "document_date",
        "expiration_date", "transaction_date",
    }
)


class CatalogueError(ValueError):
    """A catalogue file is malformed, or names something that does not exist."""


@dataclass(frozen=True, slots=True)
class Bar:
    """The quality floor the evaluation holds a type to."""

    precision: float | None = None
    recall: float | None = None


@dataclass(frozen=True, slots=True)
class PlaceholderType:
    name: str
    labels: tuple[str, ...] = ()
    prompts: Mapping[str, str] = field(default_factory=dict)
    normalizer: str = "casefold"
    validator: str | None = None
    backstop: str | None = None
    sweep: str = "word"
    threshold: float | None = None
    reversible: bool = True
    enabled: bool = True
    bar: Bar | None = None


@dataclass(frozen=True)
class Catalogue:
    version: int
    types: Mapping[str, PlaceholderType]
    #: Which file each type's last override came from. For `catalog show`.
    sources: Mapping[str, Path] = field(default_factory=dict)

    @property
    def enabled(self) -> tuple[PlaceholderType, ...]:
        return tuple(t for t in self.types.values() if t.enabled)

    def get(self, name: str) -> PlaceholderType:
        try:
            return self.types[name]
        except KeyError:
            raise CatalogueError(f"unknown placeholder type {name!r}") from None

    def schema(self) -> dict[str, str]:
        """Label to description, exactly as GLiNER2 wants it.

        Only enabled types contribute. A disabled type costs nothing at
        inference time because its labels never reach the model.
        """
        out: dict[str, str] = {}
        for placeholder in self.enabled:
            for label in placeholder.labels:
                out[label] = placeholder.prompts.get(label, label.replace("_", " "))
        return out

    def label_to_type(self) -> dict[str, str]:
        return {
            label: placeholder.name
            for placeholder in self.enabled
            for label in placeholder.labels
        }

    def threshold_for(self, name: str, default: float) -> float:
        value = self.types[name].threshold if name in self.types else None
        return default if value is None else value


def discover_catalogue_path(
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    """First hit wins: explicit env var, project file, user config."""
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    home = Path.home() if home is None else home

    explicit = env.get("PRIVAPARSE_ENTITIES")
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise CatalogueError(f"PRIVAPARSE_ENTITIES points at {path}, which does not exist")
        return path

    for candidate in (cwd / "privaparse.entities.yaml", home / ".config/privaparse/entities.yaml"):
        if candidate.exists():
            return candidate
    return None


def load_catalogue(path: Path | None = None) -> Catalogue:
    """Built-in catalogue, with ``path`` (or a discovered file) merged onto it."""
    base = _read(DEFAULT_CATALOGUE_PATH)
    overlay_path = path if path is not None else discover_catalogue_path()

    sources = {name: DEFAULT_CATALOGUE_PATH for name in base.get("placeholder_types", {})}
    if overlay_path is not None:
        overlay = _read(overlay_path)
        for name in overlay.get("placeholder_types", {}):
            sources[name] = overlay_path
        base = _deep_merge(base, overlay)

    return _build(base, sources)


# --- internals -------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogueError(f"{path} must contain a mapping at the top level")

    version = raw.get("version")
    if version != CATALOGUE_VERSION:
        raise CatalogueError(
            f"{path} declares version {version!r}; this build understands "
            f"version {CATALOGUE_VERSION} only"
        )
    return raw


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. Scalars and lists from ``overlay`` replace outright.

    Lists replace rather than concatenate because ``labels: [person]`` has to be
    a way to *narrow* a type, not only to widen it.
    """
    out = dict(base)
    for key, value in overlay.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _deep_merge(current, value)
        else:
            out[key] = value
    return out


def _build(raw: dict[str, Any], sources: Mapping[str, Path]) -> Catalogue:
    entries = raw.get("placeholder_types") or {}
    if not isinstance(entries, dict):
        raise CatalogueError("placeholder_types must be a mapping of NAME -> settings")

    types: dict[str, PlaceholderType] = {}
    for name, body in entries.items():
        types[name] = _build_type(name, body or {})

    _validate(types)
    return Catalogue(version=CATALOGUE_VERSION, types=types, sources=dict(sources))


def _build_type(name: str, body: dict[str, Any]) -> PlaceholderType:
    if not name.isupper():
        raise CatalogueError(
            f"placeholder type {name!r} must be upper case — it is rendered "
            f"literally into [[{name}_A1]]"
        )
    bar = body.get("bar")
    return PlaceholderType(
        name=name,
        labels=tuple(body.get("labels") or ()),
        prompts=dict(body.get("prompts") or {}),
        normalizer=body.get("normalizer", "casefold"),
        validator=_strip_builtin(body.get("validator")),
        backstop=_strip_builtin(body.get("backstop")),
        sweep=body.get("sweep", "word"),
        threshold=body.get("threshold"),
        reversible=bool(body.get("reversible", True)),
        enabled=bool(body.get("enabled", True)),
        bar=Bar(precision=bar.get("precision"), recall=bar.get("recall")) if bar else None,
    )


def _strip_builtin(value: str | None) -> str | None:
    """``builtin:luhn`` and ``luhn`` name the same thing."""
    if value is None:
        return None
    return value.split(":", 1)[1] if value.startswith("builtin:") else value


def _validate(types: Mapping[str, PlaceholderType]) -> None:
    normalizers = registry.known_normalizers()
    validators = registry.known_validators()
    backstops = registry.known_backstops()

    claimed: dict[str, str] = {}
    for placeholder in types.values():
        if placeholder.normalizer not in normalizers:
            raise CatalogueError(
                f"{placeholder.name}: unknown normalizer {placeholder.normalizer!r} "
                f"(known: {', '.join(sorted(normalizers))})"
            )
        if placeholder.validator is not None and placeholder.validator not in validators:
            raise CatalogueError(
                f"{placeholder.name}: unknown validator {placeholder.validator!r} "
                f"(known: {', '.join(sorted(validators))})"
            )
        if placeholder.backstop is not None and placeholder.backstop not in backstops:
            raise CatalogueError(
                f"{placeholder.name}: unknown backstop {placeholder.backstop!r} "
                f"(known: {', '.join(sorted(backstops))})"
            )
        if placeholder.sweep not in SWEEP_MODES:
            raise CatalogueError(
                f"{placeholder.name}: unknown sweep mode {placeholder.sweep!r} "
                f"(known: {', '.join(sorted(SWEEP_MODES))})"
            )
        if placeholder.threshold is not None and not 0.0 <= placeholder.threshold <= 1.0:
            raise CatalogueError(
                f"{placeholder.name}: threshold {placeholder.threshold} is outside 0.0–1.0"
            )

        if not placeholder.enabled:
            continue
        for label in placeholder.labels:
            if label in claimed:
                raise CatalogueError(
                    f"label {label!r} is claimed by both {claimed[label]} and "
                    f"{placeholder.name}; a label routes to exactly one type"
                )
            claimed[label] = placeholder.name
            if label not in MODEL_LABELS:
                log.warning(
                    "label %r is not one the detection model documents; it will "
                    "still be sent, because GLiNER is zero-shot",
                    label,
                )
```

- [ ] **Step 6: Write the default catalogue**

Create `privaparse/app/entities.default.yaml` with the three current types only.
Task 9 widens it — landing 42 labels here now would change detection behaviour
before the machinery around it exists.

```yaml
# The shipped entity catalogue.
#
# A user file (PRIVAPARSE_ENTITIES, ./privaparse.entities.yaml, or
# ~/.config/privaparse/entities.yaml) is deep-merged ONTO this one. To remove a
# type, set `enabled: false` — omitting it here changes nothing.
version: 1

placeholder_types:
  PERSON:
    labels: [person]
    prompts:
      person: "Vor- und Nachnamen von Menschen, auch mit Titeln wie Dr. oder Prof."
    normalizer: person
    sweep: word
    reversible: true
    enabled: true
    bar: { precision: 0.85, recall: 0.90 }

  EMAIL:
    labels: [email]
    prompts:
      email: "E-Mail-Adressen"
    normalizer: email
    sweep: icase
    reversible: true
    enabled: true
    bar: { precision: 0.95, recall: 0.95 }

  PHONE:
    labels: [phone_number]
    prompts:
      phone_number: "Telefon- und Mobilnummern, auch mit Landesvorwahl"
    normalizer: phone
    sweep: exact
    reversible: true
    enabled: true
    bar: { precision: 0.95, recall: 0.95 }
```

`validator:` and `backstop:` are absent on purpose. Both fields are optional,
and their registries are empty until Tasks 4 and 5 write the implementations.
Declaring them now would force a stub, and a backstop stub that returns an
empty list is indistinguishable — to a reader and to a test — from a finder
that legitimately found nothing.

- [ ] **Step 7: Register the normalizers that already exist**

`normalizer.py` already contains three real normalizers. Give them their
registry names, and add `casefold` — which the catalogue uses as the default
when a type declares no normalizer, so it has to exist from the start.

At the top of `privaparse/parser/normalizer.py`:

```python
from privaparse.parser.registry import register_normalizer
```

Decorate the three existing functions with `@register_normalizer("person")`,
`@register_normalizer("email")` and `@register_normalizer("phone")`
respectively, leaving their bodies untouched. Then add:

```python
@register_normalizer("casefold")
def normalize_casefold(value: str) -> str:
    """NFKC, collapsed whitespace, case-folded.

    The catalogue's default when a type names no normalizer, so it must be the
    most conservative useful choice: it collapses spelling noise and nothing
    else.
    """
    text = unicodedata.normalize("NFKC", value)
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()
```

Add `normalize_casefold` to `__all__`.

No `validators.py` and no `backstops.py` in this task. Their registries stay
empty and the default catalogue names neither, which is consistent: a
catalogue that referenced an unregistered validator would fail to load, and
that is exactly the fail-closed behaviour Step 2 tests for.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_catalogue.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 9: Run the full suite**

Run: `pytest`
Expected: PASS, no regressions — nothing reads the catalogue yet.

- [ ] **Step 10: Commit**

```bash
git add privaparse/parser/registry.py privaparse/app/catalogue.py privaparse/app/entities.default.yaml privaparse/parser/normalizer.py pyproject.toml tests/test_catalogue.py
git commit -m "feat: load entity types from a YAML catalogue

The three entity types were hardcoded across five modules. This puts them in
one file, deep-merged onto the built-in so an upgrade that adds a type still
reaches users who wrote a config. Loading is strict: an unknown normalizer,
validator, sweep mode or version is an error. An unknown label only warns,
because GLiNER is zero-shot and custom labels are legitimate."
```

---

### Task 2: Open the entity type

**Files:**
- Modify: `privaparse/parser/types.py:9-17`
- Modify: `privaparse/app/config.py:17-31,129-131`
- Modify: `privaparse/parser/merge.py:39,155-179,182-203`
- Modify: `privaparse/parser/gliner_detector.py:33,161`
- Test: `tests/test_catalogue.py` (extend), existing suite

**Interfaces:**
- Consumes: `Catalogue`, `load_catalogue` from Task 1.
- Produces:
  - `EntityType` — a plain class of `str` constants (`PERSON`, `EMAIL`, `PHONE`). Not an enum, not exhaustive.
  - `Span.type: str`
  - `Settings.catalogue_path: Path | None`, `Settings.catalogue -> Catalogue` (cached), `Settings.entity_schema -> dict[str, str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalogue.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_catalogue.py -k "span_accepts or constants or entity_schema" -v`
Expected: FAIL — `Span` rejects `"IBAN"` because `type` is an `EntityType` enum, and `entity_schema` returns the hardcoded dict.

- [ ] **Step 3: Open the type**

Replace `privaparse/parser/types.py:9-17` with:

```python
class EntityType:
    """Well-known placeholder type names.

    Deliberately not an ``Enum``: the set of types is decided by the catalogue
    at runtime, and an enum would make every user-defined type a second-class
    citizen. These constants exist so the code that genuinely does care about
    the three built-in types can say so without a string literal.
    """

    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
```

In the same file, change `Span.type` from `EntityType` to `str`. Add to
`__post_init__`:

```python
        if not self.type:
            raise ValueError("span type must not be empty")
```

Legality against the catalogue is *not* checked here. A `Span` is constructed
thousands of times per document and a catalogue lookup per construction would
be pure cost; the check belongs where the value first has consequences, which
is `EntityResolver` in Task 7 — immediately before anything reaches the vault.

- [ ] **Step 4: Replace enum identity checks with equality**

Four sites compare with `is`, which stops being reliable once the type is a
plain string:

- `merge.py:176` — `if span.type is EntityType.EMAIL:` → `==`
- `merge.py:178` — `if span.type is EntityType.PHONE:` → `==`
- `merge.py:197` — `if entity_type is EntityType.EMAIL:` → `==`
- `merge.py:201` — `if entity_type is EntityType.PERSON:` → `==`

Also change `merge.py:39` from `{EntityType.EMAIL: 3, ...}` to plain string keys
(the values are already strings after the change above, so this is mechanical),
and `merge.py:184` `seen: set[tuple[EntityType, str]]` to `set[tuple[str, str]]`.

- [ ] **Step 5: Point Settings at the catalogue**

In `privaparse/app/config.py`, delete `DEFAULT_ENTITY_SCHEMA` and
`SCHEMA_KEY_TO_TYPE` (lines 17–30). Add the field and replace the property:

```python
    catalogue_path: Path | None = Field(
        default=None,
        description="Entity catalogue YAML. None means: discover it (PRIVAPARSE_ENTITIES, "
        "./privaparse.entities.yaml, ~/.config/privaparse/entities.yaml), then fall back "
        "to the built-in.",
    )
```

```python
    @property
    def catalogue(self) -> "Catalogue":
        """The resolved catalogue. Cached — loading parses YAML and validates."""
        cached = self.__dict__.get("_catalogue")
        if cached is None:
            from privaparse.app.catalogue import load_catalogue

            cached = load_catalogue(self.catalogue_path)
            object.__setattr__(self, "_catalogue", cached)
        return cached

    @property
    def entity_schema(self) -> dict[str, str]:
        return self.catalogue.schema()
```

Add `from typing import TYPE_CHECKING` and a guarded
`from privaparse.app.catalogue import Catalogue` import so the annotation
resolves without a module-level cycle.

- [ ] **Step 6: Read the label map from the catalogue**

In `privaparse/parser/gliner_detector.py`, replace the
`from privaparse.app.config import SCHEMA_KEY_TO_TYPE` import (line 33) with
nothing, add to `__init__` after `self.schema = settings.entity_schema`:

```python
        self._label_to_type = settings.catalogue.label_to_type()
```

and change line 161 from
`entity_type = SCHEMA_KEY_TO_TYPE.get(label.lower())` to:

```python
                entity_type = self._label_to_type.get(label.lower())
```

Line 166 loses its `EntityType(...)` wrapper: `entity_type` is already the
string the catalogue produced.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_catalogue.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `pytest`
Expected: PASS. If `tests/test_merge.py` or `tests/test_normalizer.py` fail on
`EntityType.PERSON` usage, they do not need changing — the constants class
keeps those expressions valid. A failure there means an `is` comparison was
missed in Step 4.

- [ ] **Step 9: Commit**

```bash
git add privaparse/parser/types.py privaparse/app/config.py privaparse/parser/merge.py privaparse/parser/gliner_detector.py tests/test_catalogue.py
git commit -m "refactor: entity type is a catalogue value, not an enum

Span.type becomes a plain string and the label-to-type map comes from the
catalogue. EntityType survives as a constants class so code that genuinely
means the three built-in types still reads that way. Legality is checked at
the resolver boundary rather than per Span, because a Span is constructed
thousands of times per document and the check only has consequences once a
value is about to reach the vault."
```

---

### Task 3: Normalizer registry

**Files:**
- Modify: `privaparse/parser/normalizer.py`
- Modify: `privaparse/parser/entity_resolver.py:67`
- Test: `tests/test_normalizer.py` (extend)

**Interfaces:**
- Consumes: `registry.register_normalizer` (Task 1).
- Produces: `normalize(value: str, normalizer_name: str) -> str`; registered names `person`, `email`, `phone`, `strip_upper`, `digits`, `date_iso`, `casefold`, `identity`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_normalizer.py`:

```python
import pytest

from privaparse.parser.normalizer import normalize


@pytest.mark.parametrize(
    "name, raw, expected",
    [
        ("strip_upper", "DE89 3704 0044 0532 0130 00", "DE89370400440532013000"),
        ("strip_upper", "de89-3704-0044", "DE89370400 44".replace(" ", "")),
        ("digits", "4111 1111-1111 1111", "4111111111111111"),
        ("digits", "CVV: 123", "123"),
        ("casefold", "  Musterstraße   5 ", "musterstraße 5"),
        ("identity", "  Sk-Live-XYZ ", "  Sk-Live-XYZ "),
        ("date_iso", "12.03.2026", "2026-03-12"),
        ("date_iso", "2026-03-12", "2026-03-12"),
        ("date_iso", "12. Maerz 2026", "12. maerz 2026"),
    ],
)
def test_registered_normalizers(name, raw, expected):
    assert normalize(raw, name) == expected


def test_two_spellings_of_one_iban_collide():
    assert normalize("DE89 3704 0044", "strip_upper") == normalize("de89370400.44", "strip_upper")


def test_two_distinct_ibans_do_not_collide():
    assert normalize("DE89 3704", "strip_upper") != normalize("DE89 3705", "strip_upper")


def test_unknown_normalizer_name_raises():
    with pytest.raises(KeyError, match="normalizer"):
        normalize("x", "does_not_exist")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_normalizer.py -v`
Expected: FAIL — `normalize()` takes an entity type, not a normalizer name.

- [ ] **Step 3: Rewrite the dispatch**

In `privaparse/parser/normalizer.py`, replace `normalize()` (lines 68–74) with
the registry dispatch, and add the four normalizers the widened catalogue
needs. `person`, `email`, `phone` and `casefold` are already registered by
Task 1 — leave them alone. Registering a name twice with a different function
raises, which is the registry doing its job.

```python
_NON_DIGIT_RE = re.compile(r"\D")
_STRIP_RE = re.compile(r"[\s.\-/]")
_DATE_DMY_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$")
_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def normalize(value: str, normalizer: str) -> str:
    """Map a surface form onto its vault key using the named normalizer."""
    return registry.get_normalizer(normalizer)(value)


@register_normalizer("strip_upper")
def normalize_strip_upper(value: str) -> str:
    """Whitespace, dots, hyphens and slashes removed, then upper-cased.

    For values whose spacing is presentational: an IBAN printed in groups of
    four and the same IBAN printed solid are one value, and giving them two
    placeholders would make the document read as if two accounts were involved.
    """
    return _STRIP_RE.sub("", unicodedata.normalize("NFKC", value)).upper()


@register_normalizer("digits")
def normalize_digits(value: str) -> str:
    return _NON_DIGIT_RE.sub("", value)


@register_normalizer("identity")
def normalize_identity(value: str) -> str:
    """Unchanged. Only for irreversible types, whose key is hashed anyway."""
    return value


@register_normalizer("date_iso")
def normalize_date_iso(value: str) -> str:
    """``YYYY-MM-DD`` for the numeric forms, casefold for anything else.

    Deliberately shallow: month names vary by language and a wrong parse would
    merge two different dates onto one placeholder, which is worse than leaving
    "12. Maerz 2026" and "2026-03-12" as separate entries.
    """
    text = _WHITESPACE_RE.sub(" ", value).strip()
    match = _DATE_DMY_RE.match(text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    match = _DATE_ISO_RE.match(text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return text.casefold()
```

Add `from privaparse.parser import registry` at the top (the
`register_normalizer` import is already there from Task 1) and drop the
now-unused `EntityType` import. Add the four new function names to `__all__`.

- [ ] **Step 4: Update the one caller**

In `privaparse/parser/entity_resolver.py:67`, the call becomes catalogue-driven.
Task 7 rewrites this method fully; for now, change the signature of
`EntityResolver.__init__` to take the catalogue and use it:

```python
    def __init__(self, repo: VaultRepository, catalogue: "Catalogue") -> None:
        self.repo = repo
        self.catalogue = catalogue
```

```python
            placeholder_type = self.catalogue.get(span.type)
            normalized = normalize(span.text, placeholder_type.normalizer)
```

Update the construction site in `pseudonymizer.py:90` to
`EntityResolver(repo, settings.catalogue)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_normalizer.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add privaparse/parser/normalizer.py privaparse/parser/entity_resolver.py privaparse/parser/pseudonymizer.py tests/test_normalizer.py
git commit -m "feat: normalizers are named registry entries

normalize() dispatched on three enum members, which made a fourth type a
change to this module. It now takes a name the catalogue supplies. Five new
normalizers, each with the same tension the module docstring already names:
too little fragments one value across placeholders, too much merges two
values into one. date_iso stays shallow on purpose — a wrong month-name parse
merges two dates, which is worse than leaving them separate."
```

---

### Task 4: Validator registry — the model's veto

**Files:**
- Create: `privaparse/parser/validators.py`
- Modify: `privaparse/parser/registry.py` (import line in `load_builtins`)
- Modify: `privaparse/app/entities.default.yaml` (add `validator:` to EMAIL and PHONE)
- Modify: `privaparse/parser/merge.py:155-179`
- Test: `tests/test_validators.py`

**Interfaces:**
- Consumes: `registry.register_validator` (Task 1), `Catalogue` (Task 1).
- Produces: registered validators `email_syntax`, `phone_shape`, `iban_mod97`, `luhn`, `tax_de`, `blz_de`, `postal_de`, `ip_parse`, `expiry_shape`, `cvv_shape`. `merge_spans(..., catalogue=...)` and `resolve_spans(..., catalogue=...)` gain a keyword-only `catalogue` argument.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_validators.py`:

```python
from __future__ import annotations

import pytest

from privaparse.parser import registry

VECTORS = [
    # (validator, value, expected)
    ("iban_mod97", "DE89370400440532013000", True),
    ("iban_mod97", "DE89 3704 0044 0532 0130 00", True),
    # Correct length and country, wrong check digits — the case a length-only
    # rule would wave through.
    ("iban_mod97", "DE90370400440532013000", False),
    ("iban_mod97", "DE8937040044053201300", False),
    ("luhn", "4111111111111111", True),
    ("luhn", "4111 1111 1111 1111", True),
    ("luhn", "4111111111111112", False),
    # Passes Luhn but is far too short to be a card.
    ("luhn", "18", False),
    ("tax_de", "36574261809", True),
    ("tax_de", "36574261808", False),
    ("tax_de", "12345678901", False),
    ("blz_de", "37040044", True),
    ("blz_de", "3704004", False),
    ("postal_de", "50667", True),
    ("postal_de", "5066", False),
    ("ip_parse", "192.168.0.1", True),
    ("ip_parse", "2001:db8::1", True),
    ("ip_parse", "999.1.1.1", False),
    ("ip_parse", "1.2.3", False),
    ("expiry_shape", "03/28", True),
    ("expiry_shape", "03/2028", True),
    ("expiry_shape", "13/28", False),
    ("expiry_shape", "März", False),
    ("cvv_shape", "123", True),
    ("cvv_shape", "1234", True),
    ("cvv_shape", "12", False),
    ("cvv_shape", "12a", False),
    ("email_syntax", "max@test.de", True),
    ("email_syntax", "Systemmail", False),
    ("phone_shape", "+49 170 1234567", True),
    ("phone_shape", "2024", False),
]


@pytest.mark.parametrize("name, value, expected", VECTORS)
def test_validator_vectors(name, value, expected):
    assert registry.get_validator(name)(value) is expected


def test_every_catalogue_validator_is_registered():
    from privaparse.app.catalogue import load_catalogue

    known = registry.known_validators()
    for placeholder in load_catalogue().types.values():
        if placeholder.validator is not None:
            assert placeholder.validator in known
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_validators.py -v`
Expected: FAIL — `KeyError: unknown validator 'iban_mod97'`.

- [ ] **Step 3: Write the validators**

Create `privaparse/parser/validators.py`. Add it to the import line in
`registry.load_builtins`, which currently imports `normalizer` alone:

```python
    from privaparse.parser import normalizer, validators  # noqa: F401
```

Then, in the same file, declare the two vetoes in the default catalogue —
`EMAIL` gains `validator: builtin:email_syntax` and `PHONE` gains
`validator: builtin:phone_shape` in
`privaparse/app/entities.default.yaml`. Task 1 left those lines out because
the registry was empty; it no longer is.

The module:

```python
"""Checksum and syntax vetoes over what the model proposes.

The model decides what a span *is*; these decide whether that claim is
possible. They apply only to types whose syntax is fully decidable, and only
to spans the model produced — a backstop span is exact by construction.

There is deliberately no validator for SECRET, USERNAME, ADDRESS or PERSON. No
rule separates an API key from a random string, and a veto there would discard
real credentials. Those types are governed by their threshold alone.

At the precision the model reports across its full label set, these are the
highest-leverage precision mechanism in the pipeline, because unlike a raised
threshold they cost no recall at all.
"""

from __future__ import annotations

import ipaddress
import re

from privaparse.parser.detector import is_plausible_phone, is_valid_email
from privaparse.parser.registry import register_validator

_SEPARATORS_RE = re.compile(r"[\s.\-/]")
_DIGITS_ONLY_RE = re.compile(r"^\d+$")
_EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])\s*[/\-.]\s*(\d{2}|\d{4})$")
_CVV_RE = re.compile(r"^\d{3,4}$")

register_validator("email_syntax")(is_valid_email)
register_validator("phone_shape")(is_plausible_phone)


def _compact(value: str) -> str:
    return _SEPARATORS_RE.sub("", value).upper()


@register_validator("iban_mod97")
def is_valid_iban(value: str) -> bool:
    """ISO 7064 mod-97-10.

    Length alone is not enough: a transposed pair of digits keeps the length
    and fails the checksum, and that is exactly the kind of near-miss a model
    produces when it grabs one character too many.
    """
    compact = _compact(value)
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = ""
    for character in rearranged:
        if character.isdigit():
            digits += character
        elif character.isalpha():
            digits += str(ord(character) - ord("A") + 10)
        else:
            return False
    return int(digits) % 97 == 1


@register_validator("luhn")
def is_valid_card(value: str) -> bool:
    compact = _compact(value)
    if not _DIGITS_ONLY_RE.match(compact) or not 12 <= len(compact) <= 19:
        return False
    total = 0
    for index, character in enumerate(reversed(compact)):
        digit = int(character)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


@register_validator("tax_de")
def is_valid_tax_de(value: str) -> bool:
    """German Steuer-Identifikationsnummer: 11 digits, ISO 7064 MOD 11,10.

    The digit-repetition rule is checked too — exactly one digit appears twice
    or three times in the first ten, and that alone rejects most sequences a
    model mistakes for a tax number.
    """
    compact = _compact(value)
    if not _DIGITS_ONLY_RE.match(compact) or len(compact) != 11:
        return False

    counts: dict[str, int] = {}
    for character in compact[:10]:
        counts[character] = counts.get(character, 0) + 1
    repeated = [count for count in counts.values() if count > 1]
    if len(counts) not in (9, 8) or len(repeated) != 1:
        return False

    remainder = 10
    for character in compact[:10]:
        total = (int(character) + remainder) % 10 or 10
        remainder = (2 * total) % 11
    check = (11 - remainder) % 10
    return check == int(compact[10])


@register_validator("blz_de")
def is_valid_blz(value: str) -> bool:
    compact = _compact(value)
    return bool(_DIGITS_ONLY_RE.match(compact)) and len(compact) == 8


@register_validator("postal_de")
def is_valid_postal_de(value: str) -> bool:
    compact = _compact(value)
    return bool(_DIGITS_ONLY_RE.match(compact)) and len(compact) == 5


@register_validator("ip_parse")
def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return True


@register_validator("expiry_shape")
def is_valid_expiry(value: str) -> bool:
    return _EXPIRY_RE.match(value.strip()) is not None


@register_validator("cvv_shape")
def is_valid_cvv(value: str) -> bool:
    return _CVV_RE.match(value.strip()) is not None
```

- [ ] **Step 4: Run the validator tests**

Run: `pytest tests/test_validators.py -v`
Expected: PASS, 31 tests.

- [ ] **Step 5: Wire the veto into merge**

In `privaparse/parser/merge.py`, replace `_passes_rule_check` (lines 155–179):

```python
def _passes_rule_check(span: Span, catalogue: "Catalogue | None") -> bool:
    """Reject model proposals that are provably not what they claim to be.

    Only the model is second-guessed. A backstop span came from the rule
    itself, so re-checking it would be checking a rule against itself.

    Types without a validator — PERSON, ADDRESS, SECRET, USERNAME — have no
    decidable rule and are left to their threshold. The asymmetry that runs
    through the whole tool applies: a missed entity leaves the machine, a
    spurious one only costs readability.
    """
    if span.source != SOURCE_GLINER or catalogue is None:
        return True
    placeholder = catalogue.types.get(span.type)
    if placeholder is None or placeholder.validator is None:
        return True
    return bool(registry.get_validator(placeholder.validator)(span.text))
```

Thread the catalogue through: add `catalogue: "Catalogue | None" = None` as a
keyword-only parameter to `merge_spans` and `resolve_spans`, pass it from
`resolve_spans` into both `merge_spans` calls, and pass it at the two call
sites — `engine.py:107` and `pseudonymizer.py:82` — as
`catalogue=self.settings.catalogue` / `catalogue=settings.catalogue`.

The default of `None` keeps every existing test that calls `merge_spans`
directly working without an argument, and means "no vetoes", which is the
pre-existing behaviour for types that had none.

Add the imports: `from privaparse.parser import registry` and a
`TYPE_CHECKING`-guarded `from privaparse.app.catalogue import Catalogue`.

- [ ] **Step 6: Write the veto test**

Append to `tests/test_validators.py`:

```python
def test_model_span_failing_its_validator_is_dropped():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import merge_spans
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Bitte an Systemmail senden."
    protected = protect(text)
    bogus = Span(start=9, end=19, text="Systemmail", type="EMAIL",
                 score=0.99, source=SOURCE_GLINER)

    kept = merge_spans([bogus], protected=protected, catalogue=load_catalogue())
    assert kept == []


def test_backstop_span_is_not_second_guessed():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import merge_spans
    from privaparse.parser.types import SOURCE_REGEX, Span

    text = "Bitte an Systemmail senden."
    protected = protect(text)
    exact = Span(start=9, end=19, text="Systemmail", type="EMAIL",
                 score=1.0, source=SOURCE_REGEX)

    kept = merge_spans([exact], protected=protected, catalogue=load_catalogue())
    assert len(kept) == 1
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_validators.py tests/test_merge.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add privaparse/parser/validators.py privaparse/parser/merge.py privaparse/engine.py privaparse/parser/pseudonymizer.py tests/test_validators.py
git commit -m "feat: checksum validators veto model spans

The model decides what a span is; a validator decides whether that claim is
possible. Ten builtins, applied only to model spans of decidable types — a
backstop span came from the rule itself. No validator for SECRET, USERNAME,
ADDRESS or PERSON: no rule separates an API key from a random string, and a
veto there would discard real credentials.

Unlike a raised threshold, a checksum costs no recall, which makes this the
cheapest precision available once the label set widens."
```

---

### Task 5: Backstop registry

**Files:**
- Create: `privaparse/parser/backstops.py`
- Modify: `privaparse/parser/registry.py` (import line in `load_builtins`)
- Modify: `privaparse/app/entities.default.yaml` (add `backstop:` to EMAIL and PHONE)
- Modify: `privaparse/parser/detector.py:97-148,179-191`
- Test: `tests/test_backstops.py`

**Interfaces:**
- Consumes: `registry.register_backstop` (Task 1), `Catalogue` (Task 1).
- Produces: registered backstops `email`, `phone`, `iban`, `card`, `ip`, `vat_de`, each `(text: str) -> list[Span]` with `source=SOURCE_REGEX` and `type` left empty for the caller to stamp. `RegexDetector(catalogue, phone_region="DE")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backstops.py`:

```python
from __future__ import annotations

from privaparse.app.catalogue import load_catalogue
from privaparse.parser import registry
from privaparse.parser.detector import RegexDetector

TEXT = (
    "IBAN DE89 3704 0044 0532 0130 00, Karte 4111 1111 1111 1111, "
    "Server 192.168.0.1, USt-IdNr DE123456789, mail max@test.de, "
    "Tel +49 170 1234567. Bestellnummer 4711 vom 12.03.2026."
)


def _texts(name: str) -> list[str]:
    return [span.text for span in registry.get_backstop(name)(TEXT)]


def test_iban_backstop_finds_the_iban_and_nothing_else():
    assert _texts("iban") == ["DE89 3704 0044 0532 0130 00"]


def test_card_backstop_finds_the_card_and_not_the_order_number():
    found = _texts("card")
    assert "4111 1111 1111 1111" in found
    assert "4711" not in found


def test_ip_backstop_finds_the_address_and_not_the_date():
    found = _texts("ip")
    assert found == ["192.168.0.1"]


def test_vat_backstop_finds_the_vat_id():
    assert _texts("vat_de") == ["DE123456789"]


def test_backstops_only_run_for_enabled_types(tmp_path):
    override = tmp_path / "privaparse.entities.yaml"
    override.write_text(
        "version: 1\nplaceholder_types:\n  EMAIL:\n    enabled: false\n", encoding="utf-8"
    )
    detector = RegexDetector(load_catalogue(override))
    types = {span.type for span in detector.detect(TEXT)}
    assert "EMAIL" not in types
    assert "PHONE" in types


def test_backstop_spans_carry_the_placeholder_type():
    detector = RegexDetector(load_catalogue())
    for span in detector.detect(TEXT):
        assert span.type in {"EMAIL", "PHONE"}
        assert span.verify_against(TEXT)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_backstops.py -v`
Expected: FAIL — the stub backstops return `[]` and `RegexDetector` takes no catalogue.

- [ ] **Step 3: Write the backstops**

Create `privaparse/parser/backstops.py`. Add it to `registry.load_builtins`:

```python
    from privaparse.parser import backstops, normalizer, validators  # noqa: F401
```

and declare the two finders in `privaparse/app/entities.default.yaml` —
`EMAIL` gains `backstop: builtin:email`, `PHONE` gains
`backstop: builtin:phone`. Tasks 1 and 4 left these out because the registry
was empty.

The module:

```python
"""Regex finders that run alongside the model.

Their job is recall, not authority: a backstop span survives only where no
model span overlaps it. Every one of them is exact by construction, so the
validators in ``validators.py`` never second-guess them.

Each returns spans with an empty ``type``; the detector stamps the placeholder
type from the catalogue, because the same finder can serve a type the user
renamed.
"""

from __future__ import annotations

import re

import phonenumbers

from privaparse.parser.detector import _EMAIL_RE
from privaparse.parser.registry import register_backstop
from privaparse.parser.types import SOURCE_REGEX, Span
from privaparse.parser.validators import is_valid_card, is_valid_iban

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){2,8}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,19}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")
_VAT_DE_RE = re.compile(r"\bDE\d{9}\b")

#: Phone matching uses STRICT_GROUPING, not VALID. The lenient level reads
#: German dates (04.06.2024) as phone numbers — measured on the gold set it
#: cost three false positives and 0.14 precision while finding no real numbers.
_PHONE_LENIENCY = phonenumbers.Leniency.STRICT_GROUPING


def _span(text: str, start: int, end: int) -> Span:
    return Span(start=start, end=end, text=text[start:end], type="", score=1.0,
                source=SOURCE_REGEX)


def _matches(text: str, pattern: re.Pattern[str], check=None) -> list[Span]:
    out: list[Span] = []
    for match in pattern.finditer(text):
        if check is not None and not check(match.group(0)):
            continue
        out.append(_span(text, match.start(), match.end()))
    return out


@register_backstop("email")
def find_emails(text: str) -> list[Span]:
    return _matches(text, _EMAIL_RE)


@register_backstop("phone")
def find_phones(text: str, region: str = "DE") -> list[Span]:
    found: dict[tuple[int, int], Span] = {}
    for match in phonenumbers.PhoneNumberMatcher(text, region, leniency=_PHONE_LENIENCY):
        found.setdefault((match.start, match.end), _span(text, match.start, match.end))
    return list(found.values())


@register_backstop("iban")
def find_ibans(text: str) -> list[Span]:
    """Checksum-gated. The pattern alone matches far too much."""
    return _matches(text, _IBAN_RE, is_valid_iban)


@register_backstop("card")
def find_cards(text: str) -> list[Span]:
    """Luhn-gated, which is what keeps order and invoice numbers out."""
    return _matches(text, _CARD_RE, is_valid_card)


@register_backstop("ip")
def find_ips(text: str) -> list[Span]:
    from privaparse.parser.validators import is_valid_ip

    return _matches(text, _IPV4_RE, is_valid_ip) + _matches(text, _IPV6_RE, is_valid_ip)


@register_backstop("vat_de")
def find_vat_de(text: str) -> list[Span]:
    return _matches(text, _VAT_DE_RE)
```

- [ ] **Step 4: Make `RegexDetector` catalogue-driven**

In `privaparse/parser/detector.py`, replace the body of `RegexDetector`
(lines 97–148) with:

```python
class RegexDetector:
    """Runs the backstop of every enabled type that has one.

    Recall insurance, not authority. Overlap resolution in ``merge`` gives the
    model the final word; these spans survive where the model found nothing.
    """

    def __init__(self, catalogue: "Catalogue", phone_region: str = "DE") -> None:
        self.catalogue = catalogue
        self.phone_region = phone_region

    def detect(self, text: str) -> list[Span]:
        from privaparse.parser import registry

        spans: list[Span] = []
        for placeholder in self.catalogue.enabled:
            if placeholder.backstop is None:
                continue
            finder = registry.get_backstop(placeholder.backstop)
            for span in finder(text):
                # The finder does not know which type it is serving; the
                # catalogue does.
                spans.append(
                    Span(
                        start=span.start,
                        end=span.end,
                        text=span.text,
                        type=placeholder.name,
                        score=span.score,
                        source=span.source,
                    )
                )
        return spans
```

Keep `_EMAIL_RE`, `is_valid_email`, `is_valid_phone` and `is_plausible_phone`
where they are — `validators.py` and `backstops.py` both import them, and
moving them would create a cycle.

Update `build_default_detector` (lines 179–191): `RegexDetector()` becomes
`RegexDetector(settings.catalogue)` in both branches.

- [ ] **Step 5: Add `detect_many` to the protocol**

Still in `detector.py`, extend the `Detector` protocol and give the concrete
classes a default:

```python
@runtime_checkable
class Detector(Protocol):
    """Finds entity spans in text. Offsets refer to the text as given."""

    def detect(self, text: str) -> list[Span]: ...

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        """Detect over several texts. Overridden where batching actually pays."""
        return [self.detect(text) for text in texts]
```

Add the same three-line `detect_many` to `RegexDetector`, `StaticDetector` and
`CompositeDetector`. `CompositeDetector` gets the version that matters:

```python
    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        per_text: list[list[Span]] = [[] for _ in texts]
        for detector in self.detectors:
            for index, spans in enumerate(detector.detect_many(texts)):
                per_text[index].extend(spans)
        return per_text
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_backstops.py tests/test_detector_regex.py -v`
Expected: PASS. `tests/test_detector_regex.py` constructs `RegexDetector()`
with no arguments — update those constructions to `RegexDetector(load_catalogue())`.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add privaparse/parser/backstops.py privaparse/parser/detector.py tests/test_backstops.py tests/test_detector_regex.py
git commit -m "feat: backstop finders come from the catalogue

RegexDetector had email and phone wired in. It now runs the backstop of every
enabled type that declares one, and stamps the placeholder type from the
catalogue rather than from the finder — the same finder can serve a type the
user renamed.

IBAN and card finders are checksum-gated at the point of detection, which is
what keeps order and invoice numbers out of a pattern that would otherwise
match them. Detector gains detect_many with a loop default, for Task 8."
```

---

### Task 6: Merge precedence — the model decides

**Files:**
- Modify: `privaparse/parser/merge.py:39-50,108-149,195-203`
- Test: `tests/test_merge.py` (extend)

**Interfaces:**
- Consumes: `Catalogue` (Task 1), threaded `catalogue` kwarg (Task 4).
- Produces: unchanged signatures. `span_priority(span)` no longer consults type.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merge.py`:

```python
def test_model_span_wins_an_overlap_against_a_backstop():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import merge_spans
    from privaparse.parser.types import SOURCE_GLINER, SOURCE_REGEX, Span

    text = "Kontakt: max.mustermann@test.de"
    protected = protect(text)
    model = Span(9, 31, "max.mustermann@test.de", "EMAIL", 0.9, SOURCE_GLINER)
    backstop = Span(9, 31, "max.mustermann@test.de", "EMAIL", 1.0, SOURCE_REGEX)

    kept = merge_spans([backstop, model], protected=protected, catalogue=load_catalogue())
    assert len(kept) == 1
    assert kept[0].source == SOURCE_GLINER


def test_backstop_survives_where_the_model_found_nothing():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import merge_spans
    from privaparse.parser.types import SOURCE_GLINER, SOURCE_REGEX, Span

    text = "Anna schreibt an max@test.de"
    protected = protect(text)
    model = Span(0, 4, "Anna", "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(17, 27, "max@test.de", "EMAIL", 1.0, SOURCE_REGEX)

    kept = merge_spans([model, backstop], protected=protected, catalogue=load_catalogue())
    assert {s.source for s in kept} == {SOURCE_GLINER, SOURCE_REGEX}


def test_sweep_mode_off_finds_no_repeats():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.parser.markdown import protect
    from privaparse.parser.merge import coreference_sweep
    from privaparse.parser.types import SOURCE_GLINER, Span

    catalogue = load_catalogue()
    text = "Berlin ist gross. Berlin ist teuer."
    protected = protect(text)
    accepted = [Span(0, 6, "Berlin", "PERSON", 0.9, SOURCE_GLINER)]

    with_word = coreference_sweep(accepted, protected, catalogue=catalogue)
    assert len(with_word) == 1

    off = catalogue.types["PERSON"].__class__(**{**catalogue.types["PERSON"].__dict__, "sweep": "off"})
    narrowed = type(catalogue)(version=1, types={**catalogue.types, "PERSON": off})
    assert coreference_sweep(accepted, protected, catalogue=narrowed) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_merge.py -k "model_span_wins or backstop_survives or sweep_mode" -v`
Expected: FAIL — `_SOURCE_RANK` still favours regex, and `coreference_sweep` takes no catalogue.

- [ ] **Step 3: Flip the precedence**

Replace `merge.py:39-50`:

```python
#: Higher wins an overlap. The model decides: rules assist it, they do not
#: outrank it. Regex keeps both of its jobs — recall backstop here, and the
#: checksum veto in `_passes_rule_check` — and neither is "win a span the model
#: also found".
#:
#: This is a reversal. The old ranking put regex above the model, with a type
#: rank on top so an EMAIL span beat a PERSON span that had swallowed the local
#: part. That case is now handled by the `email_syntax` validator plus the
#: longest-span tie-break, which is where it belonged: a syntax rule, not a
#: standing claim that rules know better.
_SOURCE_RANK = {SOURCE_GLINER: 3, SOURCE_REGEX: 2, SOURCE_COREF: 1}


def span_priority(span: Span) -> int:
    """Higher wins an overlap."""
    return _SOURCE_RANK.get(span.source, 1)
```

Delete `_TYPE_RANK`.

- [ ] **Step 4: Make the sweep catalogue-driven**

Replace `_sweep_pattern` (lines 195–203):

```python
def _sweep_pattern(surface: str, sweep: str) -> re.Pattern[str] | None:
    """The rule for re-finding this value elsewhere in the document.

    ``off`` exists for types whose values are ordinary words. Sweeping for
    "Berlin" across a document produces more noise than protection, and the
    noise is indistinguishable from a detection failure when you read the
    output.
    """
    escaped = re.escape(surface)
    if sweep == "off":
        return None
    if sweep == "icase":
        return re.compile(rf"(?<![\w.+-]){escaped}(?![\w-])", re.IGNORECASE)
    if sweep == "exact":
        return re.compile(escaped)
    return re.compile(rf"(?<!\w){escaped}(?!\w)")
```

Give `coreference_sweep` a keyword-only `catalogue: "Catalogue | None" = None`,
and inside the loop:

```python
        mode = "word"
        if catalogue is not None and span.type in catalogue.types:
            mode = catalogue.types[span.type].sweep
        pattern = _sweep_pattern(surface, mode)
        if pattern is None:
            continue
```

Pass `catalogue=catalogue` from `resolve_spans` into `coreference_sweep`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_merge.py -v`
Expected: PASS. Existing tests that asserted regex beating the model must be
updated to the new expectation — that is the point of this task, not a
regression. Update the assertion, keep the test.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 7: Run the model tests** **[lab]**

In a lab sandbox — `pytest -m model -v`
Expected: PASS. This is the first task that can change what the real model
pipeline produces; if a round-trip test shifts, investigate before continuing.

- [ ] **Step 8: Commit**

```bash
git add privaparse/parser/merge.py tests/test_merge.py
git commit -m "feat: the model wins overlaps, rules assist

_SOURCE_RANK put regex above GLiNER, with a type rank on top so an EMAIL span
beat a PERSON span that had swallowed the local part. Both are gone. The
model decides what a span is; the backstop fills gaps and the validator
vetoes the impossible, and neither outranks it on a span both found.

The local-part case is now the email_syntax validator plus the longest-span
tie-break — a syntax rule rather than a standing claim that rules know better.

The coreference sweep pattern moves into the catalogue as `sweep`, with `off`
for types whose values are ordinary words."
```

---

### Task 7: Irreversible types

**Files:**
- Modify: `privaparse/parser/entity_resolver.py:57-91`
- Test: `tests/test_irreversible.py`

**Interfaces:**
- Consumes: `Catalogue` (Task 1), `EntityResolver(repo, catalogue)` (Task 3).
- Produces: `UnknownEntityTypeError`; `Resolution.usages` omits irreversible entities.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_irreversible.py`:

```python
from __future__ import annotations

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.database.models import Entity, EntityValue
from privaparse.parser.entity_resolver import EntityResolver, UnknownEntityTypeError
from privaparse.parser.types import SOURCE_GLINER, Span

SECRET = "sk-live-4f3a9c2b8e1d"

CATALOGUE = """
version: 1
placeholder_types:
  SECRET:
    labels: [secret]
    normalizer: identity
    sweep: exact
    reversible: false
"""


@pytest.fixture
def catalogue(tmp_path):
    target = tmp_path / "privaparse.entities.yaml"
    target.write_text(CATALOGUE, encoding="utf-8")
    return load_catalogue(target)


def test_secret_value_never_reaches_the_database(repo, catalogue):
    span = Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)
    EntityResolver(repo, catalogue).resolve([span])
    repo.session.commit()

    for entity in repo.session.query(Entity).all():
        assert SECRET not in repo.normalized_value_of(entity)
    assert repo.session.query(EntityValue).count() == 0


def test_secret_still_gets_a_stable_placeholder(repo, catalogue):
    resolver = EntityResolver(repo, catalogue)
    first = resolver.resolve([Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)])
    second = resolver.resolve([Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)])
    assert first.spans[0].placeholder == second.spans[0].placeholder


def test_irreversible_entity_records_no_usage(repo, catalogue):
    resolution = EntityResolver(repo, catalogue).resolve(
        [Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)]
    )
    assert resolution.spans
    assert resolution.usages == {}


def test_reversible_type_is_unaffected(repo):
    resolver = EntityResolver(repo, load_catalogue())
    resolution = resolver.resolve([Span(0, 14, "Max Mustermann", "PERSON", 0.9, SOURCE_GLINER)])
    assert len(resolution.usages) == 1


def test_unknown_type_fails_before_anything_is_written(repo):
    resolver = EntityResolver(repo, load_catalogue())
    with pytest.raises(UnknownEntityTypeError, match="NOPE"):
        resolver.resolve([Span(0, 3, "abc", "NOPE", 0.9, SOURCE_GLINER)])
    assert repo.session.query(Entity).count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_irreversible.py -v`
Expected: FAIL — `UnknownEntityTypeError` does not exist and secrets are stored in full.

- [ ] **Step 3: Implement**

In `privaparse/parser/entity_resolver.py`, add the error and rewrite `resolve`:

```python
class UnknownEntityTypeError(LookupError):
    """A span carries a type the catalogue does not define."""
```

```python
    def resolve(self, spans: Iterable[Span]) -> Resolution:
        """Resolve spans **in document order**.

        Order matters: the first spelling encountered in this document becomes
        the one ``reverse()`` puts back, so the restored text reads the way the
        author wrote it rather than the way some earlier document did.

        This is also where a span's type is checked against the catalogue —
        the last point before a value reaches the vault, and the first point
        where an unknown type would have consequences.
        """
        result = Resolution()

        for span in sorted(spans, key=lambda s: s.start):
            if span.type not in self.catalogue.types:
                raise UnknownEntityTypeError(
                    f"span at {span.start} claims type {span.type!r}, which the "
                    f"catalogue does not define"
                )
            placeholder_type = self.catalogue.get(span.type)
            normalized = normalize(span.text, placeholder_type.normalizer)
            if not normalized:
                log.debug("skipping span at %d: normalises to empty", span.start)
                continue

            register_secret(span.text)

            if not placeholder_type.reversible:
                self._resolve_irreversible(result, span, normalized)
                continue

            entity = self.repo.get_or_create_entity(span.type, normalized)
            value = self.repo.record_surface_form(entity, span.text)

            usage = result.usages.get(entity.id)
            if usage is None:
                result.usages[entity.id] = EntityUsage(entity=entity, restore_value=value)
            else:
                usage.occurrences += 1

            result.spans.append(
                ResolvedSpan(span=span, placeholder=entity.placeholder, entity_id=entity.id)
            )

        log.info(
            "resolved %d span(s) onto %d placeholder(s)",
            len(result.spans),
            result.placeholder_count,
        )
        return result

    def _resolve_irreversible(self, result: Resolution, span: Span, normalized: str) -> None:
        """Placeholder without a way back.

        The vault key is a digest, so the placeholder stays stable across
        documents while the value itself never reaches disk. No surface form is
        recorded and no usage is registered, so ``_persist`` writes no mapping
        entry and ``reverse()`` finds nothing — the one-way door is a
        consequence of what was written, not a flag someone can flip later.
        """
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        entity = self.repo.get_or_create_entity(span.type, digest)
        result.spans.append(
            ResolvedSpan(span=span, placeholder=entity.placeholder, entity_id=entity.id)
        )
```

Add `import hashlib` at the top.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_irreversible.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add privaparse/parser/entity_resolver.py tests/test_irreversible.py
git commit -m "feat: irreversible types never enter the vault restorably

reversible: false is not a check at restore time. The vault key becomes a
SHA-256 digest of the normalised value, no surface form is recorded and no
mapping entry is written, so reverse() finds nothing and leaves the
placeholder. The placeholder stays stable across documents; the value never
reaches disk.

A tool that stores API keys in a plaintext SQLite file and offers a restore
function is a credential store with extra steps.

The resolver is also where a span's type is now checked against the
catalogue — the last point before a value reaches the vault."
```

---

### Task 8: Batch pseudonymisation

**Files:**
- Modify: `privaparse/parser/pseudonymizer.py`
- Modify: `privaparse/parser/gliner_detector.py:108-175`
- Modify: `privaparse/engine.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `Detector.detect_many` (Task 5), `EntityResolver` (Task 7).
- Produces:
  - `BatchResult(mapping_id: str, texts: list[str], spans: list[list[ResolvedSpan]], detected: list[list[Span]])`, `.replacements -> int`, `.placeholders -> list[str]`
  - `pseudonymize_batch(texts, *, detector, repo, settings, source_name=None) -> BatchResult`
  - `PrivaParseEngine.pseudonymize_batch(texts, *, source_name=None) -> BatchResult`
  - `PrivaParseEngine.detect_raw(text) -> tuple[ProtectedText, list[Span]]`
  - `GlinerDetector.detect_many(texts) -> list[list[Span]]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch.py`:

```python
from __future__ import annotations

import pytest

from privaparse.database.placeholder import find_placeholders

TEXTS = [
    "Sehr geehrter Herr Max Mustermann,",
    "Bitte an max@test.de senden.",
    "Max Mustermann ruft unter +49 170 1234567 an.",
]


def test_one_mapping_covers_every_text(engine):
    result = engine.pseudonymize_batch(TEXTS)
    assert len({result.mapping_id}) == 1
    assert len(result.texts) == 3


def test_a_value_in_two_texts_gets_one_placeholder(engine):
    result = engine.pseudonymize_batch(TEXTS)
    first = {m.group(0) for m in find_placeholders(result.texts[0])}
    third = {m.group(0) for m in find_placeholders(result.texts[2])}
    assert first & third


def test_reverse_resolves_placeholders_from_any_text(engine):
    result = engine.pseudonymize_batch(TEXTS)
    for text in result.texts:
        restored = engine.reverse(result.mapping_id, text)
        assert restored.is_clean


def test_batch_refuses_text_that_already_contains_placeholders(engine):
    from privaparse.parser.pseudonymizer import AlreadyPseudonymizedError

    with pytest.raises(AlreadyPseudonymizedError):
        engine.pseudonymize_batch(["fine", "already [[PERSON_A1]] here"])


def test_empty_batch_creates_no_mapping(engine):
    result = engine.pseudonymize_batch([])
    assert result.texts == []
    assert result.replacements == 0


def test_detect_many_matches_detect_one_by_one(fake_detector):
    per_text = fake_detector.detect_many(TEXTS)
    assert per_text == [fake_detector.detect(text) for text in TEXTS]


def test_detect_raw_returns_unfiltered_spans(engine):
    protected, spans = engine.detect_raw(TEXTS[0])
    assert protected.original == TEXTS[0]
    assert isinstance(spans, list)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_batch.py -v`
Expected: FAIL — `PrivaParseEngine` has no `pseudonymize_batch`.

- [ ] **Step 3: Write the batch pseudonymiser**

In `privaparse/parser/pseudonymizer.py`, add:

```python
@dataclass(frozen=True)
class BatchResult:
    """Several texts, one mapping.

    The gateway in spec 2 sends one HTTP request carrying dozens of text nodes.
    They must share a mapping: the model's answer mixes placeholders from all
    of them, and ``reverse()`` resolves against exactly one session.
    """

    mapping_id: str
    texts: list[str] = field(default_factory=list)
    spans: list[list[ResolvedSpan]] = field(default_factory=list)
    detected: list[list[Span]] = field(default_factory=list)

    @property
    def replacements(self) -> int:
        return sum(len(group) for group in self.spans)

    @property
    def placeholders(self) -> list[str]:
        seen: dict[str, None] = {}
        for group in self.spans:
            for resolved in group:
                seen.setdefault(resolved.placeholder, None)
        return list(seen)


def pseudonymize_batch(
    texts: Sequence[str],
    *,
    detector: Detector,
    repo: VaultRepository,
    settings: "Settings",
    source_name: str | None = None,
) -> BatchResult:
    """Pseudonymise several texts under one mapping, in one transaction.

    Detection runs across all of them in a single call so the model batches;
    resolution runs in text order so the first spelling seen still wins, the
    same rule single-text pseudonymisation follows.
    """
    for index, text in enumerate(texts):
        if contains_placeholder(text):
            raise AlreadyPseudonymizedError(
                f"text {index} already contains PrivaParse placeholders. "
                f"Pseudonymising it again would nest placeholders and make the "
                f"result irreversible."
            )

    if not texts:
        return BatchResult(mapping_id="")

    protected = [protect(text, scan_code=settings.scan_code) for text in texts]
    raw = detector.detect_many([p.view for p in protected])

    per_text_spans = [
        resolve_spans(
            protected[index],
            raw[index],
            threshold=settings.threshold,
            sweep=settings.coreference_sweep,
            catalogue=settings.catalogue,
        )
        for index in range(len(texts))
    ]
    for text, spans in zip(texts, per_text_spans):
        _verify_spans(text, spans)

    resolver = EntityResolver(repo, settings.catalogue)
    resolutions = [resolver.resolve(spans) for spans in per_text_spans]
    new_texts = [
        apply_replacements(text, resolution.spans)
        for text, resolution in zip(texts, resolutions)
    ]

    digest = hashlib.sha256("\u0000".join(texts).encode("utf-8")).hexdigest()
    mapping = repo.create_mapping(text_sha256=digest, source_name=source_name)
    merged: dict[str, EntityUsage] = {}
    for resolution in resolutions:
        for entity_id, usage in resolution.usages.items():
            existing = merged.get(entity_id)
            if existing is None:
                merged[entity_id] = usage
            else:
                existing.occurrences += usage.occurrences
    for usage in merged.values():
        repo.add_mapping_entry(mapping, usage.entity, usage.restore_value,
                               occurrences=usage.occurrences)
    repo.session.commit()

    log.info(
        "pseudonymised %d text(s) as %s: %d replacement(s), %d placeholder(s)",
        len(texts),
        source_name or "<batch>",
        sum(len(r.spans) for r in resolutions),
        len(merged),
    )
    return BatchResult(
        mapping_id=mapping.id,
        texts=new_texts,
        spans=[r.spans for r in resolutions],
        detected=per_text_spans,
    )
```

Add `EntityUsage` to the `entity_resolver` import and `Sequence` to the
`typing` import. Rewrite `pseudonymize_text` to delegate:

```python
def pseudonymize_text(
    text: str,
    *,
    detector: Detector,
    repo: VaultRepository,
    settings: "Settings",
    source_name: str | None = None,
) -> PseudonymizationResult:
    """Detect, replace and persist, as one transaction."""
    batch = pseudonymize_batch(
        [text], detector=detector, repo=repo, settings=settings, source_name=source_name
    )
    return PseudonymizationResult(
        text=batch.texts[0],
        mapping_id=batch.mapping_id,
        spans=batch.spans[0],
        detected=batch.detected[0],
    )
```

The `text_sha256` for a one-element batch is now the digest of the text with no
separator appended, which matches the old value — `"\u0000".join(["x"]) == "x"`.

- [ ] **Step 4: Batch the model**

In `privaparse/parser/gliner_detector.py`, add:

```python
    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        """One model batch across every text.

        Chunking already happens per text; this flattens all the chunks into
        one submission so a request carrying fifty short strings costs one
        batched pass rather than fifty single-chunk ones.
        """
        chunk_groups = [
            chunk_text(text, self.settings.chunk_chars) if text.strip() else []
            for text in texts
        ]
        flat: list[Chunk] = [chunk for group in chunk_groups for chunk in group]
        if not flat:
            return [[] for _ in texts]

        results = self._extract(flat)

        out: list[list[Span]] = []
        cursor = 0
        for text, group in zip(texts, chunk_groups):
            if not group:
                out.append([])
                continue
            slice_ = results[cursor : cursor + len(group)]
            cursor += len(group)
            out.append(self._to_spans(slice_, group, text))
        return out
```

Change `detect` to `return self.detect_many([text])[0]`.

- [ ] **Step 5: Add the engine methods**

In `privaparse/engine.py`:

```python
    def pseudonymize_batch(
        self, texts: "Sequence[str]", *, source_name: str | None = None
    ) -> "BatchResult":
        """Pseudonymise several texts under one mapping."""
        from privaparse.parser.pseudonymizer import pseudonymize_batch

        with self.database.session() as session:
            return pseudonymize_batch(
                texts,
                detector=self.detector,
                repo=self.repository(session),
                settings=self.settings,
                source_name=source_name,
            )

    def detect_raw(self, text: str) -> "tuple[ProtectedText, list[Span]]":
        """Masked text plus unfiltered detector output.

        The threshold sweep needs the model's scores before merging drops
        anything, so one expensive pass can produce every point on the curve.
        """
        from privaparse.parser.markdown import protect

        protected = protect(text, scan_code=self.settings.scan_code)
        return protected, self.detector.detect(protected.view)
```

Add `catalogue` as a convenience property:

```python
    @property
    def catalogue(self):
        return self.settings.catalogue
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_batch.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 7: Run the full suite, then the model tests** **[lab]**

Locally: `pytest`
In a lab sandbox: `pytest -m model -v`
Expected: PASS both. `pseudonymize_text` now routes through the batch path, so
a failure in `tests/test_roundtrip.py` or `tests/test_session_scope.py` means
the delegation changed behaviour — fix the delegation, not the test.

- [ ] **Step 8: Commit**

```bash
git add privaparse/parser/pseudonymizer.py privaparse/parser/gliner_detector.py privaparse/engine.py tests/test_batch.py
git commit -m "feat: pseudonymize several texts under one mapping

Every pseudonymize() call created its own mapping session. A request carrying
twenty text nodes would create twenty, and reverse() resolves against exactly
one — so the model's answer, which mixes placeholders from all of them, could
not be restored.

pseudonymize_batch detects across every text in one model pass and records one
mapping. pseudonymize_text delegates to it, so there is one code path rather
than two that drift.

detect_raw exposes unfiltered detector output for the threshold sweep."
```

---

### Task 9: The full catalogue and the `catalog` command

This is the task that changes what PrivaParse detects. Everything before it was
mechanism.

**Files:**
- Modify: `privaparse/app/entities.default.yaml`
- Modify: `privaparse/app/main.py`
- Test: `tests/test_catalogue.py` (extend), `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: `privaparse catalog show`, `privaparse catalog validate [FILE]`; `doctor` gains catalogue lines.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalogue.py`:

```python
ALL_42 = 42


def test_default_catalogue_routes_every_model_label():
    from privaparse.app.catalogue import MODEL_LABELS, load_catalogue

    catalogue = load_catalogue()
    routed = set(catalogue.label_to_type())
    assert routed == set(MODEL_LABELS)
    assert len(routed) == ALL_42


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
```

Append to `tests/test_cli.py`:

```python
def test_catalog_show_lists_types(runner):
    result = runner.invoke(app, ["catalog", "show"])
    assert result.exit_code == 0
    assert "PERSON" in result.stdout
    assert "SECRET" in result.stdout


def test_catalog_show_prints_no_prompts_or_values(runner):
    result = runner.invoke(app, ["catalog", "show"])
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_catalogue.py tests/test_cli.py -k "catalog or 42 or 25 or irreversible" -v`
Expected: FAIL — the catalogue has three types and there is no `catalog` command.

- [ ] **Step 3: Write the full catalogue**

Replace `privaparse/app/entities.default.yaml`. Keep the header comment from
Task 1 Step 6 and the three existing types with their `bar` values, then add
the rest. Thresholds are **conservative placeholders to be replaced by Task 11's
sweep** — the value written here is a starting point, not a measurement, and
the README must not present it as one.

```yaml
  DATE_OF_BIRTH:
    labels: [date_of_birth]
    prompts: {date_of_birth: "Geburtsdatum einer Person"}
    normalizer: date_iso
    sweep: exact
    threshold: 0.6

  ADDRESS:
    labels: [address, street_address]
    prompts:
      address: "Vollstaendige Anschrift mit Strasse, Hausnummer und Ort"
      street_address: "Strasse und Hausnummer"
    normalizer: casefold
    sweep: word
    threshold: 0.6

  CITY:
    labels: [city]
    prompts: {city: "Staedte und Gemeinden"}
    normalizer: casefold
    sweep: "off"
    threshold: 0.7

  REGION:
    labels: [state_or_region]
    prompts: {state_or_region: "Bundeslaender, Regionen, Kantone"}
    normalizer: casefold
    sweep: "off"
    threshold: 0.7

  POSTAL_CODE:
    labels: [postal_code]
    prompts: {postal_code: "Postleitzahlen"}
    normalizer: digits
    validator: builtin:postal_de
    sweep: exact
    threshold: 0.6

  COUNTRY:
    labels: [country]
    prompts: {country: "Laendernamen"}
    normalizer: casefold
    sweep: "off"
    threshold: 0.8

  NATIONAL_ID:
    labels: [government_id, national_id_number]
    prompts:
      government_id: "Amtliche Ausweis- und Identifikationsnummern"
      national_id_number: "Nationale Identifikationsnummer"
    normalizer: strip_upper
    sweep: exact
    threshold: 0.6

  PASSPORT:
    labels: [passport_number]
    prompts: {passport_number: "Reisepassnummern"}
    normalizer: strip_upper
    sweep: exact
    threshold: 0.6

  DRIVERS_LICENSE:
    labels: [drivers_license_number]
    prompts: {drivers_license_number: "Fuehrerscheinnummern"}
    normalizer: strip_upper
    sweep: exact
    threshold: 0.6

  LICENSE_NUMBER:
    labels: [license_number]
    prompts: {license_number: "Lizenz- und Zulassungsnummern"}
    normalizer: strip_upper
    sweep: exact
    threshold: 0.7

  TAX_ID:
    labels: [tax_id, tax_number]
    prompts:
      tax_id: "Steuer-Identifikationsnummer"
      tax_number: "Steuernummer, Umsatzsteuer-Identifikationsnummer"
    normalizer: strip_upper
    validator: builtin:tax_de
    backstop: builtin:vat_de
    sweep: exact
    threshold: 0.5

  ACCOUNT_NUMBER:
    labels: [bank_account, account_number]
    prompts:
      bank_account: "Bankkontonummern"
      account_number: "Kontonummern"
    normalizer: strip_upper
    sweep: exact
    threshold: 0.6

  ROUTING_NUMBER:
    labels: [routing_number]
    prompts: {routing_number: "Bankleitzahlen, BIC, Routing-Nummern"}
    normalizer: digits
    validator: builtin:blz_de
    sweep: exact
    threshold: 0.6

  IBAN:
    labels: [iban]
    prompts: {iban: "IBAN-Kontonummern"}
    normalizer: strip_upper
    validator: builtin:iban_mod97
    backstop: builtin:iban
    sweep: exact
    threshold: 0.5
    bar: { precision: 0.95, recall: 0.95 }

  CARD:
    labels: [payment_card, card_number]
    prompts:
      payment_card: "Kreditkarten- und Zahlungskartennummern"
      card_number: "Kartennummern"
    normalizer: digits
    validator: builtin:luhn
    backstop: builtin:card
    sweep: exact
    threshold: 0.5
    reversible: false
    bar: { precision: 0.95, recall: 0.95 }

  CARD_EXPIRY:
    labels: [card_expiry]
    prompts: {card_expiry: "Gueltigkeitsdatum einer Zahlungskarte"}
    normalizer: date_iso
    validator: builtin:expiry_shape
    sweep: exact
    threshold: 0.6

  CARD_CVV:
    labels: [card_cvv]
    prompts: {card_cvv: "Kartenpruefnummer, CVV, CVC"}
    normalizer: digits
    validator: builtin:cvv_shape
    sweep: exact
    threshold: 0.6
    reversible: false

  USERNAME:
    labels: [username]
    prompts: {username: "Benutzernamen und Anmeldenamen"}
    normalizer: casefold
    sweep: word
    threshold: 0.8

  IP:
    labels: [ip_address]
    prompts: {ip_address: "IPv4- und IPv6-Adressen"}
    normalizer: casefold
    validator: builtin:ip_parse
    backstop: builtin:ip
    sweep: exact
    threshold: 0.5

  ACCOUNT_ID:
    labels: [account_id, sensitive_account_id]
    prompts:
      account_id: "Kunden-, Konto- und Mandantennummern"
      sensitive_account_id: "Besonders schutzwuerdige Kontokennungen"
    normalizer: casefold
    sweep: exact
    threshold: 0.7

  SECRET:
    labels: [password, secret, api_key, access_token, recovery_code]
    prompts:
      password: "Passwoerter"
      secret: "Geheimnisse und vertrauliche Zeichenketten"
      api_key: "API-Schluessel"
      access_token: "Zugriffstoken"
      recovery_code: "Wiederherstellungscodes"
    normalizer: identity
    sweep: exact
    threshold: 0.8
    reversible: false

  DATE:
    labels: [sensitive_date, document_date, expiration_date, transaction_date]
    prompts:
      sensitive_date: "Datumsangaben, die eine Person betreffen"
      document_date: "Datum eines Dokuments"
      expiration_date: "Ablaufdatum"
      transaction_date: "Datum einer Transaktion"
    normalizer: date_iso
    sweep: exact
    threshold: 0.8
```

Note the two deviations from "reversible everywhere" and why they are here
rather than in a later task: `CARD` and `CARD_CVV` join `SECRET` as
irreversible. A restored card number in an LLM answer is the same failure mode
as a restored API key, and the mechanism for it already landed in Task 7.

- [ ] **Step 4: Add the `catalog` command**

In `privaparse/app/main.py`, after the `vault_app` definition:

```python
catalog_app = typer.Typer(help="Inspect and check the entity catalogue.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
```

```python
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
```

In `doctor`, after the `scan code` line:

```python
    catalogue = settings.catalogue
    source = settings.catalogue_path or "built-in + discovered"
    typer.echo(f"catalogue  {source}")
    typer.echo(
        f"           {len(catalogue.enabled)} type(s), {len(catalogue.schema())} label(s)"
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_catalogue.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 7: Measure the throughput cost** **[lab]**

In a lab sandbox, on the same device class the README's figures came from:

```bash
privaparse bench --repeats 3
```

Copy `docs/bench-report.md` back out of the sandbox. Compare against the
figures already in the README (163 docs/s, 5.8 ms p50, ~53 KB/s on an
RTX 3060). This is the measurement the spec flags as an open risk: 3 → 42
labels is roughly fourteen times the label encoding per chunk.

Do not tune anything in response yet — Task 11's sweep comes first, and a
threshold change moves throughput too. If throughput has dropped by more than
an order of magnitude, stop and report before continuing; the lever is
`enabled: false` on the noisiest types, and that is a decision, not a fix.

- [ ] **Step 8: Commit**

```bash
git add privaparse/app/entities.default.yaml privaparse/app/main.py tests/test_catalogue.py tests/test_cli.py
git commit -m "feat: route all 42 model labels onto 25 placeholder types

Granularity errs high on purpose. Merging two types later rewrites a column
and de-duplicates rows; splitting one later is impossible, because what would
separate them was discarded at write time. SECRET is the exception and merges
five labels — every one of them is irreversible and none is distinguished by
anything downstream.

CARD and CARD_CVV join SECRET as irreversible. A restored card number in a
model answer is the same failure as a restored API key.

The thresholds in this file are conservative starting points, not
measurements. The sweep replaces them."
```

---

### Task 10: Catalogue-driven evaluation

**Files:**
- Modify: `privaparse/evaluation/harness.py`
- Modify: `privaparse/parser/types.py` (add `Span.label`)
- Modify: `privaparse/parser/gliner_detector.py:203-210`
- Test: `tests/test_eval_harness.py` (extend)

**Interfaces:**
- Consumes: `Catalogue` (Task 1).
- Produces:
  - `Span.label: str | None` — the model label a span came from, `None` for backstop and sweep spans.
  - `evaluate(detector, documents, *, label, catalogue) -> EvalReport`
  - `EvalReport.by_label: dict[str, Counts]`, `EvalReport.verdicts() -> list[tuple[str, bool, str]]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval_harness.py`:

```python
def test_report_covers_every_enabled_type():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.evaluation.harness import GoldDocument, evaluate
    from privaparse.parser.detector import StaticDetector

    catalogue = load_catalogue()
    report = evaluate(
        StaticDetector(), [GoldDocument("d1", "brief", "leer", ())],
        label="empty", catalogue=catalogue,
    )
    assert set(report.partial) == {t.name for t in catalogue.enabled}


def test_per_label_counts_attribute_false_positives():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.evaluation.harness import GoldDocument, evaluate
    from privaparse.parser.detector import StaticDetector
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Der Vorgang laeuft."
    spurious = Span(4, 11, "Vorgang", "PERSON", 0.9, SOURCE_GLINER, label="first_name")
    report = evaluate(
        StaticDetector([spurious]), [GoldDocument("d1", "notiz", text, ())],
        label="one", catalogue=load_catalogue(),
    )
    assert report.by_label["first_name"].fp == 1
    assert report.by_label["first_name"].precision == 0.0


def test_verdict_uses_the_catalogue_bar():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.evaluation.harness import Counts, EvalReport

    report = EvalReport(label="x", documents=1, catalogue=load_catalogue())
    report.partial["PERSON"] = Counts(tp=8, fp=0, fn=2)  # recall 0.80, bar 0.90
    verdicts = dict((name, ok) for name, ok, _ in report.verdicts())
    assert verdicts["PERSON"] is False


def test_type_without_a_bar_gets_no_verdict():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.evaluation.harness import Counts, EvalReport

    report = EvalReport(label="x", documents=1, catalogue=load_catalogue())
    report.partial["CITY"] = Counts(tp=1, fp=9, fn=0)
    assert "CITY" not in {name for name, _, _ in report.verdicts()}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_eval_harness.py -v`
Expected: FAIL — `evaluate` takes no `catalogue`, `Span` takes no `label`.

- [ ] **Step 3: Give spans their model label**

In `privaparse/parser/types.py`, add to `Span`:

```python
    #: The model label this span came from, when it came from the model. None
    #: for backstop and sweep spans. Diagnostic only — nothing in the pipeline
    #: branches on it. It exists so the evaluation can say *which* of the
    #: labels feeding a type produced a false positive.
    label: str | None = None
```

Add `label=self.label` to `Span.shifted`. In `gliner_detector.py:203-210`, pass
the label through `_build_span` — add a `label: str` parameter, pass it from
`_to_spans` (the loop already has `label` in scope), and set `label=label` on
the constructed `Span`. In `merge.py:_trim` and `coreference_sweep`, carry
`label=span.label` on the reconstructed spans (`_trim`) and leave it `None` on
sweep spans.

- [ ] **Step 4: Generalise the harness**

In `privaparse/evaluation/harness.py`:

Replace the module docstring's fixed-threshold paragraph with a note that the
bars now live in the catalogue, and delete `PERSON_RECALL_FLOOR`,
`PERSON_PRECISION_FLOOR` and `ENTITY_TYPES`.

Give `GoldEntity` a `label: str | None = None` field. Add to `EvalReport`:

```python
    catalogue: "Catalogue | None" = None
    by_label: dict[str, Counts] = field(default_factory=dict)

    def verdicts(self) -> list[tuple[str, bool, str]]:
        """(type, meets_bar, explanation) for every type that declares a bar.

        Types without a bar are absent rather than reported as passing. A
        silent pass on an unmeasured type is exactly the claim this project
        exists not to make.
        """
        if self.catalogue is None:
            return []
        out: list[tuple[str, bool, str]] = []
        for placeholder in self.catalogue.enabled:
            bar = placeholder.bar
            if bar is None:
                continue
            counts = self.partial.get(placeholder.name, Counts())
            if not counts.support:
                out.append((placeholder.name, True, "no gold entities — nothing measured"))
                continue
            reasons = []
            if bar.recall is not None and counts.recall < bar.recall:
                reasons.append(f"recall {counts.recall:.3f} < {bar.recall}")
            if bar.precision is not None and counts.precision < bar.precision:
                reasons.append(f"precision {counts.precision:.3f} < {bar.precision}")
            if reasons:
                out.append((placeholder.name, False, "under bar — " + " and ".join(reasons)))
            else:
                out.append((
                    placeholder.name, True,
                    f"meets bar — recall {counts.recall:.3f}, precision {counts.precision:.3f}",
                ))
        return out
```

Keep `person_partial` and `needs_finetuning` as thin wrappers over
`partial["PERSON"]` and `verdicts()`, so `main.py`'s eval summary keeps working.

Change `evaluate` to take `catalogue` and iterate its enabled types instead of
`ENTITY_TYPES`, and to accumulate per-label counts:

```python
def evaluate(
    detector: SupportsDetect,
    documents: Sequence[GoldDocument],
    *,
    label: str = "detector",
    catalogue: "Catalogue",
) -> EvalReport:
    report = EvalReport(label=label, documents=len(documents), catalogue=catalogue)
    entity_types = [t.name for t in catalogue.enabled]
    for entity_type in entity_types:
        report.exact[entity_type] = Counts()
        report.partial[entity_type] = Counts()

    for document in documents:
        spans = detector.detect(document.text)
        predicted = [_as_gold(span) for span in spans]
        for entity_type in entity_types:
            gold_of_type = [e for e in document.entities if e.type == entity_type]
            pred_of_type = [e for e in predicted if e.type == entity_type]

            _score(report.exact[entity_type], gold_of_type, pred_of_type, exact=True)
            matched_gold, matched_pred = _score(
                report.partial[entity_type], gold_of_type, pred_of_type, exact=False
            )

            for index, entity in enumerate(pred_of_type):
                counts = report.by_label.setdefault(entity.label or "(rule)", Counts())
                if index in matched_pred:
                    counts.tp += 1
                else:
                    counts.fp += 1
                    report.false_positives.append(_mistake(document, entity))
            for index, entity in enumerate(gold_of_type):
                if index not in matched_gold:
                    report.false_negatives.append(_mistake(document, entity))

    return report
```

Update `_as_gold` to carry `label=span.label`.

**Per-label recall is not computable and must not be reported.** Gold entities
carry a placeholder type, not a model label, so there is no denominator for a
missed entity's label. The per-label table shows TP, FP and precision only.
Add that sentence as a comment above `by_label`.

Extend `format_report`: iterate `catalogue.enabled` for the main table, replace
the fixed-threshold Verdict paragraph with one line per entry of `verdicts()`,
and add a per-label section:

```python
    lines.append("## Per model label")
    lines.append("")
    lines.append("Recall is not shown: gold entities carry a placeholder type, not a")
    lines.append("model label, so a missed entity has no label to attribute it to.")
    lines.append("")
    lines.append("| Run | Label | TP | FP | Precision |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    for report in reports:
        for name, counts in sorted(report.by_label.items(), key=lambda kv: -kv[1].fp):
            lines.append(
                f"| {report.label} | {name} | {counts.tp} | {counts.fp} | "
                f"{counts.precision:.3f} |"
            )
    lines.append("")
```

Update the two `run_eval(...)` call sites in `main.py` and `bench.py` to pass
`catalogue=settings.catalogue`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_eval_harness.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add privaparse/evaluation/harness.py privaparse/parser/types.py privaparse/parser/gliner_detector.py privaparse/parser/merge.py privaparse/app/main.py privaparse/evaluation/bench.py tests/test_eval_harness.py
git commit -m "feat: evaluate every catalogue type, and every model label

The harness scored three fixed types against one hardcoded PERSON verdict.
It now scores every enabled type against the bar in the catalogue, and reports
per model label as well — which is what makes a five-label SECRET type
diagnosable when its precision collapses.

Per-label recall is deliberately absent. Gold entities carry a placeholder
type, not a model label, so a missed entity has no label to attribute it to,
and a number computed anyway would be a guess wearing three decimal places.

A type with no bar is reported without a verdict rather than as passing."
```

---

### Task 11: Threshold sweep

**Files:**
- Modify: `privaparse/evaluation/harness.py`
- Modify: `privaparse/app/main.py`
- Test: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `PrivaParseEngine.detect_raw` (Task 8), `evaluate` (Task 10).
- Produces: `sweep_thresholds(engine, documents, *, thresholds, catalogue) -> dict[float, EvalReport]`, `format_sweep(results) -> str`; `privaparse eval --sweep-threshold`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sweep.py`:

```python
from __future__ import annotations

from privaparse.app.catalogue import load_catalogue
from privaparse.evaluation.harness import GoldDocument, format_sweep, sweep_thresholds


class CountingEngine:
    """Records how often the expensive pass runs."""

    def __init__(self, spans_by_text):
        self.spans_by_text = spans_by_text
        self.calls = 0
        self.settings = type("S", (), {"scan_code": False, "coreference_sweep": False})()

    def detect_raw(self, text):
        from privaparse.parser.markdown import protect

        self.calls += 1
        return protect(text), list(self.spans_by_text.get(text, []))

    @property
    def catalogue(self):
        return load_catalogue()


def _document(text, entities=()):
    return GoldDocument("d1", "notiz", text, tuple(entities))


def test_sweep_runs_the_model_once_per_document():
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})

    sweep_thresholds(engine, [_document(text)], thresholds=(0.3, 0.5, 0.7, 0.9),
                     catalogue=load_catalogue())
    assert engine.calls == 1


def test_raising_the_threshold_drops_low_scoring_spans():
    from privaparse.evaluation.harness import GoldEntity
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    gold = [GoldEntity(0, 14, "PERSON", "Max Mustermann")]
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})

    results = sweep_thresholds(engine, [_document(text, gold)], thresholds=(0.5, 0.9),
                               catalogue=load_catalogue())
    assert results[0.5].partial["PERSON"].recall == 1.0
    assert results[0.9].partial["PERSON"].recall == 0.0


def test_format_sweep_produces_one_row_per_threshold():
    from privaparse.parser.types import SOURCE_GLINER, Span

    text = "Max Mustermann schreibt."
    engine = CountingEngine({text: [Span(0, 14, "Max Mustermann", "PERSON", 0.55, SOURCE_GLINER)]})
    results = sweep_thresholds(engine, [_document(text)], thresholds=(0.3, 0.5),
                               catalogue=load_catalogue())

    rendered = format_sweep(results)
    assert "0.30" in rendered and "0.50" in rendered
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_sweep.py -v`
Expected: FAIL — `sweep_thresholds` does not exist.

- [ ] **Step 3: Implement the sweep**

Append to `privaparse/evaluation/harness.py`:

```python
DEFAULT_SWEEP = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class _ReplayDetector:
    """Serves pre-computed spans, so the merge can be redone without the model."""

    def __init__(self, protected, raw, *, threshold: float, catalogue, sweep: bool):
        self._protected = protected
        self._raw = raw
        self._threshold = threshold
        self._catalogue = catalogue
        self._sweep = sweep

    def detect(self, text: str) -> list[Span]:
        from privaparse.parser.merge import resolve_spans

        protected = self._protected[text]
        return resolve_spans(
            protected,
            self._raw[text],
            threshold=self._threshold,
            sweep=self._sweep,
            catalogue=self._catalogue,
        )


def sweep_thresholds(
    engine,
    documents: Sequence[GoldDocument],
    *,
    thresholds: Sequence[float] = DEFAULT_SWEEP,
    catalogue,
) -> dict[float, EvalReport]:
    """Score the gold set at several thresholds from one model pass.

    The model is the expensive part and its scores do not depend on the
    threshold — merging does. So detection runs once per document and every
    point on the curve is a re-merge, which is cheap. Filtering the *merged*
    spans instead would be wrong: the threshold changes which candidates
    compete for an overlap, not only which survive.
    """
    protected: dict[str, object] = {}
    raw: dict[str, list[Span]] = {}
    for document in documents:
        protected[document.text], raw[document.text] = engine.detect_raw(document.text)

    sweep_enabled = bool(getattr(engine.settings, "coreference_sweep", True))
    return {
        threshold: evaluate(
            _ReplayDetector(protected, raw, threshold=threshold,
                            catalogue=catalogue, sweep=sweep_enabled),
            documents,
            label=f"t={threshold:.2f}",
            catalogue=catalogue,
        )
        for threshold in thresholds
    }


def format_sweep(results: dict[float, EvalReport]) -> str:
    """Precision/recall per type across the swept thresholds."""
    if not results:
        return ""
    first = next(iter(results.values()))
    types = [t.name for t in first.catalogue.enabled] if first.catalogue else []

    lines = [
        "## Threshold sweep",
        "",
        "One model pass per document; each row re-merges the same scored spans.",
        "",
        "| Type | Threshold | Support | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for entity_type in types:
        for threshold in sorted(results):
            counts = results[threshold].partial.get(entity_type, Counts())
            if not counts.support and not counts.fp:
                continue
            lines.append(
                f"| {entity_type} | {threshold:.2f} | {counts.support} | "
                f"{counts.precision:.3f} | {counts.recall:.3f} | {counts.f1:.3f} |"
            )
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Add the CLI flag**

In `privaparse/app/main.py`, add to `evaluate`:

```python
    sweep_threshold: bool = typer.Option(
        False, "--sweep-threshold",
        help="Score the gold set at 0.3–0.9 from one model pass and write the curve.",
    ),
```

and, before the normal per-model loop:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_sweep.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add privaparse/evaluation/harness.py privaparse/app/main.py tests/test_sweep.py
git commit -m "feat: threshold sweep from a single model pass

Detection runs once per document; each threshold is a re-merge of the same
scored spans. Filtering merged output instead would be wrong — the threshold
changes which candidates compete for an overlap, not only which survive.

This is what turns 'start with thresholds' into a value read off a curve."
```

---

### Task 12: Gold set

Everything before this is machinery. This produces the numbers.

**Files:**
- Modify: `privaparse/evaluation/build_gold.py`
- Modify: `eval/gold/de_gold.jsonl`
- Modify: `eval/gold/de_gold_source.md`
- Test: `tests/test_eval_harness.py` (extend)

**Interfaces:**
- Consumes: validators (Task 4) to generate checksum-valid values.
- Produces: an extended gold set. Format unchanged — `entities[].type` is already a free string.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval_harness.py`:

```python
def test_every_gold_type_exists_in_the_catalogue():
    from privaparse.app.catalogue import load_catalogue
    from privaparse.evaluation import DEFAULT_GOLD_PATH
    from privaparse.evaluation.harness import load_gold

    catalogue = load_catalogue()
    for document in load_gold(DEFAULT_GOLD_PATH):
        for entity in document.entities:
            assert entity.type in catalogue.types, f"{document.id}: {entity.type}"


def test_gold_offsets_match_their_own_text():
    from privaparse.evaluation import DEFAULT_GOLD_PATH
    from privaparse.evaluation.harness import load_gold

    for document in load_gold(DEFAULT_GOLD_PATH):
        for entity in document.entities:
            assert document.text[entity.start : entity.end] == entity.text, document.id


def test_gold_set_contains_negative_documents():
    from privaparse.evaluation import DEFAULT_GOLD_PATH
    from privaparse.evaluation.harness import load_gold

    documents = load_gold(DEFAULT_GOLD_PATH)
    negatives = [d for d in documents if not d.entities]
    assert len(documents) >= 80
    assert len(negatives) >= 30, "false positives are invisible without negatives"


def test_generated_decidable_values_pass_their_validators():
    from privaparse.evaluation.build_gold import generate_decidable

    from privaparse.parser import registry

    for entity_type, value, validator in generate_decidable(seed=7):
        assert registry.get_validator(validator)(value), f"{entity_type}: {value}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_eval_harness.py -k gold -v`
Expected: FAIL — 38 documents, 10 negatives, no `generate_decidable`.

- [ ] **Step 3: Add the generator for decidable types**

Append to `privaparse/evaluation/build_gold.py`:

```python
def _iban_de(account: int, blz: int = 37040044) -> str:
    """A syntactically real German IBAN with correct check digits.

    Generated rather than hand-written because a gold set full of IBANs that
    fail their own checksum would measure the pattern, not the validator.
    """
    body = f"{blz:08d}{account:010d}"
    rearranged = body + "1314" + "00"  # DE -> 1314, placeholder check digits
    check = 98 - int(rearranged) % 97
    return f"DE{check:02d}{body}"


def _luhn_complete(prefix: str, length: int) -> str:
    partial = prefix.ljust(length - 1, "0")
    total = 0
    for index, character in enumerate(reversed(partial + "0")):
        digit = int(character)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return partial + str((10 - total % 10) % 10)


def _tax_id_de(base: str) -> str:
    """Complete a ten-digit stem with its ISO 7064 MOD 11,10 check digit."""
    remainder = 10
    for character in base:
        total = (int(character) + remainder) % 10 or 10
        remainder = (2 * total) % 11
    return base + str((11 - remainder) % 10)


def generate_decidable(seed: int = 1) -> list[tuple[str, str, str]]:
    """(placeholder type, value, validator name) for the checksum-backed types.

    Every value is generated to satisfy its own validator, so the gold set
    exercises the checksum rather than accidentally testing that a wrong number
    is rejected.
    """
    import random

    rng = random.Random(seed)
    out: list[tuple[str, str, str]] = []
    for _ in range(6):
        out.append(("IBAN", _iban_de(rng.randrange(10**9)), "iban_mod97"))
    for prefix in ("4", "51", "37"):
        out.append(("CARD", _luhn_complete(prefix + str(rng.randrange(10**8)), 16), "luhn"))
    for _ in range(4):
        out.append(("TAX_ID", _tax_id_de(f"{rng.randrange(10**9):010d}"), "tax_de"))
    for _ in range(3):
        out.append(("IP", f"{rng.randrange(1,224)}.{rng.randrange(256)}."
                          f"{rng.randrange(256)}.{rng.randrange(1,255)}", "ip_parse"))
    for _ in range(3):
        out.append(("POSTAL_CODE", f"{rng.randrange(10000, 100000)}", "postal_de"))
    return out
```

- [ ] **Step 4: Extend the corpus**

Work in `eval/gold/de_gold_source.md`, which is the human-readable source the
JSONL is built from, then rebuild. Three batches:

**Batch A — decidable types in context (12 documents).** Take the values from
`generate_decidable` and embed each in a realistic German sentence: a payment
reminder carrying an IBAN, an order confirmation carrying a card number, a tax
office letter carrying a Steuer-ID, a server log excerpt carrying an IP. Annotate
the value's offsets. Cheap, because correctness is mechanical.

**Batch B — fuzzy types (18 documents).** Hand-annotated German letters, notes
and forms carrying `ADDRESS`, `DATE_OF_BIRTH`, `NATIONAL_ID`, `PASSPORT`,
`ACCOUNT_ID`, `USERNAME`. Annotate the same way the existing 38 were. This is
the slow part and it is annotation, not engineering.

**Batch C — negatives (20+ documents, target 30 total).** No entities at all.
The list is the point, so write it out:

- Aktenzeichen (`4 O 231/24`, `Az. 12 C 45/26`)
- order, invoice and article numbers, including 16-digit ones that fail Luhn
- version and build strings (`v2.13.0+cu130`, `build 20260318`)
- German compound nouns that read like places (`Bahnhofsviertel`,
  `Marktplatzsanierung`)
- a table of figures with no names
- a bare source-code fragment, no Markdown fences — the case `protect()` does
  not cover
- salutations with no name (`Sehr geehrte Damen und Herren`)
- dates in every German form, none of them a birth date
- a price list with currency amounts
- a bank letter naming a BLZ but no account

These are where precision is decided. A corpus in which every document contains
real PII measures recall and reports success while the pseudonymised text is
unusable.

Rebuild: `python -m privaparse.evaluation.build_gold`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_eval_harness.py -v`
Expected: PASS. `test_gold_offsets_match_their_own_text` catches annotation
slips; fix the annotation, never the assertion.

- [ ] **Step 6: Run the sweep and record the numbers** **[lab]**

In a lab sandbox:

```bash
privaparse eval --sweep-threshold
```

then `privaparse eval`. Copy `docs/sweep-report.md` and `docs/eval-report.md`
back out.

Read `docs/sweep-report.md`. For each type, pick the threshold at the knee of
the precision/recall curve, respecting the project's asymmetry: a missed entity
leaves the machine, a spurious one costs readability, so prefer recall where
the curve is flat. Write the chosen values into
`privaparse/app/entities.default.yaml`, then re-run `privaparse eval` to confirm
the report.

- [ ] **Step 7: Update the README**

The `0.975` PERSON figure was measured with three labels and is no longer the
configuration PrivaParse ships. Replace the table with the new `privaparse eval`
output, and state the label count each number was obtained under. If a type has
no gold support, say so rather than omitting the row — an absent row reads as
an oversight, an explicit "not yet measured" reads as a boundary.

- [ ] **Step 8: Run the full suite, then the model tests** **[lab]**

Locally: `pytest`
In the same lab sandbox as Step 6: `pytest -m model -v`
Expected: PASS both.

- [ ] **Step 9: Commit**

```bash
git add eval/gold privaparse/evaluation/build_gold.py privaparse/app/entities.default.yaml docs/eval-report.md docs/sweep-report.md README.md tests/test_eval_harness.py
git commit -m "test: extend the gold set to the widened catalogue

38 documents covering three types cannot measure 25. Three batches: checksum-
backed values generated to satisfy their own validators, hand-annotated German
documents for the fuzzy types, and 30 negatives.

The negatives are the point. At the precision this model reports across its
full label set the failure mode is false positives, and false positives are
invisible in a corpus where every document contains real PII — the run would
measure high recall and report success while the output is unusable.

Thresholds in the catalogue are now read off the sweep. The README's 0.975 is
replaced with numbers from the configuration that actually ships, each stated
with the label count it was obtained under."
```

---

## Self-Review

**Spec coverage.** Catalogue format → Task 1. Discovery and deep-merge → Task 1.
Label-to-type mapping table → Task 9. Irreversible types → Task 7. Open
`EntityType` → Task 2. Normalizer registry → Task 3. Validator registry →
Task 4. Backstop registry → Task 5 (the spec folds this into the validator
section; it is separated here because it is a different mechanism with a
different failure mode). Merge precedence flip → Task 6. Catalogue-driven sweep
pattern → Task 6. `pseudonymize_batch` → Task 8. CLI → Task 9. Harness
generalisation and per-label reporting → Task 10. Threshold sweep → Task 11.
Gold set → Task 12. Throughput risk → Task 9 Step 7. README currency risk →
Task 12 Step 7.

**One deliberate deviation, flagged rather than hidden.** The spec says legality
is checked "at construction". Checking it in `Span.__post_init__` would mean a
catalogue lookup for every one of the thousands of spans a document produces,
and would force a global catalogue reference into a frozen dataclass. Task 2
Step 3 puts the check in `EntityResolver` instead — the last point before a
value reaches the vault, and the first point where an unknown type has
consequences. Task 7 tests that it fails before anything is written.

**One spec claim corrected by implementation.** The spec's evaluation section
implies per-label precision *and* recall. Recall per label is not computable:
gold entities carry a placeholder type, not a model label, so a missed entity
has no label to attribute it to. Task 10 reports TP, FP and precision per label,
and says so in the report itself rather than emitting a number that would be a
guess with three decimal places.

**Type consistency checked.** `Catalogue.get`/`.types`/`.enabled`/`.schema()`/
`.label_to_type()`/`.threshold_for()` are used consistently in Tasks 2, 4, 5, 6,
9, 10, 11. `registry.get_validator`/`get_backstop`/`get_normalizer` take a name
and return a callable everywhere. `PlaceholderType.backstop` is `str | None` in
Task 1 and read as such in Task 5. `Span.label` is added in Task 10 and consumed
by `_as_gold` in the same task. `EntityResolver(repo, catalogue)` — two
arguments — is introduced in Task 3 and used in Tasks 7 and 8.
`resolve_spans(..., catalogue=...)` is introduced in Task 4 and used in Tasks 6,
8 and 11. `BatchResult` fields match between Task 8's definition and its tests.

---

Plan complete and saved to `docs/superpowers/plans/2026-08-10-entity-catalogue.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
