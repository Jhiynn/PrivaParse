# The model has to hand the placeholder back unchanged

Restoration is an exact string match. `reverse` replaces a placeholder only
where it finds the one it issued, byte for byte, so the entire round trip rests
on an assumption nobody had tested: **that the model reproduces
`[[EMAIL_A1]]` exactly.**

It does not always. On a small local model it essentially never does.

## Why this was invisible until now

Every test of the response path ran against a stub provider written alongside
the code. A stub echoes perfectly. Perfect echo is the one behaviour a real
model does not reliably deliver, so the test suite could not have found this
and the passing suite meant less than it looked like it meant.

## Setup

| | |
| --- | --- |
| Provider | vLLM 0.27.1, real OpenAI-compatible server |
| Model | Qwen2.5-1.5B-Instruct, fp16, RTX 3060 |
| Gateway | `PRIVAPARSE_DETECTOR=regex`, upstream pointed at vLLM |
| Sampling | `temperature=0` |
| Measured | 2026-08-12, `eval/placeholder_fidelity.py`, `eval/e2e_real.py` |

The fidelity probe talks to vLLM **directly**, not through the gateway: it
measures the model, not PrivaParse.

## Result: 0 of 6 placeholders survived

| Case | What came back |
| --- | --- |
| echo an email | `Kontakt: [EMAIL_A1]` |
| echo a person | `Name: [PERSON_A1]` |
| echo a phone | `Tel: [PHONE_A7]` |
| answer a question about a person | `Antworten Sie an Person A1.` |
| use the address in a sentence | placeholder dropped entirely |
| emit JSON containing it | `{"empfaenger": "[["EMAIL_A1"]]"}` |

Four distinct manglings: one bracket pair dropped, the token prose-ified into
"Person A1", the placeholder omitted, and quotes injected inside the brackets.
The dropped-bracket case is the common one and looks the most harmless, which
is what makes it worst — `[EMAIL_A1]` reads like a deliberate redaction rather
than a failure.

## Streamed tool calls, by contrast, were perfect

Three runs out of three returned
`{"to": "beate.sonderzeichen@musterfirma-testxyz.de", "subject": "Test"}` —
restored, valid JSON, correct. The same model, the same placeholder, the same
gateway.

The difference is that a tool call is structured output copied into a schema
field, where the model transcribes rather than composes. Free prose invites it
to tidy up, and tidying up is exactly what breaks an exact-match token.

## What this does and does not mean

**It is not a leak.** The provider received `[[EMAIL_A1]]` in every case, which
is confirmed directly: the pseudonymisation half worked perfectly throughout.
The failure is on the way back, and the failure mode is the safe one — the user
sees a placeholder instead of their own data.

**It is silent, and it is total.** Nothing reports it. A user pointing the
gateway at a small local model gets placeholder-riddled answers with no
indication of why, and the gateway's own stats show a healthy request.

**It is model-dependent and only measured at one point.** A 1.5B model is the
weak end. Larger models are much better at reproducing odd token sequences
verbatim, and the providers this tool was built for are at the strong end. But
"much better" is not "measured", and `PRIVAPARSE_GATEWAY_UPSTREAM` exists
precisely so people can point this at a local server — which is the
configuration that fails hardest.

## What could be done about it

Nothing has been implemented; these are the options as they look now.

1. **Report it.** Count placeholders that went out and did not come back, and
   surface the ratio in `privaparse gateway stats`. Cheap, and turns a silent
   failure into a visible one. The obvious first move.
2. **Tolerant restoration.** Also match near-misses — one bracket pair, added
   whitespace — but only against placeholders *this mapping issued*, which
   makes a false restore essentially impossible. Loosens a boundary that is
   currently exact, so it deserves its own decision rather than being slipped in.
3. **A less mangle-prone placeholder format.** Would be a vault migration and
   would not fix a model that prose-ifies rather than mistypes.
4. **Say so in the README.** Done — see the gateway's known gaps.

## Reproducing

```bash
python eval/placeholder_fidelity.py --url http://127.0.0.1:9000/v1 --model qwen
```

```bash
python eval/e2e_real.py --url http://127.0.0.1:8787/v1 --model qwen
```
