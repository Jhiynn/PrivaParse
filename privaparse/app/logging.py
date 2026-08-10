"""PII-safe logging.

A privacy tool that writes the values it was hired to hide into a log file is
broken, and it fails silently — the log looks fine until someone reads it. So
this module does two things:

1. Provides loggers that are safe to use by default.
2. Keeps a bounded registry of values seen during this process and scrubs them
   out of every emitted record, as a backstop for the case where someone
   formats an original value into a message by accident.

The registry is a guard, not a licence. Log placeholders, types, offsets and
counts — never ``original_value`` or ``normalized_value``.
"""

from __future__ import annotations

import logging
import sys
from collections import OrderedDict
from typing import Any

REDACTED = "‹redacted›"
_MAX_TRACKED_SECRETS = 512
_MIN_SECRET_LEN = 4  # below this, scrubbing mangles ordinary words

_LOGGER_NAME = "privaparse"


class SecretRegistry:
    """Bounded, insertion-ordered set of values that must never reach a log."""

    def __init__(self, max_size: int = _MAX_TRACKED_SECRETS) -> None:
        self._values: OrderedDict[str, None] = OrderedDict()
        self._max_size = max_size

    def register(self, value: str | None) -> None:
        if not value or len(value) < _MIN_SECRET_LEN:
            return
        if value in self._values:
            self._values.move_to_end(value)
            return
        self._values[value] = None
        while len(self._values) > self._max_size:
            self._values.popitem(last=False)

    def scrub(self, text: str) -> str:
        if not self._values or not text:
            return text
        for secret in self._values:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def clear(self) -> None:
        self._values.clear()

    def __len__(self) -> int:
        return len(self._values)


_registry = SecretRegistry()


def register_secret(value: str | None) -> None:
    """Mark a value as never-loggable for the remainder of this process."""
    _registry.register(value)


def secret_registry() -> SecretRegistry:
    return _registry


class RedactingFilter(logging.Filter):
    """Scrub every registered secret out of the rendered record."""

    def __init__(self, registry: SecretRegistry | None = None) -> None:
        super().__init__()
        self._registry = registry if registry is not None else _registry

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._registry:
            return True
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - never let logging raise
            return True
        scrubbed = self._registry.scrub(rendered)
        if scrubbed != rendered:
            record.msg = scrubbed
            record.args = ()
        return True


def configure_logging(level: str = "INFO", *, stream: Any = None) -> None:
    """Attach a redacting handler to the ``privaparse`` logger tree."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    if name != _LOGGER_NAME and not name.startswith(f"{_LOGGER_NAME}."):
        name = f"{_LOGGER_NAME}.{name}"
    return logging.getLogger(name)
