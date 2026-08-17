"""The detection pass: everything that turns a document into its spans.

Masking, the detector, the threshold, merging and the coreference sweep, in one
place and in one order. Four call sites used to write this longhand, each
re-deriving the same four ``Settings`` fields, and no module owned the order.

The pass deliberately does **not** satisfy the :class:`Detector` protocol — see
ADR 0004. A detector is handed the masked view and *proposes* candidate spans;
the pass is handed the original document and *decides* which spans survive.
Sharing the verb ``detect`` is what let those two contracts be confused, and it
would make it legal to nest a pass inside a ``CompositeDetector``: a document
masked twice, at a seam where the offsets are no longer the ones the caller
holds. The method names here are chosen so that cannot happen structurally.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from privaparse.parser.detector import Detector, detect_batch
from privaparse.parser.markdown import ProtectedText, protect
from privaparse.parser.merge import resolve_spans
from privaparse.parser.types import Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue
    from privaparse.app.config import Settings

__all__ = ["DetectionPass"]

@dataclass(frozen=True, slots=True)
class DetectionPass:
    """A detector plus the four values that decide what its output becomes.

    The values are carried explicitly rather than as a ``Settings``: a caller
    that wants one point on a threshold curve asks for a :meth:`replace`d
    variant, and nothing downstream has to defend against a settings object
    that may or may not have the field it needs.

    Read-only — nothing here touches the vault, which is why the class lives
    beside the detector, the merge and the markdown protection it composes
    rather than inside the pseudonymizer.
    """

    detector: Detector
    threshold: float
    sweep: bool
    scan_code: bool
    catalogue: Catalogue

    @classmethod
    def from_settings(cls, settings: Settings, detector: Detector) -> DetectionPass:
        """The pass a given configuration describes."""
        return cls(
            detector=detector,
            threshold=settings.threshold,
            sweep=settings.coreference_sweep,
            scan_code=settings.scan_code,
            catalogue=settings.catalogue,
        )

    def replace(
        self,
        *,
        threshold: float | None = None,
        sweep: bool | None = None,
        scan_code: bool | None = None,
        catalogue: Catalogue | None = None,
    ) -> DetectionPass:
        """The same pass with one or more of the four values changed.

        What the *threshold sweep* asks for per point on its curve: the
        detector and its expensive output stay put, only the decision changes.
        ``sweep`` here is the coreference sweep, the value, not that evaluation.

        ``None`` means "leave this one alone" — none of the four has a
        meaningful ``None`` of its own, so the sentinel costs nothing and keeps
        the signature type-checkable. The detector is deliberately not
        replaceable: a variant is a different decision over the same
        candidates, not a different set of them.
        """
        return dataclasses.replace(
            self,
            threshold=self.threshold if threshold is None else threshold,
            sweep=self.sweep if sweep is None else sweep,
            scan_code=self.scan_code if scan_code is None else scan_code,
            catalogue=self.catalogue if catalogue is None else catalogue,
        )

    def run(self, text: str) -> list[Span]:
        """The spans this document yields, start to finish.

        Literally :meth:`run_batch` over a one-element sequence — the same way
        single-text pseudonymisation delegates to the batch form — so the two
        cannot drift apart by construction.
        """
        return self.run_batch([text])[0]

    def run_batch(self, texts: Sequence[str]) -> list[list[Span]]:
        """The spans each of ``texts`` yields, with the detector called once.

        Detection is batched across the whole sequence so a detector that
        batches actually does; the decision then runs per text.
        """
        return [
            self.resolve(protected, candidates) for protected, candidates in self.scan_batch(texts)
        ]

    def scan(self, text: str) -> tuple[ProtectedText, list[Span]]:
        """The expensive half: the masked view plus the detector's candidates.

        Split out from :meth:`resolve` because the model's candidates do not
        depend on the threshold or the sweep. One pass over a document
        therefore produces every point on the threshold curve.
        """
        return self.scan_batch([text])[0]

    def scan_batch(self, texts: Sequence[str]) -> list[tuple[ProtectedText, list[Span]]]:
        """The expensive half across several documents, in one detector call.

        The detector is only ever shown ``ProtectedText.view`` — this method is
        the only place in this module that calls one, which is what enforces the
        invariant the ``Detector`` protocol's docstring states.
        """
        protected = [protect(text, scan_code=self.scan_code) for text in texts]
        candidates = detect_batch(self.detector, [p.view for p in protected])
        # `strict` because a detector that returns the wrong number of span
        # lists is a defect, and silently truncating it would hand the caller a
        # short batch that looks like a document with nothing in it.
        return list(zip(protected, candidates, strict=True))

    def resolve(self, protected: ProtectedText, candidates: Sequence[Span]) -> list[Span]:
        """The cheap half: the threshold, the merge and the coreference sweep.

        Given what :meth:`scan` returned, this is what the four values actually
        decide, and it is re-runnable as often as a caller has settings to try.
        """
        return resolve_spans(
            protected,
            candidates,
            threshold=self.threshold,
            sweep=self.sweep,
            catalogue=self.catalogue,
        )
