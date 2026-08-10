"""A privacy tool must not leak the values it exists to hide into its own logs."""

from __future__ import annotations

import io
import logging

import pytest

from privaparse.app.logging import (
    REDACTED,
    SecretRegistry,
    configure_logging,
    get_logger,
    register_secret,
    secret_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    secret_registry().clear()
    yield
    secret_registry().clear()


def test_registered_value_never_reaches_the_handler() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    register_secret("Max Mustermann")

    log = get_logger("test")
    log.info("resolved entity for %s", "Max Mustermann")

    output = stream.getvalue()
    assert "Max Mustermann" not in output
    assert REDACTED in output


def test_scrubbing_survives_string_interpolation() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    register_secret("max@test.de")

    get_logger("test").warning(f"could not normalise {'max@test.de'}")

    assert "max@test.de" not in stream.getvalue()


def test_unregistered_text_passes_through() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    register_secret("Max Mustermann")

    get_logger("test").info("replaced 3 spans in [[PERSON_A1]]")

    assert "[[PERSON_A1]]" in stream.getvalue()


def test_short_values_are_not_tracked() -> None:
    """Scrubbing three-character strings would mangle ordinary log text."""
    registry = SecretRegistry()
    registry.register("Max")
    registry.register("")
    registry.register(None)
    assert len(registry) == 0


def test_registry_is_bounded_and_evicts_oldest() -> None:
    registry = SecretRegistry(max_size=3)
    for name in ["aaaa", "bbbb", "cccc", "dddd"]:
        registry.register(name)

    assert len(registry) == 3
    assert registry.scrub("aaaa") == "aaaa"  # evicted
    assert registry.scrub("dddd") == REDACTED


def test_reregistering_refreshes_recency() -> None:
    registry = SecretRegistry(max_size=2)
    registry.register("aaaa")
    registry.register("bbbb")
    registry.register("aaaa")  # touch
    registry.register("cccc")  # evicts bbbb, not aaaa

    assert registry.scrub("aaaa") == REDACTED
    assert registry.scrub("bbbb") == "bbbb"


def test_configure_logging_does_not_stack_handlers() -> None:
    configure_logging("INFO", stream=io.StringIO())
    configure_logging("INFO", stream=io.StringIO())
    assert len(logging.getLogger("privaparse").handlers) == 1
