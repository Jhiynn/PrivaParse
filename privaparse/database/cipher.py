"""Encryption seam for values at rest.

Phase 1 stores the vault in plaintext. That is a deliberate, documented choice —
but the vault is a permanent database of every real name, address and phone
number the tool has ever seen, which makes it the most valuable thing on the
disk. So every read and write of a stored value goes through this interface from
day one, and turning on encryption later is a swap of one class rather than a
migration of every call site.

Two different guarantees are needed, and they are not the same:

``normalized_value``
    A lookup key — the vault is queried by it. Any real cipher used here must be
    **deterministic**, otherwise equality lookups stop working and the same
    person gets a new placeholder on every document.

``original_value``
    Never queried by content, only read back. A randomized cipher is both
    acceptable and preferable here.

Hence the two separate methods below.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ValueCipher(Protocol):
    """Transforms values on the way into and out of the database."""

    def encrypt_key(self, value: str) -> str:
        """Encrypt a value that will be used as a lookup key (deterministic)."""

    def decrypt_key(self, value: str) -> str: ...

    def encrypt_value(self, value: str) -> str:
        """Encrypt a value that is only ever read back, never searched."""

    def decrypt_value(self, value: str) -> str: ...


class IdentityCipher:
    """Phase 1 default: stores plaintext.

    Present so that the seam is exercised by every code path and every test,
    rather than being an untested branch that only wakes up in Phase 2.
    """

    def encrypt_key(self, value: str) -> str:
        return value

    def decrypt_key(self, value: str) -> str:
        return value

    def encrypt_value(self, value: str) -> str:
        return value

    def decrypt_value(self, value: str) -> str:
        return value

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "IdentityCipher(plaintext)"
