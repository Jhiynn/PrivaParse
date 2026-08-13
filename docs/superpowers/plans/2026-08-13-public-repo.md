# PrivaParse Public Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `Jhiynn/PrivaParse` into a public repository a stranger can install in one command, evaluate from its measured evidence, and contribute to without asking.

**Architecture:** Nothing in the detector, vault or gateway changes behaviour. Work falls in five layers: packaging metadata so PyPI can serve the project; a documentation tree that moves the README's long-form evidence into `docs/` without rewriting a number; root governance files; `.github` workflows and templates; and the repository's own settings applied with `gh api`. A link-integrity test is written early and guards every later move.

**Tech Stack:** Python 3.11+, setuptools, ruff, pytest, GitHub Actions, PyPI trusted publishing, `gh` CLI, `/lab` sandbox for clean-room install verification.

**Spec:** [docs/superpowers/specs/2026-08-13-public-repo-design.md](../specs/2026-08-13-public-repo-design.md)

## Global Constraints

- **No behaviour change to the library or gateway.** The only source edits permitted are packaging metadata, `__version__`, ruff-driven modernizations, and updates to documentation paths quoted in comments.
- **`pytest` must report 701 passed, 7 deselected** at the end of every task, plus whatever tests that task adds.
- **No email address anywhere in the published tree.** `SECURITY.md` and `CODE_OF_CONDUCT.md` route through GitHub.
- **No `ruff format`.** Lint only. The codebase wraps signatures by hand and the formatter would rewrite files repository-wide.
- **No AI-assistant attribution** in any commit message, file, or documentation page.
- **Never introduce real PII** into tests, gold data, or examples. Synthetic only, matching the existing corpus (`Max Mustermann`, `beispiel.de`, checksum-valid generated IBANs).
- **Measured numbers are moved, never re-derived.** No benchmark figure changes in this work.
- Commit style: Conventional Commits, imperative subject, body says why.

---

### Task 1: Clear the working tree

The `gateway_allow_images` work is unrelated to this plan and must not be entangled in the restructure diff.

**Files:**
- Commit: `privaparse/app/config.py`, `privaparse/gateway/adapter/responses.py`, `privaparse/gateway/server.py`, `tests/gateway/test_responses_extract.py`
- Delete: `tests/test_docs_links.py` — a planning artefact left in the tree while sizing the link checker. Task 4 recreates it from verified content. Leaving it here would make every intervening task's expected test count wrong and would be swept into Task 2's `git add -A`.

- [ ] **Step 0: Remove the planning artefact**

```bash
rm tests/test_docs_links.py
```

- [ ] **Step 1: Confirm the pending work is green**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `701 passed, 7 deselected`

- [ ] **Step 2: Commit only the gateway files**

```bash
git add privaparse/app/config.py privaparse/gateway/adapter/responses.py privaparse/gateway/server.py tests/gateway/test_responses_extract.py
```

```bash
git commit -F - <<'MSG'
feat: let an operator forward image parts the detector cannot read

Off by default, and not out of squeamishness: a coding agent screenshots
its own work, and a screenshot can show every value that was just
pseudonymised out of the text. Turning it on means images leave the
machine unexamined -- which is the only way Codex can check its own
output visually, so the choice belongs to the operator rather than to
this code.
MSG
```

- [ ] **Step 3: Verify the tree is clean**

Run: `git status --short`
Expected: no output.

---

### Task 2: Close the ruff gate

Ruff is configured in `[tool.ruff]` but has never been installed, so lint has never run. A measured run reports **160 errors, 116 auto-fixable**. This task makes `ruff check .` exit clean so CI can gate on it.

**Files:**
- Modify: `pyproject.toml` (lines 27-30 `dev` extra, and `[tool.ruff]` at 48-50)
- Modify: ~30 source and test files via `ruff check --fix`
- Modify by hand: `privaparse/parser/pseudonymizer.py:220-223`, `privaparse/evaluation/bench.py`, `eval/gateway_latency.py`, and the remaining sites listed in Step 5

**Interfaces:**
- Produces: a clean `ruff check .`, and a pinned `[tool.ruff.lint] select` that later tasks' CI workflow depends on.

- [ ] **Step 1: Add ruff to the dev extra and pin the rule set**

The rule set must be pinned explicitly. Ruff's defaults widen between releases, and a public repository whose CI turns red because a linter shipped a new rule teaches contributors to distrust the badge.

Pinning means choosing, and two Pylint subcategories are deliberately excluded — `PLE` and `PLW` are selected, `PLC` and `PLR` are not. `PLC0415` (import-outside-top-level, 147 sites) would forbid the lazy imports that keep torch and gliner2 optional, so adopting it would break the thing that makes `[model]` an extra. `PLR` is complexity and magic-value opinion (`PLR2004` alone is 65 sites) with no defect signal here. An earlier draft of this step selected `PL` wholesale, which produced 446 findings instead of the 160 the table below was measured under; the corrected block is:

In `pyproject.toml`, change the `dev` extra to:

```toml
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.16",
]
```

and replace `[tool.ruff]` with:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "UP", "B", "C4", "ISC", "PYI", "PLE", "PLW", "S", "BLE", "RUF"]
ignore = [
    # This project's source and gold data are German. RUF001-003 flag German
    # orthography as "ambiguous unicode" -- a false positive by construction.
    "RUF001", "RUF002", "RUF003",
    # `[*xs, x]` versus `xs + [x]`. Style, no defect.
    "RUF005",
    # Deferred, not dismissed: exception chaining would improve tracebacks
    # across five sites, but it is unrelated to making this repo public.
    "B904",
    # `strict=` changes what zip does at runtime -- it raises on a length
    # mismatch where today it truncates. This change may not alter behaviour.
    "B905",
]

[tool.ruff.lint.flake8-bugbear]
# Typer's entire API is `def command(x: str = typer.Option(...))`. B008 is
# right about mutable defaults in general and wrong about this framework;
# this is the documented way to say so, rather than a blanket noqa.
extend-immutable-calls = ["typer.Option", "typer.Argument"]

[tool.ruff.lint.per-file-ignores]
# Assertions and subprocess calls are the point of a test suite.
"tests/**" = ["S101", "S603", "S607"]
"eval/**" = ["S101", "S603", "S607"]
```

- [ ] **Step 2: Install and measure the starting point**

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```

Run: `.venv/Scripts/python -m ruff check . --statistics`
Expected: B008 no longer appears (all 15 were `typer.Option` / `typer.Argument`). Roughly 145 errors remain.

- [ ] **Step 3: Apply the safe automatic fixes**

```bash
.venv/Scripts/python -m ruff check . --fix
```

This clears 116 mechanical modernizations: `UP037` quoted annotations (56), `UP045` `Optional[X]` → `X | None` (19), `UP035` deprecated imports (16), `RUF022` unsorted `__all__` (13), `UP007`, `UP012`, `I001`, `RUF010`, `RUF100`, `UP017`.

- [ ] **Step 4: Prove the automatic fixes changed nothing that matters**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `701 passed, 7 deselected`

- [ ] **Step 5: Fix the remaining violations by hand**

Twenty-nine remain. Each is listed with its resolution:

| Site | Rule | Resolution |
| --- | --- | --- |
| `privaparse/parser/pseudonymizer.py:220` | F841 | `adopted` is computed and discarded. See Step 6 — decide before touching. |
| `privaparse/app/device.py:101`, `app/logging.py:86`, `app/main.py:389`, `parser/gliner_detector.py:101`, `evaluation/bench.py:189,208,219`, `tests/test_gliner_model.py:174` | BLE001 | These catch `Exception` deliberately around optional hardware and optional subprocesses. Narrow to the real exception type where it is knowable (`OSError`, `subprocess.SubprocessError`, `ImportError`); where it genuinely is "anything the driver throws", add `# noqa: BLE001` with a one-line reason. |
| `evaluation/build_gold.py:152`, `tests/test_gliner_detector.py:356,410,421,432`, `tests/test_markdown.py:127` | RUF007 | Replace `zip(xs, xs[1:])` with `itertools.pairwise(xs)`. |
| `eval/placeholder_fidelity.py:35`, `eval/restore_matrix.py:43`, `evaluation/build_gold.py:244` | ISC004 | Parenthesize the implicitly concatenated strings inside the collection literal. |
| `evaluation/bench.py:197`, `tests/test_eval_harness.py:230,234` | RUF046 | Drop the redundant `int(...)` cast. |
| `evaluation/bench.py:177` | PLW1510 | Add explicit `check=False` to `subprocess.run` — the call already tolerates failure. |
| `evaluation/bench.py:208` | S110 | Paired with the BLE001 above; the `noqa` reason covers both. |
| `engine.py:221` | PYI034 | Annotate `__enter__` return as `Self` (`from typing import Self`). |
| `eval/gateway_latency.py:83` | RUF012 | Annotate the mutable class attribute with `ClassVar`. |
| `tests/gateway/test_responses_stream.py:187` | RUF015 | Replace `[...][0]` with `next(...)`. |
| `tests/test_eval_harness.py:199` | C402 | Rewrite the generator as a dict comprehension. |
| `tests/test_fuzzy_restore.py:84` | RUF059 | Rename the unused unpacked variable to `_mapping_id`. |
| `tests/test_gliner_model.py:174` | S112 | Paired with its BLE001; one `noqa` reason covers both. |

- [ ] **Step 6: Decide the `adopted` variable deliberately**

`privaparse/parser/pseudonymizer.py:220-223` computes `adopted` from `_adopt_existing` and never reads it, while the log line three statements later reports `len(merged)` as its placeholder count. `_adopt_existing` still runs for its side effects, so this is a reporting omission, not a correctness bug.

Report the number rather than deleting it — a discarded return value at a log site is authorial intent that did not land. Change the log call to:

```python
    log.info(
        "pseudonymised %d text(s) as %s: %d replacement(s), %d placeholder(s), "
        "%d adopted, mapping=%s",
        len(texts),
        source_name or ("<text>" if len(texts) == 1 else "<batch>"),
        sum(len(r.spans) for r in resolutions),
        len(merged),
        adopted,
        mapping.id,
    )
```

This is the single log-output change in the plan. If any test asserts on this log line, prefer the test's expectation and adjust the format string to match its shape.

- [ ] **Step 7: Verify clean lint and unchanged tests**

Run: `.venv/Scripts/python -m ruff check .`
Expected: `All checks passed!`

Run: `.venv/Scripts/python -m pytest -q`
Expected: `701 passed, 7 deselected`

- [ ] **Step 8: Commit**

```bash
git add -A
```

```bash
git commit -F - <<'MSG'
chore: make the linter that was configured actually run

ruff has sat in pyproject since the start without ever being installed,
so 160 findings had accumulated unseen. The rule set is now pinned
explicitly rather than inherited: ruff's defaults widen between
releases, and a public repo whose CI reddens because a linter shipped a
new rule teaches contributors to distrust the badge.

Fifteen of the findings were B008 against Typer's own calling
convention, answered with extend-immutable-calls rather than a blanket
suppression. One was real -- pseudonymize computed an adopted-placeholder
count and dropped it on the floor instead of logging it.
MSG
```

---

### Task 3: Packaging metadata

**Files:**
- Modify: `pyproject.toml`
- Modify: `privaparse/__init__.py`
- Delete: `requirements.txt`
- Test: `tests/test_packaging.py` (create)

**Interfaces:**
- Produces: `privaparse.__version__` (str), and the `all` extra consumed by the CI workflow in Task 9.

- [ ] **Step 1: Write the failing test**

Create `tests/test_packaging.py`:

```python
"""The package's own metadata, checked rather than assumed.

Version drift between the module and the distribution is the kind of
error that only shows up after a release, when it is expensive.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

import privaparse

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_module_version_matches_the_installed_distribution() -> None:
    assert privaparse.__version__ == metadata.version("privaparse")


def test_project_declares_the_urls_pypi_renders() -> None:
    urls = _pyproject()["project"]["urls"]
    assert set(urls) >= {"Homepage", "Documentation", "Issues", "Changelog"}
    assert all(value.startswith("https://") for value in urls.values())


def test_project_declares_classifiers_and_keywords() -> None:
    project = _pyproject()["project"]
    assert project["keywords"]
    joined = " ".join(project["classifiers"])
    assert "License :: " not in joined, "licence is expressed as an SPDX expression, not a classifier"
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in joined


def test_every_extra_is_reachable_from_all() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert set(extras) >= {"model", "gateway", "dev", "all"}


def test_build_backend_understands_the_spdx_licence_field() -> None:
    # PEP 639 landed in setuptools 77.0.3. Below that floor the licence
    # metadata is dropped rather than rejected, which is the worse failure.
    requires = " ".join(_pyproject()["build-system"]["requires"])
    assert "setuptools>=77" in requires.replace(" ", "")


def test_requirements_txt_is_gone() -> None:
    # A hand-mirrored copy of [project.dependencies] that had already drifted:
    # it omitted pyyaml and predated the gateway extra.
    assert not (PYPROJECT.parent / "requirements.txt").exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_packaging.py -q`
Expected: FAIL — `AttributeError: module 'privaparse' has no attribute '__version__'`, plus `KeyError: 'urls'`.

- [ ] **Step 3: Add `__version__`**

In `privaparse/__init__.py`, add:

```python
from importlib import metadata

__version__ = metadata.version("privaparse")
```

Append `"__version__"` to `__all__` if the module defines one, keeping it sorted — `RUF022` is now enforced.

- [ ] **Step 4: Fill in the project metadata**

First raise the build backend floor. The SPDX `license` expression and `license-files` are PEP 639, which setuptools only understands from 77.0.3; against the declared `setuptools>=68` the build either errors or silently drops the licence metadata, which is the worse of the two outcomes:

```toml
[build-system]
requires = ["setuptools>=77.0.3", "wheel"]
build-backend = "setuptools.build_meta"
```

Then in `pyproject.toml`, replace `license = { file = "LICENSE" }` with the SPDX form and add the surrounding metadata:

```toml
license = "Apache-2.0"
license-files = ["LICENSE"]
authors = [{ name = "Jhiynn" }]
keywords = ["privacy", "pii", "pseudonymization", "gdpr", "llm", "gliner", "german", "nlp"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Security",
    "Topic :: Text Processing :: Linguistic",
    "Typing :: Typed",
]

[project.urls]
Homepage = "https://github.com/Jhiynn/PrivaParse"
Documentation = "https://github.com/Jhiynn/PrivaParse/tree/main/docs"
Issues = "https://github.com/Jhiynn/PrivaParse/issues"
Changelog = "https://github.com/Jhiynn/PrivaParse/blob/main/CHANGELOG.md"
```

Add the `all` extra to `[project.optional-dependencies]`:

```toml
all = ["privaparse[model,gateway,dev]"]
```

- [ ] **Step 5: Delete the drifted requirements file**

```bash
git rm requirements.txt
```

- [ ] **Step 6: Reinstall and run the tests**

```bash
.venv/Scripts/python -m pip install -e ".[dev,gateway]"
```

Run: `.venv/Scripts/python -m pytest -q`
Expected: `707 passed, 7 deselected` (701 plus the six new packaging tests)

- [ ] **Step 7: Commit**

```bash
git add -A
```

```bash
git commit -F - <<'MSG'
feat: give the package the metadata PyPI actually renders

URLs, classifiers, keywords and an SPDX licence expression, plus an
`all` extra and a `__version__` read from the installed distribution so
the version has one home instead of two.

requirements.txt is deleted rather than updated. It mirrored
[project.dependencies] by hand and had already drifted -- no pyyaml, no
gateway extra -- and its own header told readers to use pip install -e
instead. A stale second source of truth is worse on a public repo than
no second source at all.
MSG
```

---

### Task 4: Link integrity test

Written before any file moves, while the tree is green, so it becomes a net rather than a report. The exact content below has been run against the current tree: **113 passed**.

**Files:**
- Create: `tests/test_docs_links.py`

**Interfaces:**
- Produces: two parametrized tests that later tasks rely on to catch dead references.

- [ ] **Step 1: Create the test file with exactly this content**

```python
"""Every documentation reference points at a file that exists.

Two kinds of reference rot are possible here and both have bitten this
repository's shape already: a Markdown link between docs, and a bare
``docs/...md`` path quoted in a source comment. The second kind is the
dangerous one -- nothing renders it, so nothing reveals it is dead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", ".git", ".idea", "node_modules", ".pytest_cache", ".ruff_cache", "build", "dist"}

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
# A path, not a regex. Prose in these documents quotes patterns like
# `[\d{1,2}](\d{4})`, which is link syntax to a naive reader and nothing of
# the sort to a human, so anything carrying regex punctuation is not a link.
PATH_LIKE = re.compile(r"^[A-Za-z0-9_.\-/]+$")
# No `...` -- this file's own docstring says `docs/...md` while describing the
# rule, and a checker that fails on its own explanation is a checker nobody
# keeps.
DOC_PATH = re.compile(r"docs/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.md")


def _walk(suffixes: tuple[str, ...]) -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if path.suffix not in suffixes or not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return sorted(found)


def _identifier(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("markdown", _walk((".md",)), ids=_identifier)
def test_markdown_links_resolve(markdown: Path) -> None:
    missing = []
    for match in MARKDOWN_LINK.finditer(markdown.read_text(encoding="utf-8")):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target or not PATH_LIKE.match(target):
            continue
        if not (markdown.parent / target).resolve().exists():
            missing.append(target)
    assert not missing, f"{_identifier(markdown)} links to missing: {sorted(set(missing))}"


@pytest.mark.parametrize("source", _walk((".py", ".yaml", ".yml", ".toml", ".sh")), ids=_identifier)
def test_doc_paths_quoted_in_source_resolve(source: Path) -> None:
    missing = [
        quoted
        for quoted in DOC_PATH.findall(source.read_text(encoding="utf-8"))
        if not (ROOT / quoted).exists()
    ]
    assert not missing, f"{_identifier(source)} cites missing: {sorted(set(missing))}"
```

- [ ] **Step 2: Run it and confirm it passes on the current tree**

Run: `.venv/Scripts/python -m pytest tests/test_docs_links.py -q`
Expected: `113 passed`

- [ ] **Step 3: Prove it actually catches a break**

```bash
git mv docs/label-report.md docs/label-report-moved.md
```

Run: `.venv/Scripts/python -m pytest tests/test_docs_links.py -q`
Expected: FAIL — at minimum `privaparse/app/entities.default.yaml cites missing: ['docs/label-report.md']` and the README's Markdown link.

A test that has never failed proves nothing. This step is the difference between a net and a decoration.

- [ ] **Step 4: Undo the deliberate break**

```bash
git mv docs/label-report-moved.md docs/label-report.md
```

Run: `.venv/Scripts/python -m pytest tests/test_docs_links.py -q`
Expected: `113 passed`

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `820 passed, 7 deselected` (707 plus 113 link tests)

```bash
git add tests/test_docs_links.py
```

```bash
git commit -F - <<'MSG'
test: fail when a document cites a file that is not there

Markdown links are the obvious half. The half that matters is the bare
docs/*.md path quoted in a source comment -- entities.default.yaml cites
the label report twice, config.py and gliner_detector.py cite two more --
because nothing renders those, so nothing reveals when they go dead.

Written before the documentation moves rather than after, so it guards
them instead of grading them.
MSG
```

---

### Task 5: Move the reports into `docs/benchmarks/`

**Files:**
- Move: eight report files (table below)
- Modify: `.gitignore` (the `docs/*-report.local.md` pattern)
- Modify: `privaparse/app/entities.default.yaml`, `privaparse/app/main.py`, `privaparse/app/config.py`, `privaparse/parser/gliner_detector.py`, `eval/placeholder_fidelity.py`, `eval/gateway_latency.py`, `tests/test_catalogue.py`, `tests/gateway/test_fallbacks.py`
- Modify: `README.md`, and cross-links inside the moved reports
- Create: `docs/benchmarks/README.md`

- [ ] **Step 1: Move the files with `git mv` so history follows**

```bash
mkdir -p docs/benchmarks
git mv docs/eval-report.md docs/benchmarks/detection-quality.md
git mv docs/label-report.md docs/benchmarks/labels.md
git mv docs/bench-report.md docs/benchmarks/throughput.md
git mv docs/performance-notes.md docs/benchmarks/performance-notes.md
git mv docs/gateway-latency-report.md docs/benchmarks/gateway-latency.md
git mv docs/gateway-model-fidelity-report.md docs/benchmarks/gateway-fidelity.md
git mv docs/gateway-restore-fallbacks-report.md docs/benchmarks/gateway-fallbacks.md
git mv docs/codex-cli-report.md docs/benchmarks/codex-cli.md
```

- [ ] **Step 2: Let the test enumerate every dead reference**

Run: `.venv/Scripts/python -m pytest tests/test_docs_links.py -q`
Expected: FAIL, listing each file and the paths it now cites in vain. Work the list rather than grepping by hand.

- [ ] **Step 3: Update every citation**

Apply this mapping everywhere it appears — in Markdown links, in Python comments and docstrings, and in the YAML comments of `entities.default.yaml`:

| Old path | New path |
| --- | --- |
| `docs/eval-report.md` | `docs/benchmarks/detection-quality.md` |
| `docs/label-report.md` | `docs/benchmarks/labels.md` |
| `docs/bench-report.md` | `docs/benchmarks/throughput.md` |
| `docs/performance-notes.md` | `docs/benchmarks/performance-notes.md` |
| `docs/gateway-latency-report.md` | `docs/benchmarks/gateway-latency.md` |
| `docs/gateway-model-fidelity-report.md` | `docs/benchmarks/gateway-fidelity.md` |
| `docs/gateway-restore-fallbacks-report.md` | `docs/benchmarks/gateway-fallbacks.md` |
| `docs/codex-cli-report.md` | `docs/benchmarks/codex-cli.md` |

Two of the moved reports link to each other with bare filenames (`gateway-fallbacks.md` cites `gateway-model-fidelity-report.md`, `gateway-latency.md` cites `bench-report.md`). Those are now same-directory links and need only the new basename.

- [ ] **Step 4: Update the gitignore pattern**

In `.gitignore`, replace:

```
docs/*-report.local.md
```

with:

```
docs/benchmarks/*.local.md
```

- [ ] **Step 5: Write the benchmark index**

Create `docs/benchmarks/README.md`. It is an index with orientation, not a summary that could drift from the reports:

- One paragraph stating the method the repository holds itself to: every number here is produced by a command a reader can rerun, and negative results are published alongside positive ones.
- A table of the eight reports: file, what it measures, the command that regenerates it (`privaparse eval`, `privaparse bench`, `python eval/gateway_latency.py`, `python eval/placeholder_fidelity.py`, `python eval/restore_matrix.py`, `eval/e2e_real.py`), and the date of the run it currently records.
- The headline figures, each with a link to the report it came from: PERSON P 0.973 / R 0.961 / F1 0.967 at 76 supporting entities, and the throughput figure from `throughput.md`.
- A short note that the gold set is 91 documents, 33 of them containing no PII at all, and why that matters — a positives-only corpus cannot observe a false positive.

Take every figure from the report files themselves. Do not recompute, and do not round.

- [ ] **Step 6: Verify**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `821 passed, 7 deselected` (the new index adds one parametrized link test)

- [ ] **Step 7: Commit**

```bash
git add -A
```

```bash
git commit -F - <<'MSG'
docs: gather the measured reports under docs/benchmarks

Moved with git mv so the history of each run follows it. The renames
drop the -report suffix that every file in the directory carried, which
told a reader nothing once the directory itself said it.

Eight files moved and eleven citations followed them, four of those in
source comments and YAML rather than Markdown -- the link test found
them, which is what it was written for.
MSG
```

---

### Task 6: Extract the long-form documentation pages

The README's evidence and reference prose moves to `docs/`. Content is relocated verbatim except where a sentence refers to its own position ("see below" becoming a link).

**Files:**
- Create: `docs/README.md`, `docs/install.md`, `docs/quickstart.md`, `docs/gateway.md`, `docs/configuration.md`, `docs/architecture.md`, `docs/testing.md`
- Modify: `README.md` (sections removed as they are extracted; the rewrite lands in Task 7)

- [ ] **Step 1: Create `docs/install.md`**

Move README lines 202-284 (`## Install` and `### Switching between CPU and GPU`) and 547-573 (`### Docker`). Add the new `pipx` path above the source install:

```markdown
## Install with pipx

```bash
pipx install "privaparse[gateway]"
```

That gives you the CLI and the local gateway. Person detection needs the model
backend, which pulls in PyTorch — roughly 2 GB:

```bash
pipx install "privaparse[gateway,model]"
```
```

Keep the existing torch/CUDA/`curl -C -`/`privaparse doctor` prose exactly as written; it is hard-won and none of it is superseded. Replace the Windows-only `.venv/Scripts/pip` invocations in the source-install section with commands that work on both platforms.

- [ ] **Step 2: Create `docs/quickstart.md`**

Move README lines 285-349 (`## Use`) in full.

- [ ] **Step 3: Create `docs/gateway.md`**

Move README lines 350-546: `## Gateway` and its subsections `Which clients this actually works with`, `Codex CLI`, `What it does to a request`, `What it costs`, `Restoration puts real PII into the client`, `Known gaps`. Link the Docker section, now in `install.md`.

- [ ] **Step 4: Create `docs/configuration.md`**

Not a copy of `.env.example`, which documents nine of the twenty-two settings. Build a complete table from `privaparse/app/config.py`, one row per `Field`, using the `description=` text already written there: `db_path`, `detector`, `model_id`, `model_dir`, `offline`, `threshold`, `scan_code`, `coreference_sweep`, `catalogue_path`, `device`, `quantize`, `compile`, `batch_size`, `flash_attention`, `warmup`, `chunk_chars`, `gateway_upstream`, `gateway_fuzzy`, `gateway_hint`, `gateway_allow_images`, `gateway_cache`, `log_level`.

Columns: environment variable (`PRIVAPARSE_` + upper-cased field), default, and what it does. Move README lines 574-599 in as the section's introduction.

- [ ] **Step 5: Create `docs/architecture.md`**

Move README lines 600-653: `## How it works`, `### The vault holds plaintext PII`, `### Markdown handling`.

- [ ] **Step 6: Create `docs/testing.md`**

Partly moved from README lines 654-676 (`## Development`), partly new. Cover:

- `pytest` deselects 7 tests by default. The deselection is configured in `addopts = "-m 'not model'"`, because those tests need GLiNER2 weights on disk. State the pass count from the run you actually do, not from this plan — it moves as documentation pages are added, since the link checker is parametrized per file.
- `pytest -m model` runs them once weights exist; how to get the weights (`pip install -e ".[model]"`, then any command that loads the detector).
- Coverage: `pytest --cov=privaparse --cov-report=term-missing`.
- What each suite covers: `tests/` for the library (detector, catalogue, vault, placeholder, markdown, roundtrip, restore), `tests/gateway/` for the OpenAI-compatible surface (extract, route, stream, cache, fallbacks, upstream, metrics).
- The two evidence-guarding tests contributors should know exist: `tests/test_docs_links.py` and `tests/test_packaging.py`.
- How to add a gold document, pointing at `CONTRIBUTING.md` for the full procedure.

- [ ] **Step 7: Create `docs/README.md`**

An index: one line per page under `docs/`, plus a link to `benchmarks/README.md` and to `CONTRIBUTING.md`. Order it the way a reader arrives — install, quickstart, gateway, configuration, architecture, testing, benchmarks.

- [ ] **Step 8: Verify no reference broke**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass; the count rises by seven parametrized link tests.

- [ ] **Step 9: Commit**

```bash
git add -A
```

```bash
git commit -F - <<'MSG'
docs: split the reference material out of the README

Install, quickstart, gateway, configuration, architecture and testing
each become a page. The prose moves as written -- the torch swap
instructions, the Windows torch.compile downgrade, the gateway's known
gaps -- because it was measured or hard-won, and rewriting it while
moving it would put both at risk in one commit.

configuration.md is built from the Field descriptions in config.py
rather than from .env.example, which documents nine of twenty-two
settings.
MSG
```

---

### Task 7: Rewrite the README as a landing page

**Files:**
- Modify: `README.md` (679 lines → roughly 150)

- [ ] **Step 1: Write the new README**

Structure, in order:

1. Title, one-sentence description, badge row: CI status, PyPI version, Python versions, licence.
2. The existing intro paragraph and the before/after diagram — unchanged. It explains the product faster than any prose could.
3. **Install** — the `pipx` lines from `docs/install.md`, and one sentence pointing at the full page for GPU, Docker and source installs.
4. **Try it** — `privaparse demo brief.md`, then the two real commands (`pseudonymize`, `reverse`), linking `docs/quickstart.md`.
5. **Gateway** — six lines: what it is, `privaparse serve`, pointing a client at `http://127.0.0.1:8787/v1`, link to `docs/gateway.md`.
6. **Evidence** — the condensed Status paragraph, the four-row PERSON/EMAIL/PHONE/IBAN table, the verdict sentence ("PERSON clears both floors comfortably — fine-tuning not warranted"), the one-line statement of the gold set's shape (91 documents, 33 negatives), and a link to `docs/benchmarks/`. This section is why the project is credible; it stays above the fold even in a lean README.
7. **Documentation** — a short list linking each `docs/` page.
8. **Contributing** — two lines linking `CONTRIBUTING.md`, naming the no-real-PII rule.
9. **Licence** — one line.

Keep the honesty that characterises the original: the four types disabled on measured evidence, and the fact that nine types rest on three gold entities each, are stated in `docs/benchmarks/`, and the README links there rather than omitting the caveat.

- [ ] **Step 2: Confirm nothing was lost**

Run: `git show HEAD~1:README.md > /tmp/readme-old.md` (or a scratch path on Windows), then compare the heading list against the destination table in the spec. Every one of the twenty-two original sections must be either present in the new README or present in a `docs/` page.

- [ ] **Step 3: Verify**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add README.md
```

```bash
git commit -F - <<'MSG'
docs: make the README a landing page

679 lines to roughly 150. A first-time reader now reaches an install
command in one screen instead of after two hundred lines of threshold
methodology.

The evidence section stays above the fold, condensed to the four types
with real support and a link to the full reports. That table is why
anyone should believe this project works; burying it would have been the
wrong economy.
MSG
```

---

### Task 8: Governance files

**Files:**
- Create: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`

- [ ] **Step 1: Write `CONTRIBUTING.md`**

Eleven sections, in this order. Each is prose, not a heading with a stub under it.

1. **Scope.** Merged: detection quality, new entity types carrying gold evidence, gateway client compatibility, documentation. Not merged: OCR, PDF, cloud-hosted detection, anything that sends text off the machine by default.
2. **Never paste real PII.** Issues, tests and the gold set are synthetic. Point at the existing corpus as the standard: `Max Mustermann`, `beispiel.de`, checksum-valid IBANs and tax IDs generated by `build_gold.py`. State the reason in one line — a privacy tool whose bug reports leak personal data defeats itself.
3. **Setup.** Clone, virtualenv, `pip install -e ".[dev,gateway]"`, optional `[model]`, `privaparse doctor`. Commands that work on Windows and POSIX.
4. **Tests.** `pytest`; model marks excluded by `addopts`; `pytest -m model` needs weights; coverage command. Link `docs/testing.md`.
5. **Lint.** `ruff check .`, line length 100, target py311, rule set pinned in `[tool.ruff.lint] select`. State plainly that formatting is not enforced and why.
6. **The evidence rule.** Any claim about detection quality, throughput or fidelity cites a report in `docs/benchmarks/` produced by a rerunnable command. Negative results are published too — point at the 3-label versus 35-label passage and the four types disabled on measured evidence as the standard being asked for.
7. **Adding an entity type.** Route the labels in `privaparse/app/entities.default.yaml`, with a comment saying whether the threshold is measured or a conservative guess. Add a validator in `privaparse/parser/validators.py` when the type has a checksum. Add gold documents to `eval/gold/de_gold_source.md`, regenerate with `python -m privaparse.evaluation.build_gold`, run `privaparse eval`, put the delta in the pull request. A type with zero gold entities ships disabled.
8. **Adding gold documents.** `de_gold_source.md` is the source of truth; the `.jsonl` is generated. Include documents with no PII at all.
9. **Commits and pull requests.** Conventional Commits matching the existing history, subjects that say why. Checklist: tests green, `ruff check` clean, docs updated, benchmarks rerun if a published number moved, CHANGELOG entry.
10. **Security.** Report privately through GitHub's private vulnerability reporting per `SECURITY.md`; never in a public issue.
11. **Releasing (maintainers).** Bump the version in `pyproject.toml`, update `CHANGELOG.md`, tag `vX.Y.Z`, push the tag. The release workflow builds immediately and waits for approval in the `pypi` environment before uploading. Note the one-time setup: registering the PyPI trusted publisher for `Jhiynn/PrivaParse` with workflow `release.yml`.

- [ ] **Step 2: Write `SECURITY.md`**

Sections: supported versions (0.1.x); how to report — GitHub private vulnerability reporting, with a link to the repository's advisory page and an explicit "not a public issue"; expected response time.

Then a **threat model** section that states two things plainly, because they are true and a user deserves them before trusting the tool:

- The vault stores plaintext PII on disk. It is a local SQLite database holding every real name, address and number the tool has seen. Link `docs/architecture.md`.
- Restoration writes real PII back into the client by design. That is the feature, and it means the client — including its logs and its own storage — sees the original values. Link `docs/gateway.md`.

Also state what is out of scope: the tool does not defend against an attacker with read access to the machine.

No email address.

- [ ] **Step 3: Write `CODE_OF_CONDUCT.md`**

Contributor Covenant 2.1 verbatim, with the contact placeholder filled by a GitHub route — a private report to the maintainer through the repository — not an email address.

- [ ] **Step 4: Write `CHANGELOG.md`**

Keep a Changelog format. One released entry, `## [0.1.0] - 2026-08-13`, assembled from the git history, grouped under Added / Changed / Fixed. Cover the arc the 72 commits describe: the detector and catalogue, the vault and deterministic placeholders, Markdown handling, the evaluation harness and gold set, the OpenAI-compatible gateway with streaming and the Responses API, and the Docker images. Add an `## [Unreleased]` heading above it.

Read the history rather than guessing:

```bash
git log --oneline --reverse | head -80
```

- [ ] **Step 5: Verify**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass

Run: `grep -rn "@" CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md`
Expected: no email addresses (matches on `@` inside code or handles are fine; an `x@y.z` pattern is not).

- [ ] **Step 6: Commit**

```bash
git add CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md CHANGELOG.md
```

```bash
git commit -F - <<'MSG'
docs: write down how this project is contributed to

CONTRIBUTING states the two rules that are actually load-bearing here:
never paste real PII into an issue or a test, and never publish a claim
about quality or speed without a report in docs/benchmarks that a
reviewer can rerun. Both are already practised; neither was written
down.

SECURITY names the threat model rather than reciting a template. The
vault stores plaintext PII on disk and restoration writes real PII back
into the client -- both are by design, and a user deserves to read that
before trusting the tool rather than after.

Contact routes through GitHub. A project about keeping personal data off
the wire should not open by publishing an address for scrapers.
MSG
```

---

### Task 9: GitHub Actions and templates

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml`, `.github/ISSUE_TEMPLATE/config.yml`, `.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: ruff check .

  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - run: pip install -e ".[dev,gateway]"
      # Model-marked tests need GLiNER2 weights on disk. A stock runner has
      # none, and downloading 2 GB of them on every push to prove the same
      # thing a local run proves is not a trade worth making.
      - run: pytest --cov=privaparse --cov-report=term-missing
      - name: Coverage summary
        if: always()
        shell: bash
        run: pytest --cov=privaparse --cov-report=term | tail -3 >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Write `.github/workflows/release.yml`**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install build
      - run: python -m build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: build
    runs-on: ubuntu-latest
    # Requires the maintainer's approval before the upload runs. A PyPI
    # version number cannot be reused, so a mistyped tag is permanent; one
    # click is cheap insurance.
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
```

No API token appears anywhere. Publishing authenticates with OIDC against the trusted publisher registered in Task 12.

- [ ] **Step 3: Write the issue templates**

`bug_report.yml` collects: PrivaParse version, install method (pipx / pip / Docker / source), the output of `privaparse doctor`, what happened, what was expected, and a reproduction. The reproduction field's description carries the no-real-PII rule at the moment someone is about to paste a document — that is where the rule needs to appear, not only in `CONTRIBUTING.md`.

`feature_request.yml` collects: the problem, the proposed behaviour, and — for anything touching detection — what gold evidence would show it works.

`config.yml`:

```yaml
blank_issues_enabled: false
contact_links:
  - name: Security report
    url: https://github.com/Jhiynn/PrivaParse/security/advisories/new
    about: Report a vulnerability privately. Never in a public issue.
```

- [ ] **Step 4: Write `.github/PULL_REQUEST_TEMPLATE.md`**

The checklist from `CONTRIBUTING.md` section 9, plus a line asking what evidence backs any claim about quality or speed, and a reminder that examples must be synthetic.

- [ ] **Step 5: Validate the workflow files parse**

```bash
.venv/Scripts/python -c "import yaml,sys,pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github').rglob('*.yml')]; print('parsed')"
```

Expected: `parsed`

- [ ] **Step 6: Commit**

```bash
git add .github
```

```bash
git commit -F - <<'MSG'
ci: run lint and tests on every push, gate releases on a human

Tests cross ubuntu and windows on 3.11 through 3.13; the model-marked
tests stay deselected because a stock runner has no GLiNER2 weights and
downloading 2 GB on every push to reprove what a local run proves is not
a trade worth making.

Release builds on a v* tag and then waits: the publish job runs in an
environment with a required reviewer, because a PyPI version number
cannot be reused and a mistyped tag is therefore permanent. Uploads
authenticate by OIDC, so no token is stored here.

The bug template carries the no-real-PII rule in the reproduction field,
which is where someone is actually about to paste a document.
MSG
```

---

### Task 10: Verify the install in a clean sandbox

The development machine is the worst place to test an install: the package is already there editable, the dependencies are resolved, the weights are on disk, and the shell is Windows while most readers are not.

**Files:**
- None modified unless a defect is found. Fixes land in `README.md`, `docs/install.md`, `CONTRIBUTING.md` or `pyproject.toml` as this task discovers them.

- [ ] **Step 1: Build the distributions**

```bash
.venv/Scripts/python -m pip install -q build && .venv/Scripts/python -m build
```

Expected: `dist/privaparse-0.1.0.tar.gz` and `dist/privaparse-0.1.0-py3-none-any.whl`.

- [ ] **Step 2: Create the sandbox**

Use the `/lab` skill to create a Linux sandbox with Python 3.11 or later. Upload `dist/privaparse-0.1.0-py3-none-any.whl` and `tests/data/beispiel.md`.

- [ ] **Step 3: Verify the pipx path a stranger takes**

In the sandbox:

```bash
python -m pip install --quiet pipx && python -m pipx install "./privaparse-0.1.0-py3-none-any.whl[gateway]"
```

```bash
privaparse --help && privaparse doctor
```

Expected: help text listing the commands; `doctor` reports `device=cpu` and the regex detector without requiring model weights. If `doctor` fails or demands GLiNER2 before it can report anything, that is a defect in the default path — a stranger's first command must not require a 2 GB download.

- [ ] **Step 4: Verify the round trip without a model**

```bash
PRIVAPARSE_DETECTOR=regex privaparse demo beispiel.md
```

Expected: all five stages print, and the restored text equals the original. This is the claim the README's diagram makes; it should hold on a machine that has never seen the project.

- [ ] **Step 5: Verify the contributor path**

In a second sandbox directory, clone or upload the source tree and run exactly what `CONTRIBUTING.md` prescribes:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,gateway]"
```

```bash
.venv/bin/pytest -q && .venv/bin/ruff check .
```

Expected: the same pass count as locally, and clean lint. A Linux run also proves the suite is not accidentally Windows-dependent — the tests have only ever run on one platform, which is exactly what the CI matrix in Task 9 is meant to catch, and this finds it before the badge does.

- [ ] **Step 6: Record the transcript and fix what it found**

Write the verified commands and their output into `docs/install.md` as a short "Verified on" note, naming the Python version and platform. Any command in the README or `CONTRIBUTING.md` that did not work verbatim is corrected now — the transcript is the authority, not the intention.

- [ ] **Step 7: Commit**

```bash
git add -A
```

```bash
git commit -F - <<'MSG'
docs: correct the install against a machine that had never seen it

Verified in a clean Linux sandbox rather than on the machine that wrote
the code, where the package is already installed editable, the
dependencies are resolved and the weights are on disk. The README's own
lines were run verbatim; what did not work verbatim is fixed here.
MSG
```

- [ ] **Step 8: Delete the sandbox**

---

### Task 11: Repository settings available before going public

**Files:** none. These are `gh api` calls against `Jhiynn/PrivaParse`.

- [ ] **Step 1: Set metadata and features**

```bash
gh repo edit Jhiynn/PrivaParse \
  --description "Local privacy layer for LLM traffic: detects PII, swaps it for deterministic placeholders, restores it in the answer. Nothing leaves the machine." \
  --enable-issues --enable-wiki=false --enable-projects=false \
  --add-topic privacy --add-topic pii --add-topic pseudonymization \
  --add-topic gdpr --add-topic llm --add-topic openai-api \
  --add-topic gliner --add-topic german --add-topic nlp
```

- [ ] **Step 2: Set the merge policy**

```bash
gh api -X PATCH repos/Jhiynn/PrivaParse \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F allow_auto_merge=true \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY
```

- [ ] **Step 3: Create the `main` ruleset**

Two rules only. Required status checks and required pull requests are deliberately absent: in a ruleset both apply to every push including the maintainer's, which would convert this into the full PR gate that was considered and declined. An outside contributor is already held by having no write access.

```bash
gh api -X POST repos/Jhiynn/PrivaParse/rulesets --input - <<'JSON'
{
  "name": "main guardrails",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]
}
JSON
```

- [ ] **Step 4: Replace the default labels**

Delete GitHub's unused defaults and create the project's set:

```bash
for stale in "duplicate" "invalid" "wontfix" "question"; do gh label delete "$stale" --repo Jhiynn/PrivaParse --yes 2>/dev/null || true; done
```

```bash
gh label create detector  --repo Jhiynn/PrivaParse --color 1D76DB --description "Detection: model, rules, thresholds" --force
gh label create gateway   --repo Jhiynn/PrivaParse --color 0E8A16 --description "OpenAI-compatible local gateway" --force
gh label create catalogue --repo Jhiynn/PrivaParse --color 5319E7 --description "Entity types, labels, placeholders" --force
gh label create benchmark --repo Jhiynn/PrivaParse --color FBCA04 --description "Measured evidence and evaluation" --force
gh label create docs      --repo Jhiynn/PrivaParse --color 0075CA --description "Documentation" --force
gh label create packaging --repo Jhiynn/PrivaParse --color C5DEF5 --description "Install, release, dependencies" --force
```

- [ ] **Step 5: Create the gated `pypi` environment**

```bash
gh api -X PUT repos/Jhiynn/PrivaParse/environments/pypi --input - <<'JSON'
{
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
JSON
```

Then add the tag policy and the required reviewer:

```bash
gh api -X POST repos/Jhiynn/PrivaParse/environments/pypi/deployment-branch-policies -f name='v*' -f type=tag
```

Required reviewers are set through the environment's `reviewers` field, which needs the maintainer's own user id:

```bash
gh api user --jq .id
```

```bash
gh api -X PUT repos/Jhiynn/PrivaParse/environments/pypi --input - <<'JSON'
{
  "reviewers": [{"type": "User", "id": REPLACE_WITH_THE_ID_FROM_THE_PREVIOUS_COMMAND}],
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
JSON
```

- [ ] **Step 6: Read every setting back**

A successful write is not proof. Several of these endpoints accept a request and apply less than was asked when a plan or a visibility setting does not allow the feature.

```bash
gh api repos/Jhiynn/PrivaParse --jq '{squash: .allow_squash_merge, merge: .allow_merge_commit, rebase: .allow_rebase_merge, delete_branch: .delete_branch_on_merge, wiki: .has_wiki, projects: .has_projects, topics: .topics}'
```

```bash
gh api repos/Jhiynn/PrivaParse/rulesets --jq '.[] | {name, enforcement}'
```

```bash
gh api repos/Jhiynn/PrivaParse/environments/pypi --jq '{name, reviewers: [.protection_rules[]? | select(.type=="required_reviewers") | .reviewers[].reviewer.login]}'
```

Expected: squash true, merge and rebase false, delete_branch true, wiki and projects false, nine topics, one active ruleset, and the maintainer listed as a required reviewer on `pypi`.

- [ ] **Step 7: Push everything committed so far**

```bash
git push origin main
```

---

### Task 12: Go public, then apply what only a public repository allows

Two steps in this task belong to the maintainer and cannot be run from here.

- [ ] **Step 1: Final pre-flight**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass

Run: `.venv/Scripts/python -m ruff check .`
Expected: `All checks passed!`

Confirm once more that the history carries no secret:

```bash
git log --all --pretty=format: --name-only --diff-filter=A | sort -u | grep -Ei '\.env$|secret|credential|\.pem$|\.key$'
```

Expected: no output. (`.env.example` is tracked deliberately and does not match this pattern.)

- [ ] **Step 2: MAINTAINER — flip the repository to public**

This is the irreversible step and it is the maintainer's to take. It exposes all 72 commits, not just the current tree.

- [ ] **Step 3: Enable the security features that require a public repository**

```bash
gh api -X PATCH repos/Jhiynn/PrivaParse --input - <<'JSON'
{"security_and_analysis": {"secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}}}
JSON
```

```bash
gh api -X PUT repos/Jhiynn/PrivaParse/private-vulnerability-reporting
```

- [ ] **Step 4: Read the security settings back**

```bash
gh api repos/Jhiynn/PrivaParse --jq '.security_and_analysis'
```

```bash
gh api repos/Jhiynn/PrivaParse/private-vulnerability-reporting --jq '.enabled'
```

Expected: secret scanning and push protection `enabled`; private vulnerability reporting `true`. `SECURITY.md` points at the advisory form, so this one failing silently would leave a documented reporting route that does not exist.

- [ ] **Step 5: Confirm CI is green on the public repository**

```bash
gh run list --repo Jhiynn/PrivaParse --limit 5
```

Expected: the CI workflow succeeded across all six matrix legs. The README advertises a badge; it must be green before anyone sees it.

- [ ] **Step 6: MAINTAINER — register the PyPI trusted publisher**

At pypi.org, add a pending publisher for project `privaparse`, owner `Jhiynn`, repository `PrivaParse`, workflow `release.yml`, environment `pypi`. Nothing is uploaded until a `v*` tag is pushed and the environment approval is granted, so this can be done before or after the tag.

- [ ] **Step 7: Final acceptance**

Confirm each item from the spec's verification section:

- `pytest` green, `ruff check` clean.
- A wheel installs into an empty environment and `privaparse --help` works — proven in Task 10.
- Every relative link resolves — proven by `tests/test_docs_links.py`.
- CI green on the public repository.
- A clean clone plus the README's install command reaches a successful `privaparse demo` — proven in Task 10.
- Repository settings read back as specified — proven in Task 11 Step 6 and Task 12 Step 4.
- No email address in the published tree.
