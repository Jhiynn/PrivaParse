"""Measure what the gateway adds to a request, on a coding-agent payload.

The number an operator cares about is not the provider's latency -- that is
theirs, and it dwarfs everything here. It is what PrivaParse costs on top:
extraction, detection, the vault write and the write-back, before a byte is
forwarded. So the provider is a stub. Nothing leaves the machine and no API
key is needed.

Two sizes, because a coding agent's first turn is not a chat message: 50 KB is
a modest working set (a few files plus a system prompt), 200 KB is a large
one. Two conditions, because the first turn is the worst case and no other
turn looks like it -- the second sends the same history back with one message
appended, and the detection cache answers for everything it has already seen.

Run it where the model actually runs. On a laptop GPU held in its idle power
state the numbers are meaningless (docs/performance-notes.md).

    python eval/gateway_latency.py --sizes 50 200 --repeats 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

from starlette.testclient import TestClient

from privaparse.app.config import Settings
from privaparse.engine import PrivaParseEngine
from privaparse.gateway.server import STATS_PATH, create_app

# A turn of a coding agent: a system prompt, a file the user pasted, a
# question about it. The names and addresses are the ones the gold set uses,
# so the detector has something real to find rather than a wall of lorem.
_PROSE = """\
Der Kunde Max Mustermann (max.mustermann@example.de, +49 170 1234567) meldet,
dass der Import abbricht. Seine Kollegin Erika Musterfrau hat den Fehler auf
Zeile 42 eingegrenzt. Rechnungsadresse: Hauptstrasse 12, 10115 Berlin.
"""

_CODE = '''\
def load_customer(session, customer_id: str) -> Customer:
    """Fetch one customer, or raise."""
    row = session.execute(
        select(Customer).where(Customer.id == customer_id)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"no customer {customer_id}")
    return row
'''


def _message(target_bytes: int) -> str:
    """A block of roughly `target_bytes`, two parts code to one part prose.

    Real agent traffic is mostly source. Code is masked before detection, so a
    payload of pure prose would overstate the cost and one of pure code would
    understate it.
    """
    unit = f"{_PROSE}\n```python\n{_CODE}```\n\n```python\n{_CODE}```\n\n"
    repeats = max(1, target_bytes // len(unit.encode("utf-8")))
    return unit * repeats


def _request(kilobytes: int) -> dict[str, Any]:
    body = _message(kilobytes * 1024)
    return {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a careful software engineer."},
            {"role": "user", "content": body},
            {"role": "user", "content": "Warum bricht der Import ab?"},
        ],
    }


class _StubUpstream:
    """The provider, minus the provider."""

    reply = {
        "id": "chatcmpl-bench",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    async def post_json(self, path, body, headers):
        return 200, self.reply, {"content-type": "application/json"}

    async def get_json(self, path, headers):
        return 200, self.reply, {"content-type": "application/json"}

    async def stream(self, path, body, headers):
        yield b"data: [DONE]\n\n"


def _measure(client: TestClient, payload: dict, repeats: int) -> list[float]:
    timings = []
    for _ in range(repeats):
        started = time.perf_counter()
        response = client.post("/v1/chat/completions", json=payload)
        timings.append(time.perf_counter() - started)
        if response.status_code != 200:
            raise SystemExit(f"the gateway refused the payload: {response.text[:200]}")
    return timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 200])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--db", default="gateway-bench.db")
    args = parser.parse_args()

    settings = Settings(db_path=args.db)
    engine = PrivaParseEngine(settings)
    print(f"device: {engine.device.describe()}")
    print(f"model:  {settings.model_id}")
    # Warm up at full size, not with a short string. On CUDA `compile` defaults
    # to on, and torch.compile fires on the first batch of a given shape -- so a
    # one-sentence warmup leaves the whole compilation in the first measured
    # request and produces the tell-tale result of a 50 KB payload timing slower
    # than a 200 KB one. `detect` is read-only: it touches neither the vault nor
    # the gateway's cache, so the measured runs still start cold.
    print("warming up at full payload size ...")
    engine.detect(_message(max(args.sizes) * 1024))

    rows = []
    for kilobytes in args.sizes:
        client = TestClient(create_app(settings, engine=engine, upstream=_StubUpstream()))
        payload = _request(kilobytes)
        actual = len(json.dumps(payload).encode("utf-8")) / 1024

        cold = _measure(client, payload, 1)[0]
        warm = _measure(client, payload, args.repeats)
        stats = client.get(STATS_PATH).json()

        rows.append({
            "requested_kb": kilobytes,
            "actual_kb": round(actual, 1),
            "cold_s": round(cold, 3),
            "warm_median_s": round(statistics.median(warm), 3),
            "warm_min_s": round(min(warm), 3),
            "warm_max_s": round(max(warm), 3),
            "entities": stats["entities_per_request"],
            "cache_hit_rate": stats["cache"]["hit_rate"],
        })
        print(json.dumps(rows[-1]))

    print()
    print("Cold = detection cache empty, model already warm: the first turn of a")
    print("conversation against a gateway that has been up for a while.")
    print()
    print("| Payload | Cold (first turn) | Warm median | Warm range | Entities | Cache hits |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in rows:
        print(
            f"| {row['actual_kb']} KB | {row['cold_s']:.2f} s | "
            f"{row['warm_median_s']:.2f} s | "
            f"{row['warm_min_s']:.2f}-{row['warm_max_s']:.2f} s | "
            f"{row['entities']} | {row['cache_hit_rate']} |"
        )


if __name__ == "__main__":
    main()
