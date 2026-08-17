# One module carries an operation to its outcome, and neither entry point decides what an error means

**Status:** accepted, not yet implemented — this is what issue #10 commits to.

`PrivaParseEngine` stops at a result object. Everything after it — where the
output goes, what each error *means*, what the result looks like on the wire —
was written once per entry point, and the two copies disagreed on nine
capabilities. Two of those disagreements are silent data loss: there is no HTTP
way to recover a lost mapping id, though the CLI command exists precisely
because losing one makes a document unreversible; and span JSON is spelled twice,
with the CLI omitting `label`, so a script switching from piping the CLI to
calling the route loses a field without noticing.

Error meaning was decided twice and oppositely. `_run` catches `RuntimeError`,
`ValueError` and `LookupError` and prints all three identically, so an internal
placeholder-allocation failure reaches the user exactly like their own mistake of
pseudonymising twice; the direct routes separate them into 400 / 404 / 500. The
engine itself raises nothing — its error contract is implicit across three base
classes borrowed from `parser/`, `app/` and `database/`, and was never written
down anywhere.

So an **Operation** carries one thing the user asked for from its input to its
**Outcome**, owning path derivation, encoding, overwrite policy and the
error→outcome classification. The CLI renders the outcome; the route serialises
it. Neither decides what happened.

## Considered options

**Text in, outcome out — leaving paths, encoding and overwrite in the CLI.** The
cheapest option, and it keeps file concepts off the HTTP path, which is the one
thing genuinely uncomfortable about what was chosen. Rejected because the
defects are *in* that half: `_read` is called inside `_run` for `detect` and
outside it for the others, so a latin-1 file gives a clean error from one command
and a traceback from another; `target.write_text` is unguarded and silently
clobbers. And the policy had already drifted without HTTP's help — `eval` and
`bench` `mkdir(parents=True)` before writing, the document commands do not. A
policy that has already been answered differently twice inside one file will be
answered differently a third time.

**Raising one classified exception instead of returning a total value.** Closer
to Python's grain, and it would need no wrapping inside operation bodies.
Rejected because "what happened" then splits across two channels and each entry
point still has two paths — and because a total value is the only shape where a
test asserts the classification without exercising an exception path. That is
specifically what let the three-base-classes-one-message block survive as long as
it did.

## Consequences

**The gateway relay does not use this, and neither does the eval harness.** Both
exclusions are deliberate and both are stated in the module docstring, because "a
module owns error meaning, and the relay does not use it" is the kind of thing a
future reader repairs by accident. The relay is brokering someone else's request:
its error contract is a property of the wire protocol it impersonates, already
committed in ADRs 0002 and 0003 — fail closed with 502 `unscannable`, 500 rather
than 503 when detection is down, restoration inbound never raising. Dragging that
into the CLI's vocabulary would mean `unscannable` appearing where it means
nothing. The eval harness reports scores against a gold set; its failures are
missing corpora and unmeasurable types, which do not fit the taxonomy below and
would bloat it if forced.

**Read-only queries are Operations too**, with source and sink slots empty.
Excluding them was tempting — they transform no document — but three of the nine
drifted capabilities are exactly there: catalogue parity, catalogue-as-JSON, and
list-mappings-over-HTTP. A seam that excludes queries leaves the hole open in the
place it demonstrably leaks. `GET /privaparse/vault/mappings` is a sibling of
`/privaparse/vault` rather than a key on it, so the stats call does not start
paying for a query it did not ask for.

**Six outcome kinds**, closed: `succeeded`, `rejected` (bad argument, already
pseudonymised, unknown entity type, source not valid UTF-8), `missing` (unknown
mapping, source file not found), `refused` (output exists without `--force`,
`UnreplaceableSpanError`, no covering mapping), `unavailable` (GLiNER or device
absent), `failed` (span integrity, placeholder allocation, anything
unclassified). `refused` earns its place as the bucket for "well-formed, and
PrivaParse declines" — the shape ADR 0002 already committed the gateway to;
without it an overwrite refusal and a malformed argument report identically.
An unclassified exception escaping an operation is a bug the module is tested
against, which is the price of the classification being total.

**`NoCoveringMappingError` moves from 404 to 409.** The route calls it
`mapping_not_found` today, but no mapping is missing — the text does not
correspond to any one mapping. Breaking for a caller branching on status, and
recorded here rather than only in the changelog because the current status is
what a reader would otherwise assume was intended.

**CLI exit codes stay uniformly 1.** One code per kind was considered and
dropped: Click already owns 2 for usage errors, and a script that needs to branch
should read `--json` rather than count exit codes.

**Operations are batch-native**; one text is a batch of one, and the
singular/plural collapse stays in the route where the wire format demands it.
Sinks are pre-flighted — every existence and parent-directory check runs before
the first byte — so `refused` is reached with nothing on disk. A failure *after*
pre-flight is `failed` and may leave a partial batch; that residual is stated in
the outcome rather than pretended away, because a genuine mid-write I/O error is
not something the module can undo.

**Parity is held by a conformance suite parametrised over every operation**, in
the shape ADR 0003 established for protocol adapters: every operation reachable
from both entry points, every outcome kind rendering on both, and one test
asserting every operation *has* coverage — so an operation added without it fails
by name. This is the whole point; without it, parity is remembered again.

**The span JSON respelling favours the route's version** — `label` included,
`score` unrounded. Rounding is presentation applied to a machine-readable stream,
and a consumer wanting four places can round while one wanting `label` cannot
invent it. This is the one place the change breaks an existing contract rather
than adding to it.

**The knock-on for `tests/test_cli.py` is smaller than it looks.** Its
`CliRunner` dependence and its live uvicorn server sit in the `serve` / `run` /
`gateway stats` block — process-management commands this does not touch. The
document-command tests already patch nothing beyond the detector factory.
