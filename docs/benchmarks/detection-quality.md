# Evaluation report

**Measured 2026-08-14** in a `/lab` sb-gpu sandbox on node1 — RTX 3060,
CUDA 13.0 — against the full 124-document gold set
(`eval/gold/de_gold.jsonl`: 91 documents carrying PII, 33 without), scored
at the shipped catalogue's 21 enabled types with `privaparse eval`. Model
weights: `fastino/gliner2-privacy-filter-PII-multi`, loaded from the lab's
shared model store at `/share/models/gliner2-privacy-pii-multi` with
`PRIVAPARSE_OFFLINE=1` — no network reached, nothing downloaded. That local
path is why the run label below reads `gliner2-privacy-pii-multi` rather
than the repo id: it is the store's directory name, not a different model.
`privaparse doctor` confirmed the resolved device before this ran:
`device=cuda:0 gpu=NVIDIA GeForce RTX 3060 vram=11909MiB dtype=fp16
compile=on`.

**This run supersedes the 91-document run** quoted in the top-level README's
"Does GLiNER2 need fine-tuning for German?" section (measured 2026-08-10, 76
PERSON entities, 21 enabled types). Batches D and E added 33 more documents
between the two runs, all of them positives — see
`eval/gold/de_gold_source.md`. See the README and
[docs/benchmarks/README.md](README.md) for the old numbers placed next to
these.

**CPU/GPU parity is now proven, not assumed.** A previous pass at this same
124-document re-score ran in a CPU-only sandbox (`sb-dev`, no GPU), where
`pytest -m model`'s two device tests —
`test_the_model_lands_on_the_configured_device` and
`test_swapping_cpu_for_gpu_does_not_change_what_is_detected` — skipped
outright for lack of CUDA. That run's CPU-scored numbers were committed
anyway, on the reasoning that parity had already been established by an
older cross-device run (`docs/benchmarks/performance-notes.md` section 3b).
That reasoning was sound in principle but rested on a claim, not on this
run's own evidence. This time, on real GPU hardware, `pytest -m model` ran
all 7 model-marked tests with **zero skips** — both device tests executed
and passed, including the one that runs the same text through the CPU
(fp32) and GPU (fp16) forward paths and asserts identical spans. Full
verbatim test output is in the Task 5b report.

With parity independently proven on this hardware, the table below was
re-measured directly on the GPU — the same device class the model's
originally published numbers were measured on — rather than continuing to
lean on a CPU proxy. **Every one of the 21 scored types below is identical,
to three decimal places, to the CPU-scored numbers this run supersedes** —
same precision, same recall, same F1, same support, on every row, and the
full false-positive/false-negative mistake lists (24 FP / 16 FN) match
entry-for-entry. The one place a real numeric difference showed up is the
"Per model label" table further down: two PERSON entities that the CPU
(fp32) pass attributed to the model's internal `person` label were
attributed to `full_name` instead under GPU (fp16) — a fp32-vs-fp16
numerical difference at the margin between two overlapping model labels
that both roll up into the same gold `PERSON` type. It does not change the
PERSON count, any type-level metric, or the verdict below; it is reported
here because it is a genuine measured difference and the instruction
governing this task was not to drop one quietly.

| Run | Type | Support | P (exact) | R (exact) | P (partial) | R (partial) | F1 (partial) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid/gliner2-privacy-pii-multi | PERSON | 99 | 0.888 | 0.879 | 0.969 | 0.960 | 0.964 |
| hybrid/gliner2-privacy-pii-multi | EMAIL | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | PHONE | 18 | 0.818 | 1.000 | 0.818 | 1.000 | 0.900 |
| hybrid/gliner2-privacy-pii-multi | DATE_OF_BIRTH | 4 | 0.667 | 1.000 | 0.667 | 1.000 | 0.800 |
| hybrid/gliner2-privacy-pii-multi | ADDRESS | 3 | 0.250 | 0.333 | 0.750 | 1.000 | 0.857 |
| hybrid/gliner2-privacy-pii-multi | POSTAL_CODE | 3 | 0.600 | 1.000 | 0.600 | 1.000 | 0.750 |
| hybrid/gliner2-privacy-pii-multi | NATIONAL_ID | 3 | 0.750 | 1.000 | 0.750 | 1.000 | 0.857 |
| hybrid/gliner2-privacy-pii-multi | PASSPORT | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | DRIVERS_LICENSE | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | LICENSE_NUMBER | 3 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 |
| hybrid/gliner2-privacy-pii-multi | TAX_ID | 4 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 |
| hybrid/gliner2-privacy-pii-multi | ACCOUNT_NUMBER | 3 | 0.429 | 1.000 | 0.429 | 1.000 | 0.600 |
| hybrid/gliner2-privacy-pii-multi | ROUTING_NUMBER | 3 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 |
| hybrid/gliner2-privacy-pii-multi | IBAN | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | CARD | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | CARD_EXPIRY | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | CARD_CVV | 3 | 0.167 | 0.333 | 0.167 | 0.333 | 0.222 |
| hybrid/gliner2-privacy-pii-multi | USERNAME | 3 | 0.750 | 1.000 | 0.750 | 1.000 | 0.857 |
| hybrid/gliner2-privacy-pii-multi | IP | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | ACCOUNT_ID | 3 | 0.750 | 1.000 | 0.750 | 1.000 | 0.857 |
| hybrid/gliner2-privacy-pii-multi | SECRET | 3 | 0.667 | 0.667 | 1.000 | 1.000 | 1.000 |

## Verdict

One line per catalogued type that declares a bar (the `bar:` key in the catalogue). A type with no bar is measured in the table above but has no line here — an unmeasured type is reported as absent, never as passing.

- **hybrid/gliner2-privacy-pii-multi** PERSON [OK] — meets bar — recall 0.960, precision 0.969
- **hybrid/gliner2-privacy-pii-multi** EMAIL [OK] — meets bar — recall 1.000, precision 1.000
- **hybrid/gliner2-privacy-pii-multi** PHONE [FAIL] — under bar — precision 0.818 < 0.95
- **hybrid/gliner2-privacy-pii-multi** IBAN [OK] — meets bar — recall 1.000, precision 1.000
- **hybrid/gliner2-privacy-pii-multi** CARD [OK] — meets bar — recall 1.000, precision 1.000

PHONE's own bar (precision/recall 0.95) is unchanged from before and was
already failing at 91 documents — see
["One defect, two numbers"](#one-defect-two-numbers) below for the mechanism
(the Steuer-ID/phone-backstop collision). Nothing about the 33 added
documents moved this one either direction. PERSON is the only type this
project's fine-tuning question is about (see `EvalReport.verdict()` in
`privaparse/evaluation/harness.py`); it still clears both floors.

## Old vs. new — every scored type, both directions stated

The 91-document figures are the ones the top-level README and this file
both quoted before 2026-08-14 (support column proves which run a number
belongs to; nothing here is re-derived or rounded to fit). "Moved" states
the direction plainly, including where it is a regression — the 33
documents Batches D and E added are all positives, which is exactly where a
new false negative would hide, and seven of these types had no gold
coverage at all before, so their "old" cell is a genuine absence of data,
not a zero.

| Type | Old P / R / F1 (support) | New P / R / F1 (support) | Moved |
| --- | --- | --- | --- |
| PERSON | 0.973 / 0.961 / 0.967 (76) | 0.969 / 0.960 / 0.964 (99) | P down 0.004, R down 0.001 — still clears the 0.85/0.90 bar |
| EMAIL | 1.000 / 1.000 / 1.000 (21) | 1.000 / 1.000 / 1.000 (21) | unchanged |
| PHONE | 0.818 / 1.000 / 0.900 (18) | 0.818 / 1.000 / 0.900 (18) | unchanged |
| IBAN | 1.000 / 1.000 / 1.000 (6) | 1.000 / 1.000 / 1.000 (6) | unchanged |
| CARD | 1.000 / 1.000 / 1.000 (3) | 1.000 / 1.000 / 1.000 (3) | unchanged |
| PASSPORT | 1.000 / 1.000 / 1.000 (3) | 1.000 / 1.000 / 1.000 (3) | unchanged |
| USERNAME | 1.000 / 1.000 / 1.000 (3) | 0.750 / 1.000 / 0.857 (3) | **worse** — P down 0.250 |
| IP | 1.000 / 1.000 / 1.000 (3) | 1.000 / 1.000 / 1.000 (3) | unchanged |
| DATE_OF_BIRTH | 1.000 / 1.000 / 1.000 (3) | 0.667 / 1.000 / 0.800 (4) | **worse** — P down 0.333; support +1 |
| ADDRESS | 0.750 / 1.000 / 0.857 (3) | 0.750 / 1.000 / 0.857 (3) | unchanged |
| NATIONAL_ID | 0.750 / 1.000 / 0.857 (3) | 0.750 / 1.000 / 0.857 (3) | unchanged |
| ACCOUNT_ID | 0.750 / 1.000 / 0.857 (3) | 0.750 / 1.000 / 0.857 (3) | unchanged |
| POSTAL_CODE | 0.600 / 1.000 / 0.750 (3) | 0.600 / 1.000 / 0.750 (3) | unchanged |
| TAX_ID | 1.000 / 0.000 / 0.000 (4) | 1.000 / 0.000 / 0.000 (4) | unchanged — same 4 entities, same defect |
| DRIVERS_LICENSE | no gold data | 1.000 / 1.000 / 1.000 (3) | newly measured — clean |
| LICENSE_NUMBER | no gold data | 1.000 / 0.000 / 0.000 (3) | newly measured — **recall 0, all 3 missed** |
| ACCOUNT_NUMBER | no gold data | 0.429 / 1.000 / 0.600 (3) | newly measured — weak precision |
| ROUTING_NUMBER | no gold data | 1.000 / 0.000 / 0.000 (3) | newly measured — **recall 0, all 3 missed** |
| CARD_EXPIRY | no gold data | 1.000 / 1.000 / 1.000 (3) | newly measured — clean |
| CARD_CVV | no gold data | 0.167 / 0.333 / 0.222 (3) | newly measured — **weak both ways** |
| SECRET | no gold data | 1.000 / 1.000 / 1.000 (3) | newly measured — clean (partial match) |

Reading this straight: recall fell exactly where it was expected to —
PERSON's own recall moved down (barely, 0.001) rather than up, and the
seven types that previously had zero gold coverage surfaced two outright
recall failures (LICENSE_NUMBER, ROUTING_NUMBER) and one weak type on both
axes (CARD_CVV) the moment real data existed to measure them against.
Nothing in this table is rounded toward the earlier "fine-tuning not
required" conclusion — PERSON still clears its own bar on the numbers as
measured, and that is reported because it is true, not adjusted to make it
true. USERNAME and DATE_OF_BIRTH regressed on precision alone; both are
9-total-FP-sized categories at 91 documents already (see the README's "nine
more types" caveat on n=3 support), so one additional false positive each
is the entire move — not a trend, but not hidden either.

## Per model label

Recall is not shown: gold entities carry a placeholder type, not a
model label, so a missed entity has no label to attribute it to.

| Run | Label | TP | FP | Precision |
| --- | --- | ---: | ---: | ---: |
| hybrid/gliner2-privacy-pii-multi | card_cvv | 1 | 5 | 0.167 |
| hybrid/gliner2-privacy-pii-multi | account_number | 3 | 4 | 0.429 |
| hybrid/gliner2-privacy-pii-multi | phone_number | 0 | 3 | 0.000 |
| hybrid/gliner2-privacy-pii-multi | postal_code | 3 | 2 | 0.600 |
| hybrid/gliner2-privacy-pii-multi | date_of_birth | 4 | 2 | 0.667 |
| hybrid/gliner2-privacy-pii-multi | full_name | 23 | 1 | 0.958 |
| hybrid/gliner2-privacy-pii-multi | last_name | 1 | 1 | 0.500 |
| hybrid/gliner2-privacy-pii-multi | (rule) | 51 | 1 | 0.981 |
| hybrid/gliner2-privacy-pii-multi | person | 66 | 1 | 0.985 |
| hybrid/gliner2-privacy-pii-multi | government_id | 3 | 1 | 0.750 |
| hybrid/gliner2-privacy-pii-multi | account_id | 3 | 1 | 0.750 |
| hybrid/gliner2-privacy-pii-multi | street_address | 3 | 1 | 0.750 |
| hybrid/gliner2-privacy-pii-multi | username | 3 | 1 | 0.750 |
| hybrid/gliner2-privacy-pii-multi | first_name | 5 | 0 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | passport_number | 3 | 0 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | card_expiry | 3 | 0 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | drivers_license_number | 3 | 0 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | api_key | 1 | 0 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | access_token | 1 | 0 | 1.000 |
| hybrid/gliner2-privacy-pii-multi | password | 1 | 0 | 1.000 |

## One defect, two numbers

All four of PHONE's false positives are all four of TAX_ID's false
negatives — the same defect, counted from both ends, not two separate ones.
TAX_ID reports P 1.000 / R 0.000 / F1 0.000 (support 4); the gold set is
deliberately kept in a form that keeps this gap visible rather than one
shaped to avoid it (`eval/gold/de_gold_source.md`'s Batch A note). The four
missed Steuer-IDs reach PHONE by two different routes. `de-047` keeps its
Steuer-ID in the bare, ungrouped form `generate_decidable()` actually
produces, and the phone *backstop* — the regex/`phonenumbers` rule, not the
model — matches an unbroken eleven-digit string as a German mobile number
directly. `de-048` and `de-049` write theirs the way a Finanzamt letter
actually prints one, grouped in threes (`XX XXX XXX XXX`); the backstop
finds nothing there, but the *model* labels all three `phone_number`
instead — the per-label breakdown above shows exactly this, `phone_number`
at 0 true positives and 3 false positives. `phone_shape`, the validator
that would otherwise veto a model guess that is not actually phone-shaped,
does not catch these three, because a grouped twelve-digit number genuinely
is phone-shaped — and TAX_ID's own backstop (`vat_de`, matching a VAT-ID's
`DE`-plus-nine-digits shape) does not cover a Steuer-ID's shape at all, so
nothing produces a competing exact span for the model's guess to lose to.
PHONE's 0.818 precision above and TAX_ID's 0.000 recall are this one
defect, read from opposite sides of the same four entities.

The fix is known and deliberately deferred, not missed. A checksum-
validated TAX_ID backstop would turn it into an exact span, and the
existing rule that an exact span trims back an overlapping model guess
would resolve the three grouped cases on its own — the model's
`phone_number` label would simply lose to a genuine TAX_ID match. The bare
case needs a second rule that does not exist yet: when two *regex* spans of
different types compete for the same text — a new TAX_ID backstop against
the existing phone backstop, here — nothing currently prefers the one
backed by a checksum over the one that only checked shape. Both fixes wait
on a label-ablation study to measure whether `phone_number` is a
false-positive driver generally, not only here, since that answer could
change what the second rule needs to look like.

## Mistakes: hybrid/gliner2-privacy-pii-multi

### Missed (false negatives — these reach the LLM) — 16 total

- `de-003` **PERSON** 'Müller-Lüdenscheidt' — …1187  Am 04.06.2024 rief Frau Müller-Lüdenscheidt an und erkundigte sich nach d…
- `de-010` **PERSON** 'Dr. Schmidt-Bauer' — …stätigung  Sehr geehrter Herr Dr. Schmidt-Bauer,  hiermit bestätigen wir Ihre…
- `de-019` **PERSON** 'Frau Doktor' — …Liebe Frau Doktor,  entschuldigen Sie die versp…
- `de-047` **TAX_ID** '08170772018' — …rliche Identifikationsnummer: 08170772018  Sehr geehrte Steuerpflichtig…
- `de-048` **TAX_ID** '04 826 373 520' — …rliche Identifikationsnummer: 04 826 373 520  Sehr geehrter Steuerpflichti…
- `de-049` **TAX_ID** '05 070 694 649' — …kationsnummern der Ehegatten: 05 070 694 649 und 06 996 426 306  Für die Z…
- `de-049` **TAX_ID** '06 996 426 306' — …Ehegatten: 05 070 694 649 und 06 996 426 306  Für die Zusammenveranlagung …
- `de-092` **ROUTING_NUMBER** '42604684' — …ro Baumann GmbH Bankleitzahl: 42604684 Betrag: 640,00 EUR Verwendung…
- `de-093` **ROUTING_NUMBER** 'AAAUDE8A' — …FT/BIC-Code Ihrer Bank.  BIC: AAAUDE8A Betrag: 2.150,00 USD Verwendu…
- `de-094` **ROUTING_NUMBER** 'MVGNDEB7O25' — …olgende Bankverbindung:  BIC: MVGNDEB7O25  Eine Bestätigung des Zahlung…
- `de-099` **CARD_CVV** '7530' — …die Kartenprüfnummer genannt: 7530.  Der Vorgang wurde an die Da…
- `de-100` **CARD_CVV** '975' — …tei auch die Kartenprüfnummer 975 eines einzelnen Kunden enthie…
- `de-116` **LICENSE_NUMBER** 'AP-2014-08823' — … unter der Approbationsnummer AP-2014-08823 als Ärztin zugelassen ist.  D…
- `de-117` **LICENSE_NUMBER** 'RAK-33871-M' — …er unter der Zulassungsnummer RAK-33871-M als Rechtsanwalt registriert.…
- `de-118` **LICENSE_NUMBER** 'PBEF-4471-NRW' — …ieter Ahrend unter der Nummer PBEF-4471-NRW erteilt.  Der Schein ist an d…
- …and 1 more

### Spurious (false positives — these only cost readability) — 24 total

- `de-001` **PERSON** 'Becker' — …it freundlichen Grüßen Sabine Becker Musterfirma GmbH…
- `de-007` **NATIONAL_ID** '12 O 3456/23' — … vom 03.02.2024 Aktenzeichen: 12 O 3456/23 Kundennummer: 0170 8899 Betra…
- `de-007` **ACCOUNT_ID** '0170 8899' — …n: 12 O 3456/23 Kundennummer: 0170 8899 Betrag: 1.249,00 EUR  Zahlbar…
- `de-009` **PERSON** 'König von Spanien' — …inter fällt er wieder ab. Der König von Spanien besuchte im Frühjahr die Mess…
- `de-047` **PHONE** '08170772018' — …rliche Identifikationsnummer: 08170772018  Sehr geehrte Steuerpflichtig…
- `de-048` **PHONE** '04 826 373 520' — …rliche Identifikationsnummer: 04 826 373 520  Sehr geehrter Steuerpflichti…
- `de-049` **PHONE** '05 070 694 649' — …kationsnummern der Ehegatten: 05 070 694 649 und 06 996 426 306  Für die Z…
- `de-049` **PHONE** '06 996 426 306' — …Ehegatten: 05 070 694 649 und 06 996 426 306  Für die Zusammenveranlagung …
- `de-051` **POSTAL_CODE** '04109' — …t lautet:  Kastanienallee 27, 04109 Leipzig  Bitte aktualisieren …
- `de-053` **POSTAL_CODE** '70173' — …g für das Objekt Talstraße 5, 70173 Stuttgart wurde heute von bei…
- `de-069` **ADDRESS** 'Az. 12 C 45/26' — …Az. 12 C 45/26  Termin zur mündlichen Verhan…
- `de-071` **ACCOUNT_NUMBER** '4342347850000001' — …Bestellübersicht  Artikel-Nr. 4342347850000001, Aktenordner, 10 Stück Artike…
- `de-071` **ACCOUNT_NUMBER** '7723119055406683' — …nordner, 10 Stück Artikel-Nr. 7723119055406683, Druckerpapier, 5 Pakete  Rec…
- `de-072` **ACCOUNT_NUMBER** '3301-A' — …81774400221193 Artikelnummer: 3301-A  Alle Preise verstehen sich i…
- `de-077` **CARD_CVV** '412' — … | --- | ---: | ---: | | Q1 | 412 | 355 | | Q2 | 468 | 371 | | …
- …and 9 more
