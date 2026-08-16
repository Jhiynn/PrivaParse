"""Detection results, reused between requests.

A chat client resends its whole history on every turn. Without a cache the
twentieth turn re-detects nineteen messages that have not changed since the
first, which is the dominant cost of the request path -- detection is the
expensive half, and it depends on nothing but the text and the catalogue.

**Only detection is cached.** Resolution, the vault write and the mapping row
run on every request. That is not an oversight to optimise away later: a
mapping is what ``reverse`` scopes an answer to, so a request that reused an
earlier one would restore against a mapping it never issued.

The key is ``(catalogue fingerprint, sha256(text))``. The fingerprint is
derived from the resolved catalogue rather than the file's mtime, because
mtime moves when nothing meaningful changed and stands still when a file is
restored from a backup -- both wrong in the direction that matters.

Nothing here is persisted or logged. Cached spans carry entity values, so the
cache holds PII in process memory for as long as a block stays resident; the
capacity cap is what bounds that.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import asdict
from typing import TYPE_CHECKING

from privaparse.parser.types import Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue
    from privaparse.engine import PrivaParseEngine

__all__ = ["CachingDetector", "DetectionCache", "catalogue_fingerprint"]


def catalogue_fingerprint(catalogue: Catalogue) -> str:
    """A stable digest of every enabled type.

    Any change to an enabled type invalidates every block cached before it --
    a wider rule than detection strictly needs, since a threshold is applied
    after detection and never reaches the model. The wider rule is the one
    worth having: it costs a cold cache after a catalogue edit, which is rare
    and self-inflicted, and it stays correct if the cache boundary ever moves
    to cover more of the pipeline. A rule tuned to exactly today's boundary
    would fail silently the day that happened.

    Disabled types are excluded, so toggling one is itself a change.
    """
    payload = {
        "version": catalogue.version,
        "types": [
            asdict(placeholder)
            for placeholder in sorted(catalogue.enabled, key=lambda t: t.name)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DetectionCache:
    """Bounded LRU from a text block to the spans a detector found in it."""

    def __init__(self, capacity: int = 2048) -> None:
        self.capacity = max(0, int(capacity))
        self.hits = 0
        self.misses = 0
        self._entries: OrderedDict[tuple[str, str], tuple[Span, ...]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _key(fingerprint: str, text: str) -> tuple[str, str]:
        # The text is hashed rather than stored: a key is not a place to keep a
        # document, and a digest is a fixed 64 bytes however long the block is.
        return fingerprint, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, fingerprint: str, text: str) -> list[Span] | None:
        key = self._key(fingerprint, text)
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        # A fresh list every time. The merge step is free to do what it likes
        # with what it is handed, and a caller must not be able to empty the
        # cache entry it just read.
        return list(entry)

    def put(self, fingerprint: str, text: str, spans: Sequence[Span]) -> None:
        if self.capacity == 0:
            return
        key = self._key(fingerprint, text)
        self._entries[key] = tuple(spans)
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        """Share of lookups served from the cache. 0.0 before the first one."""
        looked_up = self.hits + self.misses
        return self.hits / looked_up if looked_up else 0.0


class CachingDetector:
    """A :class:`~privaparse.parser.detector.Detector` that answers from cache.

    Wraps the engine's own detector rather than building one: the engine loads
    it lazily, and this must not be what forces the load. The engine is held
    rather than the detector for the same reason -- ``engine.detector`` is
    read only when something actually misses.
    """

    def __init__(self, engine: PrivaParseEngine, cache: DetectionCache) -> None:
        self._engine = engine
        self._cache = cache
        self._catalogue: Catalogue | None = None
        self._fingerprint = ""

    def _current_fingerprint(self) -> str:
        # Recomputed only when the catalogue object itself changes, which is
        # what a reload produces. Hashing 21 types on every request would be
        # measurable next to nothing else this class does.
        catalogue = self._engine.catalogue
        if catalogue is not self._catalogue:
            self._catalogue = catalogue
            self._fingerprint = catalogue_fingerprint(catalogue)
        return self._fingerprint

    def detect(self, text: str) -> list[Span]:
        return self.detect_many([text])[0]

    def detect_many(self, texts: Sequence[str]) -> list[list[Span]]:
        fingerprint = self._current_fingerprint()

        # Keyed by text, so a block repeated inside one request is looked up
        # once and detected at most once.
        known: dict[str, list[Span] | None] = {}
        for text in texts:
            if text not in known:
                known[text] = self._cache.get(fingerprint, text)

        missing = [text for text, spans in known.items() if spans is None]
        if missing:
            found = self._engine.detector.detect_many(missing)
            for text, spans in zip(missing, found):
                self._cache.put(fingerprint, text, spans)
                known[text] = list(spans)

        # Rebuilt in the caller's order: spans are offsets into their own
        # block, and returning them against the wrong one would rewrite the
        # document at positions that mean nothing.
        return [list(known[text] or ()) for text in texts]
