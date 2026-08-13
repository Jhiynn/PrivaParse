# Evaluation report

**Measured 2026-08-14** in a `/lab` sb-dev sandbox — 4 CPU cores, no GPU —
against the full 124-document gold set (`eval/gold/de_gold.jsonl`: 91
documents carrying PII, 33 without), scored at the shipped catalogue's 21
enabled types with `privaparse eval`. Model weights:
`fastino/gliner2-privacy-filter-PII-multi`, loaded from the lab's shared
model store at `/share/models/gliner2-privacy-pii-multi` with
`PRIVAPARSE_OFFLINE=1` — no network reached, nothing downloaded. That local
path is why the run label below reads `gliner2-privacy-pii-multi` rather
than the repo id: it is the store's directory name, not a different model.

**This run supersedes the 91-document run** quoted in the top-level README's
"Does GLiNER2 need fine-tuning for German?" section (measured 2026-08-10, 76
PERSON entities, 21 enabled types). Batches D and E added 33 more documents
between the two runs, all of them positives — see
`eval/gold/de_gold_source.md`. See the README and
[docs/benchmarks/README.md](README.md) for the old numbers placed next to
these.

A caveat on the device, stated plainly rather than left implicit: `pytest -m
model`, run in the same sandbox immediately before this, could not exercise
the CPU/GPU parity assertion itself
(`test_swapping_cpu_for_gpu_does_not_change_what_is_detected` skips outright
without CUDA, as does the device-placement test) — there is no GPU in this
sandbox to compare against. Every other model-marked test passed on CPU
against these same weights, including a full pseudonymize/reverse round
trip, so the CPU path itself is exercised and working; what is *not*
re-verified here is that CPU and GPU agree on this exact weights file. That
was last established when GPU-side numbers were measured (see
`docs/benchmarks/throughput.md` and the README's device-swap note). Full
verbatim test output is in the Task 5b report.

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
already failing at 91 documents — see "One defect, two numbers" in the
README for the mechanism (the Steuer-ID/phone-backstop collision). Nothing
about the 33 added documents moved this one either direction. PERSON is the
only type this project's fine-tuning question is about (see
`EvalReport.verdict()` in `privaparse/evaluation/harness.py`); it still
clears both floors.

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
| hybrid/gliner2-privacy-pii-multi | full_name | 21 | 1 | 0.955 |
| hybrid/gliner2-privacy-pii-multi | last_name | 1 | 1 | 0.500 |
| hybrid/gliner2-privacy-pii-multi | (rule) | 51 | 1 | 0.981 |
| hybrid/gliner2-privacy-pii-multi | person | 68 | 1 | 0.986 |
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
