"""The restoration rules a streamed answer needs, tested with plain strings.

The hold-back is the whole idea. `[[PERSON_A1]]` reaches the gateway as any
number of pieces -- `[[PER`, `SON_`, `A1]]` -- and a restorer that looked at
each piece alone would find nothing to restore in any of them and hand the
caller a placeholder. So the tail of the buffer is held back until it either
completes or can no longer become a placeholder.

None of this knows about SSE or about either protocol, which is why it is
testable without framing an event: what the two relays do with these rules is
in `test_stream.py` and `test_responses_stream.py`.

There is no pytest-asyncio in this project, so the coroutines are driven with
`asyncio.run` rather than an async test function.
"""

from __future__ import annotations

import asyncio

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.gateway.restore import (
    HoldBack,
    guarded_restore,
    max_placeholder_length,
)

PLACEHOLDER = "[[PERSON_A1]]"
REAL = "Max Mustermann"
#: The same placeholder as a model mangles it -- one bracket pair, not two.
#: Only the tolerant matcher restores this, and only if it arrives whole.
MANGLED = "[PERSON_A1]"


# --- the hold-back ---------------------------------------------------------


@pytest.mark.parametrize("cut", range(1, len(PLACEHOLDER)))
def test_a_placeholder_is_never_released_in_two_pieces(cut: int):
    """The property the whole design rests on: whatever the split, no release
    ever contains part of a placeholder and not the rest."""
    hold = HoldBack(max_hold=40)
    released = [hold.feed(PLACEHOLDER[:cut]), hold.feed(PLACEHOLDER[cut:]), hold.flush()]

    assert "".join(released) == PLACEHOLDER
    for piece in released:
        assert PLACEHOLDER in piece or "[[" not in piece


def test_nothing_is_held_when_no_bracket_appears():
    hold = HoldBack(max_hold=40)
    assert hold.feed("Hallo, alles gut?") == "Hallo, alles gut?"
    assert hold.flush() == ""


def test_a_bracket_that_cannot_become_a_placeholder_is_released_at_once():
    """Markdown, a wiki link, a Python list of lists: `[[` is not rare."""
    hold = HoldBack(max_hold=40)
    assert hold.feed("siehe [[dies hier]]") == "siehe [[dies hier]]"


def test_a_trailing_bracket_is_held_until_it_is_settled():
    hold = HoldBack(max_hold=40)
    assert hold.feed("Hallo [[") == "Hallo "
    assert hold.feed("wer?") == "[[wer?"


def test_an_endless_run_of_capitals_is_released_at_the_cap():
    """Held text that stays grammatical forever would stall the stream. The cap
    is what bounds it -- past the longest placeholder the vault can build, the
    text cannot be one."""
    hold = HoldBack(max_hold=20)
    assert hold.feed("[[" + "A" * 30) == "[[" + "A" * 30


def test_flush_returns_a_half_finished_placeholder_rather_than_dropping_it():
    hold = HoldBack(max_hold=40)
    assert hold.feed("Hallo [[PERSON_") == "Hallo "
    assert hold.flush() == "[[PERSON_"


@pytest.mark.parametrize("cut", range(1, len(MANGLED)))
def test_the_tolerant_hold_back_keeps_a_single_bracket_candidate_whole(cut: int):
    """What the strict pattern releases in pieces, the tolerant one does not.

    A model that drops a bracket emits `[PERSON_A1]`. The strict hold-back only
    ever protects `[[`, so it hands that over split across events -- and a
    fragment is unrestorable however tolerant the matcher downstream is. The
    widened pattern is what lets the mangled form arrive in one piece.
    """
    hold = HoldBack(max_hold=40, lenient=True)
    released = [hold.feed(MANGLED[:cut]), hold.feed(MANGLED[cut:]), hold.flush()]

    assert "".join(released) == MANGLED
    for piece in released:
        assert MANGLED in piece or "[" not in piece


def test_the_cap_covers_the_longest_placeholder_the_catalogue_can_render():
    catalogue = load_catalogue()
    longest = max(len(placeholder.name) for placeholder in catalogue.enabled)
    assert max_placeholder_length(catalogue) > longest + len("[[_A1]]")


# --- asking the vault, and never dying of it -------------------------------


def _restored(text: str, *, restore, lenient: bool = False) -> str:
    return asyncio.run(guarded_restore(restore, lenient=lenient)(text))


def test_text_with_nothing_to_restore_never_reaches_the_vault():
    """A token-by-token stream would otherwise hit the database once per token,
    for text that plainly holds nothing to restore."""
    asked: list[str] = []

    async def restore(text: str) -> str:
        asked.append(text)
        return text

    assert _restored("Hallo, alles gut?", restore=restore) == "Hallo, alles gut?"
    assert asked == []


def test_the_tolerant_matcher_widens_what_is_worth_asking_about():
    """`contains_placeholder` wants `[[...]]`, so the cheap skip above would
    otherwise skip exactly the mangled forms the tolerant matcher exists to
    catch -- and the setting would look like it did nothing."""
    asked: list[str] = []

    async def restore(text: str) -> str:
        asked.append(text)
        return text.replace(MANGLED, REAL)

    assert _restored(f"Hallo {MANGLED}", restore=restore, lenient=True) == f"Hallo {REAL}"
    assert asked == [f"Hallo {MANGLED}"]
    # And the same text is not worth a lookup with the matcher off.
    asked.clear()
    assert _restored(f"Hallo {MANGLED}", restore=restore) == f"Hallo {MANGLED}"
    assert asked == []


def test_a_vault_failure_leaves_the_placeholder_standing():
    """The answer already exists and has been paid for. Raising here would
    truncate it; a placeholder only costs the caller readability."""

    async def exploding(text: str) -> str:
        raise RuntimeError("the vault is unavailable")

    assert _restored(f"Hallo {PLACEHOLDER}", restore=exploding) == f"Hallo {PLACEHOLDER}"
