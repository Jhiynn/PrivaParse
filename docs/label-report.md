# Label report

The catalogue (`privaparse/app/entities.default.yaml`) lets an operator narrow
or disable which labels reach the detection model, per placeholder type. That
choice is only meaningful with numbers next to it. This report is those
numbers: every one of the 42 labels `fastino/gliner2-privacy-filter-PII-multi`
documents, measured on its own, through the full shipped pipeline, against the
124-document German gold set (`eval/gold/de_gold.jsonl`).

## How this was measured

Each label was sent to the model alone — one label active in the catalogue's
schema, nothing else — then scored against every gold entity of that label's
placeholder type. Gold support is therefore per **type**, not per label: all
four PERSON labels below read 99, for instance, because each of the four
isolated runs was scored against the same 99 gold PERSON entities, the only
gold data there is to compare a PERSON detection against. Normalizer,
validator and threshold were each label's type's shipped configuration; the
backstop was not (see below).

Three things about this method matter more than any single row, and belong
here rather than in a footnote.

**Only three labels have enough gold data to trust.** `person` rests on 99
gold entities, `email` on 21, `phone_number` on 18. Every other label below
rests on somewhere between three and six. At a support of three, missing one
entity is a recall of 0.667 and missing two is 0.333 — so a row with `n=3` or
`n=4` or `n=5` next to it is an indication, not a measurement, no matter how
clean or how bad its precision reads. Treat the rest of this table
accordingly: as a first pass worth acting on directionally, not as a verdict
to defend by the third decimal place.

**Each label was measured alone, and that changes what the numbers mean.**
Isolating one label is the only way per-label recall is computable at all —
with one label enabled, every detection necessarily came from it, while in
the shipped 42-label schema a missed entity has no label to attribute it to
(see `EvalReport.by_label` in `privaparse/evaluation/harness.py`, which
reports precision only for exactly this reason). The trade is that these
numbers do not describe how a label behaves in company: the same label can
read differently once other labels are competing for its spans, winning or
losing overlaps it would not have contested alone. A label's number here is
not necessarily its number in the shipped 21-type configuration.

**Backstops were off.** A checksum-gated regex would carry EMAIL, IBAN and
CARD to a detection regardless of what the model labelled, and this table
measures labels. EMAIL's 1.000/1.000 below is `email` the label doing the
work unassisted, not the shipped pipeline (which also has `builtin:email` to
fall back on) — the same is true of IBAN and CARD. Do not read any of the
three rows below as "the backstop is unnecessary."

## Results

One row per label still in the catalogue, grouped by placeholder type and
ranked by precision within each type — so a type an operator is configuring
has all of its labels together, best first.

| Label | Type | Support | Found | Noise | Precision | Recall | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `account_id` | ACCOUNT_ID | 3 | 3 | 6 | 0.333 | 1.000 | Finds all three (n=3), but six false positives — precision is the weak point, not recall. |
| `bank_account` | ACCOUNT_NUMBER | 3 | 2 | 3 | 0.400 | 0.667 | Missed one of three and drew three false positives (n=3). |
| `account_number` | ACCOUNT_NUMBER | 3 | 3 | 7 | 0.300 | 1.000 | Finds all three (n=3), but seven false positives — more noise than its sibling `bank_account`. |
| `address` | ADDRESS | 3 | 1 | 0 | 1.000 | 0.333 | No noise, but missed two of three (n=3) — clean is not the same as reliable at this support. |
| `street_address` | ADDRESS | 3 | 3 | 3 | 0.500 | 1.000 | Finds all three (n=3), three false positives — better recall than its sibling `address`, at the cost of some noise. |
| `card_number` | CARD | 3 | 3 | 0 | 1.000 | 1.000 | Clean at n=3 — no noise, nothing missed, but three data points is not a trend. |
| `payment_card` | CARD | 3 | 1 | 0 | 1.000 | 0.333 | No noise, but missed two of three (n=3); `card_number` found all three under the same conditions. |
| `card_cvv` | CARD_CVV | 3 | 2 | 4 | 0.333 | 0.667 | Missed one of three and drew four false positives (n=3). |
| `card_expiry` | CARD_EXPIRY | 3 | 3 | 0 | 1.000 | 1.000 | Clean at n=3 — same caveat as `card_number`: not enough to call a trend. |
| `city` | CITY | 5 | 5 | 16 | 0.238 | 1.000 | Finds every gold city (n=5), but sixteen false positives. |
| `country` | COUNTRY | 3 | 3 | 1 | 0.750 | 1.000 | Finds all three (n=3) with one false positive — cleaner than CITY or REGION here. |
| `sensitive_date` | DATE | 5 | 2 | 1 | 0.667 | 0.400 | Missed three of five, one false positive (n=5) — the best-behaved of DATE's four labels, still under half recall. |
| `document_date` | DATE | 5 | 1 | 4 | 0.200 | 0.200 | Missed four of five and drew four false positives (n=5). |
| `transaction_date` | DATE | 5 | 2 | 20 | 0.091 | 0.400 | Missed three of five and drew twenty false positives (n=5) — heavy noise without the recall to offset it. |
| `expiration_date` | DATE | 5 | 1 | 12 | 0.077 | 0.200 | Missed four of five and drew twelve false positives (n=5) — the lowest precision measured, and weak recall too. |
| `date_of_birth` | DATE_OF_BIRTH | 4 | 4 | 2 | 0.667 | 1.000 | Finds all four (n=4), two false positives. |
| `drivers_license_number` | DRIVERS_LICENSE | 3 | 3 | 1 | 0.750 | 1.000 | Finds all three (n=3), one false positive. |
| `email` | EMAIL | 21 | 21 | 0 | 1.000 | 1.000 | Well-supported (n=21) and clean — one of the three labels here with enough data to trust. |
| `iban` | IBAN | 6 | 5 | 0 | 1.000 | 0.833 | No noise, missed one of six (n=6) — the largest support outside the top three, though still thin next to them. |
| `ip_address` | IP | 3 | 3 | 0 | 1.000 | 1.000 | Clean at n=3 — not enough to call a trend. |
| `license_number` | LICENSE_NUMBER | 3 | 2 | 5 | 0.286 | 0.667 | Missed one of three and drew five false positives (n=3). |
| `national_id_number` | NATIONAL_ID | 3 | 2 | 3 | 0.400 | 0.667 | Missed one of three and drew three false positives (n=3). |
| `government_id` | NATIONAL_ID | 3 | 3 | 12 | 0.200 | 1.000 | Finds all three (n=3), but twelve false positives — four times its sibling `national_id_number`'s noise. |
| `passport_number` | PASSPORT | 3 | 3 | 0 | 1.000 | 1.000 | Clean at n=3 — not enough to call a trend. |
| `last_name` | PERSON | 99 | 90 | 1 | 0.989 | 0.909 | Well-supported (n=99) — the cleanest PERSON label, and the lowest recall of the four. |
| `first_name` | PERSON | 99 | 92 | 3 | 0.968 | 0.929 | Well-supported (n=99) — precise, recall a step above `last_name`. |
| `full_name` | PERSON | 99 | 95 | 4 | 0.960 | 0.960 | Well-supported (n=99) — precision and recall balanced, to three decimal places. |
| `person` | PERSON | 99 | 96 | 8 | 0.923 | 0.970 | Well-supported (n=99) — highest recall of the four PERSON labels, and the most false positives; one of the three labels here with enough data to trust. |
| `phone_number` | PHONE | 18 | 18 | 3 | 0.857 | 1.000 | Well-supported (n=18) — finds every number, three false positives; one of the three labels here with enough data to trust. |
| `postal_code` | POSTAL_CODE | 3 | 3 | 3 | 0.500 | 1.000 | Finds all three (n=3), three false positives. |
| `state_or_region` | REGION | 3 | 3 | 5 | 0.375 | 1.000 | Finds all three (n=3), but five false positives. |
| `routing_number` | ROUTING_NUMBER | 3 | 1 | 2 | 0.333 | 0.333 | Missed two of three and drew two false positives (n=3). |
| `api_key` | SECRET | 3 | 1 | 0 | 1.000 | 0.333 | No noise, but missed two of three (n=3) — the cleanest SECRET label, still only partial. |
| `password` | SECRET | 3 | 1 | 1 | 0.500 | 0.333 | Missed two of three and drew one false positive (n=3) — weak. |
| `access_token` | SECRET | 3 | 1 | 2 | 0.333 | 0.333 | Missed two of three and drew two false positives (n=3) — weak. |
| `tax_id` | TAX_ID | 4 | 4 | 0 | 1.000 | 1.000 | Clean at n=4 — not enough to call a trend. |
| `tax_number` | TAX_ID | 4 | 4 | 0 | 1.000 | 1.000 | Clean at n=4, identical to `tax_id` here — not enough to call a trend. |
| `username` | USERNAME | 3 | 3 | 27 | 0.100 | 1.000 | Finds all three (n=3), but the most false positives of any label measured — 27. |

Seven rows above — `city`, `state_or_region`, `country`, `sensitive_date`,
`document_date`, `expiration_date`, `transaction_date` — belong to types that
ship disabled by default (CITY, REGION, COUNTRY, DATE). CITY, REGION and DATE
are disabled on their own measured false-positive counts; COUNTRY is disabled
on judgement rather than a comparable count, since a country name alone
rarely identifies a person. This report does not change any of the four.
Note for anyone comparing numbers: each type's own comment in
`entities.default.yaml` cites a false-positive count from the Task 12 sweep
(91 documents, every type active together), not from this report (124
documents, this label alone) — CITY's comment says 13, the row above says
16, and both are real, from two different measurements. Do not average them
or treat one as a correction of the other.

## Dropped entirely

Four labels found zero true positives when measured alone and are no longer
in the catalogue as of this report (Task 14):

| Label | Type | Support | Found | Noise |
| --- | --- | ---: | ---: | ---: |
| `middle_name` | PERSON | 99 | 0 | 0 |
| `secret` | SECRET | 3 | 0 | 0 |
| `recovery_code` | SECRET | 3 | 0 | 1 |
| `sensitive_account_id` | ACCOUNT_ID | 3 | 0 | 1 |

`middle_name` and `secret` were silent — no detections at all, at both a
well-supported type (99) and a thin one (3). `recovery_code` and
`sensitive_account_id` cost a false positive each on top of that: noise
without a single match. Removing all four is free by definition (nothing was
being found) and saves the two with noise from costing anything going
forward. `MODEL_LABELS` in `privaparse/app/catalogue.py` still lists all 42 —
it documents what the model card offers, not what this catalogue chooses to
send — so this is a routing decision, not a claim that the model cannot
produce these four labels at all. Each type's own comment in
`entities.default.yaml` repeats its share of this table next to the `labels`
list it explains.

## Also worth recording

**Secrets are the weakest area measured, and the project owner named them as
important.** No SECRET label finds more than one of its three gold entities.
`api_key` is clean but partial — one of three, no noise. `password` and
`access_token` are both weak — one of three each, with one and two false
positives respectively. `secret` and `recovery_code`, dropped above, found
nothing at all. Nothing in this table argues that SECRET is in good shape;
narrowing its labels only trims which of several weak options gets sent.

**`username` finds every gold entity and produces 27 false positives doing
it** — the most raw noise measured for any label. `government_id` and `city`
share that same shape at a smaller scale: perfect recall (3/3 and 5/5) paired
with heavy noise (12 and 16 false positives). `expiration_date` and
`transaction_date` are not the same shape as those two, despite also being
noisy — they pair their noise (12 and 20 false positives) with *low* recall
(0.200 and 0.400), missing more than half their gold entities as well as
drawing false positives. Whichever failure mode costs a given deployment
more — noise a reader has to skip past, or a disclosure that slips through —
depends on the document domain, which is exactly the decision the catalogue
exists to leave open. An operator who has decided the noise in a given row
costs more than the recall it buys — for their own documents, which this
report cannot know — can act on this table directly, for example:

```yaml
# privaparse.entities.yaml — narrow instead of disabling outright
version: 1
placeholder_types:
  NATIONAL_ID:
    # government_id alone found 3 of 3 with 12 false positives; national_id_number
    # alone found 2 of 3 with 3. Keeping only the latter is a real trade — less
    # noise for less recall, not a free cut — see docs/label-report.md.
    labels: [national_id_number]
  USERNAME:
    enabled: false   # 27 false positives against 3 gold entities (n=3)
```

This file is deep-merged onto the shipped catalogue (see
`privaparse/app/entities.default.yaml`'s own header comment and
`privaparse catalog show`), so only the types actually being narrowed need to
appear in it.
