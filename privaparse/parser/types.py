"""Core value types shared by the detection pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EntityType(str, Enum):
    """Entity types supported in Phase 1."""

    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"

    def __str__(self) -> str:
        return self.value


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
    type: EntityType
    score: float = 1.0
    source: str = SOURCE_REGEX

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"span start must be >= 0, got {self.start}")
        if self.end <= self.start:
            raise ValueError(f"span end {self.end} must be greater than start {self.start}")

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def verify_against(self, text: str) -> bool:
        """True if this span still points at its own text in ``text``."""
        return text[self.start : self.end] == self.text

    def shifted(self, offset: int) -> "Span":
        """Same span, relocated by ``offset`` characters."""
        return Span(
            start=self.start + offset,
            end=self.end + offset,
            text=self.text,
            type=self.type,
            score=self.score,
            source=self.source,
        )
