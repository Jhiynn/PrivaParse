"""Name-to-callable registries for the entity catalogue.

A leaf module on purpose: it imports nothing from ``parser`` or ``app``, so the
catalogue can validate the names a config file uses without dragging the
detection pipeline — and torch — into config loading.

The implementation modules register into it at import time; ``load_builtins``
is what makes that happen without the catalogue importing them by name at
module scope.
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

__all__ = [
    "register_normalizer",
    "register_validator",
    "register_backstop",
    "get_normalizer",
    "get_validator",
    "get_backstop",
    "known_normalizers",
    "known_validators",
    "known_backstops",
    "load_builtins",
]

F = TypeVar("F", bound=Callable)

_NORMALIZERS: dict[str, Callable] = {}
_VALIDATORS: dict[str, Callable] = {}
_BACKSTOPS: dict[str, Callable] = {}


def _make_register(table: dict[str, Callable], kind: str):
    def register(name: str) -> Callable[[F], F]:
        def decorate(function: F) -> F:
            if name in table and table[name] is not function:
                raise ValueError(f"{kind} {name!r} is already registered")
            table[name] = function
            return function

        return decorate

    return register


register_normalizer = _make_register(_NORMALIZERS, "normalizer")
register_validator = _make_register(_VALIDATORS, "validator")
register_backstop = _make_register(_BACKSTOPS, "backstop")


def _make_getter(table: dict[str, Callable], kind: str):
    def get(name: str) -> Callable:
        load_builtins()
        try:
            return table[name]
        except KeyError:
            raise KeyError(f"unknown {kind} {name!r}") from None

    return get


get_normalizer = _make_getter(_NORMALIZERS, "normalizer")
get_validator = _make_getter(_VALIDATORS, "validator")
get_backstop = _make_getter(_BACKSTOPS, "backstop")


def known_normalizers() -> frozenset[str]:
    load_builtins()
    return frozenset(_NORMALIZERS)


def known_validators() -> frozenset[str]:
    load_builtins()
    return frozenset(_VALIDATORS)


def known_backstops() -> frozenset[str]:
    load_builtins()
    return frozenset(_BACKSTOPS)


_loaded = False

#: Re-entrant on purpose. Two different callers have to be kept apart here and
#: they need opposite treatment: a *concurrent* caller must wait until the
#: import has finished, while the *importing thread itself* must be let
#: straight back in, because the modules being imported call back into this
#: one to register. A plain Lock would serve the first and deadlock the second.
_import_lock = threading.RLock()


def load_builtins() -> None:
    """Import the modules that populate the registries. Idempotent.

    The flag is set *after* the imports, not before. Setting it first also
    keeps a re-entrant call from recursing -- but it hands every thread that
    arrives mid-import a registry that is still empty, and the caller has no
    way to tell that from a registry with nothing in it. Downstream it surfaces
    as ``CatalogueError: EMAIL: unknown backstop 'email' (known: )`` on a
    request whose only mistake was arriving at the same moment as another one.
    The gateway made that ordinary: every request detects in a worker thread,
    so a cold process gets a dozen threads here at once.
    """
    global _loaded
    if _loaded:
        return
    with _import_lock:
        if _loaded:
            return
        from privaparse.parser import backstops, normalizer, validators  # noqa: F401

        _loaded = True
