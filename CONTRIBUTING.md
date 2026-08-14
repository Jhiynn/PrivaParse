# Contributing to PrivaParse

PrivaParse exists to keep personal data off the wire. That shapes what this
project accepts as much as any code style guide does — read this before
opening an issue or a pull request, particularly the section on evidence.

## Scope

Merged: better detection quality (a precision/recall change against the gold
set, evidenced per "The evidence rule" below), new entity types that ship
with gold evidence rather than a hopeful threshold, gateway compatibility
with another OpenAI-compatible client, and documentation.

Not merged: OCR, PDF handling, cloud-hosted detection, or anything that sends
text off the machine by default. "Nothing leaves the machine" is not a
feature flag here — it's the thing the project is for. A PR that makes it
optional, gated behind a setting that defaults on, is the same problem with
extra steps.

## Never paste real PII

Issues, tests, and the gold set are synthetic, always. Use the same corpus
this project already uses: the name `Max Mustermann`, an address on the
`beispiel.de` domain, and checksum-valid IBANs, card numbers, and tax IDs —
the kind `generate_decidable()` in `privaparse/evaluation/build_gold.py`
produces, or the standard example IBAN `DE89 3704 0044 0532 0130 00`. Never
a real value, not even one redacted or altered from something real — a bug
report that demonstrates a detection miss by pasting someone's actual name
and address does with one comment what the whole tool exists to prevent.

The reason fits in one line: a privacy tool whose bug reports leak personal
data defeats itself.

## Setup

```bash
git clone https://github.com/Jhiynn/PrivaParse.git
cd PrivaParse
python -m venv .venv
```

Activate it — `.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
on macOS or Linux — then:

```bash
pip install -e ".[dev,gateway]"
```

That's enough to run the full non-model test suite, the CLI, and the gateway.
Add `[model]` if your change touches detection itself — it pulls in PyTorch,
roughly 2 GB:

```bash
pip install -e ".[model]"
```

Full install detail, the GPU wheel swap, and Docker are in
[docs/install.md](docs/install.md). Verify what you ended up with:

```bash
privaparse doctor
```

`doctor` prints the resolved device, dtype, model, and vault path. Check it
before you start rather than after a test fails for a reason that has
nothing to do with your change.

## Tests

```bash
pytest
```

The default run deselects 7 tests marked `model`, via
`addopts = "-m 'not model'"` in `pyproject.toml` — they need GLiNER2 weights
on disk that a fresh checkout does not have. Everything else should pass on
a clean checkout: 822 passed, 7 deselected, as of this writing. Run the
model-marked tests once weights exist (`pip install -e ".[model]"`, then any
command that loads the detector — `privaparse doctor` or `privaparse demo
<file>` — downloads them on first use):

```bash
pytest -m model
```

Coverage:

```bash
pytest --cov=privaparse --cov-report=term-missing
```

What each test directory covers, including the two tests that guard the
documentation itself rather than PrivaParse's behaviour
(`tests/test_docs_links.py`, `tests/test_packaging.py`), is in
[docs/testing.md](docs/testing.md).

## Lint

```bash
ruff check .
```

Line length 100, target `py311`. The rule set is pinned explicitly in
`[tool.ruff.lint] select` in `pyproject.toml` rather than inherited from
ruff's defaults — ruff's defaults widen between releases, and a linter that
reddens CI on its own schedule teaches contributors to distrust the badge.
The accompanying `ignore` list documents, rule by rule, *why* each deferred
rule is off (RUF001-003 flag German orthography as ambiguous unicode; B904
and B905 would be real improvements but are out of scope for a change that
isn't allowed to alter behaviour; RUF005 is a style call with no defect).
Read that comment before asking why a rule is off — it already answers it.

Formatting is not enforced, and that's deliberate rather than an oversight:
no formatter has ever run across this tree, and turning one on now would
rewrite every file for a purely stylistic reason disconnected from any
actual change — exactly the kind of diff this project's own commit history
avoids, where every commit does one describable thing. Match the surrounding
style by hand instead of running `ruff format` or `black` over a file you
touch.

## The evidence rule

This is the rule that actually governs this project, more than any process
step below it: **any claim about detection quality, throughput, or fidelity
cites a report under `docs/benchmarks/` produced by a command a reviewer can
rerun.** Not "the model handles names well" — a precision/recall table, the
command that produced it, and where it lives.

Negative results are published in the same voice as positive ones, not
hedged, softened, or left out. `docs/benchmarks/detection-quality.md`
reports that LICENSE_NUMBER and ROUTING_NUMBER measure **0.000 recall — they
detect nothing** — in the same table as the types that score cleanly, and
that stayed in the repository rather than being quietly dropped once it was
known. The same report's
["An earlier, more flattering number"](docs/benchmarks/detection-quality.md#an-earlier-more-flattering-number)
section is the other half of the standard: it explains why an earlier
PERSON F1 of 0.975, measured at 3 labels against 35 today, was not a better
system — it was a narrower one, with less surface for a false positive to
land on, and the report says so plainly instead of letting the flattering
number stand unexplained. CITY, REGION, COUNTRY, and DATE ship disabled in
the default catalogue for the same reason, in the other direction — measured
false positives with no offsetting recall (see
[docs/benchmarks/labels.md](docs/benchmarks/labels.md)).

That is what's being asked of every contribution that touches detection,
throughput, or gateway fidelity: measure it, publish what you measured even
when it's unflattering, and cite the rerunnable command. If a change could
plausibly move a published number, rerun the report and show the old value
next to the new one — don't leave a stale number next to a changed
implementation.

## Adding an entity type

1. Route the model's label(s) for the type in
   `privaparse/app/entities.default.yaml`. Every threshold in that file
   carries a comment stating plainly whether it's measured (cite the sweep
   that produced it) or a conservative guess never yet run against gold
   data — a new entry is expected to say the same, not leave the question
   unanswered.
2. If the type has a checksum or another fully decidable shape, add a
   validator in `privaparse/parser/validators.py` with
   `@register_validator("name")`, and reference it from the catalogue entry
   as `validator: builtin:name`. There is deliberately no validator for
   SECRET, USERNAME, ADDRESS, or PERSON — nothing separates a real API key
   from a random string, and a validator that guesses would discard real
   values. Don't invent one where the type doesn't have a decidable shape.
3. Add gold documents for the new type to `eval/gold/de_gold_source.md` (see
   "Adding gold documents" below), then regenerate the compiled set:

   ```bash
   python -m privaparse.evaluation.build_gold
   ```

4. Score it:

   ```bash
   privaparse eval
   ```

5. Put the delta — precision, recall, F1, and support, before and after —
   in the pull request description. That's the evidence rule applied to
   this specific case.

A type that ships with zero gold entities ships `enabled: false`. That isn't
a formality: CITY, REGION, COUNTRY, and DATE are disabled in the shipped
catalogue precisely because measurement found false positives with no
offsetting true positives, and an unmeasured type gets the same default as a
measured-and-found-wanting one, because "nobody has checked" and "checked
and it costs more than it's worth" are both reasons not to trust something
by default.

## Adding gold documents

`eval/gold/de_gold_source.md` is the source of truth; `eval/gold/de_gold.jsonl`
is a generated artifact — edit the Markdown, never the JSONL by hand. Mark
entities inline, `{{PERSON:Max Mustermann}}`, `{{IBAN:DE89 3704 0044 0532
0130 00}}`, using exactly the type names the catalogue defines
(`privaparse catalog show` lists them; `test_every_gold_type_exists_in_the_catalogue`
enforces the match). Regenerate after editing:

```bash
python -m privaparse.evaluation.build_gold
```

Include documents with no PII in them at all, not only ones that contain
it. 33 of the current 124 gold documents carry nothing to redact, and that
matters mechanically, not just for balance: a gold set built only from
positives can never produce a false positive, because there's no negative
case in it for a detector to get wrong on, and a real fraction of this
project's most useful evidence — every disabled entity type, PHONE's
precision problem — comes from documents that have nothing to find. Read the
annotation rules at the top of `de_gold_source.md` before writing a new
document; what counts as ADDRESS versus a bare place name, whether a title
is part of a name, and which dates count as personal are all decided there,
and a document that gets the convention wrong pollutes the measurement it
was meant to improve.

## Commits and pull requests

Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`), matching the
existing history — `git log --oneline` shows the convention in practice
better than a rule can state it. Subjects say *why* a change exists, not
just what changed: `fix: survive a replayed placeholder, and classify the
whole input union` carries more information than `fix: bug in responses
adapter` for the same number of words.

Before opening a pull request:

- [ ] `pytest` passes (the default, non-model run)
- [ ] `ruff check .` is clean
- [ ] Documentation is updated for anything the change affects — a new
      setting, a new command, a new entity type, a changed default
- [ ] Benchmarks are rerun and the relevant report under `docs/benchmarks/`
      updated if the change could plausibly move a published number
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`

## Security

Report a suspected vulnerability privately through GitHub's private
vulnerability reporting, as described in [SECURITY.md](SECURITY.md). Never
in a public issue — a detection bypass or a vault exposure reported in the
open is a disclosure before there's a fix, in a project whose entire point
is not disclosing things.

## Releasing (maintainers)

1. Bump the version in `pyproject.toml`.
2. Update `CHANGELOG.md` — move the `## [Unreleased]` entries under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading.
3. Tag the release and push the tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The release workflow builds the package immediately on the tag push, then
waits for manual approval in the `pypi` GitHub Environment before uploading
— a tag push alone is never sufficient to reach PyPI.

One-time setup, not a per-release step: the PyPI trusted publisher for
`Jhiynn/PrivaParse` has to be registered against the `release.yml` workflow
before the first release can upload. Trusted publishing authenticates the
workflow run itself rather than a stored token, so that registration has to
exist on PyPI's side before `release.yml` runs for real — there is no token
to rotate into GitHub secrets instead.
