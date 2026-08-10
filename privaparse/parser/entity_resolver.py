"""Turn detected spans into vault entities and placeholders."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from privaparse.app.logging import get_logger, register_secret
from privaparse.database.models import Entity, EntityValue
from privaparse.database.repository import VaultRepository
from privaparse.parser.normalizer import normalize
from privaparse.parser.types import Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue

log = get_logger("resolver")

__all__ = [
    "ResolvedSpan",
    "EntityUsage",
    "Resolution",
    "EntityResolver",
    "UnknownEntityTypeError",
]


class UnknownEntityTypeError(LookupError):
    """A span carries a type the catalogue does not define."""


@dataclass(frozen=True, slots=True)
class ResolvedSpan:
    """A span plus the placeholder that will replace it."""

    span: Span
    placeholder: str
    entity_id: str


@dataclass
class EntityUsage:
    """How one entity was used in one document."""

    entity: Entity
    restore_value: EntityValue
    occurrences: int = 1

    @property
    def placeholder(self) -> str:
        return self.entity.placeholder


@dataclass
class Resolution:
    spans: list[ResolvedSpan] = field(default_factory=list)
    usages: dict[str, EntityUsage] = field(default_factory=dict)

    @property
    def placeholder_count(self) -> int:
        return len(self.usages)


class EntityResolver:
    """Maps surface forms onto stable placeholders through the global vault."""

    def __init__(self, repo: VaultRepository, catalogue: "Catalogue") -> None:
        self.repo = repo
        self.catalogue = catalogue

    def resolve(self, spans: Iterable[Span]) -> Resolution:
        """Resolve spans **in document order**.

        Order matters: the first spelling encountered in this document becomes
        the one ``reverse()`` puts back, so the restored text reads the way the
        author wrote it rather than the way some earlier document did.

        Every span's type is checked against the catalogue in a pass of its
        own, before the pass that writes anything. An earlier valid span must
        not leave a row behind when a later span turns out to have a type the
        catalogue does not define — and interleaving the check with the writes
        would make that guarantee depend on the repository correctly rolling
        back a partially-written document on exception, which is a property of
        a driver two layers down, not of this method.
        """
        ordered = sorted(spans, key=lambda s: s.start)
        for span in ordered:
            if span.type not in self.catalogue.types:
                raise UnknownEntityTypeError(
                    f"span at {span.start} claims type {span.type!r}, which the "
                    f"catalogue does not define"
                )

        result = Resolution()

        for span in ordered:
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
