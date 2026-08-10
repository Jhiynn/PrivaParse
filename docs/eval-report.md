# Evaluation report

| Run | Type | Support | P (exact) | R (exact) | P (partial) | R (partial) | F1 (partial) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid/gliner2-privacy-filter-PII-multi | PERSON | 60 | 0.869 | 0.883 | 0.967 | 0.983 | 0.975 |
| hybrid/gliner2-privacy-filter-PII-multi | EMAIL | 20 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid/gliner2-privacy-filter-PII-multi | PHONE | 18 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Verdict

Threshold fixed in advance: PERSON partial-match recall >= 0.9, precision >= 0.85.

- **hybrid/gliner2-privacy-filter-PII-multi** — threshold met — PERSON recall 0.983, precision 0.967; fine-tuning not required

## Mistakes: hybrid/gliner2-privacy-filter-PII-multi

### Missed (false negatives — these reach the LLM) — 1 total

- `de-019` **PERSON** 'Frau Doktor' — …Liebe Frau Doktor,  entschuldigen Sie die versp…

### Spurious (false positives — these only cost readability) — 2 total

- `de-009` **PERSON** 'König von Spanien' — …inter fällt er wieder ab. Der König von Spanien besuchte im Frühjahr die Mess…
- `de-026` **PERSON** 'vier Personen' — …Zutaten für vier Personen: 500 g Mehl, 250 ml Milch, zw…

