"""Settings-level validation for fields whose bad values would otherwise
surface only much later, deep in a request path -- see `test_device.py` for
the same idea applied to `device`."""

from __future__ import annotations

import pytest

from privaparse.app.config import Settings


def test_a_key_that_cannot_round_trip_to_bytes_is_rejected_at_load() -> None:
    """`os.environ` decodes with `surrogateescape`, so `PRIVAPARSE_API_KEY`
    set straight from raw random bytes (`head -c 32 /dev/urandom`, forgetting
    to base64 or hex it first) arrives here as a `str` containing a lone
    surrogate. Left unchecked, the gateway's auth middleware would encode
    this value on every request and 500 every time with nothing explaining
    why. Refusing it here, at startup, is where the operator can still act on
    the message.
    """
    with pytest.raises(ValueError) as excinfo:
        Settings(api_key="abc\udcffdef")
    message = str(excinfo.value)
    assert "PRIVAPARSE_API_KEY" in message
    assert "base64" in message or "hex" in message
