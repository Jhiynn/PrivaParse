# Irreversibility is read from the vault, not the catalogue

**Status:** accepted, not yet implemented — this is what issue #11 commits to.

Reversal classified a placeholder by elimination: in this mapping's entries it is
*restored*, otherwise in `entities` it is *foreign*, otherwise *unknown*.
`_resolve_irreversible` deliberately writes no mapping entry, so a document's own
`[[CARD_CVV_A3]]` fell through to *foreign* — the CLI warned about placeholders
from another mapping, `strict=True` raised `ForeignPlaceholderError` saying the
same, and `reverse(None, text)` could never succeed on such a document at all.
Three untrue statements from one missing case.

The fix adds a fourth outcome, and the decision worth recording is **where its
answer comes from**: an entity with no stored surface form has no way home, and
that is a fact about what was written to the vault. It is not read from
`PlaceholderType.reversible`.

## Considered options

**Asking the catalogue whether the type is reversible.** This is what a reader
will reach for, which is why this ADR exists — the flag is right there, it is
already exposed over HTTP at `/privaparse/catalogue`, and the check is one line.
It was rejected because the catalogue is a deep-merge of a user file over the
defaults and is re-loaded per run, so the answer is not stable: flipping a type
to `reversible: true` would silently re-classify placeholders issued years
earlier, for which nothing was ever stored. `reverse_mapper.py` does not import
`Catalogue` today and now has no reason to. `_resolve_irreversible`'s own
docstring already said this — "the one-way door is a consequence of what was
written, not a flag someone can flip later" — and reading the flag would have
contradicted the code it documents.

**Writing a mapping entry with no restore value**, so an irreversible placeholder
becomes attributable to the mapping that issued it. This is the only option that
would make `strict` mode honest, and it was rejected on two counts.
`MappingEntry.restore_value_id` is `NOT NULL`, so it needs a migration that
weakens the constraint currently guaranteeing every entry has something to
restore. And it cannot backfill: vaults that already issued irreversible
placeholders have no rows to find, so the vault-sourced check would be needed
anyway as the fallback — leaving the migration buying attribution for new vaults
only, for a class of placeholder nobody can restore.

## Consequences

**An irreversible placeholder is recognised, not attributed.** A
`[[CARD_CVV_A3]]` genuinely pasted in from someone else's document classifies
identically to one this mapping issued. Nothing records which mapping issued it
and, given the above, nothing can — so the outcome says *irreversible* rather
than claiming the placeholder is yours. This is a smaller claim than *restored*
or *foreign* make, deliberately.

`is_clean` stays true and `strict` does not raise for them. A document whose only
oddity is a placeholder that was never restorable is a correctly reversed
document; `is_clean` means "nothing here surprised me", and this does not. A
caller wanting no placeholders left reads `irreversible` on the result.

`find_mapping_for` filters them out of the set it demands coverage for, and
therefore returns `str | None` — a document whose placeholders are *all*
irreversible needs no mapping and gets `mapping_id=None` on a clean result
rather than a `NoCoveringMappingError`. A document with *no* placeholders keeps
raising, and the asymmetry is deliberate: that one is almost always the wrong
file, and the mistake is worth a loud failure.

`ReverseResult` gains `mapping_id`, which removes the reason the direct reverse
route imported `find_mapping_for` and opened its own vault session — a
documented breach of that file's "thin adapter, no restoration logic here"
docstring.

`_adopt_existing` skipping an irreversible entity is correct and stays, but its
`# pragma: no cover - an entity always has one` was false: the branch is live and
untested. It becomes an explicit irreversible case with coverage.

**Unrelated, and fixed here because it is in the same file:** a span whose value
normalises to empty was dropped with a `log.debug` and its text forwarded *in
clear* — before `register_secret`, so it was not even redacted from logs. It now
raises `UnreplaceableSpanError`, carrying offsets, type and normalizer name and
never the text. A detected value that cannot be turned into a placeholder is
exactly the case where forwarding is the wrong failure. It is reachable from
ordinary input — a `digits`-normalised `CARD` span proposed over text with no
digits in it — not only from a pipeline bug, which is why it is not folded into
`SpanIntegrityError`.
