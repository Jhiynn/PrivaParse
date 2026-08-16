"""The seam that decides what leaves the machine.

Every protocol adapter's request walk turns a body into a flat, ordered list
of `TextNode`s -- every piece of free text the request carries, each with a
pointer back to where it came from -- and `write_back` puts replacements into
a copy of that body. Everything between the two is the caller's business: one
`pseudonymize_batch` over `[n.text for n in nodes]` gives every node one
mapping, which is what makes the answer reversible.

This module holds what no protocol owns. The adapters hold the rest: each
knows the fields of its own protocol that carry text and the fields known to
carry none, and refuses anything else that carries a string, at any depth,
with the `UnscannableField` defined here.

That refusal will break the first time a provider adds a field, and it is
still the right trade. A gateway that forwards what it does not understand
leaks the moment a client adopts a new API feature, and does it silently --
the failure mode is a request that looks like it worked.

The response direction is the mirror image and deliberately asymmetric: an
adapter's answer walk never raises. A failure on the way out risks
disclosure; a failure on the way back shows a placeholder, so the answer walk
skips what it does not recognise rather than aborting an answer the caller
has already paid for.

An adapter walks its own protocol, and the pieces it needs to do that are
public: `TextNode` and `UnscannableField` to speak the same language,
`refuse_if_text` to apply the allow-list rule to a field with no rule of its
own, and `walk_json_arguments` / `walk_json_value` to walk a serialised
tool-call blob the way every protocol's tool calls have to be walked. That
set is the front door -- an adapter reaching past it for something private is
a sign the machinery it wants has not been named yet.

Nothing here imports an adapter. The dependency runs one way, which is what
keeps a protocol from becoming the special case the next one has to imitate.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

__all__ = [
    "TextNode",
    "UnscannableField",
    "refuse_if_text",
    "walk_json_arguments",
    "walk_json_value",
    "write_back",
]


def _render(pointer: tuple[Any, ...]) -> str:
    return "/".join(str(step) for step in pointer) or "<body>"


class UnscannableField(Exception):
    """A field carries text the gateway has no rule for, so nothing is sent.

    Carries the pointer rather than the value: the whole point of this class
    is a field that might hold a person, and putting it in an exception
    message would write it to whatever log catches the traceback.
    """

    def __init__(self, pointer: tuple[Any, ...], reason: str) -> None:
        self.pointer = tuple(pointer)
        self.reason = reason
        super().__init__(f"{_render(self.pointer)}: {reason}")


@dataclass(frozen=True)
class TextNode:
    """One piece of free text and the path it sits at.

    `json_root` is set when the text lives inside a serialised JSON string
    (tool-call arguments): it points at the string itself, and `pointer`
    continues into the parsed structure. `write_back` needs both to know
    which nodes share a `json.dumps`.
    """

    pointer: tuple[Any, ...]
    text: str
    json_root: tuple[Any, ...] | None = None
    was_number: bool = False


# --- the machinery an adapter's walk is built from -------------------------


def walk_json_arguments(raw: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    """A tool call's serialised arguments, parsed and walked to its leaves.

    The string itself is never a node: `write_back` re-serialises the parsed
    structure from the leaves, which is why every node underneath carries the
    same `json_root`.
    """
    if raw is None:
        return
    if not isinstance(raw, str):
        raise UnscannableField(pointer, "expected serialised JSON arguments")
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        # The reason names the error class, never the payload: arguments are
        # the likeliest place in the whole request to find an address.
        raise UnscannableField(
            pointer, f"arguments are not valid JSON ({type(error).__name__})"
        ) from error
    walk_json_value(parsed, pointer, pointer, nodes)


def walk_json_value(
    value: Any,
    pointer: tuple[Any, ...],
    json_root: tuple[Any, ...],
    nodes: list[TextNode],
) -> None:
    """Every leaf of a parsed arguments blob, strings and numbers alike.

    Numbers are walked because a phone number arrives as a JSON number often
    enough to matter. Keys are not: they are parameter names from the
    client's own schema, not anything a user typed.
    """
    if value is None or isinstance(value, bool):
        # `bool` before `int` on purpose -- `True` is an `int` in Python, and
        # scanning it would send the string "True" to the detector.
        return
    if isinstance(value, str):
        nodes.append(TextNode(pointer, value, json_root=json_root))
        return
    if isinstance(value, (int, float)):
        nodes.append(TextNode(pointer, str(value), json_root=json_root, was_number=True))
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            walk_json_value(sub, pointer + (key,), json_root, nodes)
        return
    if isinstance(value, list):
        for index, sub in enumerate(value):
            walk_json_value(sub, pointer + (index,), json_root, nodes)
        return
    raise UnscannableField(pointer, f"unsupported JSON value of type {type(value).__name__}")


def _contains_string(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, dict):
        return any(_contains_string(sub) for sub in value.values())
    if isinstance(value, list):
        return any(_contains_string(sub) for sub in value)
    return False


def refuse_if_text(value: Any, pointer: tuple[Any, ...], reason: str) -> None:
    """Refuse a field with no rule -- but only if it could carry a person.

    A number or a boolean under an unrecognised key cannot hold a name, and
    refusing it would turn every harmless provider addition into an outage.
    """
    if _contains_string(value):
        raise UnscannableField(pointer, f"{reason} carrying text the gateway cannot scan")


# --- write-back ------------------------------------------------------------


def write_back(body: Any, nodes: list[TextNode], replacements: list[str]) -> dict:
    """A copy of `body` with each node's text replaced, positionally.

    The input is never mutated: a failure further down the request path has
    to leave the caller's body exactly as it arrived.
    """
    nodes = list(nodes)
    replacements = list(replacements)
    if len(nodes) != len(replacements):
        raise ValueError(
            f"expected {len(nodes)} replacements to match the extracted nodes, "
            f"got {len(replacements)}"
        )

    out = copy.deepcopy(body)
    grouped: dict[tuple[Any, ...], list[tuple[TextNode, str]]] = {}

    for node, replacement in zip(nodes, replacements):
        if node.json_root is None:
            _set(out, node.pointer, replacement)
        else:
            grouped.setdefault(node.json_root, []).append((node, replacement))

    for json_root, items in grouped.items():
        parsed = json.loads(_get(out, json_root))
        for node, replacement in items:
            value: Any = replacement
            if node.was_number and replacement == node.text:
                # Untouched, so it goes back the way it came. A leaf that was
                # actually pseudonymised comes back as a string, which is
                # correct: a placeholder is not a number.
                value = json.loads(node.text)
            subpath = node.pointer[len(json_root):]
            if not subpath:
                parsed = value
            else:
                _set(parsed, subpath, value)
        _set(out, json_root, json.dumps(parsed, ensure_ascii=False))

    return out


def _get(container: Any, pointer: tuple[Any, ...]) -> Any:
    for step in pointer:
        container = container[step]
    return container


def _set(container: Any, pointer: tuple[Any, ...], value: Any) -> None:
    for step in pointer[:-1]:
        container = container[step]
    container[pointer[-1]] = value

