# OpenAI Round-Trip Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether placeholders survive a real cloud LLM byte-identically, so that `reverse()` can still resolve them.

**Architecture:** An `LlmClient` protocol mirrors the existing `Detector` protocol, so every test runs against a fake and nothing touches the network by accident. A classifier sorts every placeholder-shaped token in the model's answer into exact / deformed / invented / foreign. A guard refuses to transmit text that still contains unambiguous original values. One new CLI command, `roundtrip`, owns the whole cycle.

**Tech Stack:** Python 3.12, httpx (already a dependency — no OpenAI SDK), typer, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-openai-roundtrip-design.md`
- **Only `roundtrip` may open a socket.** No configuration value causes `demo`, `pseudonymize`, `detect`, `eval` or `bench` to reach the network.
- **No key, no call.** A missing `OPENAI_API_KEY` aborts. Never fall back to the mock silently.
- The API key is read from the environment only. It must never appear in `.env.example`, in the vault, or in a log line.
- Deformed placeholders are **counted, never repaired**. `reverse()` and `PLACEHOLDER_RE` are not loosened by this work.
- Default model `gpt-4o-mini`.
- Follow the existing house style: `from __future__ import annotations`, frozen dataclasses for value types, `Protocol` for seams, docstrings that say *why* rather than restating the signature.
- Line length 100 (`[tool.ruff]`).
- Every task ends green on `pytest -q` (which excludes `model` and, after Task 4, `network`).

---

### Task 1: LlmClient protocol, and move the mock behind it

**Files:**
- Create: `privaparse/llm/__init__.py`
- Create: `privaparse/llm/base.py`
- Create: `privaparse/llm/mock.py`
- Delete: `privaparse/app/mock_llm.py`
- Modify: `privaparse/app/main.py:14` (import), `privaparse/app/main.py:185` (call site)
- Modify: `tests/test_mvp.py:9` (import)
- Test: `tests/test_llm_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LlmResponse(text: str, model: str, tokens_in: int = 0, tokens_out: int = 0)`; `LlmClient` protocol with attribute `name: str` and method `complete(self, prompt: str, document: str) -> LlmResponse`; `MockLlmClient()`; `mock_llm_response(text: str) -> str` re-exported from its new home.

- [ ] **Step 1: Write the failing test**

`tests/test_llm_base.py`:

```python
from __future__ import annotations

from privaparse.llm.base import LlmClient, LlmResponse
from privaparse.llm.mock import MockLlmClient, mock_llm_response


def test_mock_satisfies_the_client_protocol() -> None:
    """The protocol is what lets every other test avoid the network."""
    assert isinstance(MockLlmClient(), LlmClient)


def test_mock_carries_placeholders_into_its_answer() -> None:
    client = MockLlmClient()
    response = client.complete("Antworte bitte", "Hallo [[PERSON_A1]], Mail [[EMAIL_A2]].")

    assert isinstance(response, LlmResponse)
    assert "[[PERSON_A1]]" in response.text
    assert response.model == "mock"


def test_mock_response_function_still_available() -> None:
    """tests/test_mvp.py uses it directly; the move must not break that."""
    assert "[[PERSON_A1]]" in mock_llm_response("Hallo [[PERSON_A1]].")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'privaparse.llm'`

- [ ] **Step 3: Create the package and the protocol**

`privaparse/llm/__init__.py`:

```python
"""Clients for the model on the other side of the pseudonymisation boundary.

Everything in this package sees placeholders, never original values. The one
client that reaches the network is guarded by :mod:`privaparse.llm.guard`.
"""
```

`privaparse/llm/base.py`:

```python
"""The seam between PrivaParse and whatever answers the prompt.

Mirrors the `Detector` protocol in `privaparse.parser.detector`: a narrow
interface with a fake implementation, so the default test suite never opens a
socket and a second provider is a new file rather than a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LlmResponse:
    """What came back, plus what it cost."""

    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


@runtime_checkable
class LlmClient(Protocol):
    """Answers a prompt about a document.

    ``document`` is always pseudonymised text. Nothing in this package should
    ever be handed an original value; :mod:`privaparse.llm.guard` enforces that
    for the client that transmits.
    """

    name: str

    def complete(self, prompt: str, document: str) -> LlmResponse: ...
```

- [ ] **Step 4: Move the mock**

Copy `privaparse/app/mock_llm.py` to `privaparse/llm/mock.py` unchanged, then append the client wrapper and update the module docstring:

```python
"""A stand-in for the LLM, so the round trip can be exercised without a network.

Deliberately cooperative: it echoes placeholders back verbatim because it was
written to. That is exactly the assumption `privaparse roundtrip` exists to
test against a real model.
"""
```

Append at the end of `privaparse/llm/mock.py`:

```python
class MockLlmClient:
    """The :class:`~privaparse.llm.base.LlmClient` face of :func:`mock_llm_response`."""

    name = "mock"

    def complete(self, prompt: str, document: str) -> LlmResponse:
        # The prompt is ignored on purpose: the mock has one behaviour, and
        # pretending to follow instructions would make it look more capable
        # than it is.
        return LlmResponse(text=mock_llm_response(document), model="mock")
```

Add to its imports:

```python
from privaparse.llm.base import LlmResponse
```

Then delete `privaparse/app/mock_llm.py`.

- [ ] **Step 5: Update the two call sites**

`privaparse/app/main.py` line 14 — replace:

```python
from privaparse.app.mock_llm import mock_llm_response
```

with:

```python
from privaparse.llm.mock import mock_llm_response
```

`tests/test_mvp.py` line 9 — same replacement.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, 288 passed (285 existing + 3 new)

- [ ] **Step 7: Commit**

```bash
git add privaparse/llm tests/test_llm_base.py privaparse/app/main.py tests/test_mvp.py
git rm privaparse/app/mock_llm.py
git commit -m "refactor: put the mock LLM behind an LlmClient protocol"
```

---

### Task 2: Placeholder fidelity classifier

**Files:**
- Create: `privaparse/llm/fidelity.py`
- Test: `tests/test_llm_fidelity.py`

**Interfaces:**
- Consumes: `privaparse.database.placeholder.PLACEHOLDER_RE`.
- Produces: `Fidelity` (str enum: `EXACT`, `DEFORMED`, `INVENTED`, `FOREIGN`); `Occurrence(raw: str, canonical: str | None, kind: Fidelity, start: int, end: int)`; `FidelityReport` with `.occurrences: list[Occurrence]`, `.counts: dict[Fidelity, int]`, `.rate: float`, `.deformations: list[str]`; `classify(answer: str, *, issued: set[str], is_known: Callable[[str], bool]) -> FidelityReport`.

**Design note for the implementer.** Do not try to find deformations with a
loose regex — `Version A1` would match one. Work from the other end: you already
know which placeholders were issued, so search for each one's *core*
(`PERSON_A1` out of `[[PERSON_A1]]`) case-insensitively, and decide from the
surrounding characters whether it was well-formed. That has no false positives.
Well-formed matches found by `PLACEHOLDER_RE` are handled separately, because
those may also be invented or foreign, whose cores are unknown in advance.

Note also what is *not* a deformation: `**[[PERSON_A1]]**` resolves correctly,
because `PLACEHOLDER_RE` matches the inner token and ignores the asterisks.
Bold, italics and surrounding punctuation are harmless. The breaking variants
are the ones that alter the token itself or its brackets.

- [ ] **Step 1: Write the failing test**

`tests/test_llm_fidelity.py`:

```python
"""Classifying what a real model does to placeholders.

A deformed placeholder is worse than an omitted one: omission loses
information, deformation leaves a masked name in a document the user believes
has been restored.
"""

from __future__ import annotations

import pytest

from privaparse.llm.fidelity import Fidelity, classify

ISSUED = {"[[PERSON_A1]]", "[[EMAIL_A2]]"}


def _known(placeholder: str) -> bool:
    """Stands in for the vault: A1/A2 are ours, B7 belongs to another session."""
    return placeholder in ISSUED or placeholder == "[[PERSON_B7]]"


def _classify(answer: str):
    return classify(answer, issued=ISSUED, is_known=_known)


def test_a_verbatim_placeholder_is_exact() -> None:
    report = _classify("Sehr geehrte(r) [[PERSON_A1]],")
    assert report.counts[Fidelity.EXACT] == 1
    assert report.counts[Fidelity.DEFORMED] == 0


def test_markdown_emphasis_is_not_a_deformation() -> None:
    """`reverse()` matches the inner token and leaves the asterisks alone."""
    report = _classify("Hallo **[[PERSON_A1]]**, bitte melden.")
    assert report.counts[Fidelity.EXACT] == 1
    assert report.counts[Fidelity.DEFORMED] == 0


@pytest.mark.parametrize(
    "variant",
    [
        "[PERSON_A1]",
        "[[ PERSON_A1 ]]",
        "PERSON_A1",
        "{{PERSON_A1}}",
        "[[PERSON A1]]",
        r"\[\[PERSON_A1\]\]",
        "[[person_a1]]",
    ],
)
def test_broken_wrappings_are_deformations(variant: str) -> None:
    report = _classify(f"Hallo {variant}, bitte melden.")
    assert report.counts[Fidelity.DEFORMED] == 1
    assert report.counts[Fidelity.EXACT] == 0
    assert report.occurrences[0].canonical == "[[PERSON_A1]]"


def test_a_placeholder_broken_across_a_line_is_still_found() -> None:
    """Models wrap lines. A placeholder split by one must not vanish from the
    statistics — an uncounted deformation is worse than a counted one."""
    report = _classify("Sehr geehrte(r) PERSON_\nA1, danke.")
    assert report.counts[Fidelity.DEFORMED] == 1
    assert report.occurrences[0].canonical == "[[PERSON_A1]]"


def test_a_placeholder_never_issued_is_invented() -> None:
    report = _classify("Grüße an [[PERSON_ZZ9]].")
    assert report.counts[Fidelity.INVENTED] == 1


def test_a_placeholder_from_another_session_is_foreign() -> None:
    report = _classify("Grüße an [[PERSON_B7]].")
    assert report.counts[Fidelity.FOREIGN] == 1
    assert report.counts[Fidelity.INVENTED] == 0


def test_rate_counts_only_exact_against_deformed() -> None:
    """Invented and foreign measure something else and must not dilute it."""
    report = _classify("[[PERSON_A1]] und [EMAIL_A2] und [[PERSON_ZZ9]]")
    assert report.counts[Fidelity.EXACT] == 1
    assert report.counts[Fidelity.DEFORMED] == 1
    assert report.counts[Fidelity.INVENTED] == 1
    assert report.rate == pytest.approx(0.5)


def test_rate_is_one_when_nothing_came_back() -> None:
    """Omitting every placeholder is legitimate, not a fidelity failure."""
    report = _classify("Vielen Dank für Ihre Nachricht.")
    assert report.occurrences == []
    assert report.rate == 1.0


def test_deformations_are_reported_verbatim() -> None:
    """The point is to see which variants a model actually produces."""
    report = _classify("Hallo [PERSON_A1] und [[ EMAIL_A2 ]].")
    assert sorted(report.deformations) == ["[PERSON_A1]", "[[ EMAIL_A2 ]]"]


def test_repeated_placeholders_are_counted_once_each() -> None:
    report = _classify("[[PERSON_A1]] ... [[PERSON_A1]]")
    assert report.counts[Fidelity.EXACT] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_fidelity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'privaparse.llm.fidelity'`

- [ ] **Step 3: Write the implementation**

`privaparse/llm/fidelity.py`:

```python
"""How faithfully did the model hand our placeholders back?

Not "did they all come back" — a model asked to summarise may legitimately
mention none of them. The question is narrower: of the ones it *did* emit, how
many are byte-identical, and therefore still resolvable by ``reverse()``.

A deformed placeholder is the worst outcome available. An omitted one loses
information visibly; a deformed one leaves a masked name sitting in a document
the user believes has been restored, with nothing to signal it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from privaparse.database.placeholder import PLACEHOLDER_RE

__all__ = ["Fidelity", "Occurrence", "FidelityReport", "classify"]

#: How far around a deformed core to quote, so the variant is recognisable.
_CONTEXT = 4


class Fidelity(str, Enum):
    EXACT = "exact"
    DEFORMED = "deformed"
    INVENTED = "invented"
    FOREIGN = "foreign"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Occurrence:
    raw: str
    canonical: str | None
    kind: Fidelity
    start: int
    end: int


@dataclass
class FidelityReport:
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def counts(self) -> dict[Fidelity, int]:
        tally = {kind: 0 for kind in Fidelity}
        for occurrence in self.occurrences:
            tally[occurrence.kind] += 1
        return tally

    @property
    def rate(self) -> float:
        """Exact against deformed.

        Invented and foreign are excluded deliberately: they measure the model
        making things up, not the model damaging what it was given, and
        averaging the two would hide both.
        """
        tally = self.counts
        seen = tally[Fidelity.EXACT] + tally[Fidelity.DEFORMED]
        return tally[Fidelity.EXACT] / seen if seen else 1.0

    @property
    def deformations(self) -> list[str]:
        return [o.raw for o in self.occurrences if o.kind is Fidelity.DEFORMED]


def classify(
    answer: str,
    *,
    issued: set[str],
    is_known: Callable[[str], bool],
) -> FidelityReport:
    """Sort every placeholder-shaped token in ``answer`` into one of four classes.

    ``issued`` are the placeholders this document was actually given.
    ``is_known`` answers whether the vault has ever issued one, which is what
    separates a foreign placeholder from an invented one.
    """
    report = FidelityReport()
    claimed: list[tuple[int, int]] = []

    # Well-formed tokens first. Their cores are unknown in advance when they
    # are invented, so they cannot be found by searching for issued cores.
    for match in PLACEHOLDER_RE.finditer(answer):
        placeholder = match.group(0)
        if placeholder in issued:
            kind = Fidelity.EXACT
        elif is_known(placeholder):
            kind = Fidelity.FOREIGN
        else:
            kind = Fidelity.INVENTED
        report.occurrences.append(
            Occurrence(
                raw=placeholder,
                canonical=placeholder if kind is not Fidelity.INVENTED else None,
                kind=kind,
                start=match.start(),
                end=match.end(),
            )
        )
        claimed.append(match.span())

    # Then the damaged ones. Searching for each issued core rather than for a
    # loose pattern is what keeps "Version A1" out of the results.
    for placeholder in sorted(issued):
        core = placeholder.strip("[]")
        for match in _core_pattern(core).finditer(answer):
            if _overlaps(match.span(), claimed):
                continue
            start = max(0, match.start() - _CONTEXT)
            end = min(len(answer), match.end() + _CONTEXT)
            report.occurrences.append(
                Occurrence(
                    raw=_trim_variant(answer[start:end], answer[match.start() : match.end()]),
                    canonical=placeholder,
                    kind=Fidelity.DEFORMED,
                    start=match.start(),
                    end=match.end(),
                )
            )
            claimed.append(match.span())

    report.occurrences.sort(key=lambda o: o.start)
    return report


def _core_pattern(core: str) -> re.Pattern[str]:
    """Match ``PERSON_A1`` however the model spelled the separator or the case.

    The separator class includes whitespace, not just space and underscore,
    because a model that wraps a line mid-placeholder would otherwise produce a
    deformation that appears in no count at all — the one outcome worse than a
    counted one.
    """
    type_part, _, suffix = core.partition("_")
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(type_part)}[\s_]{re.escape(suffix)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in taken)


def _trim_variant(window: str, core: str) -> str:
    """Keep the brackets around the core, drop the surrounding prose."""
    index = window.find(core)
    if index == -1:  # pragma: no cover - the core came from this window
        return core
    before = window[:index]
    after = window[index + len(core) :]
    lead = re.search(r"[\[{\\ ]*$", before)
    trail = re.match(r"^[\]} \\]*", after)
    return (lead.group(0) if lead else "") + core + (trail.group(0) if trail else "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_fidelity.py -v`
Expected: PASS, 16 passed (the deformation case is parametrised seven ways)

- [ ] **Step 5: Commit**

```bash
git add privaparse/llm/fidelity.py tests/test_llm_fidelity.py
git commit -m "feat: classify placeholder fidelity in an LLM answer"
```

---

### Task 3: Residual-value guard

**Files:**
- Create: `privaparse/llm/guard.py`
- Test: `tests/test_llm_guard.py`

**Interfaces:**
- Consumes: `restore_table: dict[str, str]` as returned by `VaultRepository.restore_table(mapping_id)`.
- Produces: `ResidualFinding(placeholder: str, entity_type: str, count: int, blocking: bool)`; `ResidualValueError(RuntimeError)`; `scan_residuals(text: str, restore_table: dict[str, str]) -> list[ResidualFinding]`; `assert_safe_to_send(text: str, restore_table: dict[str, str], *, allow_residual: bool = False) -> list[ResidualFinding]`.

**Design note for the implementer.** The realistic mistake this catches is not a
bug in `pseudonymize()` — it is a caller handing over the original file instead
of the `.pseudo` one. But not every hit is a leak: a document can contain
`Winter` as a surname *and* as a season, and the season survives
pseudonymisation correctly. So blocking depends on whether the value could
plausibly be ordinary prose.

- [ ] **Step 1: Write the failing test**

`tests/test_llm_guard.py`:

```python
"""The last check before anything leaves the machine.

This is the one place in the project where a mistake ships data off the box
rather than merely spoiling a file, so it fails closed for values that cannot
be innocent, and warns for values that can.
"""

from __future__ import annotations

import pytest

from privaparse.llm.guard import (
    ResidualValueError,
    assert_safe_to_send,
    scan_residuals,
)

TABLE = {
    "[[PERSON_A1]]": "Max Mustermann",
    "[[EMAIL_A2]]": "max@test.de",
    "[[PHONE_A3]]": "+49 170 1234567",
    "[[PERSON_A4]]": "Winter",
}


def test_clean_text_passes() -> None:
    assert assert_safe_to_send("Hallo [[PERSON_A1]], Mail [[EMAIL_A2]].", TABLE) == []


@pytest.mark.parametrize(
    "leaked",
    ["max@test.de", "+49 170 1234567", "Max Mustermann"],
)
def test_unambiguous_values_refuse_to_send(leaked: str) -> None:
    """An address, a number or a full name cannot be innocent prose."""
    with pytest.raises(ResidualValueError) as excinfo:
        assert_safe_to_send(f"Bitte an {leaked} schicken.", TABLE)
    assert "refusing to send" in str(excinfo.value)


def test_a_single_name_token_only_warns() -> None:
    """`Winter` is a surname here and also a season; the season is not a leak."""
    findings = assert_safe_to_send("Im Winter war es kalt.", TABLE)

    assert [f.blocking for f in findings] == [False]
    assert findings[0].entity_type == "PERSON"


def test_allow_residual_overrides_the_refusal() -> None:
    findings = assert_safe_to_send(
        "Bitte an max@test.de schicken.", TABLE, allow_residual=True
    )
    assert [f.blocking for f in findings] == [True]


def test_scan_counts_occurrences() -> None:
    findings = scan_residuals("max@test.de und nochmal max@test.de", TABLE)
    assert len(findings) == 1
    assert findings[0].count == 2
    assert findings[0].placeholder == "[[EMAIL_A2]]"


def test_scan_respects_word_boundaries() -> None:
    """`Winter` must not fire on `Wintergarten`."""
    assert scan_residuals("Der Wintergarten ist neu.", TABLE) == []


def test_the_error_never_quotes_the_leaked_value() -> None:
    """The exception text may reach a log; the value must not travel with it."""
    with pytest.raises(ResidualValueError) as excinfo:
        assert_safe_to_send("Bitte an max@test.de schicken.", TABLE)
    assert "max@test.de" not in str(excinfo.value)
    assert "[[EMAIL_A2]]" in str(excinfo.value)


def test_an_empty_table_is_not_an_error() -> None:
    """A document with no PII produces no restore table and is safe to send."""
    assert assert_safe_to_send("Ein Text ohne alles.", {}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'privaparse.llm.guard'`

- [ ] **Step 3: Write the implementation**

`privaparse/llm/guard.py`:

```python
"""Refuse to transmit text that still contains original values.

Everywhere else in PrivaParse a bug spoils a file. Here it ships a name to a
third party, so this check fails closed.

It cannot fail closed on everything, though. A document may contain ``Winter``
as a surname and as a season, and the season is still in the text after
pseudonymisation because it was never PII. Blocking that would make the guard
the thing that breaks working documents. So the rule follows whether the value
could plausibly be ordinary prose: an address or a phone number could not, a
lone forename could.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import PLACEHOLDER_RE

log = get_logger("llm.guard")

__all__ = ["ResidualFinding", "ResidualValueError", "scan_residuals", "assert_safe_to_send"]


class ResidualValueError(RuntimeError):
    """Raised when the outgoing text still contains an unambiguous original."""


@dataclass(frozen=True)
class ResidualFinding:
    placeholder: str
    entity_type: str
    count: int
    blocking: bool


def scan_residuals(text: str, restore_table: dict[str, str]) -> list[ResidualFinding]:
    """Every original value from ``restore_table`` still present in ``text``."""
    findings: list[ResidualFinding] = []

    for placeholder, original in restore_table.items():
        value = original.strip()
        if not value:
            continue
        occurrences = len(_boundary_pattern(value).findall(text))
        if not occurrences:
            continue
        entity_type = _type_of(placeholder)
        findings.append(
            ResidualFinding(
                placeholder=placeholder,
                entity_type=entity_type,
                count=occurrences,
                blocking=_is_unambiguous(entity_type, value),
            )
        )
    return findings


def assert_safe_to_send(
    text: str,
    restore_table: dict[str, str],
    *,
    allow_residual: bool = False,
) -> list[ResidualFinding]:
    """Check ``text``, raising if it carries an unambiguous original value.

    Returns every finding, blocking or not, so the caller can report the
    warnings too. ``allow_residual`` downgrades the refusal but never silences
    the warning.
    """
    findings = scan_residuals(text, restore_table)
    blocking = [f for f in findings if f.blocking]

    for finding in findings:
        log.warning(
            "outgoing text still contains the original behind %s (%s, %dx)%s",
            finding.placeholder,
            finding.entity_type,
            finding.count,
            "" if finding.blocking else " — ambiguous value, sending anyway",
        )

    if blocking and not allow_residual:
        names = ", ".join(f.placeholder for f in blocking)
        raise ResidualValueError(
            f"refusing to send: the text still contains the original value behind "
            f"{names}. This usually means the original file was passed instead of "
            f"the pseudonymised one. Override with --allow-residual if it is "
            f"genuinely a coincidence."
        )
    return findings


def _type_of(placeholder: str) -> str:
    match = PLACEHOLDER_RE.fullmatch(placeholder)
    return match.group(1) if match else "UNKNOWN"


def _is_unambiguous(entity_type: str, value: str) -> bool:
    """Could this value plausibly be ordinary prose?

    An address or a phone number could not. A full name effectively could not.
    A single token could — it may be a season, a month, a common noun.
    """
    if entity_type in {"EMAIL", "PHONE"}:
        return True
    return len(value.split()) >= 2


def _boundary_pattern(value: str) -> re.Pattern[str]:
    """Match the value as a unit, so `Winter` does not fire on `Wintergarten`."""
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_guard.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add privaparse/llm/guard.py tests/test_llm_guard.py
git commit -m "feat: refuse to transmit text still holding original values"
```

---

### Task 4: OpenAI client

**Files:**
- Create: `privaparse/llm/openai.py`
- Modify: `pyproject.toml:38-41` (add the `network` marker and exclude it by default)
- Test: `tests/test_llm_openai.py`

**Interfaces:**
- Consumes: `LlmResponse`, `LlmClient` from Task 1.
- Produces: `OpenAIClient(model: str = "gpt-4o-mini", *, api_key: str | None = None, max_tokens: int = 1024, timeout: float = 60.0, base_url: str = "https://api.openai.com/v1", transport=None)`; `MissingApiKeyError(RuntimeError)`; `OpenAIError(RuntimeError)`; `estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None`.

**Design note for the implementer.** `httpx` is already a dependency (it arrives
with `huggingface_hub`), so do not add the OpenAI SDK for one POST. The
`transport` parameter exists so tests can substitute `httpx.MockTransport` and
exercise the real request-building and response-parsing code without a network.

- [ ] **Step 1: Write the failing test**

`tests/test_llm_openai.py`:

```python
"""The only client that reaches the network.

All of these run against httpx.MockTransport except the one marked `network`,
so the real request-building and response-parsing code is covered without a
socket.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from privaparse.llm.base import LlmClient
from privaparse.llm.openai import (
    MissingApiKeyError,
    OpenAIClient,
    OpenAIError,
    estimate_cost,
)


def _transport(handler):
    return httpx.MockTransport(handler)


def _ok(text: str = "Antwort mit [[PERSON_A1]].", tokens=(11, 7)):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini-2024-07-18",
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
            },
        )

    return _transport(handler)


def test_client_satisfies_the_protocol() -> None:
    client = OpenAIClient(api_key="sk-test", transport=_ok())
    assert isinstance(client, LlmClient)


def test_a_successful_call_returns_text_and_usage() -> None:
    client = OpenAIClient(api_key="sk-test", transport=_ok())
    response = client.complete("Antworte bitte", "Hallo [[PERSON_A1]].")

    assert response.text == "Antwort mit [[PERSON_A1]]."
    assert response.tokens_in == 11
    assert response.tokens_out == 7
    assert response.tokens_total == 18


def test_the_document_and_the_prompt_both_reach_the_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = OpenAIClient(api_key="sk-test", transport=_transport(handler))
    client.complete("Fasse zusammen", "Hallo [[PERSON_A1]].")

    body = json.dumps(seen)
    assert "Fasse zusammen" in body
    assert "[[PERSON_A1]]" in body
    assert seen["model"] == "gpt-4o-mini"


def test_the_api_key_travels_in_the_header_only() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    OpenAIClient(api_key="sk-secret", transport=_transport(handler)).complete("p", "d")

    assert seen["auth"] == "Bearer sk-secret"
    assert "sk-secret" not in seen["body"]


def test_a_missing_key_aborts_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent fallback to the mock would make a broken integration look green."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError, match="OPENAI_API_KEY"):
        OpenAIClient()


def test_the_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert OpenAIClient(transport=_ok()).complete("p", "d").text


def test_an_http_error_is_reported_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    client = OpenAIClient(api_key="sk-secret", transport=_transport(handler))
    with pytest.raises(OpenAIError) as excinfo:
        client.complete("p", "d")

    assert "401" in str(excinfo.value)
    assert "Invalid API key" in str(excinfo.value)
    assert "sk-secret" not in str(excinfo.value)


def test_a_malformed_response_is_an_error_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = OpenAIClient(api_key="sk-test", transport=_transport(handler))
    with pytest.raises(OpenAIError, match="unexpected response"):
        client.complete("p", "d")


def test_cost_estimate_for_a_known_model() -> None:
    assert estimate_cost("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)


def test_cost_estimate_is_none_for_an_unknown_model() -> None:
    """Better no number than a wrong one."""
    assert estimate_cost("some-future-model", 1000, 1000) is None


@pytest.mark.network
def test_a_real_call_reaches_openai() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    client = OpenAIClient(max_tokens=64)
    response = client.complete(
        "Antworte in einem Satz auf Deutsch.",
        "Hallo [[PERSON_A1]], bitte um Rückruf.",
    )
    assert response.text.strip()
    assert response.tokens_out > 0
```

- [ ] **Step 2: Register the marker**

`pyproject.toml`, replace lines 38-41:

```toml
markers = [
    "model: requires GLiNER2 weights on disk (slow, needs `pip install -e .[model]`)",
    "network: reaches a third-party API and costs money (needs OPENAI_API_KEY)",
]
addopts = "-m 'not model and not network'"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_openai.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'privaparse.llm.openai'`

- [ ] **Step 4: Write the implementation**

`privaparse/llm/openai.py`:

```python
"""OpenAI chat completions over httpx.

No SDK: this is one POST, and httpx is already a dependency. A smaller
dependency surface is worth more here than the convenience, in a tool whose
entire claim is about what does and does not leave the machine.
"""

from __future__ import annotations

import os

import httpx

from privaparse.app.logging import get_logger
from privaparse.llm.base import LlmResponse

log = get_logger("llm.openai")

__all__ = [
    "OpenAIClient",
    "MissingApiKeyError",
    "OpenAIError",
    "estimate_cost",
    "DEFAULT_MODEL",
]

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: USD per million tokens, (input, output). Deliberately short: a stale price is
#: worse than no price, so unknown models report no estimate at all.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


class MissingApiKeyError(RuntimeError):
    """Raised when no API key is available."""


class OpenAIError(RuntimeError):
    """Raised when the API rejects the call or answers something unexpected."""


class OpenAIClient:
    """Sends a prompt and a pseudonymised document to OpenAI.

    The ``transport`` parameter exists for tests: an ``httpx.MockTransport``
    exercises request building and response parsing without a socket.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise MissingApiKeyError(
                "OPENAI_API_KEY is not set. PrivaParse will not fall back to the "
                "mock client — a silent fallback would make a broken integration "
                "look like a passing test."
            )
        self._key = key
        self.name = "openai"
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def complete(self, prompt: str, document: str) -> LlmResponse:
        payload = {
            "model": self.model,
            "max_completion_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": document},
            ],
        }
        log.info("sending %d characters to %s (%s)", len(document), self.base_url, self.model)

        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise OpenAIError(f"request to {self.base_url} failed: {exc}") from exc

        if response.status_code != 200:
            raise OpenAIError(f"HTTP {response.status_code}: {_error_message(response)}")

        return _parse(response, fallback_model=self.model)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenAIClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _parse(response: httpx.Response, *, fallback_model: str) -> LlmResponse:
    try:
        body = response.json()
        text = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OpenAIError(f"unexpected response shape from the API: {exc}") from exc

    usage = body.get("usage") or {}
    return LlmResponse(
        text=text or "",
        model=body.get("model") or fallback_model,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
    )


def _error_message(response: httpx.Response) -> str:
    """The API's own message, never the request that carried the key."""
    try:
        return str(response.json().get("error", {}).get("message", response.text[:200]))
    except ValueError:
        return response.text[:200]


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD for this call, or ``None`` when the price is not known.

    Prices move; reporting a stale number with two decimal places would look
    more authoritative than it is.
    """
    for known, (price_in, price_out) in _PRICES.items():
        if model.startswith(known):
            return (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_openai.py -v`
Expected: PASS, 11 passed, 1 deselected (the `network` one)

- [ ] **Step 6: Commit**

```bash
git add privaparse/llm/openai.py tests/test_llm_openai.py pyproject.toml
git commit -m "feat: add an OpenAI client behind the LlmClient protocol"
```

---

### Task 5: The `roundtrip` command

**Files:**
- Create: `privaparse/llm/roundtrip.py`
- Modify: `privaparse/app/main.py` (add the command after `demo`, around line 200)
- Modify: `README.md` (a section under "Use")
- Test: `tests/test_roundtrip.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4, plus `PrivaParseEngine.pseudonymize`, `.reverse`, `.repository`, `.database`.
- Produces: `RoundTripResult(source, mapping_id, sent, answer, restored, fidelity, residuals, response)`; `run_roundtrip(engine, text, *, client, prompt, source_name=None, allow_residual=False) -> RoundTripResult`; `format_roundtrip_report(results) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_roundtrip.py`:

```python
"""The whole cycle against a fake model: pseudonymise, send, classify, reverse."""

from __future__ import annotations

import pytest

from privaparse.engine import PrivaParseEngine
from privaparse.llm.base import LlmResponse
from privaparse.llm.fidelity import Fidelity
from privaparse.llm.guard import ResidualValueError
from privaparse.llm.roundtrip import format_roundtrip_report, run_roundtrip


class ScriptedClient:
    """Answers with a template, so each test can dictate what the model does."""

    name = "scripted"

    def __init__(self, template: str) -> None:
        self.template = template
        self.seen: list[str] = []

    def complete(self, prompt: str, document: str) -> LlmResponse:
        self.seen.append(document)
        return LlmResponse(text=self.template, model="scripted", tokens_in=10, tokens_out=5)


def test_a_faithful_model_round_trips_cleanly(engine: PrivaParseEngine) -> None:
    client = ScriptedClient("Sehr geehrte(r) [[PERSON_A1]], danke.")
    result = run_roundtrip(
        engine, "Hallo, ich bin Max Mustermann.", client=client, prompt="Antworte"
    )

    assert "Max Mustermann" in result.restored.text
    assert result.fidelity.counts[Fidelity.EXACT] == 1
    assert result.fidelity.rate == 1.0
    assert result.restored.is_clean


def test_only_pseudonymised_text_is_handed_to_the_client(
    engine: PrivaParseEngine,
) -> None:
    client = ScriptedClient("ok")
    run_roundtrip(engine, "Hallo, ich bin Max Mustermann.", client=client, prompt="p")

    assert "Max Mustermann" not in client.seen[0]
    assert "[[PERSON_A1]]" in client.seen[0]


def test_a_deformed_answer_is_counted_and_left_masked(
    engine: PrivaParseEngine,
) -> None:
    """The failure this whole harness exists to detect."""
    client = ScriptedClient("Sehr geehrte(r) [PERSON_A1], danke.")
    result = run_roundtrip(
        engine, "Hallo, ich bin Max Mustermann.", client=client, prompt="Antworte"
    )

    assert result.fidelity.counts[Fidelity.DEFORMED] == 1
    assert result.fidelity.rate == 0.0
    assert "Max Mustermann" not in result.restored.text
    assert "[PERSON_A1]" in result.restored.text


def test_an_omitted_placeholder_is_not_a_failure(engine: PrivaParseEngine) -> None:
    client = ScriptedClient("Vielen Dank für Ihre Nachricht.")
    result = run_roundtrip(
        engine, "Hallo, ich bin Max Mustermann.", client=client, prompt="Fasse zusammen"
    )

    assert result.fidelity.occurrences == []
    assert result.fidelity.rate == 1.0


def test_an_invented_placeholder_is_reported(engine: PrivaParseEngine) -> None:
    client = ScriptedClient("Grüße an [[PERSON_ZZ9]].")
    result = run_roundtrip(
        engine, "Hallo, ich bin Max Mustermann.", client=client, prompt="p"
    )

    assert result.fidelity.counts[Fidelity.INVENTED] == 1
    assert result.restored.unknown == ["[[PERSON_ZZ9]]"]


def test_the_guard_stops_a_send_before_the_client_is_touched(
    engine: PrivaParseEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pseudonymiser that leaves an address behind must not reach the network."""
    from privaparse.llm import roundtrip as module

    client = ScriptedClient("ok")
    monkeypatch.setattr(
        module, "_pseudonymise", lambda engine, text, source: _LeakyResult(text)
    )

    with pytest.raises(ResidualValueError):
        run_roundtrip(engine, "Mail an max@test.de", client=client, prompt="p")
    assert client.seen == []


class _LeakyResult:
    """Stands in for a pseudonymiser that failed to replace anything."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.mapping_id = "leaky"
        self.spans: list = []

    @property
    def placeholders(self) -> list[str]:
        return []


def test_report_shows_the_rate_and_the_variants() -> None:
    from privaparse.llm.fidelity import classify
    from privaparse.llm.roundtrip import RoundTripResult
    from privaparse.parser.reverse_mapper import ReverseResult

    issued = {"[[PERSON_A1]]"}
    fidelity = classify("Hallo [PERSON_A1].", issued=issued, is_known=lambda p: p in issued)
    result = RoundTripResult(
        source="brief.md",
        mapping_id="m1",
        sent="Hallo [[PERSON_A1]].",
        answer="Hallo [PERSON_A1].",
        restored=ReverseResult(text="Hallo [PERSON_A1]."),
        fidelity=fidelity,
        residuals=[],
        response=LlmResponse(text="", model="gpt-4o-mini", tokens_in=10, tokens_out=5),
    )

    report = format_roundtrip_report([result])
    assert "gpt-4o-mini" in report
    assert "[PERSON_A1]" in report
    assert "0.000" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_roundtrip.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'privaparse.llm.roundtrip'`

- [ ] **Step 3: Write the orchestration**

`privaparse/llm/roundtrip.py`:

```python
"""One document through the whole cycle, with the answer graded on the way back.

The cycle owns pseudonymisation rather than accepting an already-pseudonymised
file. That keeps the mapping in one place instead of asking the caller to carry
an id between two commands, and it means the guard sees text this process
produced rather than text it was handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from privaparse.app.logging import get_logger
from privaparse.engine import PrivaParseEngine
from privaparse.llm.base import LlmClient, LlmResponse
from privaparse.llm.fidelity import Fidelity, FidelityReport, classify
from privaparse.llm.guard import ResidualFinding, assert_safe_to_send
from privaparse.llm.openai import estimate_cost
from privaparse.parser.pseudonymizer import PseudonymizationResult
from privaparse.parser.reverse_mapper import ReverseResult

log = get_logger("roundtrip")

__all__ = ["RoundTripResult", "run_roundtrip", "format_roundtrip_report"]


@dataclass
class RoundTripResult:
    source: str
    mapping_id: str
    sent: str
    answer: str
    restored: ReverseResult
    fidelity: FidelityReport
    residuals: list[ResidualFinding]
    response: LlmResponse

    @property
    def cost(self) -> float | None:
        return estimate_cost(self.response.model, self.response.tokens_in, self.response.tokens_out)


def run_roundtrip(
    engine: PrivaParseEngine,
    text: str,
    *,
    client: LlmClient,
    prompt: str,
    source_name: str | None = None,
    allow_residual: bool = False,
) -> RoundTripResult:
    """Pseudonymise, guard, send, classify, reverse."""
    pseudo = _pseudonymise(engine, text, source_name)

    with engine.database.session() as session:
        restore_table = engine.repository(session).restore_table(pseudo.mapping_id)

    residuals = assert_safe_to_send(pseudo.text, restore_table, allow_residual=allow_residual)

    response = client.complete(prompt, pseudo.text)

    issued = set(pseudo.placeholders)
    with engine.database.session() as session:
        repo = engine.repository(session)
        fidelity = classify(response.text, issued=issued, is_known=repo.placeholder_is_known)

    restored = engine.reverse(pseudo.mapping_id, response.text)

    log.info(
        "round trip: %d exact, %d deformed, %d invented, %d foreign",
        fidelity.counts[Fidelity.EXACT],
        fidelity.counts[Fidelity.DEFORMED],
        fidelity.counts[Fidelity.INVENTED],
        fidelity.counts[Fidelity.FOREIGN],
    )

    return RoundTripResult(
        source=source_name or "<text>",
        mapping_id=pseudo.mapping_id,
        sent=pseudo.text,
        answer=response.text,
        restored=restored,
        fidelity=fidelity,
        residuals=residuals,
        response=response,
    )


def _pseudonymise(
    engine: PrivaParseEngine, text: str, source: str | None
) -> PseudonymizationResult:
    """Seam so a test can simulate a pseudonymiser that leaked."""
    return engine.pseudonymize(text, source_name=source)


def format_roundtrip_report(results: Sequence[RoundTripResult]) -> str:
    lines = ["# Round-trip report", ""]
    lines.append(
        "Fidelity is exact against deformed, over the placeholders the model chose "
        "to emit. Omitting a placeholder is not counted as a failure — a model "
        "asked to summarise may legitimately mention none. A **deformed** one is "
        "the failure that matters: it leaves a masked name in a document the "
        "reader believes has been restored."
    )
    lines.append("")
    lines.append(
        "| Source | Model | exact | deformed | invented | foreign | fidelity | tokens | USD |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    for result in results:
        counts = result.fidelity.counts
        cost = f"{result.cost:.4f}" if result.cost is not None else "–"
        lines.append(
            f"| {result.source} | {result.response.model} "
            f"| {counts[Fidelity.EXACT]} | {counts[Fidelity.DEFORMED]} "
            f"| {counts[Fidelity.INVENTED]} | {counts[Fidelity.FOREIGN]} "
            f"| {result.fidelity.rate:.3f} | {result.response.tokens_total} | {cost} |"
        )

    variants = sorted({v for r in results for v in r.fidelity.deformations})
    lines.append("")
    lines.append("## Deformations observed")
    lines.append("")
    if variants:
        lines.append("Verbatim, so the shapes are visible rather than described:")
        lines.append("")
        for variant in variants:
            lines.append(f"- `{variant}`")
        lines.append("")
        lines.append(
            "Each of these left a name masked. `reverse()` is deliberately not "
            "loosened to accept them — see the design note in the spec."
        )
    else:
        lines.append("None. Every placeholder the model emitted came back byte-identical.")

    residuals = [f for r in results for f in r.residuals]
    if residuals:
        lines.append("")
        lines.append("## Residual-value warnings")
        lines.append("")
        for finding in residuals:
            lines.append(
                f"- {finding.placeholder} ({finding.entity_type}) still present "
                f"{finding.count}x before sending"
            )

    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_roundtrip.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Add the CLI command**

In `privaparse/app/main.py`, after the `demo` command (before `doctor`):

```python
@app.command()
def roundtrip(
    ctx: typer.Context,
    file: Optional[Path] = typer.Argument(
        None, exists=True, dir_okay=False, readable=True, help="Original document."
    ),
    gold: bool = typer.Option(
        False, "--gold", help="Run the German gold set instead of a single file."
    ),
    prompt: str = typer.Option(
        "Antworte auf dieses Schreiben.", "--prompt", "-p", help="What to ask the model."
    ),
    model: list[str] = typer.Option(
        [], "--model", help="OpenAI model. Repeat to compare several."
    ),
    max_tokens: int = typer.Option(1024, "--max-tokens", min=16),
    report: Optional[Path] = typer.Option(None, "--report"),
    no_files: bool = typer.Option(
        False, "--no-files", help="Skip the .pseudo/.answer/.restored files."
    ),
    allow_residual: bool = typer.Option(
        False, "--allow-residual", help="Send even if an original value is still present."
    ),
) -> None:
    """Send documents to OpenAI and measure whether the placeholders survive.

    This is the only command that reaches the network. It pseudonymises the
    input itself, so pass the original — not an already-pseudonymised file.

    A single file answers "did it work this time". `--gold` runs all 38 German
    gold documents and answers "how often does it work", which is the number
    worth having.
    """
    from privaparse.evaluation import DEFAULT_REPORT_DIR
    from privaparse.evaluation.harness import load_gold
    from privaparse.llm.openai import DEFAULT_MODEL, OpenAIClient
    from privaparse.llm.roundtrip import format_roundtrip_report, run_roundtrip

    if gold == (file is not None):
        typer.secho(
            "Pass either a FILE or --gold, not both and not neither.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    engine = _engine(ctx)
    models = model or [DEFAULT_MODEL]

    if gold:
        documents = [(d.id, d.text) for d in _run(lambda: load_gold(_default_gold()))]
        write_files = False
    else:
        documents = [(str(file), _read(file))]
        write_files = not no_files and len(models) == 1

    calls = len(documents) * len(models)
    characters = sum(len(text) for _, text in documents) * len(models)
    typer.secho(
        f"About to send {characters} characters to api.openai.com in {calls} call(s) "
        f"across {len(models)} model(s): {', '.join(models)}",
        fg=typer.colors.YELLOW,
        err=True,
    )

    results = []
    for model_id in models:
        client = _run(lambda: OpenAIClient(model_id, max_tokens=max_tokens))
        try:
            for source, text in documents:
                result = _run(
                    lambda: run_roundtrip(
                        engine,
                        text,
                        client=client,
                        prompt=prompt,
                        source_name=source,
                        allow_residual=allow_residual,
                    )
                )
                results.append(result)

                if write_files and file is not None:
                    file.with_suffix(f".pseudo{file.suffix}").write_text(
                        result.sent, encoding="utf-8"
                    )
                    file.with_suffix(f".answer{file.suffix}").write_text(
                        result.answer, encoding="utf-8"
                    )
                    file.with_suffix(f".restored{file.suffix}").write_text(
                        result.restored.text, encoding="utf-8"
                    )
        finally:
            client.close()

    text_report = format_roundtrip_report(results)
    target = report or (DEFAULT_REPORT_DIR / "roundtrip-report.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text_report, encoding="utf-8")

    # One line per model, not per document: a gold-set run is 38 results and
    # the interesting number is the rate across them.
    typer.echo()
    for model_id in models:
        subset = [r for r in results if r.response.model.startswith(model_id.split("-2024")[0])]
        if not subset:
            subset = results
        exact = sum(r.fidelity.counts[Fidelity.EXACT] for r in subset)
        deformed = sum(r.fidelity.counts[Fidelity.DEFORMED] for r in subset)
        invented = sum(r.fidelity.counts[Fidelity.INVENTED] for r in subset)
        tokens = sum(r.response.tokens_total for r in subset)
        costs = [r.cost for r in subset if r.cost is not None]
        rate = exact / (exact + deformed) if (exact + deformed) else 1.0

        typer.secho(
            f"{model_id:<20} {len(subset):>3} doc(s)  exact {exact:>4}  "
            f"deformed {deformed:>3}  invented {invented:>3}  fidelity {rate:.3f}  "
            f"{tokens} tokens  "
            + (f"${sum(costs):.4f}" if costs else "cost unknown"),
            fg=typer.colors.GREEN if not deformed else typer.colors.RED,
        )
    typer.echo(f"\nreport written to {target}")
```

Add to the imports at the top of `main.py`:

```python
from privaparse.llm.fidelity import Fidelity
```

- [ ] **Step 6: Add a CLI test**

Append to `tests/test_cli.py`:

```python
def test_roundtrip_refuses_without_an_api_key(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key, no call — and no silent fall back to the mock."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = _run(workspace, "roundtrip", str(workspace / "beispiel.md"))

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def test_roundtrip_announces_the_destination_before_sending(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = _run(workspace, "roundtrip", str(workspace / "beispiel.md"))
    assert "api.openai.com" in result.output


def test_no_other_command_reaches_the_network(workspace: Path) -> None:
    """The promise: only `roundtrip` opens a socket."""
    import privaparse.app.main as main_module

    source = Path(main_module.__file__).read_text(encoding="utf-8")
    body = source.split("def roundtrip(")[0] + source.split("def doctor(")[1]
    assert "OpenAIClient" not in body
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS. 285 existed before this plan; the five tasks add 3 + 16 + 10 + 10 + 10, so 334 with one `network` test deselected. If the count is lower, a file was skipped.

- [ ] **Step 8: Document it in the README**

In `README.md`, after the `reverse` example under "Use", add:

````markdown
### Measuring a real model

`roundtrip` is the only command that reaches the network. It pseudonymises the
file itself, sends it to OpenAI with your prompt, and reports whether the
placeholders came back intact:

```bash
OPENAI_API_KEY=sk-... privaparse roundtrip brief.md --prompt "Antworte auf dieses Schreiben"
```

It grades the answer rather than trusting it. A placeholder the model *omitted*
is fine — a summary need not mention everyone. A placeholder the model
*deformed* (`[PERSON_A1]` instead of `[[PERSON_A1]]`) is the failure worth
knowing about: `reverse()` will not resolve it, so a name stays masked in a
document you believe has been restored. Both counts land in
`docs/roundtrip-report.md`.

`--model` is repeatable, so one run compares models against each other. No other
command in PrivaParse opens a socket, and none can be configured to.
````

- [ ] **Step 9: Commit**

```bash
git add privaparse/llm/roundtrip.py privaparse/app/main.py tests/test_roundtrip.py tests/test_cli.py README.md
git commit -m "feat: add roundtrip, measuring placeholder fidelity through a real LLM"
```

---

## Verification

**Without a key and without the network** — the whole suite:

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: 319 passed, deselected `model` and `network`.

**With a key** — one real call:

```bash
$env:OPENAI_API_KEY="sk-..."; .venv/Scripts/python.exe -m pytest -m network -v
```

**End to end on a real document:**

```bash
privaparse roundtrip tests/data/beispiel.md --prompt "Antworte auf dieses Schreiben"
```

Expect the destination announced before the call, then a line per model, then
`docs/roundtrip-report.md`. Read `beispiel.answer.md` next to
`beispiel.restored.md`: every placeholder in the first should be a real value in
the second, and any that is not is exactly what the harness is for.

**The comparison that answers the original question** — 38 documents, so a rate
rather than an anecdote:

```bash
privaparse roundtrip --gold --model gpt-4o-mini
```

Roughly 38 calls at a few hundred tokens each; the count and the character total
are printed before the first one. Add `--model gpt-4o` to compare, and expect the
cost to rise accordingly.
