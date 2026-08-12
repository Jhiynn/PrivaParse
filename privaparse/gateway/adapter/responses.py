"""The OpenAI Responses API: its shape, and the walk over it.

A second protocol rather than a second branch, which is what the extraction
seam was built for. It is not a renamed Chat Completions:

* `messages` becomes `input`, which is either a bare string or a list of items
  drawn from a union with 32 members;
* the system prompt moves out to a top-level `instructions` string;
* the answer arrives as `output[]` items instead of `choices[]`;
* streaming uses typed events (`response.output_text.delta`) rather than
  chat chunks -- see `stream_responses.py`.

Codex CLI speaks only this protocol: `wire_api = "chat"` was removed in
February 2026, so Chat Completions cannot serve it at all.

**Four item types are walked.** `message`, `function_call`,
`function_call_output` and `reasoning` are what a coding agent sends;
everything else in that union stops the request. The union is long and still
growing, and a walk that guessed at members it had never seen would be
guessing about what leaves the machine.
"""

from __future__ import annotations

from typing import Any

from privaparse.gateway.extract import (
    TextNode,
    UnscannableField,
    _refuse_if_text,
    _walk_json_arguments,
    _walk_json_value,
)

__all__ = [
    "INPUT_FIELD",
    "INSTRUCTIONS_FIELD",
    "extract_input",
    "extract_output",
    "with_placeholder_hint",
]

INPUT_FIELD = "input"
INSTRUCTIONS_FIELD = "instructions"
OUTPUT_FIELD = "output"

# Top-level fields skipped whole. Several nest strings, which is the point of
# naming them: `reasoning` carries an effort setting, `text` a response format,
# `tools` the client's own schema -- the same argument the chat adapter makes
# for `response_format` and `tools`. Identifiers (`model`, `user`,
# `previous_response_id`, `conversation`, `prompt_cache_key`) must reach the
# provider verbatim or the request means something else.
IGNORED_REQUEST_FIELDS = frozenset({
    "background", "conversation", "include", "include_obfuscation",
    "max_output_tokens", "max_tool_calls", "metadata", "model",
    "parallel_tool_calls", "previous_response_id", "prompt_cache_key",
    "prompt_cache_options", "prompt_cache_retention", "reasoning",
    "safety_identifier", "service_tier", "store", "stream", "stream_options",
    "temperature", "text", "tool_choice", "tools", "top_logprobs", "top_p",
    "truncation", "ttl", "type", "user",
})

# Item types the walk understands.
MESSAGE_ITEM = "message"
FUNCTION_CALL_ITEM = "function_call"
FUNCTION_CALL_OUTPUT_ITEM = "function_call_output"
REASONING_ITEM = "reasoning"

# Structural item fields: identifiers and routing, never free text.
IGNORED_ITEM_FIELDS = frozenset({"type", "role", "id", "call_id", "status", "name"})

# Content parts. Only text can be scanned; an image or a file reference is
# data the detector cannot read, so forwarding it would send it unexamined.
TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text"})
PART_TEXT_FIELD = "text"


def extract_input(body: Any) -> list[TextNode]:
    """Every scannable string in a Responses request. Fails closed."""
    if not isinstance(body, dict):
        raise UnscannableField((), "the request body is not a JSON object")

    nodes: list[TextNode] = []
    for key, value in body.items():
        pointer = (key,)
        if key == INSTRUCTIONS_FIELD:
            # A system prompt, and an agent's system prompt is assembled from
            # whatever it has been reading.
            if isinstance(value, str):
                nodes.append(TextNode(pointer, value))
            elif value is not None:
                raise UnscannableField(pointer, "expected instructions to be a string")
        elif key == INPUT_FIELD:
            _walk_input(value, pointer, nodes)
        elif key in IGNORED_REQUEST_FIELDS:
            continue
        else:
            _refuse_if_text(value, pointer, "unknown request field")
    return nodes


def _walk_input(value: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if isinstance(value, str):
        nodes.append(TextNode(pointer, value))
        return
    if not isinstance(value, list):
        raise UnscannableField(pointer, "expected a string or a list of input items")

    for index, item in enumerate(value):
        at = pointer + (index,)
        if not isinstance(item, dict):
            raise UnscannableField(at, "expected an input item object")
        _walk_item(item, at, nodes)


def _walk_item(item: dict, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    # `type` is optional on EasyInputMessage, so an item with a role and
    # content is a message even when nothing says so.
    kind = item.get("type")
    if kind is None and "role" in item and "content" in item:
        kind = MESSAGE_ITEM

    if kind == REASONING_ITEM:
        # Encrypted content is opaque, and a summary is the model's own words
        # about text that was scanned where it entered. Neither is a place a
        # person typed something.
        return

    if kind == MESSAGE_ITEM:
        for key, value in item.items():
            field = pointer + (key,)
            if key in IGNORED_ITEM_FIELDS:
                continue
            if key == "content":
                _walk_content(value, field, nodes)
            else:
                _refuse_if_text(value, field, "unknown message field")
        return

    if kind == FUNCTION_CALL_ITEM:
        for key, value in item.items():
            field = pointer + (key,)
            if key in IGNORED_ITEM_FIELDS:
                continue
            if key == "arguments":
                _walk_json_arguments(value, field, nodes)
            else:
                _refuse_if_text(value, field, "unknown function call field")
        return

    if kind == FUNCTION_CALL_OUTPUT_ITEM:
        for key, value in item.items():
            field = pointer + (key,)
            if key in IGNORED_ITEM_FIELDS:
                continue
            if key == "output":
                # A tool result: the single largest way a codebase's contents
                # enter a conversation.
                if isinstance(value, str):
                    nodes.append(TextNode(field, value))
                elif isinstance(value, list):
                    _walk_content(value, field, nodes)
                elif value is not None:
                    raise UnscannableField(field, "expected a string or content list")
            else:
                _refuse_if_text(value, field, "unknown function call output field")
        return

    raise UnscannableField(pointer, f"input item of type {kind!r} cannot be scanned")


def _walk_content(value: Any, pointer: tuple[Any, ...], nodes: list[TextNode]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        nodes.append(TextNode(pointer, value))
        return
    if not isinstance(value, list):
        raise UnscannableField(pointer, "expected a string or a list of content parts")

    for index, part in enumerate(value):
        at = pointer + (index,)
        if not isinstance(part, dict):
            raise UnscannableField(at, "expected a content part object")
        if part.get("type") not in TEXT_PART_TYPES:
            raise UnscannableField(
                at, f"content part of type {part.get('type')!r} cannot be scanned"
            )
        for key, sub in part.items():
            field = at + (key,)
            if key == "type":
                continue
            if key == PART_TEXT_FIELD:
                if isinstance(sub, str):
                    nodes.append(TextNode(field, sub))
                else:
                    raise UnscannableField(field, "expected the text part to be a string")
            else:
                # `annotations` and `logprobs` ride along on output_text.
                _refuse_if_text(sub, field, "unknown content part field")


def with_placeholder_hint(body: dict) -> dict:
    """A copy of `body` with the hint as the first input item.

    An item rather than an edit of `instructions`: rewriting the caller's
    system prompt is a larger liberty than adding to the list, and a bare
    string `input` has no instructions to edit anyway.
    """
    from privaparse.gateway.adapter.openai import PLACEHOLDER_HINT

    if not isinstance(body, dict) or INPUT_FIELD not in body:
        return body
    existing = body[INPUT_FIELD]
    items = [existing] if isinstance(existing, str) else list(existing or ())
    if isinstance(existing, str):
        items = [{"type": MESSAGE_ITEM, "role": "user", "content": existing}]

    out = dict(body)
    out[INPUT_FIELD] = [
        {"type": MESSAGE_ITEM, "role": "system", "content": PLACEHOLDER_HINT},
        *items,
    ]
    return out


# --- the response direction, which never aborts ----------------------------


def extract_output(body: Any) -> list[TextNode]:
    """Every restorable string in a Responses answer. Never raises.

    The same asymmetry the chat adapter has: refusing on the way out protects
    a person, refusing on the way back only costs the caller their answer.
    """
    nodes: list[TextNode] = []
    if not isinstance(body, dict):
        return nodes
    items = body.get(OUTPUT_FIELD)
    if not isinstance(items, list):
        return nodes

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        at = (OUTPUT_FIELD, index)
        kind = item.get("type")

        if kind == MESSAGE_ITEM:
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part_index, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                text = part.get(PART_TEXT_FIELD)
                if part.get("type") in TEXT_PART_TYPES and isinstance(text, str):
                    nodes.append(TextNode(at + ("content", part_index, PART_TEXT_FIELD), text))

        elif kind == FUNCTION_CALL_ITEM:
            raw = item.get("arguments")
            if not isinstance(raw, str):
                continue
            root = at + ("arguments",)
            try:
                import json

                parsed = json.loads(raw)
            except ValueError:
                # A truncated tool call still deserves its placeholders back.
                nodes.append(TextNode(root, raw))
                continue
            _walk_json_value(parsed, root, root, nodes)

    return nodes
