# Direct API

Five routes that expose PrivaParse's own detection, pseudonymisation and
restoration directly over HTTP, so the tool is usable from a shell script or
another language without an LLM in the loop and without going through the
[gateway](gateway.md)'s OpenAI-shaped proxy. Nothing here forwards to a
provider — each route is a thin adapter over an engine method, and every
response comes from that method, not from a model.

Like the gateway, this API binds loopback only. See
[No authentication](#no-authentication) at the end for why that is the whole
access-control story.

| Method | Path |
| --- | --- |
| POST | `/privaparse/detect` |
| POST | `/privaparse/pseudonymize` |
| POST | `/privaparse/reverse` |
| GET | `/privaparse/catalogue` |
| GET | `/privaparse/vault` |

## POST /privaparse/detect

Runs detection and returns the spans found, without touching the vault —
nothing is pseudonymised and no mapping is created. Accepts either `text` (a
single string) or `texts` (an array of strings); the response key is always
`detections`, but its *shape* mirrors which form the caller used: an array of
spans for `text`, an array of arrays for `texts`.

This runs the same pipeline `pseudonymize` runs before it writes anything —
masking fenced code blocks, applying the catalogue's threshold, merging
overlaps and the coreference sweep — not the detector's raw, unfiltered
output. What this endpoint reports is exactly what `pseudonymize` would
remove, so it is safe to use as a pre-flight check.

Batch request and response — `texts`, one detection list per input, in order:

```bash
curl -s http://127.0.0.1:8787/privaparse/detect \
  -d '{"texts": ["Schreiben Sie an max@test.de", "nichts hier"]}'
```

```json
{
  "detections": [
    [
      {
        "start": 17, "end": 28, "text": "max@test.de",
        "type": "EMAIL", "score": 1.0, "source": "regex", "label": null
      }
    ],
    []
  ]
}
```

Singular request — `text`, one flat list back, not a list containing one list:

```bash
curl -s http://127.0.0.1:8787/privaparse/detect -d '{"text": "max@test.de"}'
```

```json
{
  "detections": [
    {
      "start": 0, "end": 11, "text": "max@test.de",
      "type": "EMAIL", "score": 1.0, "source": "regex", "label": null
    }
  ]
}
```

A body that isn't `{"text": ...}` or `{"texts": [...]}` is a 400, with a
message that depends on what was actually wrong — a body that isn't a JSON
object at all:

```json
{"error": {"message": "the request body must be a JSON object", "type": "invalid_request_error"}}
```

or a JSON object missing both `text` and `texts`:

```json
{"error": {"message": "provide either `text` or `texts`", "type": "invalid_request_error"}}
```

Order and arity are preserved across a batch: an empty string or a text with
no PII still gets its own (empty) entry at the matching index, rather than
being dropped.

## POST /privaparse/pseudonymize

Detects, replaces matches with placeholders (`[[EMAIL_A1]]`-shaped), and
writes the mapping to the vault under one `mapping_id`. Also accepts either
`text` or `texts`, and follows the same rule `detect` does: the response key
is fixed — always `texts` — and its *shape* mirrors which form the caller
used: a single string for `text`, an array of strings for `texts`. A key that
changed name with arity would break a client the moment it switched from one
text to several; a key whose value collapses is something a caller handles
once, at the point it already knows which form it sent.

```bash
curl -s http://127.0.0.1:8787/privaparse/pseudonymize \
  -d '{"text": "Schreiben Sie an max@test.de"}'
```

```json
{
  "mapping_id": "6d759980-0326-4c5a-a685-f61bb1353782",
  "texts": "Schreiben Sie an [[EMAIL_A1]]"
}
```

The same value pseudonymised twice in one batch gets the same placeholder
both times — determinism within a mapping is the point:

```bash
curl -s http://127.0.0.1:8787/privaparse/pseudonymize \
  -d '{"texts": ["max@test.de", "max@test.de"]}'
```

```json
{
  "mapping_id": "dd43f07d-339f-4ad9-ba3d-806d229b455a",
  "texts": ["[[EMAIL_A1]]", "[[EMAIL_A1]]"]
}
```

By default the response above is the whole story: no `spans` key, and the
original value appears nowhere in the body — it can be logged. Pass
`include_spans: true` to get the matched spans back too, each carrying the
`placeholder` and vault `entity_id` it resolved to:

```bash
curl -s http://127.0.0.1:8787/privaparse/pseudonymize \
  -d '{"text": "Schreiben Sie an max@test.de", "include_spans": true}'
```

```json
{
  "mapping_id": "cfebc177-5171-4d62-8584-293597c0a328",
  "texts": "Schreiben Sie an [[EMAIL_A1]]",
  "spans": [
    {
      "start": 17, "end": 28, "text": "max@test.de",
      "type": "EMAIL", "score": 1.0, "source": "regex", "label": null,
      "placeholder": "[[EMAIL_A1]]",
      "entity_id": "47fe0377-fa7d-4ff6-9f83-ae63986f5764"
    }
  ]
}
```

`source_name` is accepted too, recorded against the mapping for later lookup
(the same field `privaparse pseudonymize` writes from a filename) — optional,
and irrelevant to the response shape.

A body with neither `text` nor `texts` is a 400, same envelope as `detect`.

## POST /privaparse/reverse

Restores placeholders back to the original values a mapping issued. Always
takes `text`; `mapping_id` is optional — omit it and the route looks for the
one mapping that issued every placeholder in the text, so a caller who kept
track of the id doesn't have to pass it back:

```bash
curl -s http://127.0.0.1:8787/privaparse/reverse \
  -d '{"text": "Schreiben Sie an [[EMAIL_A1]]", "mapping_id": "e210956a-f31b-4559-99b3-38d446a48d11"}'
```

```json
{
  "text": "Schreiben Sie an max@test.de",
  "mapping_id": "e210956a-f31b-4559-99b3-38d446a48d11",
  "restored": 1,
  "recovered": 0,
  "foreign": [],
  "unknown": []
}
```

`restored` counts placeholders resolved exactly; `foreign` lists placeholders
that belong to a different mapping and were left standing; `unknown` lists
placeholders no mapping in this vault ever issued. `recovered` counts
placeholders resolved through fuzzy matching — this route never sets that,
so it is always `0` here; fuzzy restoration is a gateway-only knob
(`PRIVAPARSE_GATEWAY_FUZZY`), not something this endpoint exposes.

Partial coverage — a text mixing placeholders from two mappings, with no
`mapping_id` given to break the tie — resolves nothing and comes back as a
404, not a partial answer. This is what stops one caller reading another's
values by guessing at a placeholder:

```json
{
  "error": {
    "message": "no single session issued all 2 placeholder(s) in this text — the placeholders are spread across several sessions. List sessions with: privaparse vault mappings",
    "type": "mapping_not_found"
  }
}
```

An unknown `mapping_id`, or a body with no `text`, are the other 400/404
cases — same `mapping_not_found` / `invalid_request_error` envelope.

**`strict: true`.** By default, a placeholder belonging to a different
mapping (with `mapping_id` given explicitly, so no session lookup happens) is
left in the text and reported in `foreign` — nothing leaks, it just isn't
restored. Set `strict: true` and that same situation is a 400 instead,
carrying the engine's own message, `type: "invalid_request_error"`:

```json
{
  "error": {
    "message": "placeholders from another session appeared in this text: [[EMAIL_A1]]",
    "type": "invalid_request_error"
  }
}
```

## GET /privaparse/catalogue

The enabled entity types and how each is configured — no request body. The
shipped catalogue has 21 enabled types; here's the shape, trimmed to two
entries:

```json
{
  "version": 1,
  "types": [
    {
      "name": "EMAIL",
      "enabled": true,
      "reversible": true,
      "threshold": null,
      "labels": ["email"],
      "validator": "email_syntax"
    },
    {
      "name": "CARD",
      "enabled": true,
      "reversible": false,
      "threshold": 0.5,
      "labels": ["payment_card", "card_number"],
      "validator": "luhn"
    }
  ]
}
```

`threshold` is `null` when the type declares no per-type override — the
process-wide default (`--threshold` / `PRIVAPARSE_THRESHOLD`, `0.5`) applies
to it instead. `reversible: false` marks a type that gets detected and
pseudonymised but never restored — `CARD`, `CARD_CVV` and `SECRET` ship that
way, so those values never round-trip back into a reply.

## GET /privaparse/vault

Counts only — how many mappings, entities and surface forms the vault holds,
broken down by type. No request body, no stored value in the response:

```bash
curl -s http://127.0.0.1:8787/privaparse/vault
```

```json
{
  "mappings": 1,
  "entities": 1,
  "surface_forms": 1,
  "by_type": {"EMAIL": 1}
}
```

`mappings` is the number of pseudonymisation sessions recorded; `entities` is
the number of distinct real-world values (a person, an email address) the
vault has ever stored; `surface_forms` is the number of distinct textual
variants seen for those entities. None of the three can be turned back into
the value they count.

## Which responses carry personal data

- `POST /privaparse/detect` — always. Returning the matched text is what the
  route is for.
- `POST /privaparse/pseudonymize` — only with `include_spans: true`. The
  default response has no PII in it and can be logged; asking for spans puts
  the original matched text back into the body.
- `POST /privaparse/reverse` — always, by definition. It is a
  de-pseudonymisation endpoint; its whole job is handing real values back.
- `GET /privaparse/catalogue`, `GET /privaparse/vault` — never. Configuration
  and counts only, regardless of what the vault holds.

That's the line for what a caller can log or forward and what it can't:
`detect` and `reverse` responses can't; `pseudonymize` can't only if
`include_spans` was set; everything else always can.

## No authentication

There isn't any, and the reason is the same one the gateway relies on:
`privaparse serve` refuses to bind anything but loopback (`127.0.0.1` /
`localhost`), so no request reaches these routes from anywhere but this
machine. Reachability is the access control. The vault behind both APIs
stores plaintext values and has no per-user access control of its own — see
[Restoration puts real PII into the client](gateway.md#restoration-puts-real-pii-into-the-client)
for what that means for a request that does leave the machine, and
[SECURITY.md](../SECURITY.md) for the full threat model, including what's
explicitly out of scope. Reach this API from another machine over an SSH
tunnel, not by binding a wider address.
