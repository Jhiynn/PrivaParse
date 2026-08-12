"""Everything the gateway knows about OpenAI's request and response shape.

`extract.py` holds the walking; this module holds the knowledge of where text
sits. Keeping the two apart is what makes a second protocol a new file rather
than a new branch inside the walker.

Two lists and one rule. `IGNORED_REQUEST_FIELDS` names the top-level fields
that carry no scannable text; the message fields below name what is walked.
Anything not named here that carries a string is refused by `extract` -- see
`UnscannableField` for why that trade is worth making.
"""

from __future__ import annotations

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
# `extract` refuses it rather than forwarding it unexamined.
PART_TYPE_FIELD = "type"
TEXT_PART_TYPE = "text"
TEXT_PART_FIELD = "text"

# Response shape. The response walk is deliberately lenient (see
# `extract_response`), so these are the places it looks rather than the only
# places it tolerates.
RESPONSE_CHOICES_FIELD = "choices"
RESPONSE_MESSAGE_FIELD = "message"

# The opt-in outbound hint. Deliberately short: it is prepended to somebody
# else's prompt, and every token of it is billed to them. It names the shape
# rather than explaining the scheme, because the provider does not need to be
# told what the placeholders stand for.
PLACEHOLDER_HINT = (
    "Some values in this conversation are replaced by privacy placeholders of the "
    "form [[TYPE_A1]]. Reproduce any such token exactly as written, character for "
    "character, including both pairs of square brackets. Never translate, rename, "
    "reformat, quote, split or omit one."
)


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
