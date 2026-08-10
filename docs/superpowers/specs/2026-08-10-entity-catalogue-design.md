# Entity Catalogue and Measurement Loop

Design document. 2026-08-10.

## Problem

PrivaParse detects three entity types: `PERSON`, `EMAIL`, `PHONE`. They are
hardcoded in five places — the `EntityType` enum, `DEFAULT_ENTITY_SCHEMA`,
`SCHEMA_KEY_TO_TYPE`, the `normalize()` dispatch, and the type-specific rule
checks in `merge.py`. Adding a type means editing all five.

The detection model, `fastino/gliner2-privacy-filter-PII-multi`, documents 42
PII labels. We use five of them. The rest are one config line away in principle
and a refactor away in practice.

Two things must be true before the label set is widened, and neither is true
today:

**The type set must be configurable rather than compiled in.** A privacy tool
whose entity coverage is a source-code constant cannot be adapted to a
jurisdiction, a document domain, or a user's own risk assessment.

**Widening must be measurable.** The model card reports, across its full label
set, an average F1 of 0.477 — legal domain precision 0.346 at recall 0.750,
medical 0.369 at 0.686. PrivaParse's own measured PERSON F1 of 0.975 was
obtained with three labels on a German gold set. Both numbers are real and they
describe the same model. The difference is the label count. Turning on 42
labels without extending the evaluation would leave the README quoting 0.975
next to a shipped reality closer to 0.35 precision.

## Goals

- Entity types defined in a configuration file, not in code.
- All 42 model labels available; enabled by default, disabled by opt-out.
- Per-type detection threshold and per-type quality bar, both in the same file.
- Deterministic types (IBAN, payment card, tax ID, IP) get a checksum veto over
  the model's own proposals.
- Regex detection continues as a recall backstop, but the model decides.
- Evaluation reports per placeholder type *and* per model label, with a
  threshold sweep, so tuning is read off a measurement rather than guessed.
- `pseudonymize_batch()` — one mapping across many texts, which the gateway
  (spec 2) requires.

## Non-goals

- No network code. No proxy, no server, no HTTP.
- No new detection model, no fine-tuning.
- No decision on which labels to merge. That decision is an output of this
  work, not an input.
- No multi-user or encrypted vault. `ValueCipher` stays as it is.

## The catalogue

One YAML file. Ships as package data at
`privaparse/app/entities.default.yaml`; user overrides are discovered in this
order, first hit wins:

1. `PRIVAPARSE_ENTITIES` (explicit path)
2. `./privaparse.entities.yaml`
3. `~/.config/privaparse/entities.yaml`

A user file is **deep-merged onto** the built-in catalogue, never a
replacement. Removing a type requires `enabled: false`, so a PrivaParse upgrade
that adds a type still reaches users who wrote a config a year ago. Silent
omission would make an upgrade quietly narrow someone's protection.

### Format

```yaml
version: 1

placeholder_types:
  PERSON:
    labels: [person, full_name, first_name, middle_name, last_name]
    prompts:
      person: "Vor- und Nachnamen von Menschen, auch mit Titeln wie Dr. oder Prof."
      full_name: "Vollstaendige Namen einer Person"
    normalizer: person
    threshold: 0.5
    reversible: true
    enabled: true
    bar: { precision: 0.85, recall: 0.90 }

  IBAN:
    labels: [iban]
    normalizer: strip_upper
    validator: builtin:iban_mod97      # veto over the model
    backstop: builtin:iban             # finds what the model missed
    sweep: exact
    threshold: 0.5
    reversible: true
    enabled: true

  SECRET:
    labels: [password, secret, api_key, access_token, recovery_code]
    normalizer: identity
    threshold: 0.8
    reversible: false                  # never written back
    enabled: true
```

| Field | Meaning |
| --- | --- |
| key | Placeholder type. Appears in `[[PERSON_A1]]` and in `entities.type`. |
| `labels` | Model labels routed to this type. Many-to-one. |
| `prompts` | Per-label description handed to GLiNER2 verbatim. Described labels beat bare labels. |
| `normalizer` | Registry name. Decides what counts as the same value. |
| `validator` | Optional. Vetoes a model span that provably is not what it claims. |
| `backstop` | Optional. Regex detector run alongside the model. |
| `sweep` | How the coreference sweep re-finds this value: `word` (word-boundary), `icase` (case-insensitive with boundary), `exact` (literal), `off`. Default `word`. |
| `threshold` | Score cutoff for this type. Falls back to the global `threshold`. |
| `reversible` | `false` means the value never enters the vault in restorable form. |
| `enabled` | `false` removes the labels from the schema entirely — no model cost. |
| `bar` | Quality floor. The eval reports which types are under their own bar. |

`validator` and `backstop` name registry entries; builtins are written
`builtin:<name>`. The tables below list the `<name>` part.

The `sweep` field replaces the hardcoded dispatch in `_sweep_pattern()`, which
currently special-cases `EMAIL` as case-insensitive and `PERSON` as
word-bounded. Both become catalogue values: `EMAIL` is `icase`, `PERSON` is
`word`, and the structured types (`IBAN`, `CARD`, `TAX_ID`, `SECRET`) are
`exact` — a credential or account number repeated later in the document must be
masked at every occurrence, and a literal match is the right rule for a value
with no inflection. `COUNTRY` and `CITY` are `off`: sweeping for "Berlin"
across a document produces more noise than protection.

Loading is strict, consistent with the project's fail-closed stance: an unknown
`normalizer` or `validator` name is an error, not a warning. An unknown
`version` is an error. A label not among the model's documented 42 produces a
warning only — GLiNER is zero-shot and custom labels are a legitimate use.

### Label to placeholder-type mapping

All 42 labels are routed. 25 placeholder types.

| Placeholder type | Labels | Normalizer | Validator | Backstop |
| --- | --- | --- | --- | --- |
| `PERSON` | person, full_name, first_name, middle_name, last_name | person | — | — |
| `DATE_OF_BIRTH` | date_of_birth | date_iso | — | — |
| `EMAIL` | email | email | email_syntax | email |
| `PHONE` | phone_number | phone | phone_shape | phone |
| `ADDRESS` | address, street_address | casefold | — | — |
| `CITY` | city | casefold | — | — |
| `REGION` | state_or_region | casefold | — | — |
| `POSTAL_CODE` | postal_code | digits | postal_de | — |
| `COUNTRY` | country | casefold | — | — |
| `NATIONAL_ID` | government_id, national_id_number | strip_upper | — | — |
| `PASSPORT` | passport_number | strip_upper | — | — |
| `DRIVERS_LICENSE` | drivers_license_number | strip_upper | — | — |
| `LICENSE_NUMBER` | license_number | strip_upper | — | — |
| `TAX_ID` | tax_id, tax_number | strip_upper | tax_de | vat_de |
| `ACCOUNT_NUMBER` | bank_account, account_number | strip_upper | — | — |
| `ROUTING_NUMBER` | routing_number | digits | blz_de | — |
| `IBAN` | iban | strip_upper | iban_mod97 | iban |
| `CARD` | payment_card, card_number | digits | luhn | card |
| `CARD_EXPIRY` | card_expiry | date_iso | expiry_shape | — |
| `CARD_CVV` | card_cvv | digits | cvv_shape | — |
| `USERNAME` | username | casefold | — | — |
| `IP` | ip_address | casefold | ip_parse | ip |
| `ACCOUNT_ID` | account_id, sensitive_account_id | casefold | — | — |
| `SECRET` | password, secret, api_key, access_token, recovery_code | identity | — | — |
| `DATE` | sensitive_date, document_date, expiration_date, transaction_date | date_iso | — | — |

Two mapping rules, both consequences of the vault's `(type, normalized_value)`
unique key:

**Granularity errs high.** `LICENSE_NUMBER` stays separate from
`DRIVERS_LICENSE`, `CITY` from `ADDRESS`. Merging two types later is a
migration that rewrites a column and de-duplicates rows — mechanical, and
scripted once. Splitting one type into two later is impossible without
re-detecting every stored value, because the information that would separate
them was discarded at write time. When in doubt, keep them apart.

**`SECRET` is the exception and merges five labels.** Granularity buys nothing
there: every one of those labels is irreversible, never restored, and never
distinguished by anything downstream. Per-label diagnostics still exist — see
the evaluation section, which reports per model label independently of the
placeholder type.

### Irreversible types

`reversible: false` is not a flag checked at restore time. It changes what is
written:

- `entities.normalized_value` holds a SHA-256 digest of the normalized value,
  not the value. The placeholder stays deterministic and stable; the plaintext
  never reaches disk.
- No `entity_values` row. There is no surface form to restore.
- No `mapping_entries` row. `reverse()` finds nothing and leaves the
  placeholder in place, which is already its behaviour for unknown
  placeholders.

The result is a one-way door by construction rather than by policy. A tool that
stores API keys in a plaintext SQLite file and offers a restore function is a
credential store with extra steps.

`ValueCipher` is untouched — this is a different concern (should the value
exist at all) from the one the cipher seam addresses (how a stored value is
protected).

## Code changes

### Open entity type

`EntityType` stops being an `Enum` and becomes a validated string newtype.
`Span.type` becomes `str`. Legality is checked against the loaded catalogue at
construction, so an unknown type still fails fast — the check moves from the
type system to the catalogue, it does not disappear.

Already open, no change needed: `entities.type` is `String(32)`, and
`PLACEHOLDER_RE` already matches `[A-Z][A-Z0-9]*`. **No database migration, no
placeholder format change.** Vaults written by the current version keep working.

Touched: `parser/types.py`, `parser/merge.py`, `parser/normalizer.py`,
`parser/detector.py`, `parser/gliner_detector.py`, `app/config.py`.

### Normalizer registry

`normalize()` stops dispatching on three enum members and looks up a name from
the catalogue in a registry.

| Name | Behaviour |
| --- | --- |
| `person` | Existing. NFKC, collapsed whitespace, leading titles dropped, casefold. |
| `email` | Existing. |
| `phone` | Existing. E.164 via `phonenumbers`, digit fallback. |
| `strip_upper` | Whitespace, dots and hyphens removed, uppercased. IBAN, tax IDs, card numbers. |
| `digits` | Non-digits removed. |
| `date_iso` | German and ISO date forms parsed to `YYYY-MM-DD`; casefold fallback when unparseable. |
| `casefold` | NFKC, collapsed whitespace, casefold. |
| `identity` | Unchanged. Only for irreversible types, where the value is hashed anyway. |

The existing warning in `normalizer.py` applies to every new entry: normalising
too little fragments one value across several placeholders, too much merges two
distinct values into one. `strip_upper` on IBAN is correct because whitespace in
an IBAN is presentational. `casefold` on `ADDRESS` is a deliberate
under-normalisation — `Hauptstr. 5` and `Hauptstraße 5` will get separate
placeholders, and merging them would need address parsing this spec does not do.

### Validator registry — the model's veto

`_passes_rule_check()` in `merge.py` currently hardcodes two checks. It becomes
a catalogue lookup. A validator applies **only to spans the model produced**;
backstop spans are already exact by construction.

Builtins: `email_syntax` (existing), `phone_shape` (existing),
`iban_mod97` (ISO 7064), `luhn`, `tax_de` (German Steuer-ID, 11 digits with
check digit), `blz_de`, `postal_de`, `ip_parse` (stdlib `ipaddress`),
`expiry_shape`, `cvv_shape`.

Validators exist only for types whose syntax is fully decidable. `SECRET` gets
none: there is no rule that separates an API key from a random string, and a
veto there would discard real credentials. `USERNAME`, `ADDRESS`, `PERSON` get
none for the same reason. Those types are governed by their threshold alone.

At precision ~0.35 across the full label set, the validators are the single
highest-leverage precision mechanism in the pipeline, because they are free of
the recall cost a higher threshold imposes.

### Merge precedence: the model decides

`merge.py:40` today ranks `{regex: 3, gliner: 2, coref: 1}` and lets
`_TYPE_RANK` override the source. That encodes "rules win". The new rule is
"the model decides, rules assist":

```python
_SOURCE_RANK = {SOURCE_GLINER: 3, SOURCE_REGEX: 2, SOURCE_COREF: 1}
```

`_TYPE_RANK` is removed. A backstop span survives only where no model span
overlaps it. Regex keeps both of its jobs and loses neither:

- **Backstop (recall):** finds what the model missed.
- **Validator (precision):** vetoes what the model got provably wrong.

Neither job is "outrank the model on a span both found".

The reason the old ranking existed — a `PERSON` span swallowing an email's
local part — is now handled by `email_syntax` on the validator side plus the
existing longest-span tie-break, not by a type rank.

### `pseudonymize_batch`

```python
def pseudonymize_batch(
    self, texts: Sequence[str], *, source_name: str | None = None
) -> BatchResult: ...
```

Detects across all texts in one model batch, resolves every value against the
vault, and records **one** mapping covering all of them. `BatchResult` carries
the shared `mapping_id`, a per-text result list, and the aggregate counts.

Additive; `pseudonymize()` becomes a one-element call through it. Required by
spec 2, where one HTTP request carries 20–200 text nodes that must share a
mapping — otherwise `reverse()` cannot resolve the response.

### CLI

- `privaparse catalog show` — resolved catalogue: enabled types, label counts,
  thresholds, which file each value came from.
- `privaparse catalog validate [FILE]` — load and report errors without running
  anything.
- `privaparse doctor` gains the catalogue path and enabled type/label counts.

## Measurement

### Harness

`evaluation/harness.py` computes metrics for three fixed types and applies a
hardcoded PERSON fine-tuning verdict. Both become catalogue-driven:

- Per **placeholder type**: precision, recall, F1, partial and exact.
- Per **model label**: the same metrics. This is what makes a five-label
  `SECRET` type diagnosable — if precision collapses, the per-label table shows
  whether `username` or `api_key` is responsible.
- Verdict per type against its `bar`. Types without a `bar` are reported
  without a verdict rather than silently passing.

### Threshold sweep

```
privaparse eval --sweep-threshold
```

Runs 0.3 … 0.9 in steps of 0.1 per placeholder type and writes the
precision/recall curve into the report. One model pass produces every point:
detection runs once at the lowest threshold and the curve is computed by
filtering the scored spans, so the sweep costs one run, not seven.

This is what makes "start with thresholds" a measurement rather than a guess —
the value is read off a curve, then written into the catalogue.

### Gold set

Current: 38 documents, 60 PERSON / 20 EMAIL / 18 PHONE entities, 10 documents
with no PII. Format needs no change — `entities[].type` is already a free
string.

Target: ~80 documents. Three kinds of work, very unequal in cost:

**Decidable types — low cost.** IBAN, card, tax ID, BLZ, IP, postal code,
expiry, CVV. Checksum-valid synthetic values embedded in real German sentence
contexts. Generated by extending `evaluation/build_gold.py`; correctness is
verifiable without human judgement.

**Fuzzy types — high cost.** `ADDRESS`, `DATE_OF_BIRTH`, `USERNAME`,
`ACCOUNT_ID`, `NATIONAL_ID`, `PASSPORT`, the date family. German documents
annotated by hand. This is the schedule risk in this spec, and it is annotation
work, not engineering.

**Negative documents — moderate cost, most important.** Roughly half the target
set. Text that looks like PII and is not: Aktenzeichen, product and order
numbers, version strings, invoice dates, table data, code fragments, German
compound nouns that read like place names.

The last category is the one that decides whether this work succeeds. At
precision ~0.35 the failure mode is false positives, and false positives are
invisible in a corpus made of documents that all contain real PII. A gold set
without negatives would measure high recall and report success while the
pseudonymised text is unusable.

## Order of work

1. Catalogue format, loader, merge semantics, validation.
2. Open `EntityType`; normalizer and validator registries.
3. Merge precedence flip.
4. `pseudonymize_batch`.
5. Default catalogue with all 42 labels, thresholds at conservative defaults.
6. Harness generalisation, per-label reporting, threshold sweep.
7. Gold set extension, negatives first.
8. Run the sweep. Write measured thresholds into the catalogue.
9. Report which types sit under their bar. That report is the input to the
   label-consolidation decision, which is out of scope here.

Steps 1–6 are engineering and can be test-driven. Step 7 is annotation. Step 8
is a measurement. Step 9 is a decision this document deliberately does not make.

## Testing

- **Catalogue loader:** deep-merge semantics, first-hit discovery order,
  unknown normalizer/validator/version rejected, unknown label warns, disabled
  type produces no model label.
- **Validators:** known-valid and known-invalid vectors per builtin, including
  the boundary cases (IBAN with correct length and wrong checksum; card that
  passes Luhn but is not a card length).
- **Normalizers:** the identity property that matters is that two spellings of
  one value collide and two distinct values do not. One test per registry entry.
- **Merge precedence:** a model span and a backstop span on the same text must
  resolve to the model's; a backstop span on text the model did not claim must
  survive.
- **Irreversible types:** after pseudonymising a text with a secret, the vault
  contains no row whose stored value equals the secret, and `reverse()` leaves
  the placeholder untouched.
- **`pseudonymize_batch`:** N texts produce one mapping id; a value appearing in
  two of them gets one placeholder; `reverse()` resolves placeholders from any
  of the N.
- **Round trip, unchanged:** the existing CPU/GPU parity test must still pass
  with the full catalogue.

## Risks

**Throughput.** 3 → 42 labels is roughly fourteen times the label encoding per
chunk. The measured 53 KB/s will drop, by an unknown factor. `privaparse bench`
already measures this; it must be run before spec 2 builds a request path on
top. If the drop is severe, `enabled: false` on the noisiest types is the lever,
and the catalogue already provides it.

**Precision in code-bearing text.** `USERNAME`, `ACCOUNT_ID` and `SECRET`
against source code is the worst case for the developer audience spec 2
targets. `protect()` already masks fenced and inline code, which covers Markdown
but not the bare source a coding agent sends. Thresholds are the first lever;
the sweep will show whether they are enough.

**Annotation effort is on the critical path.** Steps 1–6 can be finished and
merged while the gold set is still growing; step 8 cannot start until step 7 is
done. The catalogue ships with conservative defaults in the meantime, and the
README must not claim measured numbers for unmeasured types.

**README currency.** The 0.975 PERSON figure was measured with three labels. It
becomes wrong the moment the default catalogue widens. It gets re-measured in
step 8 and updated, or it gets qualified with the label count it was obtained
under. Leaving it unqualified would be the exact failure this spec exists to
avoid.

## Out of scope

Spec 2 (local OpenAI-compatible gateway) depends on this one and covers: HTTP
server, JSON extraction and write-back, detection cache, streaming restoration,
`serve` and `run` commands, packaging. It inherits the type system, the
`reversible` policy and `pseudonymize_batch` from here.
