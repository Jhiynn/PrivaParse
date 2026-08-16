"""OpenAI Chat Completions: its shape, and the walk over it.

Everything the gateway knows about this one wire protocol -- where text sits
in a request, where it sits in an answer -- lives here, the way the Responses
adapter next door owns its own. The extraction seam holds what no protocol
owns: the text node, the refusal, the allow-list rule, the walks over a
serialised tool-call blob, and the write-back.

Two lists and one rule. `IGNORED_REQUEST_FIELDS` names the top-level fields
that carry no scannable text; the message fields below name what is walked.
Anything not named here that carries a string is refused -- see
`UnscannableField` for why that trade is worth making.
"""

from __future__ import annotations

from typing import Any

from privaparse.gateway.adapter.shared import PLACEHOLDER_HINT
from privaparse.gateway.extract import (
    TextNode,
    UnscannableField,
    refuse_if_text,
    walk_json_arguments,
    walk_restorable_arguments,
)

__all__ = [
    "extract_answer",
    "extract_request",
    "with_placeholder_hint",
]

# Top-level request fields skipped whole. Several of them are strings or nest
# strings, which is the point of listing them: `model` and `user` are
# identifiers the provider needs verbatim, `stop` holds client-chosen
# sequences, and `response_format` nests a JSON schema whose strings describe
# a shape rather than a person. Pseudonymising any of them would corrupt the
# request without protecting anyone.
#
# `tools` belongs to the same group and is the largest of them: a function
# name, a description of what it does, a JSON Schema for its parameters. All
# of it is written by the client's own code rather than typed by a user, and
# replacing a name or a description with a placeholder would degrade the
# model's choice of tool while protecting nobody. Refusing it is not the safer
# reading it appears to be -- it makes the gateway unusable with every agent
# and IDE that declares tools, which sends exactly those users back to the
# provider directly. What it does leave open is a client that writes a person
# into a tool description; that text is forwarded.
#
# The deprecated `functions` / `function_call` pair is deliberately absent. It
# is the same shape and the same argument, but nothing here is tested against
# it, and a refusal a caller can see beats an allowance nobody checked.
IGNORED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "tools",
        "temperature",
        "stream",
        "max_tokens",
        "max_completion_tokens",
        "top_p",
        "n",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "seed",
        "user",
        "logprobs",
        "top_logprobs",
        "response_format",
        "tool_choice",
        "parallel_tool_calls",
        "stream_options",
    }
)

MESSAGES_FIELD = "messages"

# Message fields holding free text. `name` is here rather than in the ignored
# set because it is client-supplied and names a participant; a placeholder in
# it can violate the provider's charset for that field, but that only happens
# when the field actually held a person, which is exactly the case where
# forwarding it would have been the worse outcome.
TEXT_MESSAGE_FIELDS = frozenset({"content", "name", "refusal"})

# Structural message fields: identifiers and routing, never free text.
IGNORED_MESSAGE_FIELDS = frozenset({"role", "tool_call_id"})

TOOL_CALLS_FIELD = "tool_calls"
IGNORED_TOOL_CALL_FIELDS = frozenset({"id", "type", "index"})
FUNCTION_FIELD = "function"

# The function name is chosen by the client's own code, not by a user typing.
IGNORED_FUNCTION_FIELDS = frozenset({"name"})

# Serialised JSON: parsed, walked to its leaves, and re-serialised on the way
# back out.
FUNCTION_ARGUMENTS_FIELD = "arguments"
JSON_FUNCTION_FIELDS = frozenset({FUNCTION_ARGUMENTS_FIELD})

# Multimodal content parts. Only a text part can be scanned; anything else --
# an image, audio, a file reference -- is data the detector cannot read, so
# `extract_request` refuses it rather than forwarding it unexamined. The one
# way past that is the operator's own `gateway_allow_images`, which is the
# allow-list's single deliberate hole and is scoped to these parts alone.
PART_TYPE_FIELD = "type"
TEXT_PART_TYPE = "text"
TEXT_PART_FIELD = "text"

# Response shape. The answer walk is deliberately lenient (see
# `extract_answer`), so these are the places it looks rather than the only
# places it tolerates.
RESPONSE_CHOICES_FIELD = "choices"
RESPONSE_MESSAGE_FIELD = "message"


# --- the request direction, which fails closed -----------------------------


def extract_request(body: Any, *, allow_images: bool = False) -> list[TextNode]:
    """Every scannable string in a Chat Completions request, in a stable order.

    Raises `UnscannableField` on anything the walk has no rule for.

    ``allow_images`` forwards a content part the detector cannot read rather
    than refusing it -- see ``Settings.gateway_allow_images`` for what that
    costs. It is scoped to content parts and to nothing else: an unknown
    request field, an unknown message field and a non-string where text
    belongs all still stop the request with it on.
    """
    if not isinstance(body, dict):
        raise UnscannableField((), "the request body is not a JSON object")

    nodes: list[TextNode] = []
    for key, value in body.items():
        pointer = (key,)
        if key == MESSAGES_FIELD:
            _walk_messages(value, pointer, nodes, allow_images)
        elif key in IGNORED_REQUEST_FIELDS:
            continue
        else:
            refuse_if_text(value, pointer, "unknown request field")
    return nodes


def _walk_messages(messages: Any, pointer: tuple[Any, ...], nodes: list[TextNode],
                   allow_images: bool = False) -> None:
    if not isinstance(messages, list):
        raise UnscannableField(pointer, "expected a list of messages")

    for index, message in enumerate(messages):
        at = pointer + (index,)
        if not isinstance(message, dict):
            raise UnscannableField(at, "expected a message object")

        for key, value in message.items():
            field = at + (key,)
            if key in IGNORED_MESSAGE_FIELDS:
                continue
            if key == TOOL_CALLS_FIELD:
                _walk_tool_calls(value, field, nodes)
            elif key in TEXT_MESSAGE_FIELDS:
                _walk_text_field(value, field, nodes, allow_images)
            else:
                refuse_if_text(value, field, "unknown message field")


def _walk_text_field(value: Any, pointer: tuple[Any, ...], nodes: list[TextNode],
                     allow_images: bool = False) -> None:
    """A message text field: a string, a list of content parts, or absent."""
    if value is None:
        return
    if isinstance(value, str):
        nodes.append(TextNode(pointer, value))
        return
    if isinstance(value, list):
        _walk_content_parts(value, pointer, nodes, allow_images)
        return
    raise UnscannableField(pointer, "expected a string or a list of content parts")


def _walk_content_parts(parts: list[Any], pointer: tuple[Any, ...], nodes: list[TextNode],
                        allow_images: bool = False) -> None:
    for index, part in enumerate(parts):
        at = pointer + (index,)
        if not isinstance(part, dict):
            raise UnscannableField(at, "expected a content part object")

        part_type = part.get(PART_TYPE_FIELD)
        if part_type != TEXT_PART_TYPE:
            # An image, an audio clip, a file reference: the detector cannot
            # read any of it, so forwarding it would send unexamined content.
            if allow_images:
                # Forwarded unexamined. The operator asked for that; see
                # Settings.gateway_allow_images. The part is skipped whole
                # rather than walked, so nothing inside it is scanned and
                # nothing inside it is replaced -- it reaches the provider as
                # it was written. Text in the same message is still walked,
                # which is the next iteration of this loop.
                continue
            raise UnscannableField(at, f"content part of type {part_type!r} cannot be scanned")

        for key, value in part.items():
            field = at + (key,)
            if key == PART_TYPE_FIELD:
                continue
            if key == TEXT_PART_FIELD:
                _walk_text_field(value, field, nodes, allow_images)
            else:
                refuse_if_text(value, field, "unknown content part field")


def _walk_tool_calls(calls: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if not isinstance(calls, list):
        raise UnscannableField(pointer, "expected a list of tool calls")

    for index, call in enumerate(calls):
        at = pointer + (index,)
        if not isinstance(call, dict):
            raise UnscannableField(at, "expected a tool call object")

        for key, value in call.items():
            field = at + (key,)
            if key in IGNORED_TOOL_CALL_FIELDS:
                continue
            if key == FUNCTION_FIELD:
                _walk_function(value, field, nodes)
            else:
                refuse_if_text(value, field, "unknown tool call field")


def _walk_function(function: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if not isinstance(function, dict):
        raise UnscannableField(pointer, "expected a function object")

    for key, value in function.items():
        field = pointer + (key,)
        if key in IGNORED_FUNCTION_FIELDS:
            continue
        if key in JSON_FUNCTION_FIELDS:
            walk_json_arguments(value, field, nodes)
        else:
            refuse_if_text(value, field, "unknown function field")


def with_placeholder_hint(body: dict) -> dict:
    """A copy of `body` with the hint prepended as its own system message.

    Its own message rather than an edit of the caller's: rewriting somebody
    else's system prompt is a larger liberty than adding to the list, and a
    separate message is trivial for them to spot in a request log.

    Called after pseudonymisation, so the hint never reaches the detector and
    can never be stored as an entity of its own.
    """
    if not isinstance(body, dict) or not isinstance(body.get(MESSAGES_FIELD), list):
        return body
    out = dict(body)
    out[MESSAGES_FIELD] = [
        {"role": "system", "content": PLACEHOLDER_HINT},
        *body[MESSAGES_FIELD],
    ]
    return out


# --- the response direction, which never aborts ----------------------------


def extract_answer(body: Any) -> list[TextNode]:
    """Every restorable string in a Chat Completions answer. Never raises.

    Where `extract_request` refuses what it cannot place, this skips it. The
    asymmetry is the design: an unrecognised field on the way out might be a
    name being disclosed, while on the way back it is at worst a placeholder
    the user sees.
    """
    nodes: list[TextNode] = []
    if not isinstance(body, dict):
        return nodes

    choices = body.get(RESPONSE_CHOICES_FIELD)
    if not isinstance(choices, list):
        return nodes

    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get(RESPONSE_MESSAGE_FIELD)
        if not isinstance(message, dict):
            continue
        at = (RESPONSE_CHOICES_FIELD, index, RESPONSE_MESSAGE_FIELD)
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
                text = part.get(TEXT_PART_FIELD)
                if isinstance(text, str):
                    nodes.append(
                        TextNode(pointer + (field, index, TEXT_PART_FIELD), text)
                    )

    calls = message.get(TOOL_CALLS_FIELD)
    if not isinstance(calls, list):
        return

    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        function = call.get(FUNCTION_FIELD)
        if not isinstance(function, dict):
            continue
        raw = function.get(FUNCTION_ARGUMENTS_FIELD)
        if not isinstance(raw, str):
            continue
        root = pointer + (
            TOOL_CALLS_FIELD,
            index,
            FUNCTION_FIELD,
            FUNCTION_ARGUMENTS_FIELD,
        )
        walk_restorable_arguments(raw, root, nodes)
