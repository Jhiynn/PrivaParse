"""How often does a real value actually come back, per gateway configuration?

Measures the round trip end to end: a prompt carrying real PII goes into the
gateway, the model answers over placeholders, and the question is whether the
caller gets their own value back. Run it once per configuration --
`PRIVAPARSE_GATEWAY_FUZZY` and `PRIVAPARSE_GATEWAY_HINT` are read by the
server, so the gateway has to be restarted between runs.

    python eval/restore_matrix.py --label "fuzzy=off hint=off" --model qwen

Prints one JSON line per run, so a shell loop over the four combinations
produces something a table can be built from.
"""

from __future__ import annotations

import argparse
import json

import httpx

# Values the regex backstops catch on their own, so the measurement is about
# restoration rather than about detection quality.
EMAIL = "beate.sonderzeichen@musterfirma-testxyz.de"
PHONE = "+49 170 1234567"

CASES = [
    ("echo the address", f"Wiederhole exakt, ohne Kommentar:\nKontakt: {EMAIL}", EMAIL, False),
    ("echo the phone", f"Wiederhole exakt, ohne Kommentar:\nTel: {PHONE}", PHONE, False),
    ("answer a question",
     f"Die Rueckfrage geht an {EMAIL}. An welche Adresse soll ich schreiben? "
     "Antworte in einem kurzen Satz.", EMAIL, False),
    ("use it in a sentence",
     f"Schreibe einen Satz, in dem die Adresse {EMAIL} unveraendert vorkommt.",
     EMAIL, False),
    ("json output",
     f'Gib nur JSON zurueck, ohne Erklaerung: {{"empfaenger": "{EMAIL}"}}', EMAIL, False),
    ("streamed echo", f"Wiederhole exakt, ohne Kommentar:\nKontakt: {EMAIL}", EMAIL, True),
]


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


def run_case(base: str, model: str, prompt: str, stream: bool) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 90}
    if stream:
        body["stream"] = True
        with httpx.stream("POST", f"{base}/chat/completions", json=body,
                          timeout=180) as response:
            return _sse_content("\n\n".join(response.iter_lines()))
    response = httpx.post(f"{base}/chat/completions", json=body, timeout=180)
    return response.json()["choices"][0]["message"]["content"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787/v1")
    parser.add_argument("--model", default="qwen")
    parser.add_argument("--label", default="unlabelled")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    restored, rows = 0, []
    for name, prompt, expected, stream in CASES:
        try:
            answer = run_case(args.url, args.model, prompt, stream)
        except Exception as error:  # noqa: BLE001 - a run reports, never raises
            rows.append({"case": name, "ok": False, "error": f"{type(error).__name__}"})
            continue
        ok = expected in answer
        restored += ok
        rows.append({"case": name, "ok": ok, "answer": answer.strip()[:100]})
        if args.verbose:
            print(f"  [{'ok ' if ok else 'MISS'}] {name}: {json.dumps(answer.strip()[:90])}")

    print(json.dumps({
        "label": args.label,
        "restored": restored,
        "total": len(CASES),
        "cases": {row["case"]: bool(row.get("ok")) for row in rows},
    }))


if __name__ == "__main__":
    main()
