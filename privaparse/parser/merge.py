"""Turn raw detector output into a clean, non-overlapping span list.

Three jobs, in order:

1. **Filter and tidy** — drop low-confidence spans, trim the punctuation the
   model likes to include at span edges.
2. **Resolve overlaps** — when two detectors claim overlapping text, pick one.
3. **Coreference sweep** — re-find every accepted surface form elsewhere in the
   document, so a name the model caught in line 3 and missed in line 40 still
   gets its placeholder in both places.

Step 3 exists because of the asymmetry that runs through this whole tool: a
missed entity leaves the machine, a spurious one only costs readability.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterable, Sequence

from privaparse.app.logging import get_logger
from privaparse.parser import registry
from privaparse.parser.markdown import ProtectedText
from privaparse.parser.types import SOURCE_COREF, SOURCE_GLINER, SOURCE_REGEX, EntityType, Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue

log = get_logger("merge")

__all__ = ["merge_spans", "coreference_sweep", "resolve_spans", "span_priority"]

# Hyphens are deliberately absent: German double-barrelled names
# (Müller-Lüdenscheidt) would lose half their surface form.
_LEADING_TRIM = " \t\r\n(<[{\"'„»‚‹*_"
_TRAILING_TRIM = " \t\r\n.,;:!?)>]}\"'“«‘›*_"

#: Minimum surface length before a value is worth sweeping for. Below this,
#: substring matching produces noise ("Li" inside "Lieferung").
_MIN_SWEEP_LENGTH = 3

_TYPE_RANK = {"EMAIL": 3, "PHONE": 3, "PERSON": 1}
_SOURCE_RANK = {SOURCE_REGEX: 3, SOURCE_GLINER: 2, SOURCE_COREF: 1}


def span_priority(span: Span) -> int:
    """Higher wins an overlap.

    Type dominates source: a PERSON span overlapping an email is nearly always
    the model grabbing the local part, and the email is the better answer even
    though it came from a "dumber" detector.
    """
    return _TYPE_RANK.get(span.type, 1) * 10 + _SOURCE_RANK.get(span.source, 1)


def resolve_spans(
    protected: ProtectedText,
    spans: Iterable[Span],
    *,
    threshold: float = 0.5,
    sweep: bool = True,
    catalogue: "Catalogue | None" = None,
) -> list[Span]:
    """Full cleanup: merge, then optionally sweep, then merge again."""
    merged = merge_spans(spans, protected=protected, threshold=threshold, catalogue=catalogue)
    if not sweep:
        return merged

    extra = coreference_sweep(merged, protected)
    if not extra:
        return merged
    # The sweep can only add spans, so a second merge just settles overlaps
    # between new and existing ones.
    return merge_spans(
        [*merged, *extra], protected=protected, threshold=threshold, catalogue=catalogue
    )


def merge_spans(
    spans: Iterable[Span],
    *,
    protected: ProtectedText | None = None,
    threshold: float = 0.5,
    catalogue: "Catalogue | None" = None,
) -> list[Span]:
    """Drop weak spans, trim edges, and resolve overlaps greedily by priority."""
    candidates: list[Span] = []
    for span in spans:
        if span.score < threshold:
            continue
        trimmed = _trim(span, protected.original if protected else None)
        if trimmed is None:
            continue
        if not _passes_rule_check(trimmed, catalogue):
            log.debug("dropping %s span that fails its own syntax rule", trimmed.type)
            continue
        if protected is not None and protected.is_protected(trimmed.start, trimmed.end):
            # Belt and braces: the masked view should already have hidden this.
            continue
        candidates.append(trimmed)

    # Best first, so a simple greedy pass is enough.
    candidates.sort(key=lambda s: (-span_priority(s), -s.length, -s.score, s.start))

    accepted: list[Span] = []
    for span in candidates:
        if any(span.overlaps(other) for other in accepted):
            continue
        accepted.append(span)

    accepted.sort(key=lambda s: s.start)
    return accepted


def coreference_sweep(
    accepted: Sequence[Span],
    protected: ProtectedText,
) -> list[Span]:
    """Find further occurrences of already-accepted surface forms.

    Only searches the masked view, so repeats inside code fences stay untouched.
    """
    if not accepted:
        return []

    view = protected.view
    extra: list[Span] = []
    seen: set[tuple[int, int]] = {(s.start, s.end) for s in accepted}

    for span in _unique_by_surface(accepted):
        surface = span.text.strip()
        if len(surface) < _MIN_SWEEP_LENGTH:
            continue

        pattern = _sweep_pattern(surface, span.type)
        for match in pattern.finditer(view):
            key = (match.start(), match.end())
            if key in seen:
                continue
            if any(match.start() < s.end and s.start < match.end() for s in accepted):
                continue
            seen.add(key)
            extra.append(
                Span(
                    start=match.start(),
                    end=match.end(),
                    text=protected.original[match.start() : match.end()],
                    type=span.type,
                    score=span.score,
                    source=SOURCE_COREF,
                )
            )

    if extra:
        log.debug("coreference sweep added %d span(s)", len(extra))
    return extra


# --- helpers ---------------------------------------------------------------


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


def _unique_by_surface(spans: Sequence[Span]) -> list[Span]:
    """One representative per (type, surface form)."""
    seen: set[tuple[str, str]] = set()
    out: list[Span] = []
    for span in spans:
        key = (span.type, span.text.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(span)
    return out


def _sweep_pattern(surface: str, entity_type: str) -> re.Pattern[str]:
    escaped = re.escape(surface)
    if entity_type == EntityType.EMAIL:
        # Addresses are case-insensitive in practice, and boundaries stop
        # "max@test.de" from matching inside "notmax@test.de".
        return re.compile(rf"(?<![\w.+-]){escaped}(?![\w-])", re.IGNORECASE)
    if entity_type == EntityType.PERSON:
        return re.compile(rf"(?<!\w){escaped}(?!\w)")
    return re.compile(escaped)


def _trim(span: Span, text: str | None) -> Span | None:
    """Strip leading/trailing noise from a span. Exact detectors are left alone."""
    if span.source == SOURCE_REGEX:
        return span

    surface = span.text
    start, end = span.start, span.end

    lead = len(surface) - len(surface.lstrip(_LEADING_TRIM))
    if lead:
        surface = surface[lead:]
        start += lead

    trail = len(surface) - len(surface.rstrip(_TRAILING_TRIM))
    if trail:
        surface = surface[:-trail]
        end -= trail

    if not surface or end <= start:
        return None
    if start == span.start and end == span.end:
        return span

    if text is not None and text[start:end] != surface:
        # Offsets and surface disagreed before trimming; trust neither.
        log.debug("dropping span with inconsistent offsets at %d", span.start)
        return None

    return Span(
        start=start,
        end=end,
        text=surface,
        type=span.type,
        score=span.score,
        source=span.source,
    )
