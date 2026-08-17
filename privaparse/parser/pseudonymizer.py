"""Replace detected spans with placeholders and record the mapping."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import contains_placeholder
from privaparse.database.repository import VaultRepository
from privaparse.parser.detection_pass import DetectionPass
from privaparse.parser.detector import Detector
from privaparse.parser.entity_resolver import (
    EntityResolver,
    EntityUsage,
    ResolvedSpan,
    UnknownEntityTypeError,
)
from privaparse.parser.types import Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue
    from privaparse.app.config import Settings

log = get_logger("pseudonymizer")

__all__ = [
    "AlreadyPseudonymizedError",
    "BatchResult",
    "PseudonymizationResult",
    "SpanIntegrityError",
    "apply_replacements",
    "detect_many",
    "pseudonymize_batch",
    "pseudonymize_text",
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


@dataclass(frozen=True)
class BatchResult:
    """Several texts, one mapping.

    The gateway in spec 2 sends one HTTP request carrying dozens of text nodes.
    They must share a mapping: the model's answer mixes placeholders from all
    of them, and ``reverse()`` resolves against exactly one mapping.
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


def detect_many(
    texts: Sequence[str],
    *,
    detector: Detector,
    settings: Settings,
) -> list[list[Span]]:
    """The detection pass over several texts: masking, the detector, the
    threshold, merging and the coreference sweep.

    This is exactly the detection step ``pseudonymize_batch`` runs before it
    ever touches the vault -- so there is one code path for "what would this
    pass find", not two that can drift apart. Read-only: nothing is written.

    The order itself lives in :class:`DetectionPass`; this keeps its signature
    for the callers that already have a ``Settings`` and a detector in hand.
    """
    return DetectionPass.from_settings(settings, detector).run_batch(texts)


def pseudonymize_text(
    text: str,
    *,
    detector: Detector,
    repo: VaultRepository,
    settings: Settings,
    source_name: str | None = None,
) -> PseudonymizationResult:
    """Detect, replace and persist, as one transaction.

    Delegates to :func:`pseudonymize_batch` with a single-element list, so
    there is one code path from detection through to the mapping row rather
    than two that can quietly drift apart.
    """
    batch = pseudonymize_batch(
        [text], detector=detector, repo=repo, settings=settings, source_name=source_name
    )
    return PseudonymizationResult(
        text=batch.texts[0],
        mapping_id=batch.mapping_id,
        spans=batch.spans[0],
        detected=batch.detected[0],
    )


def pseudonymize_batch(
    texts: Sequence[str],
    *,
    detector: Detector,
    repo: VaultRepository,
    settings: Settings,
    source_name: str | None = None,
    adopt_placeholders: bool = False,
) -> BatchResult:
    """Pseudonymise several texts under one mapping, in one transaction.

    Refuses any text that already contains placeholders: pseudonymising twice
    produces a document that cannot be reversed cleanly, and silently doing it
    would hand back something that looks right and is not.

    Detection runs across all of them in a single call so the model batches;
    resolution runs in text order so the first spelling seen still wins, the
    same rule single-text pseudonymisation follows.

    Every text's spans are checked against the catalogue before *any* of them
    are written, not just within one text. ``EntityResolver.resolve()`` only
    promises that ordering within a single call, and this calls it once per
    text — so without a batch-wide check first, a bad type in text 3 would
    still leave text 1 and text 2 already written by the time text 3's own
    call raised. The fix cannot be "let the exception unwind and roll it
    back": the vault's nested-SAVEPOINT writes have a known pre-existing
    rollback defect (see the reasoning in ``EntityResolver.resolve()``), so
    an unknown-type rejection must not depend on rollback to leave nothing
    behind — it must never write in the first place. That guarantee covers
    only this one failure mode: an exception raised mid-``resolve()`` for any
    other reason can still leave an earlier text's entities written, the same
    pre-existing gap ``EntityResolver.resolve()`` has within a single text.

    An empty batch still creates a mapping row, with zero entries, rather
    than returning a sentinel. ``PrivaParseEngine.reverse`` treats a falsy
    ``mapping_id`` as "find whichever mapping issued every placeholder in
    this text" — so an empty string or ``None`` handed back here would make
    ``reverse(empty.mapping_id, answer)`` silently resolve against some
    *other* mapping that happens to cover the text, not against this call at
    all. A real id that happens to have issued nothing behaves like every
    other mapping id instead: ``reverse()`` against it correctly resolves
    nothing.
    """
    if not adopt_placeholders:
        for index, text in enumerate(texts):
            if contains_placeholder(text):
                raise AlreadyPseudonymizedError(
                    f"text {index} already contains PrivaParse placeholders. "
                    "Pseudonymising it again would nest placeholders and make the "
                    "result irreversible. Reverse it first, or pass the original "
                    "document."
                )

    per_text_spans = detect_many(texts, detector=detector, settings=settings)
    if adopt_placeholders:
        # A detector shown `[[PERSON_A1]]` can decide that is a name. Wrapping
        # it in a second placeholder is the nesting the refusal above exists to
        # prevent, so anything overlapping an existing one is dropped.
        per_text_spans = [
            _drop_spans_over_placeholders(text, spans)
            for text, spans in zip(texts, per_text_spans)
        ]

    for text, spans in zip(texts, per_text_spans):
        _verify_spans(text, spans)
    _ensure_known_types(per_text_spans, settings.catalogue)

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
        repo.add_mapping_entry(
            mapping, usage.entity, usage.restore_value, occurrences=usage.occurrences
        )

    adopted = 0
    if adopt_placeholders:
        adopted = _adopt_existing(texts, mapping, repo, already={u.entity.id for u in
                                                                 merged.values()})
    repo.session.commit()

    log.info(
        "pseudonymised %d text(s) as %s: %d replacement(s), %d placeholder(s), "
        "%d adopted, mapping=%s",
        len(texts),
        source_name or ("<text>" if len(texts) == 1 else "<batch>"),
        sum(len(r.spans) for r in resolutions),
        len(merged),
        adopted,
        mapping.id,
    )
    return BatchResult(
        mapping_id=mapping.id,
        texts=new_texts,
        spans=[r.spans for r in resolutions],
        detected=per_text_spans,
    )


def apply_replacements(text: str, spans: list[ResolvedSpan]) -> str:
    """Substitute placeholders back-to-front so earlier offsets stay valid."""
    out = text
    for resolved in sorted(spans, key=lambda r: r.span.start, reverse=True):
        span = resolved.span
        out = f"{out[: span.start]}{resolved.placeholder}{out[span.end :]}"
    return out


def _drop_spans_over_placeholders(text: str, spans: list[Span]) -> list[Span]:
    """Remove detections that land on a placeholder already in the text."""
    from privaparse.database.placeholder import find_placeholders

    taken = [(m.start(), m.end()) for m in find_placeholders(text)]
    if not taken:
        return spans
    return [
        span
        for span in spans
        if not any(span.start < end and start < span.end for start, end in taken)
    ]


def _adopt_existing(
    texts: Sequence[str],
    mapping,  # type: ignore[no-untyped-def]
    repo: VaultRepository,
    *,
    already: set,
) -> int:
    """Join placeholders already in the text to this request's mapping.

    Without this the answer to a replayed turn comes back holding a
    placeholder that only an *earlier* mapping could resolve, and ``reverse``
    scopes to exactly one. The value is not re-detected and the text is not
    touched -- only the vault entry that lets this mapping restore it.

    A placeholder the vault never issued is skipped: invented downstream, or
    left over from a vault that no longer exists. There is nothing to adopt
    and nothing worth failing over. So is an irreversible one, for which the
    vault holds nothing readable to restore.
    """
    from privaparse.database.placeholder import find_placeholders

    seen: set[str] = set()
    adopted = 0
    for text in texts:
        for match in find_placeholders(text):
            placeholder = match.group(0)
            if placeholder in seen:
                continue
            seen.add(placeholder)

            entity = repo.entity_by_placeholder(placeholder)
            if entity is None or entity.id in already:
                continue
            if not entity.values:
                # An irreversible entity: the vault holds no readable form of
                # it, so there is nothing this mapping could be given to
                # restore. Adopting it is not possible and not needed --
                # reversal recognises it from the same absence.
                continue
            repo.add_mapping_entry(mapping, entity, entity.values[0], occurrences=1)
            already.add(entity.id)
            adopted += 1

    if adopted:
        log.info("adopted %d placeholder(s) already present into this mapping", adopted)
    return adopted


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


def _ensure_known_types(per_text_spans: Sequence[list[Span]], catalogue: Catalogue) -> None:
    """Raise before any text in the batch is resolved, if any span names a
    type the catalogue does not define.

    ``EntityResolver.resolve()`` already validates every span it is given
    before writing any of them — but that promise is scoped to a single call,
    and ``pseudonymize_batch`` calls it once per text. Left to ``resolve()``
    alone, a bad type in text 3 would only surface when its own call ran, by
    which point text 1 and text 2 had already gone through *their* calls and
    written their entities. One pass over every text's spans here, before the
    first ``resolve()`` call, is what extends "nothing is written before
    every span is confirmed" from one document to the whole batch.
    """
    for index, spans in enumerate(per_text_spans):
        for span in spans:
            if span.type not in catalogue.types:
                raise UnknownEntityTypeError(
                    f"text {index}: span at {span.start} claims type "
                    f"{span.type!r}, which the catalogue does not define"
                )
