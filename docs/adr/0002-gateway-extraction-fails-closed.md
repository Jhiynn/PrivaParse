# The gateway extracts by allow-list and fails closed

The gateway knows, per protocol adapter, exactly which request fields carry
free text and which carry none. A field that is named by neither and holds a
string — at any depth — stops the request where it stands, before a byte
reaches the upstream. We accept that this breaks the first time a provider
ships a new API feature, because the alternative fails in the direction that
matters: a gateway that forwards what it does not understand starts leaking the
moment a client adopts that feature, and does it silently, with a request that
looks like it worked.

## Consequences

A refusal reads as a bug to whoever hits it. It is not — it is the design
working, and adding the new field to the adapter is the fix.

The response direction is deliberately asymmetric: extraction on the way back
never raises and skips what it does not recognise. A failure outbound risks
disclosure; a failure inbound costs readability of an answer the caller has
already paid for.

The rule has exactly one deliberate hole, and it is opt-in. With
`gateway_allow_images` on, a content part the detector cannot read — an image, a
file reference — is forwarded instead of refused, on every protocol. Text beside
it in the same message is still pseudonymised, and the part itself reaches the
provider unmodified and unscanned. It is off by default, and the reason is not
squeamishness: a coding agent screenshots its own work, and a screenshot can
show every value that was just pseudonymised out of the text. Nothing else is
exempt — an unknown top-level field, an unknown message field, and a non-string
where text was expected all still stop the request where it stands.

The hole is read at the *part*, not at its payload, and that carries a
residual: with the setting on, a content part whose type is not one of the
protocol's text types is skipped whole, so a part of a type the walk has never
heard of is forwarded along with whatever text it happens to carry. Keying the
skip on a list of known image and file types instead would mean the next part
type a provider ships breaking every operator who opted in — which is the
outage the opt-in exists to end, reintroduced inside it. The conformance suite
asserts this residual rather than leaving it to be discovered from a request
log.
