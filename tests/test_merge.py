"""Overlap resolution and the coreference sweep."""

from __future__ import annotations

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.parser.markdown import protect
from privaparse.parser.merge import coreference_sweep, merge_spans, resolve_spans
from privaparse.parser.types import SOURCE_COREF, SOURCE_GLINER, SOURCE_REGEX, EntityType, Span


def _span(text: str, needle: str, entity_type: EntityType, **kw) -> Span:
    start = text.index(needle)
    return Span(
        start=start,
        end=start + len(needle),
        text=needle,
        type=entity_type,
        score=kw.pop("score", 0.9),
        source=kw.pop("source", SOURCE_GLINER),
    )


def test_low_confidence_spans_are_dropped() -> None:
    """Threshold filtering, not the veto — no catalogue needed."""
    text = "Max Mustermann war da."
    weak = _span(text, "Max Mustermann", EntityType.PERSON, score=0.2)
    assert merge_spans([weak], threshold=0.5, catalogue=None) == []
    assert merge_spans([weak], threshold=0.1, catalogue=None) == [weak]


def test_email_beats_an_overlapping_person_span() -> None:
    """The model grabbing the local part of an address is the common case.

    Priority resolution, not the veto — no catalogue needed. The email span
    is SOURCE_REGEX (never vetoed) and PERSON has no validator either way.
    """
    text = "Kontakt max@test.de bitte."
    email = _span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)
    person = _span(text, "max", EntityType.PERSON, score=0.95)

    merged = merge_spans([person, email], catalogue=None)
    assert [s.type for s in merged] == [EntityType.EMAIL]


def test_regex_wins_over_the_model_for_the_same_type() -> None:
    """Priority resolution, not the veto: REGEX outranks GLINER for the same
    type regardless of whether the model's span would itself pass a check."""
    text = "Ruf +49 170 1234567 an."
    from_regex = _span(text, "+49 170 1234567", EntityType.PHONE, source=SOURCE_REGEX, score=1.0)
    from_model = _span(text, "170 1234567", EntityType.PHONE, score=0.99)

    merged = merge_spans([from_model, from_regex], catalogue=None)
    assert merged == [from_regex]


def test_longer_span_wins_at_equal_priority() -> None:
    """Priority resolution, not the veto — no catalogue needed."""
    text = "Anna Maria Schmidt kam."
    short = _span(text, "Anna", EntityType.PERSON, score=0.9)
    long = _span(text, "Anna Maria Schmidt", EntityType.PERSON, score=0.9)

    assert merge_spans([short, long], catalogue=None) == [long]


def test_non_overlapping_spans_are_all_kept_and_sorted() -> None:
    """Ordering, not the veto — no catalogue needed."""
    text = "Max Mustermann und Erika Musterfrau."
    b = _span(text, "Erika Musterfrau", EntityType.PERSON)
    a = _span(text, "Max Mustermann", EntityType.PERSON)

    merged = merge_spans([b, a], catalogue=None)
    assert [s.text for s in merged] == ["Max Mustermann", "Erika Musterfrau"]


def test_trailing_punctuation_is_trimmed_off_model_spans() -> None:
    """Trimming, not the veto — no catalogue needed."""
    text = "Das war Max Mustermann."
    start = text.index("Max Mustermann.")
    ragged = Span(
        start=start,
        end=start + len("Max Mustermann."),
        text="Max Mustermann.",
        type=EntityType.PERSON,
        score=0.9,
        source=SOURCE_GLINER,
    )
    merged = merge_spans([ragged], protected=protect(text), catalogue=None)
    assert [s.text for s in merged] == ["Max Mustermann"]
    assert merged[0].verify_against(text)


def test_regex_spans_are_never_trimmed() -> None:
    """Trimming an exact match can only damage it — a trailing dot may belong
    to the address. Not the veto — no catalogue needed."""
    text = "Mail an max@test.de."
    email = _span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)
    assert merge_spans([email], catalogue=None) == [email]


@pytest.mark.parametrize(
    ("surface", "entity_type"),
    [
        ("Systemmail", EntityType.EMAIL),
        ("Rundmail", EntityType.EMAIL),
        ("Durchwahl", EntityType.PHONE),
        ("Aktenzeichen", EntityType.PHONE),
    ],
)
def test_model_proposals_that_are_not_valid_syntax_are_dropped(
    surface: str, entity_type: EntityType
) -> None:
    """Found by the gold-set eval: the model tagged "Systemmail" as an email
    address. Email and phone have decidable syntax, so this is checkable.

    The veto is catalogue-driven (Task 4), so it only fires when a catalogue
    is supplied — this passes the real one to exercise it.
    """
    text = f"Wird per {surface} versendet."
    span = _span(text, surface, entity_type)
    assert merge_spans([span], protected=protect(text), catalogue=load_catalogue()) == []


@pytest.mark.parametrize(
    "surface",
    [
        "+49 (0) 151 4433221",  # 0151 wants eight subscriber digits, not seven
        "+49 151 443322",
        "0151 4433221",
        "+1 555 0100",
    ],
)
def test_phone_shaped_numbers_survive_even_when_the_numbering_plan_rejects_them(
    surface: str,
) -> None:
    """The defect this replaced: requiring plan validity discarded a number the
    model had returned with confidence 1.00, and it went to the LLM in clear.

    Typos, foreign formats and newly issued ranges all fail the plan and all
    still need pseudonymising. This is a claim about the veto itself
    (``phone_shape``, i.e. ``is_plausible_phone``) — it must run, or the test
    proves nothing. Pass the real catalogue.
    """
    text = f"Mobil: {surface}"
    span = _span(text, surface, EntityType.PHONE)
    assert merge_spans([span], protected=protect(text), catalogue=load_catalogue()) == [span]


@pytest.mark.parametrize("surface", ["Durchwahl 12", "Raum 4", "Version 2"])
def test_too_few_digits_is_still_not_a_phone_number(surface: str) -> None:
    """Relaxing plan validity must not open the door to anything with a digit.

    The veto is catalogue-driven (Task 4), so it only fires when a catalogue
    is supplied — this passes the real one to exercise it.
    """
    text = f"Siehe {surface} bitte."
    span = _span(text, surface, EntityType.PHONE)
    assert merge_spans([span], protected=protect(text), catalogue=load_catalogue()) == []


@pytest.mark.parametrize(
    ("surface", "entity_type"),
    [
        ("max@test.de", EntityType.EMAIL),
        ("+49 170 1234567", EntityType.PHONE),
    ],
)
def test_valid_model_proposals_survive_the_rule_check(
    surface: str, entity_type: EntityType
) -> None:
    """The check must not cost recall where the model is right and the regex
    scanner happened to miss it. The check has to actually run for this to
    mean anything, so pass the real catalogue."""
    text = f"Kontakt {surface} bitte."
    span = _span(text, surface, entity_type)
    assert merge_spans([span], protected=protect(text), catalogue=load_catalogue()) == [span]


def test_person_spans_are_not_subject_to_a_syntax_rule() -> None:
    """There is no decidable rule for what is a name, so PERSON is left to the
    model and the confidence threshold.

    A catalogue is passed deliberately: this must exercise the "type has no
    validator" fallthrough inside ``_passes_rule_check``, not the earlier
    ``catalogue is None`` short-circuit — those are different code paths, and
    only a real catalogue tells them apart.
    """
    text = "Herr Systemmail kam."
    span = _span(text, "Systemmail", EntityType.PERSON)
    assert merge_spans([span], protected=protect(text), catalogue=load_catalogue()) == [span]


def test_span_that_trims_to_nothing_is_dropped() -> None:
    """Trimming, not the veto — this never reaches ``_passes_rule_check`` at
    all, since ``_trim`` drops it first. No catalogue needed."""
    text = '"..." sagte er'
    junk = Span(start=1, end=4, text="...", type=EntityType.PERSON, score=0.9, source=SOURCE_GLINER)
    assert merge_spans([junk], protected=protect(text), catalogue=None) == []


def test_hyphenated_names_survive_trimming() -> None:
    """Trimming hyphens would cut German double-barrelled names in half. Not
    the veto — PERSON has no validator — so no catalogue needed."""
    text = "Frau Müller-Lüdenscheidt kam."
    span = _span(text, "Müller-Lüdenscheidt", EntityType.PERSON)
    assert merge_spans([span], protected=protect(text), catalogue=None) == [span]


# --- coreference sweep -----------------------------------------------------


def test_sweep_finds_a_repeat_the_model_missed() -> None:
    text = "Max Mustermann kam. Später ging Max Mustermann wieder."
    protected = protect(text)
    first_only = [_span(text, "Max Mustermann", EntityType.PERSON)]

    extra = coreference_sweep(first_only, protected)
    assert len(extra) == 1
    assert extra[0].source == SOURCE_COREF
    assert extra[0].verify_against(text)
    assert extra[0].start > first_only[0].start


def test_sweep_does_not_reach_into_protected_regions() -> None:
    text = 'Max Mustermann schrieb.\n\n```\nautor = "Max Mustermann"\n```\n'
    protected = protect(text)
    accepted = [_span(text, "Max Mustermann", EntityType.PERSON)]

    assert coreference_sweep(accepted, protected) == []


def test_sweep_respects_word_boundaries_for_names() -> None:
    text = "Ernst kam. Der Ernstfall trat ein."
    protected = protect(text)
    accepted = [_span(text, "Ernst", EntityType.PERSON)]

    assert coreference_sweep(accepted, protected) == []


def test_sweep_matches_emails_case_insensitively() -> None:
    text = "max@test.de und MAX@TEST.DE"
    protected = protect(text)
    accepted = [_span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)]

    extra = coreference_sweep(accepted, protected)
    assert [s.text for s in extra] == ["MAX@TEST.DE"]


def test_sweep_ignores_very_short_surface_forms() -> None:
    text = "Bo kam. Bo ging."
    protected = protect(text)
    accepted = [_span(text, "Bo", EntityType.PERSON)]
    assert coreference_sweep(accepted, protected) == []


def test_sweep_keeps_original_casing_of_the_new_occurrence() -> None:
    text = "max@test.de und MAX@TEST.DE"
    protected = protect(text)
    accepted = [_span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)]
    extra = coreference_sweep(accepted, protected)
    assert extra[0].verify_against(text)


def test_resolve_spans_runs_the_whole_cleanup() -> None:
    """Pipeline mechanics (merge, sweep, merge again) — not the veto. Every
    span here is either PERSON (no validator) or SOURCE_REGEX (never
    vetoed), so no catalogue is needed."""
    text = "Max Mustermann (max@test.de). Rückfragen an Max Mustermann."
    protected = protect(text)
    raw = [
        _span(text, "Max Mustermann", EntityType.PERSON),
        _span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0),
        Span(start=0, end=3, text="Max", type=EntityType.PERSON, score=0.3, source=SOURCE_GLINER),
    ]

    resolved = resolve_spans(protected, raw, catalogue=None)
    assert [s.text for s in resolved] == [
        "Max Mustermann",
        "max@test.de",
        "Max Mustermann",
    ]
    for span in resolved:
        assert span.verify_against(text)


def test_resolve_spans_can_skip_the_sweep() -> None:
    """The ``sweep`` flag, not the veto — PERSON has no validator, so no
    catalogue is needed."""
    text = "Max Mustermann kam. Max Mustermann ging."
    protected = protect(text)
    raw = [_span(text, "Max Mustermann", EntityType.PERSON)]

    assert len(resolve_spans(protected, raw, sweep=False, catalogue=None)) == 1
    assert len(resolve_spans(protected, raw, sweep=True, catalogue=None)) == 2
