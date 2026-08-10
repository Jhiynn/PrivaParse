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
from privaparse.parser.types import SOURCE_COREF, SOURCE_GLINER, SOURCE_REGEX, Span

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

#: Below this, a piece of a model span left over after trimming around an
#: exact span is more likely leftover noise than a name — a stray word or
#: two once the address or number it was wrapped around is cut away. A
#: separate constant from _MIN_SWEEP_LENGTH on purpose: the two floors
#: protect different steps and have no reason to move together.
_MIN_TRIM_LENGTH = 3

#: Breaks ties between overlapping spans of equal length, once length has
#: already decided what it can. It does not decide model-versus-exact
#: overlaps — `_trim_to_exact_spans` removes those before this ever runs —
#: so in practice this now only ever settles a tie between two spans of the
#: *same* source: two GLiNER guesses at a name's edges, or two regex spans
#: from different backstops.
_SOURCE_RANK = {SOURCE_GLINER: 3, SOURCE_REGEX: 2, SOURCE_COREF: 1}


def span_priority(span: Span) -> int:
    """Higher wins an overlap between spans of equal length."""
    return _SOURCE_RANK.get(span.source, 1)


def resolve_spans(
    protected: ProtectedText,
    spans: Iterable[Span],
    *,
    threshold: float = 0.5,
    sweep: bool = True,
    catalogue: "Catalogue | None",
) -> list[Span]:
    """Full cleanup: merge, then optionally sweep, then merge again."""
    merged = merge_spans(spans, protected=protected, threshold=threshold, catalogue=catalogue)
    if not sweep:
        return merged

    extra = coreference_sweep(merged, protected, catalogue=catalogue)
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
    catalogue: "Catalogue | None",
) -> list[Span]:
    """Drop weak spans, trim edges, cut model spans back from any exact span
    they overlap, and resolve what overlap is left greedily: longest span
    first, source breaking ties between equal lengths."""
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

    candidates = _trim_to_exact_spans(candidates, protected, catalogue)

    # Best first, so a simple greedy pass is enough. By this point every
    # model/exact overlap has already been resolved above, so this sort only
    # ever compares spans of the same source — two GLiNER guesses, or two
    # regex spans. There, "longer wins" is safe: it is one entity read two
    # ways, not a fuzzy guess sitting on top of a proven boundary, so nothing
    # is lost by keeping the fuller reading. That is not true across
    # sources — a longer model span can still bury a shorter, exact one
    # entirely — which is exactly why that case is resolved separately,
    # above, instead of by this key. Source only breaks a tie between spans
    # of identical length.
    candidates.sort(key=lambda s: (-s.length, -span_priority(s), -s.score, s.start))

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
    *,
    catalogue: "Catalogue | None",
) -> list[Span]:
    """Find further occurrences of already-accepted surface forms.

    Only searches the masked view, so repeats inside code fences stay untouched.

    ``catalogue`` is required, not defaulted, on purpose: a default of ``None``
    would make every type silently fall back to "word" sweeping, and that
    fallback is a behaviour change nothing would report. Pass ``None``
    explicitly where a test genuinely does not need catalogue-driven modes.
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

        if catalogue is None:
            mode = "word"
        elif span.type in catalogue.types:
            mode = catalogue.types[span.type].sweep
        else:
            # A real catalogue was given but does not recognise this type.
            # EntityResolver fails closed on the identical condition
            # (Catalogue.get raises for an unknown type) — guessing "word"
            # here would be the one place left that quietly did not.
            log.debug("skipping sweep for a type %r the catalogue does not know", span.type)
            continue
        pattern = _sweep_pattern(surface, mode)
        if pattern is None:
            continue
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
                    # No model label: this occurrence was never seen by the
                    # model, only found by re-scanning for a surface form
                    # accepted elsewhere. label stays None, same as a backstop.
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


def _trim_to_exact_spans(
    candidates: list[Span],
    protected: ProtectedText | None,
    catalogue: "Catalogue | None",
) -> list[Span]:
    """Cut model spans back from the boundary of any exact span they overlap.

    A SOURCE_REGEX span is not an estimate — it is checksum-gated or an exact
    syntax match (email, IBAN, card, IP) — so it is a proven boundary, not a
    competing guess. A SOURCE_GLINER span overlapping one is trimmed to
    whatever survives outside it: the model can be right about *what kind of
    thing it found* while still being wrong about *where its own span ends*.
    A PERSON tag that reaches into an address's local part is the model
    mis-drawing a boundary, not a second entity sharing the text.

    Runs once, before the greedy accept loop, not as part of it. A trimmed
    model span no longer overlaps the exact span that trimmed it, so the
    length-then-priority sort below never has to choose between a proven
    boundary and an estimated one — it only ever resolves ties between spans
    of the same source, where "longer wins" is actually safe (see the
    comment on that sort).
    """
    exact = [c for c in candidates if c.source == SOURCE_REGEX]
    if not exact:
        return candidates

    kept: list[Span] = []
    for span in candidates:
        if span.source != SOURCE_GLINER:
            kept.append(span)
            continue
        overlapping = [e for e in exact if span.overlaps(e)]
        if not overlapping:
            kept.append(span)
            continue

        narrowed = _largest_remainder(span, overlapping)
        if narrowed is None:
            continue  # entirely inside an exact span: not a separate entity

        if protected is not None and not narrowed.verify_against(protected.original):
            # Should be unreachable — a slice of a valid span's own text is
            # a valid span — but the boundary rule is new and a document is
            # never worth guessing about. Refuse rather than trust it.
            log.debug("dropping span with inconsistent offsets after trim at %d", span.start)
            continue

        # New territory the cut may have exposed (a trailing space where the
        # address used to start) gets the same edge trim any candidate gets,
        # and the same veto, in case narrowing changed whether the span
        # still looks like what it claims to be.
        narrowed = _trim(narrowed, protected.original if protected else None)
        if narrowed is None:
            continue
        if not _passes_rule_check(narrowed, catalogue):
            continue
        kept.append(narrowed)
    return kept


def _largest_remainder(span: Span, exact_spans: Sequence[Span]) -> Span | None:
    """The largest contiguous piece of ``span`` that lies outside every span
    in ``exact_spans``, or None if nothing long enough survives.

    "Largest" rather than "all pieces": a Span is one contiguous range, and
    when an exact span sits in the middle of a longer model span, splitting
    it in two, only one side can be kept without inventing a second span the
    model never proposed.
    """
    pieces = [(span.start, span.end)]
    for exact in exact_spans:
        pieces = [
            remainder
            for start, end in pieces
            for remainder in _subtract(start, end, exact.start, exact.end)
        ]
    if not pieces:
        return None

    # Longest wins; the leftmost of an exact tie breaks it, so the result
    # never depends on the order exact_spans happened to be found in.
    start, end = max(pieces, key=lambda piece: (piece[1] - piece[0], -piece[0]))
    if end - start < _MIN_TRIM_LENGTH:
        return None

    offset = start - span.start
    text = span.text[offset : offset + (end - start)]
    # Same model detection, just narrowed to the part outside the exact span —
    # not a new one, so it keeps the label that produced it. Without this, the
    # PERSON/EMAIL "local part" overlap (the exact scenario this function
    # exists for) would report every trimmed PERSON span as unlabelled.
    return Span(
        start=start,
        end=end,
        text=text,
        type=span.type,
        score=span.score,
        source=span.source,
        label=span.label,
    )


def _subtract(start: int, end: int, cut_start: int, cut_end: int) -> list[tuple[int, int]]:
    """``[start, end)`` with ``[cut_start, cut_end)`` removed, as 0-2 pieces."""
    pieces = []
    if start < cut_start:
        pieces.append((start, min(end, cut_start)))
    if end > cut_end:
        pieces.append((max(start, cut_end), end))
    return [piece for piece in pieces if piece[1] > piece[0]]


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


def _sweep_pattern(surface: str, sweep: str) -> re.Pattern[str] | None:
    """The rule for re-finding this value elsewhere in the document.

    ``off`` exists for types whose values are ordinary words. Sweeping for
    "Berlin" across a document produces more noise than protection, and the
    noise is indistinguishable from a detection failure when you read the
    output.

    ``word`` and ``exact`` compile to the identical pattern below: both are
    case-sensitive, boundary-anchored, literal matches. ``exact`` used to mean
    "no boundary at all", which is not a real distinction so much as a missing
    one — a five-digit POSTAL_CODE swept with the old ``exact`` pattern would
    happily match as a substring of an unrelated nine-digit customer number,
    splicing a placeholder into the middle of it. A sweep with no boundary
    check is not "exact", it is unbounded. Boundary-anchoring is not optional
    for any mode that runs by default, so there is nothing left for a separate
    branch to do differently. The catalogue keeps the two names distinct
    because they still document different intent for the type author — `word`
    for values whose surface form is prose (names), `exact` for values with no
    inflection (account numbers, secrets) — and a future mode could yet need
    to tell them apart (an inflection-tolerant `word`, say). There is no
    behavioural difference between them today.
    """
    escaped = re.escape(surface)
    if sweep == "off":
        return None
    if sweep == "icase":
        return re.compile(rf"(?<![\w.+-]){escaped}(?![\w-])", re.IGNORECASE)
    return re.compile(rf"(?<!\w){escaped}(?!\w)")


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
        label=span.label,
    )
