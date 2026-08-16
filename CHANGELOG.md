# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- A Responses stream that stops without releasing what the gateway is holding
  now hands it over instead of dropping it: the tail the hold-back kept out of
  the deltas in case it grew into a placeholder, and every fragment of a tool
  call whose arguments were still accumulating. Covers a connection that drops
  mid-answer, a stream whose bytes simply run out, and a terminal event that
  arrives with no `.done` before it — in the last case the held events go out
  in front of the terminal event, where a client is still reading. The Chat
  Completions path has flushed both since it shipped (#17).
- Events the gateway inserts into a Responses stream are renumbered, so a
  client never sees one `sequence_number` twice or a count that stops rising
  where something was inserted.

### Changed

- Documentation, docstrings and test names say *mapping* where they meant a
  pseudonymisation and everything it issued; *session* now means a database
  session and nothing else. The *vault* names the local database as a whole
  rather than one table. No behaviour change.
- The two protocol adapters are peers: each now holds the walk over its own
  protocol, and both name their walks `extract_request` and `extract_answer`
  rather than one pair borrowing the Responses API's own field names. The
  extraction seam keeps only what no protocol owns and imports no adapter.
  No behaviour change.
- One route body serves every protocol. The two route functions — some
  fifty-five of eighty lines identical, comments included — are one body over
  a *protocol adapter*: a frozen value carrying the protocol's name, the path
  it is served at, its request walk, its answer walk, its hint insertion and
  its stream relay. The app mounts one route per entry in a single tuple of
  adapters, so a third protocol is a value rather than a third copy of the
  route, and a per-protocol difference has to be declared as a field. The
  callable fields are declared as `typing.Protocol` callback types, which
  makes a request walk that omits a parameter another's has a type error where
  the adapter is built — the silence that let `gateway_allow_images` reach one
  route out of two. Both routes answer exactly as they did (#21).
- A refusal names the protocol that made it: the chat route logged `refused a
  request` where the Responses route named itself, and an operator reading the
  log could not tell which client had hit it.
- The longest placeholder the catalogue can render is measured once, when the
  app is built, rather than on every streaming request.

## [0.1.0] - 2026-08-14

### Added

- A CLI (`privaparse pseudonymize`, `reverse`, `detect`, `demo`, `doctor`,
  `eval`, `bench`, `vault stats`/`mappings`, `catalog show`/`validate`,
  `serve`, `run`) and a Python library (`privaparse.pseudonymize`,
  `privaparse.reverse`, `PrivaParseEngine`) for local PII detection and
  pseudonymisation of German text.
- A configurable entity catalogue (`privaparse/app/entities.default.yaml`)
  routing GLiNER2 labels and regex/`phonenumbers` rules onto 25 placeholder
  types, 21 enabled by default, each with its own threshold, optional
  validator, and optional backstop finder. User catalogues layer on top via
  `PRIVAPARSE_ENTITIES`, a project file, or a user config file.
  (`fastino/gliner2-privacy-filter-PII-multi`, built on
  `microsoft/mdeberta-v3-base`.)
- Checksum and shape validators (`privaparse/parser/validators.py`) for
  IBAN (mod 97), Luhn card numbers, German tax IDs and VAT-IDs, German bank
  routing numbers and BIC, German postal codes, IP addresses, card expiry,
  and CVV shape — vetoing model spans that don't match, without discarding
  spans for types that have no decidable shape.
- A local SQLite vault handing out deterministic, stable placeholders
  (`[[PERSON_A1]]`): a value gets the same placeholder in every document,
  forever, and `reverse()` resolves only the placeholders the session that
  called it actually issued. `pseudonymize_batch` pseudonymises several
  texts under one shared mapping.
- Markdown-aware protection: fenced code, inline code, HTML comments, and
  URLs are masked before detection, with YAML frontmatter and `mailto:`
  targets scanned as deliberate exceptions to that masking.
- An evaluation harness (`privaparse eval`, `privaparse.evaluation`) scoring
  every catalogue type, and every individual model label, against a German
  gold set under `eval/gold/`, plus `build_gold.py` compiling an annotated
  Markdown source (`de_gold_source.md`) into offset-verified JSONL.
- A threshold sweep (`privaparse bench --matrix`) measuring precision and
  recall across a range of thresholds from a single model pass, and a
  throughput benchmark across dtype/compile/batch-size configurations.
- An OpenAI-compatible local gateway (`privaparse serve`, `privaparse run`)
  serving `/v1/chat/completions`, `/v1/responses` (both streaming and
  non-streaming, tool calls included), and `/v1/models` — pseudonymising a
  request's text fields under one mapping per request and restoring the
  answer before it reaches the client.
  - Streamed answers restored through a hold-back buffer, and streamed tool
    calls assembled and restored as a whole, with arguments parsed and
    re-serialised rather than string-substituted.
  - Per-request-block detection caching, keyed by catalogue and text, so a
    client that resends its whole history each turn doesn't re-scan it.
  - `PRIVAPARSE_GATEWAY_FUZZY` and `PRIVAPARSE_GATEWAY_HINT`, two opt-in
    ways to recover a placeholder a small model mangled on the way back.
  - A Responses API adapter verified against a live Codex CLI turn,
    including `additional_tools` and unrecognised-field handling
    (`client_metadata`).
  - An operator opt-in to forward image parts the detector cannot read,
    off by default.
- Docker images with `slim` and `full` build targets — `full` bakes in
  GLiNER2 weights and runs fully offline (`PRIVAPARSE_OFFLINE=1`).

### Changed

- Entity types moved from a fixed enum to catalogue values loaded from
  YAML at startup, with normalizers and backstop finders as named registry
  entries rather than hardcoded branches.
- Overlap resolution: the model wins overlapping spans, rules assist, and
  an exact (regex or validator-backed) span acts as a boundary rather than
  competing with a model span on equal terms.
- All 42 labels the model offers routed onto the 25 placeholder types;
  four labels (`middle_name`, `sensitive_account_id`, `secret`,
  `recovery_code`) measured alone against the gold set, found zero
  matching entities each, and were dropped from their type's routing.
- CITY, REGION, and DATE ship disabled by default after measurement showed
  false positives with no offsetting true positives on the gold set;
  COUNTRY ships disabled too, but on judgement — it measured neither false
  positives nor true positives, so a country name alone rarely identifying
  a person is what disabled it, not a measured cost.

### Fixed

- Overlap-merge and positional-replay defects in batch pseudonymisation
  that could mis-place or re-issue a placeholder.
- The gateway's request path now fails closed — a field it has no rule for
  stops the request with a 502 instead of being forwarded unexamined.
- `.env` excluded from Docker images via `.dockerignore`, so local
  configuration (including a device pin) can no longer silently override
  the image's own settings at build time.
- A startup race where the model registry could be reported ready before
  it was actually filled.
- The missing-GLiNER2 guard imported a module that succeeds whether or not
  GLiNER2 itself is installed, so its friendly install message had never
  once reached a user — a raw `ModuleNotFoundError` from the CLI, or a bare
  500 from the gateway, was what a first-time user actually saw. The
  gateway now catches this case and returns a 500 with an OpenAI-compatible
  error envelope instead.
