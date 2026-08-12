"""The registry's lazy import, under one thread and under many.

The gateway is what made this matter. Every request runs detection in a worker
thread, so on a cold process several threads reach `load_builtins()` at the
same instant -- and a guard that keeps a *re-entrant* call from recursing is
not the same thing as a guard that keeps a *concurrent* call from reading a
half-filled table.
"""

from __future__ import annotations

import sys
import threading

import pytest

import privaparse.parser as parser_package
from privaparse.parser import registry

_BUILTIN_MODULES = ("backstops", "normalizer", "validators")


@pytest.fixture()
def cold_registry():
    """Put the registry back to how it looks in a process that just started.

    Reaches into module privates because there is no other way to get a second
    cold start inside one interpreter, and a cold start is the only moment the
    race exists. Everything is restored afterwards.
    """
    saved = (
        dict(registry._NORMALIZERS),
        dict(registry._VALIDATORS),
        dict(registry._BACKSTOPS),
        registry._loaded,
    )
    saved_modules = {
        name: sys.modules[f"privaparse.parser.{name}"]
        for name in _BUILTIN_MODULES
        if f"privaparse.parser.{name}" in sys.modules
    }

    def reset() -> None:
        # The tables are cleared as well as the modules evicted: re-importing a
        # module builds new function objects, and `register_*` refuses to
        # rebind a name to a different function.
        registry._NORMALIZERS.clear()
        registry._VALIDATORS.clear()
        registry._BACKSTOPS.clear()
        registry._loaded = False
        for name in _BUILTIN_MODULES:
            # Both, and the second one is the one that is easy to miss:
            # `from privaparse.parser import backstops` is satisfied by the
            # attribute on the parent package without consulting sys.modules
            # at all, so evicting the module alone leaves a "cold" registry
            # that never refills -- which looks exactly like the bug under
            # test and is not it.
            sys.modules.pop(f"privaparse.parser.{name}", None)
            if hasattr(parser_package, name):
                delattr(parser_package, name)

    reset()
    yield reset

    for name in _BUILTIN_MODULES:
        sys.modules.pop(f"privaparse.parser.{name}", None)
    for name, module in saved_modules.items():
        sys.modules[f"privaparse.parser.{name}"] = module
        setattr(parser_package, name, module)
    registry._NORMALIZERS.clear()
    registry._NORMALIZERS.update(saved[0])
    registry._VALIDATORS.clear()
    registry._VALIDATORS.update(saved[1])
    registry._BACKSTOPS.clear()
    registry._BACKSTOPS.update(saved[2])
    registry._loaded = saved[3]


def test_a_cold_registry_fills_up_on_first_use(cold_registry):
    assert "email" in registry.known_backstops()
    assert "casefold" in registry.known_normalizers()


def test_no_thread_ever_sees_a_half_filled_registry(cold_registry):
    """Every thread must get the whole table or wait for it -- never a partial.

    Without a lock, the first thread in sets the "already loaded" flag and then
    starts importing; every thread arriving during that import is waved through
    to a table that is still empty. Downstream that surfaces as
    `CatalogueError: EMAIL: unknown backstop 'email' (known: )`, a 500 on a
    request that did nothing wrong except arrive at the same moment as another.
    """
    threads = 32
    start = threading.Barrier(threads)
    seen: list[frozenset[str]] = []
    guard = threading.Lock()

    def read() -> None:
        start.wait()
        backstops = registry.known_backstops()
        with guard:
            seen.append(backstops)

    workers = [threading.Thread(target=read) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    empty = [table for table in seen if not table]
    partial = [table for table in seen if table and "email" not in table]
    assert not empty, f"{len(empty)}/{threads} threads read an empty backstop registry"
    assert not partial, f"{len(partial)}/{threads} threads read a partial backstop registry"


def test_a_reentrant_call_during_the_import_does_not_deadlock(cold_registry):
    """The reason the flag was set early in the first place.

    A module being imported by `load_builtins` can call back into the registry
    -- that is what the decorators do. Whatever keeps concurrent callers out
    has to let the importing thread back in, or the first import deadlocks
    against itself.
    """
    finished = threading.Event()

    def load() -> None:
        registry.load_builtins()
        # Re-entrant by construction: already inside, ask again.
        registry.known_backstops()
        finished.set()

    worker = threading.Thread(target=load, daemon=True)
    worker.start()
    worker.join(timeout=30)

    assert finished.is_set(), "load_builtins deadlocked against a re-entrant call"
