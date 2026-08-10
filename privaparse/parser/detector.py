"""Entity detectors.

Everything downstream talks to the :class:`Detector` protocol, which is what
lets the round-trip pipeline, the tests and the eval harness run against a
regex-only detector, a fake, or the real GLiNER2 model without changing a line.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterable, Protocol, Sequence, runtime_checkable

import phonenumbers

from privaparse.app.logging import get_logger
from privaparse.parser.types import SOURCE_REGEX, Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue
    from privaparse.app.config import Settings
    from privaparse.app.device import ResolvedDevice

log = get_logger("detector")

__all__ = [
    "Detector",
    "RegexDetector",
    "StaticDetector",
    "CompositeDetector",
    "build_default_detector",
    "is_valid_email",
    "is_valid_phone",
    "is_plausible_phone",
]

# Pragmatic rather than RFC-complete: the exotic corners of the address grammar
# (quoted local parts, comments) do not appear in the documents this handles,
# and matching them would cost precision everywhere else.
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}(?![\w-])"
)


def is_valid_email(text: str) -> bool:
    """True if ``text`` is entirely an email address.

    Used to check what a model proposes: a span that is not syntactically an
    address cannot be one, whatever the model's confidence says.
    """
    return _EMAIL_RE.fullmatch(text.strip()) is not None


def is_valid_phone(text: str, region: str = "DE") -> bool:
    """True if ``text`` is a number the national numbering plan recognises."""
    try:
        parsed = phonenumbers.parse(text.strip(), region)
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.is_valid_number(parsed)


#: A number needs at least this many digits before it is credible as one.
_MIN_PHONE_DIGITS = 7


def is_plausible_phone(text: str, region: str = "DE") -> bool:
    """True if ``text`` is *shaped* like a phone number, plan-valid or not.

    Deliberately weaker than :func:`is_valid_phone`. A number can be perfectly
    phone-shaped and still fail the numbering plan — a typo, a foreign format, a
    newly issued range the library's data predates, an internal extension. Those
    still have to be pseudonymised, so this check exists for spans the model has
    already judged to be phone numbers, where the only question left is whether
    the model was talking nonsense.

    ``+49 (0) 151 4433221`` is the case that motivated it: the model gave it
    confidence 1.00, the numbering plan rejects it (0151 wants eight subscriber
    digits, not seven), and the strict check silently sent it to the LLM.
    """
    stripped = text.strip()
    if sum(character.isdigit() for character in stripped) < _MIN_PHONE_DIGITS:
        return False
    try:
        parsed = phonenumbers.parse(stripped, region)
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.is_possible_number(parsed)


@runtime_checkable
class Detector(Protocol):
    """Finds entity spans in text. Offsets refer to the text as given."""

    def detect(self, text: str) -> list[Span]: ...

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        """Detect over several texts. Overridden where batching actually pays."""
        return [self.detect(text) for text in texts]


class RegexDetector:
    """Runs the backstop of every enabled type that has one.

    Recall insurance, not authority. Overlap resolution in ``merge`` gives the
    model the final word; these spans survive where the model found nothing.
    """

    def __init__(self, catalogue: "Catalogue") -> None:
        self.catalogue = catalogue

    def detect(self, text: str) -> list[Span]:
        from privaparse.parser import registry

        spans: list[Span] = []
        for placeholder in self.catalogue.enabled:
            if placeholder.backstop is None:
                continue
            finder = registry.get_backstop(placeholder.backstop)
            for start, end in finder(text):
                # The finder returns offsets, not a typed Span: it does not
                # know which placeholder it is serving; the catalogue does.
                spans.append(
                    Span(
                        start=start,
                        end=end,
                        text=text[start:end],
                        type=placeholder.name,
                        score=1.0,
                        source=SOURCE_REGEX,
                    )
                )
        return spans

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        return [self.detect(text) for text in texts]


class CompositeDetector:
    """Runs several detectors and concatenates their spans.

    Overlap resolution is deliberately *not* done here — that is
    :func:`privaparse.parser.merge.merge_spans`, which needs to see every
    candidate at once to make a sensible choice.
    """

    def __init__(self, detectors: Iterable[Detector]) -> None:
        self.detectors = list(detectors)

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for detector in self.detectors:
            spans.extend(detector.detect(text))
        return spans

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        per_text: list[list[Span]] = [[] for _ in texts]
        for detector in self.detectors:
            # Detector is a structural Protocol; nothing inherits its
            # detect_many default. GlinerDetector, and every hand-written test
            # fake, only ever defined detect — so the method may not exist.
            batch = getattr(detector, "detect_many", None)
            results = batch(texts) if batch else [detector.detect(t) for t in texts]
            for index, spans in enumerate(results):
                per_text[index].extend(spans)
        return per_text


class StaticDetector:
    """Returns a fixed span list. For tests and for benchmark baselines."""

    def __init__(self, spans: Sequence[Span] = ()) -> None:
        self.spans = list(spans)

    def detect(self, text: str) -> list[Span]:
        return [s for s in self.spans if s.end <= len(text)]

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        return [self.detect(text) for text in texts]


def build_default_detector(
    settings: "Settings", device: "ResolvedDevice", progress=None
) -> Detector:
    """Assemble the detector described by ``settings.detector``."""
    mode = settings.detector

    if mode == "regex":
        return RegexDetector(settings.catalogue)

    gliner = _build_gliner_detector(settings, device, progress=progress)
    if mode == "gliner":
        return gliner
    return CompositeDetector([gliner, RegexDetector(settings.catalogue)])


def _build_gliner_detector(
    settings: "Settings", device: "ResolvedDevice", progress=None
) -> Detector:
    try:
        from privaparse.parser.gliner_detector import GlinerDetector
    except ImportError as exc:  # pragma: no cover - exercised by install state
        raise RuntimeError(
            "The GLiNER2 backend is not installed. Either install it with\n"
            "    pip install -e '.[model]'\n"
            "or run with PRIVAPARSE_DETECTOR=regex for email and phone only."
        ) from exc
    return GlinerDetector(settings, device, progress=progress)
