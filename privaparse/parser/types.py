"""Core value types shared by the detection pipeline."""

from __future__ import annotations

from dataclasses import dataclass


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


#: Where a span came from. Used by the merge step to break ties.
SOURCE_REGEX = "regex"
SOURCE_GLINER = "gliner"
SOURCE_COREF = "coref"


@dataclass(frozen=True, slots=True)
class Span:
    """A detected entity, addressed by character offsets into the source text.

    Offsets always refer to the **original** document, never to a masked or
    chunked view — every producer is responsible for translating back before
    handing a span on.
    """

    start: int
    end: int
    text: str
    type: str
    score: float = 1.0
    source: str = SOURCE_REGEX
    #: The model label this span came from, when it came from the model. None
    #: for backstop and sweep spans. Diagnostic only — nothing in the pipeline
    #: branches on it. It exists so the evaluation can say *which* of the
    #: labels feeding a type produced a false positive.
    label: str | None = None

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"span start must be >= 0, got {self.start}")
        if self.end <= self.start:
            raise ValueError(f"span end {self.end} must be greater than start {self.start}")
        if not self.type:
            raise ValueError("span type must not be empty")

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end

    def verify_against(self, text: str) -> bool:
        """True if this span still points at its own text in ``text``."""
        return text[self.start : self.end] == self.text

    def shifted(self, offset: int) -> Span:
        """Same span, relocated by ``offset`` characters."""
        return Span(
            start=self.start + offset,
            end=self.end + offset,
            text=self.text,
            type=self.type,
            score=self.score,
            source=self.source,
            label=self.label,
        )
