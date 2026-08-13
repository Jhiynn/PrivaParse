"""What the gateway counts about itself.

Enough for an operator to see that it is working and roughly what it costs:
how many requests went through, how many entities they carried on average,
how long PrivaParse's own share of a request took, and how well the detection
cache is doing.

Nothing here holds text. Not a message, not an entity value, not a type name
-- a counter per type would tell whoever reads it that this vault has seen
health data, which is the kind of thing the tool exists to keep quiet about.
Every field is a number.

The latency recorded is PrivaParse's own share: extraction, detection and the
vault write, up to the moment the request is handed to the provider. The
provider's own time is not the gateway's to report, and including it would
bury the one number an operator can actually act on.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.gateway.cache import DetectionCache

__all__ = ["Metrics"]


class Metrics:
    """Running totals for one gateway process."""

    def __init__(self, window: int = 1024) -> None:
        self.requests = 0
        self.entities = 0
        # A median over every request since startup describes a week ago as
        # much as it describes now. The window keeps it about the present, and
        # keeps a long-running process from growing a list forever.
        self._latencies: deque[float] = deque(maxlen=window)

    def record(self, *, entities: int, seconds: float) -> None:
        self.requests += 1
        self.entities += entities
        self._latencies.append(seconds)

    @property
    def entities_per_request(self) -> float:
        return self.entities / self.requests if self.requests else 0.0

    @property
    def p50_seconds(self) -> float:
        if not self._latencies:
            return 0.0
        ordered = sorted(self._latencies)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    def snapshot(self, cache: DetectionCache) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "entities_per_request": round(self.entities_per_request, 2),
            "pseudonymize_p50_ms": round(self.p50_seconds * 1000, 1),
            "cache": {
                "hits": cache.hits,
                "misses": cache.misses,
                "hit_rate": round(cache.hit_rate, 3),
                "blocks": len(cache),
                "capacity": cache.capacity,
            },
        }
