# Testing

```bash
pytest
```

The default run skips everything that needs model weights and finishes in
about twenty seconds. To include the model tests:

```bash
pytest -m model
```

7/7 pass under the current 21-type catalogue, last checked in the same GPU
sandbox session that produced
[benchmarks/detection-quality.md](benchmarks/detection-quality.md).

Schema changes go through Alembic, because the vault holds data that cannot be
regenerated:

```bash
alembic upgrade head
```

## What the default run covers

`pytest` deselects 7 tests by default. The deselection is configured in
`pyproject.toml`, via `addopts = "-m 'not model'"`, because those 7 need
GLiNER2 weights on disk that a fresh checkout does not have. The default run
passes everything else, in about twenty seconds — run it to see the current
total rather than trust a number written here. It moves as documentation
pages are added: `tests/test_docs_links.py` is parametrized per file, so
every Markdown page this project gains adds one more passing test.

## Running the model tests

`pytest -m model` runs the 7 deselected tests once GLiNER2 weights exist on
disk. Get them with `pip install -e ".[model]"`, then run any command that
loads the detector — `privaparse doctor` or `privaparse demo <file>` both
trigger a download on first use if the weights are not already cached.

## Coverage

```bash
pytest --cov=privaparse --cov-report=term-missing
```

## What each suite covers

`tests/` covers the library: the detector, the catalogue, the vault,
placeholder generation, Markdown handling, the pseudonymize/reverse round
trip, and restoration. `tests/gateway/` covers the OpenAI-compatible surface:
extraction, request routing, streaming, the detection cache, the fuzzy/hint
fallbacks, the upstream relay, and metrics.

One suite there is parametrised rather than per-protocol.
`tests/gateway/test_adapter_conformance.py` holds the rules the route body
guarantees whatever protocol adapter it is pointed at — failing closed with a
pointer and no value, the provider never seeing the name, the answer coming
back restored, a body with no text at all forwarded without a mapping, one
mapping per request, the hint exactly once and only when something was
replaced, `gateway_allow_images` honoured with it on and refusing with it off,
scoped to content parts either way, and read at the part rather than at its
payload (the residual ADR-0002 names), 500 rather than 503 when detection is
unavailable (with the install guidance, streaming and not), restoration never
aborting, and a stream that ends without a terminal event losing nothing.
Each is written once and run against every entry in `ADAPTERS`, so a rule
proven on one protocol cannot quietly go unproven on the next.

Fixtures live in a `CONFORMANCE` table keyed by adapter name; an adapter with
no entry fails by name. A set carries sample bodies **and** accessors — given
a forwarded request, where is the text the gateway sent; given a reply, where
is the text it restored — so no shared assertion reads a body positionally,
and the fake upstream's echo mode takes its reader and writer from the set
rather than assuming one protocol's shape.

What stays in `test_server.py` and `test_responses_route.py` is what is
genuinely protocol-shaped: how a tool call is reserialised, where `usage`
sits, where the hint lands, what a bare-string `input` does.

## Evidence-guarding tests

Two tests in the default run exist specifically to keep the documentation
honest rather than to test PrivaParse's own behaviour:

- `tests/test_docs_links.py` checks that every Markdown link between docs,
  and every bare `docs/...md` path quoted in source, resolves to a file that
  actually exists.
- `tests/test_packaging.py` checks the packaging metadata `pyproject.toml`
  declares.

## Adding a gold document

The gold set that backs every detection-quality number in this project lives
under `eval/gold/`. See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full
procedure for adding a document to it.
