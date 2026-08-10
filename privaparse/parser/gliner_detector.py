"""GLiNER2 backend.

Isolated in its own module so that importing PrivaParse does not import torch.
Milestones A–C, the whole test suite and the regex-only CLI all run without the
model backend installed.

Two things this module is careful about:

**Offsets.** Everything downstream addresses text by character offset, so a
span whose offsets do not actually describe its own text would rewrite the wrong
part of the document — and leak the entity it was supposed to hide. Every span
the model returns is verified against the source, repaired if it can be located
nearby, and dropped otherwise.

**Chunking.** Long documents are split here rather than handed to
``extract_entities_long``, because splitting locally means the offsets are ours
and can be verified. The chunks still go through the model in one batch via
``batch_extract_entities``, so nothing is lost in throughput.

Chunk size is a recall setting, not only a speed setting. GLiNER scores
candidate spans against the whole chunk, so a longer chunk means more competing
candidates and names that slip under the threshold. Measured on a 7.2 KB German
document: ``chunk_chars=1500`` gave PERSON recall 0.900, ``512`` gave 0.950 at
identical precision, and ``512`` was also the faster of the two on GPU. See
``docs/performance-notes.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from privaparse.app.logging import get_logger
from privaparse.parser.types import SOURCE_GLINER, Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.config import Settings
    from privaparse.app.device import ResolvedDevice

log = get_logger("gliner")

__all__ = ["GlinerDetector", "Chunk", "chunk_text"]

_WARMUP_TEXT = "Herr Max Mustermann, max@test.de, +49 170 1234567."

#: How far from the reported offset to look when repairing a span.
_REPAIR_WINDOW = 64


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    offset: int


class GlinerDetector:
    """Runs GLiNER2 over (possibly chunked) text and returns verified spans."""

    def __init__(
        self,
        settings: "Settings",
        device: "ResolvedDevice",
        *,
        model: Any = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.settings = settings
        self.device = device
        self.schema = settings.entity_schema
        self._label_to_type = settings.catalogue.label_to_type()
        #: Optional (done, total) callback so a CLI can draw a progress bar.
        self._progress = progress
        self._model = model if model is not None else self._load_model()

        if settings.warmup:
            self._warmup()

    # --- model lifecycle ---------------------------------------------------

    def _load_model(self) -> Any:
        from gliner2 import GLiNER2

        if self.settings.flash_attention:
            _enable_flash_deberta()

        kwargs: dict[str, Any] = {"map_location": self.device.device}
        if self.device.quantize:
            kwargs["quantize"] = True
        if self.device.compile:
            kwargs["compile"] = True

        log.info(
            "loading %s (%s)", self.settings.model_id, self.device.describe()
        )
        return GLiNER2.from_pretrained(self.settings.model_id, **kwargs)

    def _warmup(self) -> None:
        """Pay the compile and CUDA-init cost now, not on the first real request."""
        try:
            self._extract([Chunk(_WARMUP_TEXT, 0)])
        except Exception as exc:  # pragma: no cover - warmup must never be fatal
            log.warning("warmup pass failed (continuing): %s", exc)
        else:
            log.debug("warmup complete")

    # --- detection ---------------------------------------------------------

    def detect(self, text: str) -> list[Span]:
        """Convenience wrapper: one text through the batched path."""
        return self.detect_many([text])[0]

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        """One model batch across every text.

        Chunking already happens per text; this flattens all the chunks into
        one submission so a request carrying fifty short strings costs one
        batched pass rather than fifty single-chunk ones.
        """
        chunk_groups = [
            chunk_text(text, self.settings.chunk_chars) if text.strip() else []
            for text in texts
        ]
        flat: list[Chunk] = [chunk for group in chunk_groups for chunk in group]
        if not flat:
            return [[] for _ in texts]

        results = self._extract(flat)

        out: list[list[Span]] = []
        cursor = 0
        for text, group in zip(texts, chunk_groups):
            if not group:
                out.append([])
                continue
            slice_ = results[cursor : cursor + len(group)]
            cursor += len(group)
            out.append(self._to_spans(slice_, group, text))
        return out

    def _extract(self, chunks: Sequence[Chunk]) -> list[dict[str, Any]]:
        """Run the model over every chunk, reporting progress on long documents.

        Batches are submitted one at a time rather than handing the whole list
        to the library, so there is something to report between them. A 119 KB
        document is 375 chunks and over a minute of silence otherwise, which
        looks exactly like a hang.
        """
        texts = [c.text for c in chunks]
        options = {"include_spans": True, "include_confidence": True}

        if len(texts) == 1:
            return [self._model.extract_entities(texts[0], self.schema, **options)]

        batch_size = self.settings.batch_size
        results: list[dict[str, Any]] = []
        report_every = max(batch_size, (len(texts) // 10) or 1)
        next_report = report_every

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            results.extend(
                self._model.batch_extract_entities(
                    batch, self.schema, batch_size=batch_size, **options
                )
            )
            if self._progress is not None:
                self._progress(len(results), len(texts))
            if len(results) >= next_report:
                log.info("scanning: %d/%d chunks", len(results), len(texts))
                next_report += report_every

        return results

    def _to_spans(
        self,
        results: Sequence[dict[str, Any]],
        chunks: Sequence[Chunk],
        text: str,
    ) -> list[Span]:
        spans: dict[tuple[int, int, str], Span] = {}
        dropped = 0

        for result, chunk in zip(results, chunks):
            for label, items in (result or {}).get("entities", {}).items():
                entity_type = self._label_to_type.get(label.lower())
                if entity_type is None:
                    continue

                for item in items:
                    span = self._build_span(item, chunk, text, entity_type)
                    if span is None:
                        dropped += 1
                        continue
                    # Chunks overlap, so the same entity can arrive twice.
                    spans.setdefault((span.start, span.end, str(span.type)), span)

        if dropped:
            log.warning("dropped %d span(s) whose offsets could not be verified", dropped)
        return sorted(spans.values(), key=lambda s: s.start)

    def _build_span(
        self,
        item: Any,
        chunk: Chunk,
        text: str,
        entity_type: str,
    ) -> Span | None:
        surface, local_start, local_end, score = _unpack(item)
        if not surface:
            return None

        start = _locate(
            text,
            surface,
            hint=chunk.offset + local_start if local_start is not None else chunk.offset,
        )
        if start is None:
            log.debug("could not place %r in the source text", entity_type)
            return None

        end = start + len(surface)
        if local_end is not None and local_start is not None:
            expected = local_end - local_start
            if expected != len(surface):
                log.debug("model span length disagreed with its own text; trusting the text")

        return Span(
            start=start,
            end=end,
            text=surface,
            type=entity_type,
            score=score,
            source=SOURCE_GLINER,
        )


# --- helpers ---------------------------------------------------------------


def _enable_flash_deberta() -> None:
    """Opt into the FlashDeberta attention kernels, if they are installed.

    Worth having because self-attention is the actual bottleneck here: cost per
    chunk grows quadratically with chunk length, which is why a 1500-character
    window costs 4.6x what a 512-character one does rather than 3x. Flash
    attention attacks exactly that term.

    ``gliner2`` reads the environment variable when the model is constructed, so
    this has to run before ``from_pretrained``. Like ``torch.compile``, an
    unavailable accelerator warns and continues — it changes speed, not results.
    """
    import importlib.util
    import os

    if importlib.util.find_spec("flashdeberta") is None:
        log.warning(
            "flash_attention is enabled but the 'flashdeberta' package is not "
            "installed; continuing with standard attention"
        )
        return

    os.environ["USE_FLASHDEBERTA"] = "1"
    log.info("FlashDeberta attention enabled")


def _unpack(item: Any) -> tuple[str, int | None, int | None, float]:
    """Accept both the plain-string and the dict form GLiNER2 can return."""
    if isinstance(item, str):
        return item, None, None, 1.0
    if isinstance(item, dict):
        return (
            str(item.get("text", "")),
            _as_int(item.get("start")),
            _as_int(item.get("end")),
            float(item.get("confidence", item.get("score", 1.0))),
        )
    return "", None, None, 1.0


def _as_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _locate(text: str, surface: str, *, hint: int) -> int | None:
    """Find ``surface`` in ``text``, preferring a position near ``hint``.

    Tokenizer offsets can be off by a character or two; rather than trusting
    them blindly or discarding a real detection, look for the actual string
    close to where the model said it was.
    """
    if 0 <= hint <= len(text) - len(surface) and text.startswith(surface, hint):
        return hint

    window_start = max(0, hint - _REPAIR_WINDOW)
    window_end = min(len(text), hint + len(surface) + _REPAIR_WINDOW)
    local = text.find(surface, window_start, window_end)
    if local != -1:
        return local

    # Last resort: a unique occurrence anywhere is still unambiguous.
    first = text.find(surface)
    if first != -1 and text.find(surface, first + 1) == -1:
        return first
    return None


def chunk_text(text: str, max_chars: int, overlap: int = 200) -> list[Chunk]:
    """Split at paragraph boundaries, keeping a small overlap.

    The overlap exists so an entity sitting on a chunk boundary is seen whole by
    at least one chunk; duplicates are removed afterwards by offset.

    Two guards, both learned the hard way. ``overlap`` is capped at a quarter of
    the window, and each step is required to advance. Without them a small
    ``max_chars`` degenerates: ``_split_point`` may end a chunk halfway into the
    window, ``end - overlap`` then lands *behind* where the chunk started, and
    the loop crawls forward a character at a time. Measured at
    ``max_chars=256``, that turned a 7.3 KB document into 2374 chunks and 203
    seconds of work instead of roughly 30 chunks and 5 seconds.
    """
    if len(text) <= max_chars:
        return [Chunk(text, 0)]

    overlap = max(0, min(overlap, max_chars // 4))

    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            end = _split_point(text, start, end)
        chunks.append(Chunk(text[start:end], start))
        if end >= len(text):
            break
        # Never step backwards, and never skip text: falling back to `end` drops
        # the overlap for this one boundary rather than stalling or leaving a gap.
        next_start = end - overlap
        start = next_start if next_start > start else end
    return chunks


def _split_point(text: str, start: int, end: int) -> int:
    """Prefer a paragraph break, then a line break, then a hard cut."""
    floor = start + (end - start) // 2
    for separator in ("\n\n", "\n", " "):
        index = text.rfind(separator, floor, end)
        if index != -1:
            return index + len(separator)
    return end
