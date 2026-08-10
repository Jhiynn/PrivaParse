"""Overlap resolution and the coreference sweep."""

from __future__ import annotations

import dataclasses

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.engine import PrivaParseEngine
from privaparse.parser.detector import StaticDetector
from privaparse.parser.markdown import protect
from privaparse.parser.merge import coreference_sweep, merge_spans, resolve_spans, span_priority
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


def test_a_person_span_fully_inside_an_overlapping_email_is_dropped() -> None:
    """This is the local-part leak, caught live: a PERSON span from the model
    over just "max" used to lose to a type rank that put EMAIL above PERSON,
    then (this task's second attempt) to a plain source rank that let the
    *shorter* PERSON span win regardless, then (third attempt) to a length
    comparison that happened to get this specific case right but was wrong
    in general (see the straddling tests below). None of those are the
    actual mechanism now: PERSON(8,11) sits entirely inside EMAIL(8,19), so
    ``_trim_to_exact_spans`` removes it before length or source is ever
    consulted — a local part is not a second entity, not a competitor that
    lost a contest.
    test_a_person_shaped_local_part_does_not_leak_the_rest_of_the_address
    proves this holds at the rewritten-text level, not just here at the span
    level.

    Priority resolution, not the veto — no catalogue needed. The email span
    is SOURCE_REGEX (never vetoed) and PERSON has no validator either way.
    """
    text = "Kontakt max@test.de bitte."
    email = _span(text, "max@test.de", EntityType.EMAIL, source=SOURCE_REGEX, score=1.0)
    person = _span(text, "max", EntityType.PERSON, score=0.95)
    assert person.start >= email.start and person.end <= email.end  # the property this relies on

    merged = merge_spans([person, email], catalogue=None)
    assert merged == [email]


def test_a_model_phone_fragment_fully_inside_a_regex_match_is_dropped() -> None:
    """Same lesson as the email case above, for two spans sharing a claimed
    type: the model's span is missing the country code and sits entirely
    inside the regex span that has it (both end at the same index), so it is
    removed by the boundary rule before any sort runs — not because it lost
    a length or source comparison, but because there is nothing of it left
    outside the exact span.

    Priority resolution, not the veto — no catalogue needed, since PHONE's
    veto never runs without one."""
    text = "Ruf +49 170 1234567 an."
    from_regex = _span(text, "+49 170 1234567", EntityType.PHONE, source=SOURCE_REGEX, score=1.0)
    from_model = _span(text, "170 1234567", EntityType.PHONE, score=0.99)
    assert from_model.start >= from_regex.start and from_model.end <= from_regex.end

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


def test_sweep_exact_mode_does_not_splice_into_a_longer_digit_run() -> None:
    """``exact`` used to compile with no boundary at all, so a short
    structured value could match as a substring of an unrelated longer digit
    run. Reproduces the review's own POSTAL_CODE example: sweeping "12345"
    must not find it inside "912345678", a wholly unrelated customer number
    that just happens to contain those five digits in the middle."""
    text = "PLZ 12345 Berlin. Kundennummer 912345678 bitte angeben."
    protected = protect(text)
    accepted = [_span(text, "12345", "POSTAL_CODE", source=SOURCE_REGEX, score=1.0)]

    extra = coreference_sweep(accepted, protected, catalogue=load_catalogue())
    assert extra == []


def test_sweep_exact_mode_does_not_splice_cvv_into_a_longer_digit_run() -> None:
    """Same failure mode as above, for CARD_CVV — and CARD_CVV is
    reversible: false, so a spliced fragment here could never be restored
    even for the document that legitimately contains it."""
    text = "CVV 123 auf der Karte. Bestellnummer 91237 im System."
    protected = protect(text)
    accepted = [_span(text, "123", "CARD_CVV", source=SOURCE_REGEX, score=1.0)]

    extra = coreference_sweep(accepted, protected, catalogue=load_catalogue())
    assert extra == []


def test_sweep_exact_mode_still_finds_a_genuine_repeat() -> None:
    """The boundary fix must not turn into a false negative: a postal code
    that legitimately appears twice, each time as its own standalone token,
    still has to be found and masked at the second occurrence."""
    text = "PLZ 12345 Berlin. Zur Bestätigung erneut: 12345 auf dem Umschlag."
    protected = protect(text)
    accepted = [_span(text, "12345", "POSTAL_CODE", source=SOURCE_REGEX, score=1.0)]

    extra = coreference_sweep(accepted, protected, catalogue=load_catalogue())
    assert len(extra) == 1
    assert extra[0].text == "12345"
    assert extra[0].verify_against(text)
    assert extra[0].start > accepted[0].start


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


def test_an_identical_model_span_is_absorbed_by_the_exact_span_it_matches() -> None:
    """Model and backstop claim the identical range. This is the degenerate
    case of the boundary rule, not a priority contest: the model span's
    "part outside the exact span" is empty (there is nothing outside an
    identical range), so it is absorbed and only the exact span survives.
    Source priority is not consulted — there is no tie to break, because the
    model span never becomes a candidate for the sort at all."""
    text = "Kontakt: max.mustermann@test.de"
    protected = protect(text)
    model = Span(9, 31, "max.mustermann@test.de", "EMAIL", 0.9, SOURCE_GLINER)
    backstop = Span(9, 31, "max.mustermann@test.de", "EMAIL", 1.0, SOURCE_REGEX)

    kept = merge_spans([backstop, model], protected=protected, catalogue=load_catalogue())
    assert kept == [backstop]


def test_span_priority_ranks_gliner_above_regex_above_coref() -> None:
    """A direct check of the ordering `_SOURCE_RANK` encodes, independent of
    whether an overlap scenario exercises it. It used to double as a claim
    about GLINER-versus-REGEX overlaps specifically (deleted along with
    ``test_the_model_wins_between_equal_length_overlapping_spans`` above):
    that pairing can no longer reach the sort at all, since
    ``_trim_to_exact_spans`` resolves every GLiNER/regex overlap before the
    sort key is ever computed. What is left to test at that level is
    model-versus-model and regex-versus-regex, which do not depend on the
    relative *values* in `_SOURCE_RANK`, only on spans from the same source
    tying. The ordering itself still needs pinning somewhere; here."""
    gliner = Span(0, 4, "Anna", "PERSON", 0.9, SOURCE_GLINER)
    regex = Span(0, 4, "Anna", "PERSON", 0.9, SOURCE_REGEX)
    coref = Span(0, 4, "Anna", "PERSON", 0.9, SOURCE_COREF)
    assert span_priority(gliner) > span_priority(regex) > span_priority(coref)


def test_a_straddling_model_span_below_the_trim_floor_is_dropped() -> None:
    """A partial overlap, not a full one: the model span extends two
    characters past the exact span's start, which is below
    ``_MIN_TRIM_LENGTH`` (3). A trimmed remnant that short is more likely
    noise than a name, so it is dropped rather than kept as a two-character
    fragment — the same floor a full-containment overlap hits by having
    nothing left at all, just reached from partial overlap instead."""
    text = "Kontakt ABCDEFGH bitte."
    start = text.index("ABCDEFGH")
    model = Span(start, start + 4, text[start : start + 4], "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(
        start + 2, start + 6, text[start + 2 : start + 6], "EMAIL", 1.0, SOURCE_REGEX
    )

    kept = merge_spans([backstop, model], catalogue=None)
    assert kept == [backstop]


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
    part "max" as PERSON, the backstop finds the whole address. Neither
    source rank alone nor length-first sorting got this right — both let the
    shorter, wrong-shaped PERSON span survive on its own terms, and the
    rewritten document read "Kontakt: [[PERSON_..]]@test.de", domain in
    clear. What actually fixes it: PERSON(9,12) sits entirely inside
    EMAIL(9,20), so the boundary rule removes it before any sort runs — a
    local part is not a second entity to compete with. ``merge_spans``'s
    surviving-span list alone would not show the leak: a reader has to know
    the email span was 9-20 and the person span 9-12 to notice the wrong one
    won. It is only visible in the document text, so this goes through the
    real engine instead of calling merge_spans directly. This is one of the
    two exact outcomes specified when the boundary rule was ruled in.
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

    assert result.text == "Kontakt: [[EMAIL_A1]]"


def test_a_straddling_model_span_keeps_its_larger_remainder() -> None:
    """The untested direction: round 2's length-first sort let a *longer*
    model span simply win an overlap outright, which is exactly how
    PERSON(0,23) used to displace EMAIL(20,31) entirely and leak the domain
    — nothing pinned that a longer model span does not just win outright
    any more. It is trimmed to fit around the exact span instead:
    PERSON(0,23) keeps its larger remainder (0,20), punctuation-trimmed to
    (0,19), while EMAIL(20,31) survives untouched — both kept, non
    -overlapping, neither displacing the other. This is the other of the
    two exact outcomes specified when the boundary rule was ruled in, at
    the span level rather than the rewritten-text level.
    """
    text = "Herr Max Mustermann max@test.de"
    model = Span(0, 23, text[0:23], "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(20, 31, text[20:31], "EMAIL", 1.0, SOURCE_REGEX)
    assert model.length > backstop.length  # the direction round 2 never tested

    kept = merge_spans([backstop, model], protected=protect(text), catalogue=None)
    assert [(s.start, s.end, s.text, s.source) for s in kept] == [
        (0, 19, "Herr Max Mustermann", SOURCE_GLINER),
        (20, 31, "max@test.de", SOURCE_REGEX),
    ]


def test_a_straddling_model_span_end_to_end(settings) -> None:
    """The rewritten-text half of the test above — the two exact outcomes
    specified when the boundary rule was ruled in were both given as
    document text, not span lists, so both get an end-to-end test."""
    text = "Herr Max Mustermann max@test.de"
    model = Span(0, 23, text[0:23], "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(20, 31, text[20:31], "EMAIL", 1.0, SOURCE_REGEX)

    engine = PrivaParseEngine(
        settings, detector=StaticDetector([model, backstop]), configure_logs=False
    )
    try:
        result = engine.pseudonymize(text)
    finally:
        engine.close()

    assert result.text == "[[PERSON_A1]] [[EMAIL_A2]]"


def test_a_model_span_keeps_the_larger_side_when_an_exact_span_splits_it() -> None:
    """None of the straddling cases above have room on both sides of the
    exact span — this is the one where trimming genuinely produces two
    candidate remainders, and the point of "largest part" rather than "all
    parts": a Span is one contiguous range, so keeping both would invent a
    second span the model never proposed. The exact span sits in the middle
    with 5 characters to its left and 16 to its right; the right side
    survives."""
    text = "Kontakt ABCDEFGHIJKLMNOPQRSTUVWXYZ bitte."
    model = Span(8, 34, text[8:34], "PERSON", 0.9, SOURCE_GLINER)
    exact = Span(13, 18, text[13:18], "EMAIL", 1.0, SOURCE_REGEX)

    kept = merge_spans([exact, model], protected=protect(text), catalogue=None)
    assert [(s.start, s.end, s.text, s.source) for s in kept] == [
        (13, 18, "FGHIJ", SOURCE_REGEX),
        (18, 34, "KLMNOPQRSTUVWXYZ", SOURCE_GLINER),
    ]


def test_a_model_span_is_trimmed_against_every_exact_span_it_overlaps() -> None:
    """``_largest_remainder`` folds over every exact span the model span
    overlaps, not just the first one found — two backstops firing close
    together (an email and a phone number in the same sentence) must both
    carve into the same over-broad model span, not just whichever the
    implementation happened to check first. Both survive whole either way;
    what matters here is that *both* cuts land, not just one.

    The leading "Kontakt: " piece also survives, at 9 characters before its
    trailing colon and space are stripped by the ordinary punctuation trim,
    7 after — a real if slightly absurd side effect of a German word for
    "contact" being long enough to clear ``_MIN_TRIM_LENGTH`` on its own.
    Asserted explicitly rather than hidden behind a superset check, since a
    reader guessing at this test's outcome would not expect a third span.
    """
    text = "Kontakt: max@test.de oder +49 170 1234567 laut Herr Mustermann."
    email_start = text.index("max@test.de")
    email_end = email_start + len("max@test.de")
    phone_start = text.index("+49 170 1234567")
    phone_end = phone_start + len("+49 170 1234567")
    surname_end = text.index(" laut")

    # One fuzzy PERSON span the model drew across the whole introduction,
    # swallowing both the email and the phone number along the way.
    model = Span(0, surname_end, text[0:surname_end], "PERSON", 0.9, SOURCE_GLINER)
    email = Span(email_start, email_end, text[email_start:email_end], "EMAIL", 1.0, SOURCE_REGEX)
    phone = Span(phone_start, phone_end, text[phone_start:phone_end], "PHONE", 1.0, SOURCE_REGEX)

    kept = merge_spans([model, email, phone], protected=protect(text), catalogue=None)
    assert [(s.start, s.end, s.type, s.text) for s in kept] == [
        (0, 7, "PERSON", "Kontakt"),
        (9, 20, "EMAIL", "max@test.de"),
        (26, 41, "PHONE", "+49 170 1234567"),
    ]


def test_sweep_fails_closed_for_a_type_the_catalogue_does_not_recognise() -> None:
    """merge.py used to fall back to "word" sweeping for any type the
    catalogue did not itself recognise, inconsistent with EntityResolver,
    which fails closed on the identical condition (Catalogue.get raises for
    an unknown type). Harmless in the shipped pipeline because the resolver
    would already have aborted earlier for the same span — but a type this
    catalogue does not know finding a repeat here at all is proof the old
    fallback would have swept where it had no business guessing. A real
    catalogue is required to exercise the "recognised vs not" distinction at
    all: with catalogue=None every type falls back to "word" deliberately
    (see coreference_sweep's docstring), so this needs the genuine article.
    """
    catalogue = load_catalogue()
    text = "Foo kam. Foo ging."
    accepted = [Span(0, 3, "Foo", "NOT_A_REAL_CATALOGUE_TYPE", 0.9, SOURCE_GLINER)]

    assert coreference_sweep(accepted, protect(text), catalogue=catalogue) == []


def test_email_surviving_intact_lets_the_sweep_find_its_later_repeat(settings) -> None:
    """The sweep regression this closes: when a long PERSON span used to
    displace EMAIL entirely (round 2's length-first bug), EMAIL never became
    an accepted span, so coreference_sweep had no EMAIL surface to look for
    at all — icase matching was never broken, there was simply nothing left
    to sweep with it. With EMAIL surviving intact once the PERSON span is
    trimmed around it instead of eliminated, the sweep runs against a real
    EMAIL span again and the later, differently-cased repeat is found and
    masked with the same placeholder.
    """
    text = "Max Mustermann max@test.de schreibt. Antwort bitte an MAX@TEST.DE."
    email_start = text.index("max@test.de")
    email_end = email_start + len("max@test.de")
    model = Span(0, email_end, text[0:email_end], "PERSON", 0.9, SOURCE_GLINER)
    backstop = Span(
        email_start, email_end, text[email_start:email_end], "EMAIL", 1.0, SOURCE_REGEX
    )

    engine = PrivaParseEngine(
        settings, detector=StaticDetector([model, backstop]), configure_logs=False
    )
    try:
        result = engine.pseudonymize(text)
    finally:
        engine.close()

    assert "MAX@TEST.DE" not in result.text
    assert result.text == (
        "[[PERSON_A1]] [[EMAIL_A2]] schreibt. Antwort bitte an [[EMAIL_A2]]."
    )


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
