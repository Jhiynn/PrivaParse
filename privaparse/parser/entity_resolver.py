"""Turn detected spans into vault entities and placeholders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from privaparse.app.logging import get_logger, register_secret
from privaparse.database.models import Entity, EntityValue
from privaparse.database.repository import VaultRepository
from privaparse.parser.normalizer import normalize
from privaparse.parser.types import Span

log = get_logger("resolver")

__all__ = ["ResolvedSpan", "EntityUsage", "Resolution", "EntityResolver"]


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

    def __init__(self, repo: VaultRepository) -> None:
        self.repo = repo

    def resolve(self, spans: Iterable[Span]) -> Resolution:
        """Resolve spans **in document order**.

        Order matters: the first spelling encountered in this document becomes
        the one ``reverse()`` puts back, so the restored text reads the way the
        author wrote it rather than the way some earlier document did.
        """
        result = Resolution()

        for span in sorted(spans, key=lambda s: s.start):
            normalized = normalize(span.text, span.type)
            if not normalized:
                log.debug("skipping span at %d: normalises to empty", span.start)
                continue

            register_secret(span.text)
            entity = self.repo.get_or_create_entity(str(span.type), normalized)
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
