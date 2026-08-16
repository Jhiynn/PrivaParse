# Catalogue granularity errs high, because splitting a placeholder type is a one-way door

When a kind of PII could plausibly be one placeholder type or two, the catalogue
defines two. Merging two types later is a mechanical migration — rewrite a
column, de-duplicate the rows. Splitting one type into two later is not possible
at all: the vault stores the normalised value under the type it was assigned, so
the information that would separate the two was discarded at write time, and
recovering it would mean re-detecting every value ever stored.

## Consequences

The catalogue carries types with thin or no gold coverage, and some of them
measure badly — that is the accepted cost of the asymmetry, not an oversight.
A type that turns out to be wrong can be disabled (`enabled: false`) or folded
into another; a type that was never separated cannot be recovered.

SECRET is the deliberate exception: it merges three model labels into one type,
because all three are irreversible, never restored, and never distinguished by
anything downstream — so there is nothing a future split could give back.
