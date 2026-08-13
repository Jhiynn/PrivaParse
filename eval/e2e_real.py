"""Round trip through a running gateway against a real provider.

Everything the test suite asserts about SSE framing and streamed tool-call
shapes was written against a stub provider written by the same hand as the
code. This runs the same paths against bytes from a real OpenAI-compatible
server -- vLLM, llama.cpp, or an actual provider.

    python -m uvicorn eval.stub_provider:app --port 9000 &   # or a real server
    PRIVAPARSE_GATEWAY_UPSTREAM=http://127.0.0.1:9000 privaparse serve &
    python eval/e2e_real.py --model qwen
"""

from __future__ import annotations

import argparse
import json
import os

import httpx


def _headers() -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


ADDRESS = "beate.sonderzeichen@musterfirma-testxyz.de"
PROMPT = f"Wiederhole die folgende Zeile exakt, ohne Kommentar:\nKontakt: {ADDRESS}"


def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def non_streaming(base: str, model: str) -> None:
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0, "max_tokens": 80}
    text = httpx.post(f"{base}/chat/completions", json=body, headers=_headers(),
                      timeout=120).json()["choices"][0]["message"]["content"]
    report("non-streaming restores the address", ADDRESS in text, text.strip()[:120])


def streaming(base: str, model: str) -> None:
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0, "max_tokens": 80, "stream": True}
    pieces, events = [], 0
    with httpx.stream("POST", f"{base}/chat/completions", json=body,
                      headers=_headers(), timeout=120) as response:
        for line in response.iter_lines():
            stripped = line.strip()
            if not stripped.startswith("data: ") or stripped == "data: [DONE]":
                continue
            events += 1
            for choice in json.loads(stripped[6:]).get("choices", []):
                piece = choice.get("delta", {}).get("content")
                if isinstance(piece, str):
                    pieces.append(piece)
    text = "".join(pieces)
    report(f"streaming restores the address ({events} events)", ADDRESS in text,
           text.strip()[:120])


def tool_call(base: str, model: str) -> None:
    body = {
        "model": model,
        "messages": [{"role": "user",
                      "content": f"Schicke eine Mail an {ADDRESS} mit dem Betreff Test."}],
        "tools": [{"type": "function", "function": {
            "name": "send_mail",
            "description": "Send an email",
            "parameters": {"type": "object", "properties": {
                "to": {"type": "string", "description": "recipient address"},
                "subject": {"type": "string"}}, "required": ["to"]}}}],
        "tool_choice": "auto", "temperature": 0, "max_tokens": 120, "stream": True,
    }
    calls, events = [], 0
    with httpx.stream("POST", f"{base}/chat/completions", json=body,
                      headers=_headers(), timeout=120) as response:
        if response.status_code != 200:
            response.read()
            report("streamed tool call", False,
                   f"HTTP {response.status_code} {response.text[:150]}")
            return
        for line in response.iter_lines():
            stripped = line.strip()
            if not stripped.startswith("data: ") or stripped == "data: [DONE]":
                continue
            events += 1
            for choice in json.loads(stripped[6:]).get("choices", []):
                calls.extend(choice.get("delta", {}).get("tool_calls") or [])

    if not calls:
        report("streamed tool call", False, f"the model emitted no tool call ({events} events)")
        return
    arguments = "".join(c.get("function", {}).get("arguments", "") for c in calls)
    try:
        json.loads(arguments)
        valid = True
    except ValueError:
        valid = False
    report("streamed tool call restores the address", ADDRESS in arguments, arguments[:160])
    report("streamed tool call arguments are valid JSON", valid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787/v1")
    parser.add_argument("--model", default="qwen")
    parser.add_argument("--only", choices=["plain", "stream", "tools"])
    args = parser.parse_args()

    if args.only in (None, "plain"):
        non_streaming(args.url, args.model)
    if args.only in (None, "stream"):
        streaming(args.url, args.model)
    if args.only in (None, "tools"):
        tool_call(args.url, args.model)
