"""Replace detected entities with placeholders and record the session."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import contains_placeholder
from privaparse.database.repository import VaultRepository
from privaparse.parser.detector import Detector
from privaparse.parser.entity_resolver import EntityResolver, Resolution, ResolvedSpan
from privaparse.parser.markdown import protect
from privaparse.parser.merge import resolve_spans
from privaparse.parser.types import Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.config import Settings

log = get_logger("pseudonymizer")

__all__ = [
    "PseudonymizationResult",
    "AlreadyPseudonymizedError",
    "SpanIntegrityError",
    "pseudonymize_text",
    "apply_replacements",
]


class AlreadyPseudonymizedError(ValueError):
    """Raised when the input already contains placeholders."""


class SpanIntegrityError(RuntimeError):
    """Raised when a span's offsets no longer match its own text."""


@dataclass(frozen=True)
class PseudonymizationResult:
    text: str
    mapping_id: str
    spans: list[ResolvedSpan] = field(default_factory=list)
    detected: list[Span] = field(default_factory=list)

    @property
    def replacements(self) -> int:
        return len(self.spans)

    @property
    def placeholders(self) -> list[str]:
        seen: dict[str, None] = {}
        for resolved in self.spans:
            seen.setdefault(resolved.placeholder, None)
        return list(seen)


def pseudonymize_text(
    text: str,
    *,
    detector: Detector,
    repo: VaultRepository,
    settings: "Settings",
    source_name: str | None = None,
) -> PseudonymizationResult:
    """Detect, replace and persist, as one transaction.

    Refuses text that already contains placeholders: pseudonymising twice
    produces a document that cannot be reversed cleanly, and silently doing it
    would hand back something that looks right and is not.
    """
    if contains_placeholder(text):
        raise AlreadyPseudonymizedError(
            "This text already contains PrivaParse placeholders. Pseudonymising it "
            "again would nest placeholders and make the result irreversible. Reverse "
            "it first, or pass the original document."
        )

    protected = protect(text, scan_code=settings.scan_code)
    raw_spans = detector.detect(protected.view)
    spans = resolve_spans(
        protected,
        raw_spans,
        threshold=settings.threshold,
        sweep=settings.coreference_sweep,
        catalogue=settings.catalogue,
    )
    _verify_spans(text, spans)

    resolution = EntityResolver(repo, settings.catalogue).resolve(spans)
    new_text = apply_replacements(text, resolution.spans)

    mapping = _persist(repo, resolution, text=text, source_name=source_name)
    repo.session.commit()

    log.info(
        "pseudonymised %s: %d replacement(s), %d placeholder(s), mapping=%s",
        source_name or "<text>",
        len(resolution.spans),
        resolution.placeholder_count,
        mapping,
    )
    return PseudonymizationResult(
        text=new_text,
        mapping_id=mapping,
        spans=resolution.spans,
        detected=spans,
    )


def apply_replacements(text: str, spans: list[ResolvedSpan]) -> str:
    """Substitute placeholders back-to-front so earlier offsets stay valid."""
    out = text
    for resolved in sorted(spans, key=lambda r: r.span.start, reverse=True):
        span = resolved.span
        out = f"{out[: span.start]}{resolved.placeholder}{out[span.end :]}"
    return out


def _verify_spans(text: str, spans: list[Span]) -> None:
    """Fail loudly if any span no longer describes the text it points at.

    Getting this wrong corrupts the document *and* leaks the entity that should
    have been replaced, so it must never degrade quietly.
    """
    for span in spans:
        if not span.verify_against(text):
            raise SpanIntegrityError(
                f"span at [{span.start}:{span.end}] (type={span.type}, "
                f"source={span.source}) does not match the source text — "
                f"refusing to rewrite the document"
            )


def _persist(
    repo: VaultRepository,
    resolution: Resolution,
    *,
    text: str,
    source_name: str | None,
) -> str:
    mapping = repo.create_mapping(
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_name=source_name,
    )
    for usage in resolution.usages.values():
        repo.add_mapping_entry(
            mapping,
            usage.entity,
            usage.restore_value,
            occurrences=usage.occurrences,
        )
    return mapping.id
