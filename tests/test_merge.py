"""Overlap resolution and the coreference sweep."""

from __future__ import annotations

import dataclasses

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.engine import PrivaParseEngine
from privaparse.parser.detector import StaticDetector
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


def test_the_longer_regex_email_wins_over_a_shorter_overlapping_person_span() -> None:
    """This is the local-part leak, caught live: a PERSON span from the model
    over just "max" used to lose to a type rank that put EMAIL above PERSON.
    Deleting that rank and ranking by source alone (this task's first attempt)
    was also wrong — GLINER outranks REGEX, so the *shorter* PERSON span won
    regardless of length, and "@test.de" would have shipped in clear. Length
    has to lead the sort for the longer, correct span to survive; source only
    speaks when two overlapping spans are the same length (see
    test_the_model_wins_between_equal_length_overlapping_spans).
    test_a_person_shaped_local_part_does_not_leak_the_rest_of_the_address
    proves this holds at the rewritten-text level, not just here at the span
    level.

    Priority resolution, not the veto — no catalogue needed. The email span
    is SOURCE_REGEX (never vetoed) and PERSON has no validator either way.
    """
    text = "Kontakt max@test.de bitte."
    email = _span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)
    person = _span(text, "max", EntityType.PERSON, score=0.95)

    merged = merge_spans([person, email], catalogue=None)
    assert [s.type for s in merged] == [EntityType.EMAIL]


def test_the_longer_regex_phone_wins_over_a_shorter_overlapping_model_fragment() -> None:
    """Same lesson as the email case above, for two spans that share a claimed
    type: the model's span is missing the country code, the regex span has
    it, and length decides before source is ever consulted — a shorter model
    span must not win just because GLINER outranks REGEX. Because these two
    spans differ in length by construction (that is the whole scenario), this
    pair cannot demonstrate the source tie-break; that needs spans of equal
    length, which is what test_the_model_wins_between_equal_length_overlapping_spans
    is for.

    Priority resolution, not the veto — no catalogue needed, since PHONE's
    veto never runs without one."""
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
    """Repeat-finding, not sweep-mode selection. PERSON's no-catalogue
    fallback is "word", the same mode the catalogue itself gives PERSON, so
    no catalogue is needed to see the repeat found."""
    text = "Max Mustermann kam. Später ging Max Mustermann wieder."
    protected = protect(text)
    first_only = [_span(text, "Max Mustermann", EntityType.PERSON)]

    extra = coreference_sweep(first_only, protected, catalogue=None)
    assert len(extra) == 1
    assert extra[0].source == SOURCE_COREF
    assert extra[0].verify_against(text)
    assert extra[0].start > first_only[0].start


def test_sweep_does_not_reach_into_protected_regions() -> None:
    """Protected-region masking, not sweep-mode selection — every non-off
    mode is masked the same way, so no catalogue is needed here."""
    text = 'Max Mustermann schrieb.\n\n```\nautor = "Max Mustermann"\n```\n'
    protected = protect(text)
    accepted = [_span(text, "Max Mustermann", EntityType.PERSON)]

    assert coreference_sweep(accepted, protected, catalogue=None) == []


def test_sweep_respects_word_boundaries_for_names() -> None:
    """Boundary enforcement is what the catalogue's "word" mode means for
    PERSON, so this exercises the catalogue rather than leaning on the
    no-catalogue fallback that happens to default to the same mode."""
    text = "Ernst kam. Der Ernstfall trat ein."
    protected = protect(text)
    accepted = [_span(text, "Ernst", EntityType.PERSON)]

    assert coreference_sweep(accepted, protected, catalogue=load_catalogue()) == []


def test_sweep_matches_emails_case_insensitively() -> None:
    """EMAIL's "icase" mode is catalogue-configured; the no-catalogue
    fallback is "word", which is case sensitive and would fail this for the
    wrong reason. The real catalogue is required for the assertion to mean
    anything."""
    text = "max@test.de und MAX@TEST.DE"
    protected = protect(text)
    accepted = [_span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)]

    extra = coreference_sweep(accepted, protected, catalogue=load_catalogue())
    assert [s.text for s in extra] == ["MAX@TEST.DE"]


def test_sweep_ignores_very_short_surface_forms() -> None:
    """The length filter runs before a sweep pattern is even chosen, so no
    catalogue is needed to see a two-character surface rejected."""
    text = "Bo kam. Bo ging."
    protected = protect(text)
    accepted = [_span(text, "Bo", EntityType.PERSON)]
    assert coreference_sweep(accepted, protected, catalogue=None) == []


def test_sweep_keeps_original_casing_of_the_new_occurrence() -> None:
    """Same reasoning as the case-insensitivity test above: EMAIL's "icase"
    mode only applies with the real catalogue."""
    text = "max@test.de und MAX@TEST.DE"
    protected = protect(text)
    accepted = [_span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)]
    extra = coreference_sweep(accepted, protected, catalogue=load_catalogue())
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


# --- the model decides ------------------------------------------------------


def test_model_span_wins_an_overlap_against_a_backstop() -> None:
    """Model and backstop claim the identical span — equal length by
    construction — so source is what decides, and the model wins."""
    text = "Kontakt: max.mustermann@test.de"
    protected = protect(text)
    model = Span(9, 31, "max.mustermann@test.de", "EMAIL", 0.9, SOURCE_GLINER)
    backstop = Span(9, 31, "max.mustermann@test.de", "EMAIL", 1.0, SOURCE_REGEX)

    kept = merge_spans([backstop, model], protected=protected, catalogue=load_catalogue())
    assert len(kept) == 1
    assert kept[0].source == SOURCE_GLINER


def test_the_model_wins_between_equal_length_overlapping_spans() -> None:
    """The dedicated equal-length case: two overlapping spans of identical
    length, one from each source, deliberately not at the same start/end (a
    coincidence of matching offsets could not be mistaken for the length
    comparison actually running). Length is tied, so priority breaks it and
    the model's span survives — this is what "the model decides" means once
    length leads the sort, and it needs its own case that cannot pass by
    accident the way an unequal-length pair could (length alone would decide
    those regardless of which source said what)."""
    text = "Kontakt ABCDEFGH bitte."
    start = text.index("ABCDEFGH")
    model = Span(start, start + 4, text[start : start + 4], "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(
        start + 2, start + 6, text[start + 2 : start + 6], "PERSON", 1.0, SOURCE_REGEX
    )
    assert model.length == backstop.length  # the property this test depends on

    kept = merge_spans([backstop, model], catalogue=None)
    assert len(kept) == 1
    assert kept[0].source == SOURCE_GLINER


def test_backstop_and_model_coexist_when_they_do_not_overlap() -> None:
    """Not an overlap-precedence test — these two spans never touch, so
    neither length nor source ever gets consulted; both are kept regardless.
    Renamed from a name that implied it tested "the backstop surviving an
    overlap", which it never did (its two spans are 13 characters apart) —
    it tests the recall backstop filling a gap the model left, which is a
    real and separate property worth keeping, just not this file's overlap
    machinery."""
    text = "Anna schreibt an max@test.de"
    protected = protect(text)
    model = Span(0, 4, "Anna", "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(17, 28, "max@test.de", "EMAIL", 1.0, SOURCE_REGEX)

    kept = merge_spans([model, backstop], protected=protected, catalogue=load_catalogue())
    assert {s.source for s in kept} == {SOURCE_GLINER, SOURCE_REGEX}


def test_a_person_shaped_local_part_does_not_leak_the_rest_of_the_address(settings) -> None:
    """Reproduces a real leak, found in review: the model tags just the local
    part "max" as PERSON, the backstop finds the whole address. Before length
    led the sort, the shorter PERSON span won on source rank alone, and the
    rewritten document read "Kontakt: [[PERSON_..]]@test.de" — the domain
    shipped in clear. ``merge_spans``'s surviving-span list alone would not
    show this: a reader has to know the email span was 9-20 and the person
    span 9-12 to notice the wrong one won. The leak is only visible in the
    document text, so this goes through the real engine instead of calling
    merge_spans directly.
    """
    text = "Kontakt: max@test.de"
    model = Span(9, 12, "max", "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(9, 20, "max@test.de", "EMAIL", 1.0, SOURCE_REGEX)

    engine = PrivaParseEngine(
        settings, detector=StaticDetector([model, backstop]), configure_logs=False
    )
    try:
        result = engine.pseudonymize(text)
    finally:
        engine.close()

    assert "@test.de" not in result.text
    assert "[[EMAIL_" in result.text


def test_sweep_mode_off_finds_no_repeats() -> None:
    """``PlaceholderType`` is frozen and ``slots=True``, so it has no
    ``__dict__`` — narrow it with ``dataclasses.replace`` rather than reaching
    for instance state that does not exist."""
    catalogue = load_catalogue()
    text = "Berlin ist gross. Berlin ist teuer."
    protected = protect(text)
    accepted = [Span(0, 6, "Berlin", "PERSON", 0.9, SOURCE_GLINER)]

    with_word = coreference_sweep(accepted, protected, catalogue=catalogue)
    assert len(with_word) == 1

    off = dataclasses.replace(catalogue.types["PERSON"], sweep="off")
    narrowed = dataclasses.replace(catalogue, types={**catalogue.types, "PERSON": off})
    assert coreference_sweep(accepted, protected, catalogue=narrowed) == []
