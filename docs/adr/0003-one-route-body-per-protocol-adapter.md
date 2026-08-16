# One route body serves every protocol, because the second copy already drifted

The gateway speaks Chat Completions and the Responses API, with Anthropic
Messages next. Those two routes were written twice — some fifty-five of eighty
lines identical, comments included — and only five things varied: which request
walk, which hint, which upstream path, which stream restorer, which answer walk.
Nothing named that set, so the interface a third protocol had to satisfy was
implicit, and it drifted: `gateway_allow_images` was wired into `/v1/responses`
only, while `/v1/chat/completions` refused image parts unconditionally. The
setting is documented as a global rule, there was no route-level test of it, and
it shipped that way. So the five substitutions become one `ProtocolAdapter`
value, one route body consumes it, and `create_app` mounts a route per adapter.

## Considered options

Leaving the two routes as copies and fixing only the drift. Each copy is, on its
own, more readable than a route body parameterised over an adapter — that
readability is what was traded away. It bought a shape in which a per-protocol
difference has to be *declared as a field* rather than arrived at by editing one
file and forgetting the other.

## Consequences

The callable signatures are declared as `typing.Protocol` types on the
dataclass fields. An adapter whose request walk omits a parameter another's has
is then a type error rather than a silence — which is the specific failure this
decision exists to prevent, so weakening those annotations to bare `Callable`
gives up most of what was bought.

The shared invariants live in a conformance suite parametrised over every
adapter: fails closed with a pointer and no value, one mapping per request, the
hint present exactly once and only when an entity was replaced,
`gateway_allow_images` honoured, 500 rather than 503 when detection is
unavailable, restoration never aborting, and a stream that ends without a
terminal event losing nothing. Fixtures live in the test suite rather than on
the adapter, with one test asserting every adapter has a set — so a new protocol
without conformance coverage fails by name.

The streaming slot takes the whole relay rather than a per-event rewrite, so SSE
never enters the adapter contract; both OpenAI adapters implement it through one
shared SSE framing module, and a non-SSE protocol can supply its own without
reshaping the value. The price is that the end-of-stream flush is guaranteed by
the conformance test rather than by the type.

Per-protocol upstream addressing is deliberately not a field yet. Anthropic will
need `x-api-key` and `anthropic-version` forwarded, and `upstream.py` forwards
neither; adding the field with no adapter to exercise it would ship an untested
widening of a security-relevant allow-list. Related and still open:
`settings.gateway_upstream` is a single base URL, so serving two providers'
protocols at once is not expressible today. The third adapter forces that
question; this decision does not answer it.
