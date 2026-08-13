# Testing

```bash
.venv/Scripts/python -m pytest
```

The default run skips everything that needs model weights and finishes in a
couple of seconds. To include the model tests:

```bash
.venv/Scripts/python -m pytest -m model
```

7/7 pass under the current 21-type catalogue, last checked in the same
sandbox session the numbers above came from.

Schema changes go through Alembic, because the vault holds data that cannot be
regenerated:

```bash
.venv/Scripts/alembic upgrade head
```

## What the default run covers

`pytest` deselects 7 tests by default. The deselection is configured in
`pyproject.toml`, via `addopts = "-m 'not model'"`, because those 7 need
GLiNER2 weights on disk that a fresh checkout does not have. The default run
currently passes 830, 7 deselected. That count moves as documentation pages
are added — `tests/test_docs_links.py` is parametrized per file, so every
Markdown page this project gains adds one more passing test.

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

`tests/` covers the library: the detector, the entity catalogue, the vault,
placeholder generation, Markdown handling, the pseudonymize/reverse round
trip, and restoration. `tests/gateway/` covers the OpenAI-compatible surface:
extraction, request routing, streaming, the detection cache, the fuzzy/hint
fallbacks, the upstream relay, and metrics.

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
under `eval/gold/`. See `CONTRIBUTING.md` for the full procedure for adding a
document to it.
