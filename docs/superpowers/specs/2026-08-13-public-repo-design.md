# Making PrivaParse a public repository

Date: 2026-08-13
Status: approved, ready for planning

## The problem

`Jhiynn/PrivaParse` is private, has 72 commits, a working library, an
OpenAI-compatible gateway, 701 passing tests and eight measured reports. What
it does not have is any of the machinery a stranger needs: no way to install it
without cloning, no CI, no contribution guidance, no changelog, no security
policy, and a 679-line README that is simultaneously the landing page, the
benchmark write-up and the design rationale.

The goal is a repository someone can find, install in one command, evaluate
from its evidence, and contribute to without asking. The library and the
gateway do not change.

## Non-goals

- No behaviour change to the detector, the vault or the gateway. The only code
  edits are packaging metadata and a `__version__` accessor.
- No documentation site generator. Plain Markdown, rendered by GitHub.
- No Dependabot, CodeQL or `pip-audit`. Deliberately declined; they can be
  added later without undoing anything here.
- No `ruff format`. See "The ruff gate" below.
- No rewriting of the measured reports. Their numbers and their prose survive
  the move intact.

## Decisions

**Distribution: PyPI, installed with `pipx`.** The name `privaparse` is
unclaimed (pypi.org returns 404). A stranger's install becomes one line with no
clone, no virtualenv and no `git`. The Docker path stays documented but is not
published to a registry; the image is 5.3–6.6 GB, which is torch, and pushing
that on every tag buys little.

**README shrinks to a landing page; its long-form content moves to `docs/`.**
Nothing is deleted. The evidence-heavy passages are the repository's strongest
asset and they stay in full — but a first-time reader should reach an install
command inside one screen, not after 200 lines of threshold methodology.

**Docs are plain Markdown in `docs/`.** Renders on GitHub, no build step, no
second thing to keep green.

**CI runs lint, tests and coverage.** `ruff check` plus `pytest` across
{ubuntu, windows} × {3.11, 3.12, 3.13}. Model-marked tests stay deselected;
they need GLiNER2 weights on disk, which a stock runner does not have and
should not download.

**The repository's own settings are configured, not left at GitHub's
defaults.** Merge policy, security features, labels, a `main` ruleset and a
gated release environment are applied with `gh api` and written down here, so
the configuration is reviewable rather than a state someone clicked into
existence. See section 7.

**No published contact address.** Both `SECURITY.md` and
`CODE_OF_CONDUCT.md` route through GitHub rather than email. A repository whose
entire premise is keeping personal data off the wire should not open by
publishing its maintainer's address for scrapers.

## Work

### 1. Packaging

`pyproject.toml` gains the metadata PyPI renders and users search:

- `authors`, `keywords`, `classifiers` (Development Status, Intended Audience,
  Topic :: Security, the supported Python versions, OS Independent).
- `urls`: Homepage, Documentation, Issues, Changelog.
- `license = "Apache-2.0"` as an SPDX expression with `license-files`, in place
  of the deprecated `{ file = "LICENSE" }` table form.
- An `all` extra combining `model`, `gateway` and `dev`.
- `ruff` added to the `dev` extra. It is configured in `[tool.ruff]` but not
  installed, so it has never run.

`privaparse/__init__.py` exposes `__version__` via
`importlib.metadata.version("privaparse")`, so the version has one home.

`requirements.txt` is deleted. It is a hand-maintained mirror of
`[project.dependencies]` that has already drifted — it omits `pyyaml` and
predates the `gateway` extra. On a public repository a stale second source of
truth is a bug report waiting to happen; the file's own header already tells
readers to use `pip install -e` instead.

### 2. Documentation tree

```
README.md                             landing page, ~150 lines
CONTRIBUTING.md
CODE_OF_CONDUCT.md
SECURITY.md
CHANGELOG.md
docs/
  README.md                           index
  install.md                          pipx, source, CPU/GPU, torch swap, Docker
  quickstart.md                       demo, pseudonymize, reverse
  gateway.md                          endpoint, clients, Codex CLI, costs, gaps
  configuration.md                    every setting, from config.py
  architecture.md                     how it works, the vault, Markdown handling
  testing.md                          suites, marks, coverage, gold documents
  benchmarks/
    README.md                         index, headline numbers, method
    detection-quality.md
    labels.md
    throughput.md
    performance-notes.md
    gateway-latency.md
    gateway-fidelity.md
    gateway-fallbacks.md
    codex-cli.md
  superpowers/                        unchanged; internal specs and plans
```

Existing reports move with `git mv` so history follows them:

| Today | Becomes |
| --- | --- |
| `docs/eval-report.md` | `docs/benchmarks/detection-quality.md` |
| `docs/label-report.md` | `docs/benchmarks/labels.md` |
| `docs/bench-report.md` | `docs/benchmarks/throughput.md` |
| `docs/performance-notes.md` | `docs/benchmarks/performance-notes.md` |
| `docs/gateway-latency-report.md` | `docs/benchmarks/gateway-latency.md` |
| `docs/gateway-model-fidelity-report.md` | `docs/benchmarks/gateway-fidelity.md` |
| `docs/gateway-restore-fallbacks-report.md` | `docs/benchmarks/gateway-fallbacks.md` |
| `docs/codex-cli-report.md` | `docs/benchmarks/codex-cli.md` |

`detection-quality.md` is the one merge: today's `eval-report.md` is 25 lines of
run output while the verdict prose that interprets it lives in the README. They
become one page.

`.gitignore` currently ignores `docs/*-report.local.md`. That pattern is
updated to `docs/benchmarks/*.local.md` to keep matching after the move.

### 3. README rewrite

Every section has a destination. Nothing is dropped.

| README section today | Destination |
| --- | --- |
| Title, intro, before/after diagram | stays |
| Status | stays, condensed to a short paragraph |
| Does GLiNER2 need fine-tuning for German? | `docs/benchmarks/detection-quality.md`; the four-row PERSON/EMAIL/PHONE/IBAN table and the verdict sentence stay in the README |
| Chunk size affects recall | `docs/benchmarks/throughput.md` |
| Throughput | `docs/benchmarks/throughput.md`; one headline figure stays |
| Install, Switching between CPU and GPU | `docs/install.md`; README keeps the `pipx` lines |
| Use | `docs/quickstart.md`; README keeps `demo` plus the two real commands |
| Gateway and all six subsections | `docs/gateway.md`; README keeps a six-line quickstart |
| Restoration puts real PII into the client | `docs/gateway.md`, and cited by `SECURITY.md` |
| Docker | `docs/install.md` |
| Configuration | `docs/configuration.md` |
| How it works, the vault, Markdown handling | `docs/architecture.md`; the vault section is also cited by `SECURITY.md` |
| Development | `CONTRIBUTING.md` and `docs/testing.md` |
| Licence | stays, one line |

The new README carries badges for CI status, PyPI version, supported Python
versions and licence.

`docs/configuration.md` is not a copy of `.env.example`, which documents nine
settings. It is a complete table built from the `Field(description=...)` text
already written on every setting in `privaparse/app/config.py`, so the
documentation and the code agree by construction.

### 4. CONTRIBUTING.md

Written in full, not stubbed. Sections in order:

1. **Scope.** What gets merged: detection quality, new entity types carrying
   gold evidence, gateway client compatibility, documentation. What does not:
   OCR, PDF, cloud-hosted detection, anything that sends text off the machine
   by default. A contributor should learn the project's premise before writing
   code that contradicts it.
2. **Never paste real PII.** Issues, tests and the gold set are synthetic.
   The existing corpus already models the standard — `Max Mustermann`,
   `beispiel.de`, and IBANs and tax IDs generated with valid checksums by
   `build_gold.py`. A privacy tool whose bug reports leak personal data defeats
   itself, so this is stated near the top rather than buried.
3. **Setup.** Clone, virtualenv, `pip install -e ".[dev,gateway]"`, the
   optional `[model]` extra, `privaparse doctor`. Commands work on Windows and
   POSIX alike.
4. **Tests.** `pytest` runs 701 tests and deselects 7; model-marked tests need
   weights on disk and are excluded by `addopts`. Coverage invocation included.
   Detail lives in `docs/testing.md`.
5. **Lint.** `ruff check .`, line length 100, target py311. States explicitly
   that formatting is not enforced.
6. **The evidence rule.** Any claim about detection quality, throughput or
   fidelity cites a report in `docs/benchmarks/` produced by a command a
   reviewer can rerun. Negative results are reported too — the README's own
   3-label versus 35-label passage and its four types disabled on measured
   evidence are the standard being asked for.
7. **Adding an entity type.** Route the model's labels in
   `entities.default.yaml`, with a comment stating whether the threshold is a
   measured value or a conservative guess. Add a validator in
   `parser/validators.py` when the type has a checksum. Add gold documents to
   `eval/gold/de_gold_source.md`, regenerate with
   `python -m privaparse.evaluation.build_gold`, run `privaparse eval`, and put
   the delta in the pull request. A type with zero gold entities ships
   disabled.
8. **Adding gold documents.** `de_gold_source.md` is the source of truth and
   the `.jsonl` is generated from it. Include documents with no PII at all; the
   README already explains why a positives-only set cannot observe a false
   positive.
9. **Commits and pull requests.** Conventional Commits, matching the existing
   history (`fix: survive a replayed placeholder`), with subjects that say why.
   Checklist: tests green, ruff clean, docs updated, benchmarks rerun if a
   published number moved, CHANGELOG entry added.
10. **Security.** Report privately per `SECURITY.md`; never in a public issue.
11. **Releasing (maintainers).** Bump the version, update the CHANGELOG, tag
    `vX.Y.Z`, push the tag; the release workflow publishes to PyPI.

### 5. Other root files

**`SECURITY.md`** — supported versions, private reporting through GitHub's
private vulnerability reporting (enabled in section 7), and a threat-model
section that is specific rather than boilerplate. Two facts belong there
because they are true and a user deserves them before they trust the tool: the
vault stores plaintext PII on disk, and restoration by design writes real PII
back into the client. Both already have README prose; `SECURITY.md` states them
plainly and links to it. No email address appears.

**`CODE_OF_CONDUCT.md`** — Contributor Covenant 2.1. The template's contact
placeholder is filled with a GitHub route — a private report to the maintainer
through the repository — not an email address.

**`CHANGELOG.md`** — Keep a Changelog format, one `0.1.0` entry assembled from
the 72 commits.

### 6. `.github/`

- `workflows/ci.yml` — `ruff check` plus `pytest` on {ubuntu-latest,
  windows-latest} × {3.11, 3.12, 3.13}, installing `.[dev,gateway]`. Coverage
  written to the job summary. Runs on push to `main` and on pull requests.
- `workflows/release.yml` — on a `v*` tag: build sdist and wheel, then publish
  to PyPI with trusted publishing (OIDC). No API token is stored in the
  repository. The publish job runs in the `pypi` environment, which requires
  the maintainer's approval, so the build happens on the tag but the upload
  waits for a human. A PyPI version number cannot be reused, which makes a
  mistyped or premature tag permanent; one click is cheap insurance against
  that.
- `ISSUE_TEMPLATE/bug_report.yml` — version, install method, device
  (`privaparse doctor` output), reproduction. Carries the no-real-PII rule at
  the point where someone is about to paste a document.
- `ISSUE_TEMPLATE/feature_request.yml` and `ISSUE_TEMPLATE/config.yml`.
- `PULL_REQUEST_TEMPLATE.md` — the checklist from CONTRIBUTING section 9.

### 7. Repository configuration

Applied with `gh api` and recorded here so the settings can be reviewed,
re-applied and argued with. Every item below is a repository setting, not a
file in the tree.

**Metadata.** Description, and topics `privacy`, `pii`, `pseudonymization`,
`gdpr`, `llm`, `openai-api`, `gliner`, `german`, `nlp`. Issues enabled; wiki,
projects and discussions disabled — an unattended wiki on a security-adjacent
project is a liability, and issues cover the traffic a new repository gets.

**Merge policy.** Squash merges only, with the pull request title as the commit
subject and its body as the message; merge commits and rebase merges disabled.
Head branches deleted on merge. Auto-merge enabled. This keeps `main` readable
in the same one-commit-per-change shape the history already has.

**`main` ruleset — guardrails, not a gate.** Two rules: block force-pushes
(`non_fast_forward`) and block branch deletion (`deletion`). The maintainer
keeps pushing to `main` directly, which is how all 72 existing commits were
made.

Deliberately *not* included: a required pull request and required status
checks. In a ruleset both apply to every push, including the maintainer's, so
adding them would silently convert this into the full-PR-gate option that was
considered and declined. Outside contributors are already constrained by
something stronger than a rule — they have no write access, so their changes
can only arrive as pull requests, which CI runs on and the maintainer merges.
The protection that matters against an outside contributor is therefore already
in place; the rules above exist to make a bad local command survivable.

**Security.** Secret scanning and push protection on, private vulnerability
reporting on, dependency graph on. Push protection is the one with teeth here:
it rejects a push containing a recognised credential before it reaches a public
history, which is exactly the accident that is expensive to undo.

Ordering matters. Free secret scanning and private vulnerability reporting
require the repository to be public, so these are applied *after* the
visibility flip, not before. The plan sequences them accordingly rather than
failing halfway through a batch of `gh api` calls.

**Labels.** GitHub's defaults are replaced with a set that matches this
codebase: `detector`, `gateway`, `catalogue`, `benchmark`, `docs`, `packaging`,
`bug`, `enhancement`, `good first issue`, `help wanted`. Stock labels like
`wontfix` and `duplicate` are dropped; unused labels are noise on a triage
screen.

**`pypi` environment.** Created with the maintainer as a required reviewer and
`refs/tags/v*` as its deployment branch policy. `release.yml`'s publish job
targets it, so a tag builds immediately and uploads only after approval.

**Not configured:** GitHub Pages (no docs site), Dependabot and code scanning
(declined above), and a social preview image (needs an image asset that does
not exist yet).

## Gates and risks

**The ruff gate.** Ruff is configured but has never been installed, so the
violation count is unknown. Run `ruff check .` before writing the CI workflow.
If the output is small, fix it. If it is large, narrow `[tool.ruff.lint] select`
to the rules the codebase already satisfies and record the deferred rules in
CONTRIBUTING, rather than landing a red badge or a sweeping reformat. The
decision is made on the actual output, not guessed now.

`ruff format` is not adopted at all. The codebase wraps signatures by hand and
the formatter would rewrite files across the repository, burying real history
under a cosmetic commit.

**PyPI is a one-way door.** A claimed name cannot be given up cleanly and a
published version number cannot be reused. The release workflow fires only on a
`v*` tag, so publishing stays a deliberate act.

**Git history was checked before public exposure.** No `.env`, key, token,
credential or PEM file has ever been added in 72 commits; only `.env.example`.
No `sk-`-shaped strings appear in the history. Nothing needs rewriting.

**Two steps require the maintainer** and cannot be automated from here:
flipping repository visibility to public, and registering the PyPI trusted
publisher for `Jhiynn/PrivaParse` + `release.yml`. Both are documented in
CONTRIBUTING's release section. The visibility flip is also a sequencing
dependency, not just a chore — the security settings in section 7 are
unavailable on a private repository.

**Going public exposes the whole history, not the current tree.** Everything
ever committed becomes readable, including files deleted later. The check
described above covers this and came back clean, but it is worth naming as the
distinct risk it is: reviewing `HEAD` proves nothing about commit 12.

**Uncommitted work exists** — the `gateway_allow_images` setting, four files
with tests, unrelated to this change. It is committed separately before this
work begins, so the restructure diff stays reviewable.

## Verification

The work is done when all of the following hold:

- `pytest` reports 701 passed, 7 deselected, as it does today.
- `ruff check .` exits clean under whatever rule set the gate settles on.
- `python -m build` produces an sdist and a wheel; installing the wheel into an
  empty virtualenv gives a working `privaparse --help` and `privaparse doctor`.
- **The install is verified in a clean Linux sandbox, not on the development
  machine.** The machine that wrote the code is the worst place to test whether
  a stranger can install it: the package is already installed editable, the
  dependencies are already resolved, the model weights are already on disk, and
  the shell is Windows while most readers are not. A `/lab` sandbox with
  nothing but a Python image runs the README's own commands verbatim —
  `pipx install` against the built wheel, then `privaparse doctor`, then
  `privaparse demo` on a sample document — and the README is wrong until that
  transcript is clean. `pip install -e ".[dev,gateway]"` from a fresh clone is
  checked the same way, since that is the contributor path in
  `CONTRIBUTING.md`.
- Every relative link in `README.md` and under `docs/` resolves to a file that
  exists. Checked mechanically, not by eye — the restructure moves eight files
  and rewrites a 679-line README, which is exactly the situation that produces
  dead links.
- The CI workflow is green on a branch before `main` advertises its badge.
- A clean clone plus the README's install command reaches a successful
  `privaparse demo` without consulting any other document.
- The repository's settings read back as specified: `gh api` confirms the
  merge policy, the `main` ruleset's two rules, secret scanning and push
  protection enabled, private vulnerability reporting enabled, the label set,
  and the `pypi` environment with a required reviewer. Read back, not assumed
  from a successful write — several of these endpoints accept a request and
  apply less than was asked when a plan or visibility setting does not allow
  the feature.
- No email address appears anywhere in the published tree.
