"""The seam that decides what leaves the machine.

`extract` turns a request body into a flat, ordered list of `TextNode`s --
every piece of free text the request carries, each with a pointer back to
where it came from. `write_back` puts replacements into a copy of that body.
Everything between the two is the caller's business: one `pseudonymize_batch`
over `[n.text for n in nodes]` gives every node one mapping, which is what
makes the answer reversible.

The walk is an allow-list in both directions. `adapter.openai` names the
fields that hold text and the fields known to hold none; anything else that
carries a string, at any depth, raises `UnscannableField`.

That refusal will break the first time a provider adds a field, and it is
still the right trade. A gateway that forwards what it does not understand
leaks the moment a client adopts a new API feature, and does it silently --
the failure mode is a request that looks like it worked.

The response direction is the mirror image and deliberately asymmetric:
`extract_response` never raises. A failure on the way out risks disclosure; a
failure on the way back shows a placeholder, so the response walk skips what
it does not recognise rather than aborting an answer the caller has already
paid for.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from privaparse.gateway.adapter import openai as shape


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


# --- the request direction, which fails closed -----------------------------


def extract(body: Any) -> list[TextNode]:
    """Every scannable string in `body`, in a stable order.

    Raises `UnscannableField` on anything the walk has no rule for.
    """
    if not isinstance(body, dict):
        raise UnscannableField((), "the request body is not a JSON object")

    nodes: list[TextNode] = []
    for key, value in body.items():
        pointer = (key,)
        if key == shape.MESSAGES_FIELD:
            _walk_messages(value, pointer, nodes)
        elif key in shape.IGNORED_REQUEST_FIELDS:
            continue
        else:
            _refuse_if_text(value, pointer, "unknown request field")
    return nodes


def _walk_messages(messages: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if not isinstance(messages, list):
        raise UnscannableField(pointer, "expected a list of messages")

    for index, message in enumerate(messages):
        at = pointer + (index,)
        if not isinstance(message, dict):
            raise UnscannableField(at, "expected a message object")

        for key, value in message.items():
            field = at + (key,)
            if key in shape.IGNORED_MESSAGE_FIELDS:
                continue
            if key == shape.TOOL_CALLS_FIELD:
                _walk_tool_calls(value, field, nodes)
            elif key in shape.TEXT_MESSAGE_FIELDS:
                _walk_text_field(value, field, nodes)
            else:
                _refuse_if_text(value, field, "unknown message field")


def _walk_text_field(value: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    """A message text field: a string, a list of content parts, or absent."""
    if value is None:
        return
    if isinstance(value, str):
        nodes.append(TextNode(pointer, value))
        return
    if isinstance(value, list):
        _walk_content_parts(value, pointer, nodes)
        return
    raise UnscannableField(pointer, "expected a string or a list of content parts")


def _walk_content_parts(parts: list[Any], pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    for index, part in enumerate(parts):
        at = pointer + (index,)
        if not isinstance(part, dict):
            raise UnscannableField(at, "expected a content part object")

        part_type = part.get(shape.PART_TYPE_FIELD)
        if part_type != shape.TEXT_PART_TYPE:
            # An image, an audio clip, a file reference: the detector cannot
            # read any of it, so forwarding it would send unexamined content.
            raise UnscannableField(at, f"content part of type {part_type!r} cannot be scanned")

        for key, value in part.items():
            field = at + (key,)
            if key == shape.PART_TYPE_FIELD:
                continue
            if key == shape.TEXT_PART_FIELD:
                _walk_text_field(value, field, nodes)
            else:
                _refuse_if_text(value, field, "unknown content part field")


def _walk_tool_calls(calls: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if not isinstance(calls, list):
        raise UnscannableField(pointer, "expected a list of tool calls")

    for index, call in enumerate(calls):
        at = pointer + (index,)
        if not isinstance(call, dict):
            raise UnscannableField(at, "expected a tool call object")

        for key, value in call.items():
            field = at + (key,)
            if key in shape.IGNORED_TOOL_CALL_FIELDS:
                continue
            if key == shape.FUNCTION_FIELD:
                _walk_function(value, field, nodes)
            else:
                _refuse_if_text(value, field, "unknown tool call field")


def _walk_function(function: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if not isinstance(function, dict):
        raise UnscannableField(pointer, "expected a function object")

    for key, value in function.items():
        field = pointer + (key,)
        if key in shape.IGNORED_FUNCTION_FIELDS:
            continue
        if key in shape.JSON_FUNCTION_FIELDS:
            _walk_json_arguments(value, field, nodes)
        else:
            _refuse_if_text(value, field, "unknown function field")


def _walk_json_arguments(raw: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
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
    _walk_json_value(parsed, pointer, pointer, nodes)


def _walk_json_value(
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
            _walk_json_value(sub, pointer + (key,), json_root, nodes)
        return
    if isinstance(value, list):
        for index, sub in enumerate(value):
            _walk_json_value(sub, pointer + (index,), json_root, nodes)
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


def _refuse_if_text(value: Any, pointer: tuple[Any, ...], reason: str) -> None:
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


# --- the response direction, which never aborts ----------------------------


def extract_response(body: Any) -> list[TextNode]:
    """Every restorable string in a completion body. Never raises.

    Where `extract` refuses what it cannot place, this skips it. The
    asymmetry is the design: an unrecognised field on the way out might be a
    name being disclosed, while on the way back it is at worst a placeholder
    the user sees.
    """
    nodes: list[TextNode] = []
    if not isinstance(body, dict):
        return nodes

    choices = body.get(shape.RESPONSE_CHOICES_FIELD)
    if not isinstance(choices, list):
        return nodes

    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get(shape.RESPONSE_MESSAGE_FIELD)
        if not isinstance(message, dict):
            continue
        at = (shape.RESPONSE_CHOICES_FIELD, index, shape.RESPONSE_MESSAGE_FIELD)
        _collect_response_message(message, at, nodes)

    return nodes


def _collect_response_message(
    message: dict, pointer: tuple[Any, ...], nodes: list[TextNode]
) -> None:
    for field in ("content", "refusal"):
        value = message.get(field)
        if isinstance(value, str):
            nodes.append(TextNode(pointer + (field,), value))
        elif isinstance(value, list):
            for index, part in enumerate(value):
                if not isinstance(part, dict):
                    continue
                text = part.get(shape.TEXT_PART_FIELD)
                if isinstance(text, str):
                    nodes.append(
                        TextNode(pointer + (field, index, shape.TEXT_PART_FIELD), text)
                    )

    calls = message.get(shape.TOOL_CALLS_FIELD)
    if not isinstance(calls, list):
        return

    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        function = call.get(shape.FUNCTION_FIELD)
        if not isinstance(function, dict):
            continue
        raw = function.get(shape.FUNCTION_ARGUMENTS_FIELD)
        if not isinstance(raw, str):
            continue
        root = pointer + (
            shape.TOOL_CALLS_FIELD,
            index,
            shape.FUNCTION_FIELD,
            shape.FUNCTION_ARGUMENTS_FIELD,
        )
        try:
            parsed = json.loads(raw)
        except ValueError:
            # A truncated tool call still deserves its placeholders back, so
            # the whole string is restored as text rather than dropped.
            nodes.append(TextNode(root, raw))
            continue
        _walk_json_value(parsed, root, root, nodes)
