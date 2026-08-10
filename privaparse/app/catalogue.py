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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import TYPE_NAME_RE
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
    if not TYPE_NAME_RE.fullmatch(name):
        raise CatalogueError(
            f"placeholder type {name!r} must match {TYPE_NAME_RE.pattern!r} — "
            f"it is rendered literally into [[{name}_A1]], and PLACEHOLDER_RE "
            f"would not recognise anything else as a type name"
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
        reversible=_require_bool(body, "reversible", True, name),
        enabled=_require_bool(body, "enabled", True, name),
        bar=Bar(precision=bar.get("precision"), recall=bar.get("recall")) if bar else None,
    )


def _require_bool(body: dict[str, Any], key: str, default: bool, type_name: str) -> bool:
    """Reject anything that is not a real boolean.

    ``bool("false")`` is ``True`` — a non-empty string is truthy — so a YAML
    author who writes ``enabled: "false"`` would silently get a type that
    stays on. Fail closed instead of guessing what a non-boolean value meant.
    """
    if key not in body:
        return default
    value = body[key]
    if not isinstance(value, bool):
        raise CatalogueError(
            f"{type_name}: {key} must be true or false, got {value!r}"
        )
    return value


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
