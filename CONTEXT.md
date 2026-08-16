# PrivaParse

A local privacy layer for text on its way to an LLM: it detects PII, replaces it
with stable placeholders, and restores the original values in the answer. The
vocabulary below is the one the code, the docs and the issue tracker should all
speak.

## Scope

**Phase 1**:
The shipped scope — plain text and Markdown, local models only, and no
cross-type linking between placeholders. No OCR, no PDF, no cloud model.
_Avoid_: v1, MVP, the current release

## Detection

**Span**:
One detected occurrence of PII in a document, addressed by character offsets
into the original text.
_Avoid_: match, hit, detection, entity

**Backstop**:
A rule-based finder that runs alongside the model, there for recall rather than
authority.
_Avoid_: fallback, regex detector, rule

**Validator**:
A syntax or checksum veto over what the model proposes.
_Avoid_: checker, filter, guard

**Protected region**:
A part of a document masked before detection because it should never be scanned
— a code fence, a URL, an HTML comment.
_Avoid_: redacted region, hidden region, ignored region

**Coreference sweep**:
The pass that re-finds an already accepted surface form everywhere else in the
same document. Always qualified; a bare "sweep" names nothing.
_Avoid_: sweep, second pass, propagation

**Surface form**:
One observed spelling of a value — `Dr. Max Mustermann` beside
`MAX MUSTERMANN`. What restoration puts back.
_Avoid_: original value, variant, alias

**Normalised value**:
The canonical form of a value, and therefore what decides whether two surface
forms are the same entity.
_Avoid_: canonical value, key, cleaned value

## The catalogue

**Catalogue**:
The definition of every placeholder type PrivaParse knows, and the one place a
type's behaviour is declared.
_Avoid_: entity catalogue, schema, type registry

**Placeholder type**:
A category of PII — PERSON, EMAIL, IBAN. The catalogue defines them; a user
file may add or disable them.
_Avoid_: entity type, entity, category, class

**Label**:
A name the detection model itself emits. Several labels can feed one
placeholder type, and the model offers more than the catalogue routes.
_Avoid_: type, tag, class

**Threshold**:
The confidence score a model span must reach to be kept. A detection setting,
never a quality target.
_Avoid_: bar, cutoff, confidence level

**Bar**:
The precision or recall floor the evaluation holds a placeholder type to, fixed
before a run is scored. A quality target, never a detection setting.
_Avoid_: threshold, gate, target

**Irreversible type**:
A placeholder type whose values are never stored in a form anything can read
back, so its placeholders have no way home.
_Avoid_: one-way type, hashed type, redacted type

## The vault

**Vault**:
The local database — every entity PrivaParse has ever seen, every surface form
of it, and every mapping issued. The most sensitive file the tool produces.
_Avoid_: store, cache, the DB

**Entity**:
One distinct PII value in the vault, identified by its type and normalised
value. The thing a placeholder stands for.
_Avoid_: entity type, record, subject, PII item

**Placeholder**:
The token that replaces a value in a document — `[[PERSON_A1]]`. One per
entity, and the same one in every document, forever.
_Avoid_: token, alias, pseudonym, mask

**Mapping**:
One pseudonymisation and everything it issued. Restoration is scoped to a single
mapping, which is why "mapping" and not "session" — a session here is a database
session and nothing else.
_Avoid_: session, run, batch, job

## The gateway

**Gateway**:
The local OpenAI-compatible server a client points at instead of the provider.
It pseudonymises what goes out and restores what comes back.
_Avoid_: proxy, middleware, shim

**Upstream**:
The provider the gateway forwards to.
_Avoid_: backend, origin, remote

**Protocol adapter**:
Everything the gateway knows about one wire protocol: where text sits in a
request, where it sits in an answer, and the path it is served at.
_Avoid_: shape, dialect, wire format, protocol handler, backend

**Route body**:
The single request path every protocol adapter is served through. The adapters
are what differ; this is what does not.
_Avoid_: handler, endpoint, route

**Text node**:
One piece of free text carried by a request, together with where in the body it
came from.
_Avoid_: field, message, chunk, block

## Evaluation

**Gold set**:
The annotated German corpus every detection number is measured against —
including the documents that contain no PII at all.
_Avoid_: test set, ground truth, corpus

**Support**:
The number of gold entities of a type behind a measured score.
_Avoid_: sample size, n, coverage

**Threshold sweep**:
The evaluation that scores one detection run at every threshold. Always
qualified.
_Avoid_: sweep

**Label sweep**:
The evaluation that scores each model label on its own against the gold set.
Always qualified.
_Avoid_: sweep
