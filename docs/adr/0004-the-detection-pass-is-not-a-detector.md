# The detection pass is not a detector

**Status:** accepted, partly implemented — `DetectionPass` exists as of #39,
with the pseudonymizer's batched detection delegating to it; the callers named
under *Consequences* move onto it in #40. Unlike ADRs 0001–0003, this one was
written ahead of the code, so read the consequences as what the change commits
to rather than as what is there today.

"Text in, final spans out" — `protect` → `detector.detect(view)` → `resolve_spans`
— was written longhand at four call sites, each re-deriving the same four
`Settings` fields, and no module owned the order. It now lives in one place, a
`DetectionPass` built from a detector and those four values. The decision worth
recording is what the pass deliberately is *not*: it does not satisfy the
`Detector` protocol, even though satisfying it would have let every existing
caller keep its parameter type unchanged.

The two contracts are genuinely different and only look alike. A detector is
handed the **masked view** and proposes candidate spans. The pass is handed the
**original text** and returns spans that survived the threshold, the merge and
the coreference sweep. Sharing the verb `detect` is what let those contracts be
confused in the first place — `evaluate()` in the eval harness was typed
`SupportsDetect` and fed three incompatible things: bare detectors in tests,
`_ReplayDetector` (masked and merged, no model), and `PrivaParseEngine` itself
from the benchmark, which is the full pass. A fourth caller already wanted the
pass and had to borrow the detector's name for it. Letting `DetectionPass`
implement `Detector` would have preserved exactly that ambiguity, and would have
made it legal to nest a pass inside a `CompositeDetector` — masking a document
twice, at a seam where the offsets are no longer the ones the caller holds.

## Considered options

Giving `DetectionPass` a `detect(text)` method so both satisfy one protocol. It
is the cheap option and it is why this ADR exists: the reader who finds
`evaluate` and `sweep_thresholds` each taking their own thing will reach for it
within a minute. It was rejected because the collision it re-creates is the
whole defect, not an incidental cost of fixing it.

Wrapping the eval harness's test fakes in a pass, so `evaluate` could take only
a `DetectionPass`. This does not work, and the reason is worth knowing before
someone tries it again: several harness tests feed deliberately malformed span
sets — two overlapping `PERSON` spans asserting `tp == 1`, spans that a merge
would drop — precisely to exercise the scorer's matching rules. Running them
through a pass first would delete the inputs before the assertion could see
them. `evaluate` is a scorer, not a pipeline stage, and it now takes
precomputed spans per document rather than anything callable.

## Consequences

The `Detector` protocol's `detect_many` default body is deleted. It was never
inherited — `Detector` is a structural `Protocol` — so `CompositeDetector`
probed for the method with `getattr` while `RegexDetector` and `StaticDetector`
each re-spelled the same one-liner. One `detect_batch(detector, texts)` helper
owns that probe now. The protocol stays structural: hand-written fakes that
inherit from nothing are load-bearing in the tests and in the gateway.

The invariant that a detector is only ever shown the masked view is stated in
the protocol's docstring and enforced by the pass being its only caller. It is
not encoded in the type. Handing detectors a `ProtectedText` instead of a `str`
would prove it, at the cost of every test fake and the caching detector's string
key — worth revisiting only if a second caller of `Detector` ever appears.

`CachingDetector` stays *below* the seam, keyed on the masked view plus a
catalogue fingerprint. "The pass owns masking and resolution" invites the
conclusion that the cache belongs to the pass too; it does not. The model's
candidates do not depend on the threshold or the sweep, so caching above the
pass would force both into the key and shrink the hit rate for nothing.

The pass carries the four values explicitly — `threshold`, `sweep`, `scan_code`,
`catalogue` — rather than a `Settings`, with a `from_settings` constructor and a
`replace(...)` for variants. This is what retires
`getattr(engine.settings, "coreference_sweep", True)` in the threshold sweep:
that default existed because the sweep was handed an object it could not be sure
had the field, and the test fake standing in for it built its settings as
`type("S", (), {...})()`. A value with four declared fields has nothing to
defend against, and the sweep now asks the pass for a variant of itself instead
of assembling a fake detector.

`PrivaParseEngine.detect_raw` is removed; its behaviour is `DetectionPass.scan`,
the expensive half, paired with `resolve` as the cheap half that the threshold
sweep re-runs per point on the curve. `_ReplayDetector` — a class whose docstring
existed almost entirely to justify a positional counter and an out-of-sync
`RuntimeError`, both artefacts of `evaluate` pulling rather than being pushed —
is deleted along with `SupportsDetect` and `SupportsDetectRaw`.
`engine.detect` and `engine.detect_many` remain as one-line delegations and both
now accept an injected detector; previously only the batch form did, so the
gateway's single-text and batch answers came off different assemblies.

After this, the only caller of `resolve_spans` and `merge_spans` is the pass,
which always holds a real `ProtectedText` and a real `Catalogue`. Their
`protected=None` and `catalogue=None` modes therefore have no caller left —
which is the precondition issue #14 needs, and is deliberately left to it.
