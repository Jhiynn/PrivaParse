"""Does the model give a placeholder back exactly as it received it?

Restoration is an exact string match: `reverse` puts a value back only where
it finds the placeholder it issued, byte for byte. That makes model fidelity a
hard dependency of the whole design, and nothing measured it -- the test
suite's stub provider echoes perfectly, which is the one thing a real model
does not reliably do.

Point it at any OpenAI-compatible server (the provider directly, not the
gateway -- this measures the model, not PrivaParse):

    python eval/placeholder_fidelity.py --url http://127.0.0.1:9000/v1 --model qwen

See docs/benchmarks/gateway-fidelity.md for what a 1.5B model scored.
"""

from __future__ import annotations

import argparse
import json
import os

import httpx


def _headers() -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}

CASES = [
    ("echo, email", "Wiederhole exakt, ohne Kommentar:\nKontakt: [[EMAIL_A1]]", "[[EMAIL_A1]]"),
    ("echo, person", "Wiederhole exakt, ohne Kommentar:\nName: [[PERSON_A1]]", "[[PERSON_A1]]"),
    ("echo, phone", "Wiederhole exakt, ohne Kommentar:\nTel: [[PHONE_A7]]", "[[PHONE_A7]]"),
    ("answer a question",
     ("Kunde [[PERSON_A1]] hat geschrieben. An wen soll ich antworten? "
      "Antworte in einem kurzen Satz."), "[[PERSON_A1]]"),
    ("inside a sentence",
     "Schreibe einen Satz, in dem die Adresse [[EMAIL_A1]] unveraendert vorkommt.",
     "[[EMAIL_A1]]"),
    ("json output",
     'Gib nur JSON zurueck: {"empfaenger": "[[EMAIL_A1]]"}', "[[EMAIL_A1]]"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9000/v1")
    parser.add_argument("--model", default="qwen")
    args = parser.parse_args()

    intact = 0
    for name, prompt, placeholder in CASES:
        body = {"model": args.model, "temperature": 0, "max_tokens": 90,
                "messages": [{"role": "user", "content": prompt}]}
        answer = httpx.post(
            f"{args.url}/chat/completions", json=body, headers=_headers(), timeout=120
        ).json()["choices"][0]["message"]["content"]
        ok = placeholder in answer
        intact += ok
        print(f"[{'exact' if ok else 'MANGLED'}] {name}: {json.dumps(answer.strip()[:90])}")

    print(f"\n{intact}/{len(CASES)} placeholders came back byte-exact")


if __name__ == "__main__":
    main()
