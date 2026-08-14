# PrivaParse

*Local PII detection and pseudonymisation for text sent to an LLM — nothing
leaves the machine.*

[![CI](https://github.com/Jhiynn/PrivaParse/actions/workflows/ci.yml/badge.svg)](https://github.com/Jhiynn/PrivaParse/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/privaparse.svg)](https://pypi.org/project/privaparse/)
[![Python versions](https://img.shields.io/pypi/pyversions/privaparse.svg)](https://pypi.org/project/privaparse/)
[![Licence](https://img.shields.io/badge/licence-Apache%202.0-blue.svg)](LICENSE)

A local privacy layer for text you want to send to an LLM. It detects personally
identifiable information, replaces it with deterministic placeholders, keeps the
mapping in a local database, and restores the original values in the model's
answer.

Nothing leaves the machine. Phase 1 calls no external service at all.

```
Hallo,                                  Hallo,
mein Name ist Max Mustermann.           mein Name ist [[PERSON_A1]].
Sie erreichen mich unter        ──▶     Sie erreichen mich unter
max@test.de                             [[EMAIL_A2]]
oder +49 170 1234567.                   oder [[PHONE_A3]].
```

The LLM only ever sees the right-hand side. `reverse` puts the left-hand side
back into whatever the model replies.

## Install

```bash
pipx install "privaparse[gateway]"
```

That gives you the CLI and the local gateway. Person detection needs the model
backend, which pulls in PyTorch — roughly 2 GB:

```bash
pipx install "privaparse[gateway,model]"
```

GPU setup, Docker, and installing from source are in [docs/install.md](docs/install.md).

## Try it

Save this as `brief.md`:

```
Mein Name ist Max Mustermann, erreichbar unter max@test.de.
```

```bash
privaparse --detector regex demo brief.md
```

`demo` runs the whole round trip and prints every stage. Person detection
needs the `[model]` extra; without it (the plain `[gateway]` install above),
`--detector regex` keeps detection to email and phone — drop it once
`[model]` is installed. The real workflow is two commands:

```bash
privaparse pseudonymize brief.md -o brief.pseudo.md
```

```bash
privaparse reverse antwort.md -o antwort.klar.md
```

More commands, and the Python library, are in [docs/quickstart.md](docs/quickstart.md).

## Gateway

Point any OpenAI-compatible client at PrivaParse and it pseudonymises requests
going out, restores answers coming back — no code changes on the client side.

```bash
privaparse serve
```

```bash
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 aider
```

Without the `[model]` extra, run `privaparse --detector regex serve` —
otherwise the server starts fine but every request that reaches detection
returns a 500.

Which clients this works with today, what it costs, and its known gaps are in
[docs/gateway.md](docs/gateway.md).

## Evidence

Phase 1 ships a 25-type entity catalogue — 21 enabled by default, four disabled
on measured evidence (see [docs/benchmarks/labels.md](docs/benchmarks/labels.md))
— for plain text and Markdown, as a CLI and a Python library. No OCR, no PDF, no
cloud models.

### Does GLiNER2 need fine-tuning for German? No.

Measured on the German gold set in `eval/gold/` — **124 documents, 33 of them
containing no PII at all** (a corpus of nothing but real PII can never produce
a false positive, which is exactly where a wider catalogue turns out to
struggle) — scored at the shipped catalogue's 21 enabled types. The threshold
was fixed before this run: fine-tuning is warranted if PERSON partial-match
recall drops below 0.90 or precision below 0.85.

| Type | Precision (partial) | Recall (partial) | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| PERSON | 0.969 | 0.960 | 0.964 | 99 |
| EMAIL | 1.000 | 1.000 | 1.000 | 21 |
| PHONE | 0.818 | 1.000 | 0.900 | 18 |
| IBAN | 1.000 | 1.000 | 1.000 | 6 |

PERSON clears both floors comfortably — **fine-tuning not warranted** — and
has at every catalogue width measured, including an older, narrower one
that scored it higher (see
[why that isn't the better number](docs/benchmarks/detection-quality.md#an-earlier-more-flattering-number)).
`fastino/gliner2-privacy-filter-PII-multi` is built on
`microsoft/mdeberta-v3-base`, so German is not a special case for it. EMAIL
and PHONE come from rules, not the model, so they are the control group:
EMAIL is clean, PHONE is not — its 0.818 precision and TAX_ID's 0.000 recall
are the same defect, counted from opposite ends; see
[One defect, two numbers](docs/benchmarks/detection-quality.md#one-defect-two-numbers)
for the mechanism.

The rest of the catalogue is measured too, and it is not uniformly clean.
Nine types rest on three gold entities each — thin. Seven more, measured for
the first time this run, were not all clean either: **LICENSE_NUMBER and
ROUTING_NUMBER measured 0.000 recall — they detect nothing** — and CARD_CVV
measured 0.167 precision. Full tables, the per-label breakdown, and every
missed or spurious detection are in
[docs/benchmarks/detection-quality.md](docs/benchmarks/detection-quality.md).

Reproduce with `privaparse eval` (needs GLiNER2 — see
[Install](docs/install.md)). Chunk size as a recall setting is in
[docs/benchmarks/performance-notes.md](docs/benchmarks/performance-notes.md);
throughput is in [docs/benchmarks/throughput.md](docs/benchmarks/throughput.md).

## Documentation

| Page | Covers |
| --- | --- |
| [Install](docs/install.md) | pipx and source installs, CPU/GPU, Docker |
| [Quickstart](docs/quickstart.md) | `privaparse demo`, the CLI, the Python library |
| [Gateway](docs/gateway.md) | The local OpenAI-compatible gateway |
| [Configuration](docs/configuration.md) | Every `PRIVAPARSE_*` setting |
| [Architecture](docs/architecture.md) | The pipeline, the vault, Markdown handling |
| [Testing](docs/testing.md) | Running the suite, coverage |
| [Benchmarks](docs/benchmarks/README.md) | Every measured number, with the command that reproduces it |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for issues and pull requests.
The rule that matters most: no real PII in examples, tests, or gold
documents, ever.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
