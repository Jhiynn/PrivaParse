"""PrivaParse — a local privacy layer for text handed to an LLM.

Typical use::

    import privaparse

    result = privaparse.pseudonymize(text)
    answer = my_llm(result.text)              # sees only placeholders
    original = privaparse.reverse(result.mapping_id, answer)

The module-level functions delegate to a lazily created default
:class:`~privaparse.engine.PrivaParseEngine`. A long-running service should
construct its own engine once at startup and call its methods instead, so the
model is loaded a single time.
"""

from __future__ import annotations

from importlib import metadata

__version__ = metadata.version("privaparse")

__all__ = [
    "PrivaParseEngine",
    "__version__",
    "default_engine",
    "detect",
    "pseudonymize",
    "reset_default_engine",
    "reverse",
]


def __getattr__(name: str):
    if name in __all__:
        from privaparse import engine as _engine

        return getattr(_engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
