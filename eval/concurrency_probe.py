"""Fire N requests at a running gateway at once; check nobody gets anybody else's data.

Every request carries a value only it knows. The provider echoes placeholders
back, so a caller only ever sees a real address again if restoration worked --
and if it resolved against *its own* mapping. A response holding another
request's address is the failure this exists to find: one shared vault, N
concurrent mappings, and `reverse` scoped to exactly one of them.

This is what caught the registry race fixed in `parser/registry.py`: seven of
twenty-four concurrent requests came back 500 on a cold process, because a
lazy import handed every thread arriving mid-import an empty backstop table.
Nothing in the unit suite could see it -- a race needs concurrency and a cold
process at the same moment.

    python -m uvicorn eval.stub_provider:app --host 127.0.0.1 --port 9000 &
    PRIVAPARSE_DETECTOR=regex PRIVAPARSE_GATEWAY_UPSTREAM=http://127.0.0.1:9000 \
        privaparse serve &
    python eval/concurrency_probe.py 64 --stream
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

GATEWAY = "http://127.0.0.1:8787/v1/chat/completions"


def _sse_content(raw: str) -> str:
    out = []
    for block in raw.split("\n\n"):
        line = block.strip()
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        for choice in json.loads(line[6:]).get("choices", []):
            piece = choice.get("delta", {}).get("content")
            if isinstance(piece, str):
                out.append(piece)
    return "".join(out)


async def one(client: httpx.AsyncClient, index: int, stream: bool) -> dict:
    address = f"kunde{index}@beispiel{index}.de"
    body = {
        "model": "stub-model",
        "messages": [{"role": "user", "content": f"Bitte antworte an {address}"}],
    }
    if stream:
        body["stream"] = True

    try:
        response = await client.post(GATEWAY, json=body, timeout=180)
    except Exception as error:  # noqa: BLE001 - the probe reports, never raises
        return {"i": index, "address": address, "error": f"{type(error).__name__}: {error}"}

    if response.status_code != 200:
        return {"i": index, "address": address,
                "error": f"HTTP {response.status_code}", "body": response.text[:200]}

    text = _sse_content(response.text) if stream else \
        response.json()["choices"][0]["message"]["content"]
    return {"i": index, "address": address, "text": text}


async def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    stream = "--stream" in sys.argv

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(one(client, i, stream) for i in range(count)))

    failures = [r for r in results if r.get("error")]
    missing = [r for r in results if not r.get("error") and r["address"] not in r["text"]]
    leaked = []
    for row in results:
        if row.get("error"):
            continue
        for other in results:
            if other["i"] != row["i"] and other["address"] in row["text"]:
                leaked.append({"request": row["i"], "saw": other["i"]})

    print(json.dumps({
        "requests": count,
        "streaming": stream,
        "errors": len(failures),
        "not_restored": len(missing),
        "cross_talk": len(leaked),
    }))
    for row in (failures + missing)[:5]:
        print("  detail:", json.dumps(row)[:300])
    for row in leaked[:5]:
        print("  LEAK:", row)

    raise SystemExit(1 if (failures or missing or leaked) else 0)


if __name__ == "__main__":
    asyncio.run(main())
